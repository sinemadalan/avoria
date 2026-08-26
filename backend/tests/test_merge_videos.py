import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app.application.ports.jobs import JobOperation
from backend.app.infrastructure.queue import _public_failure_message
from backend.app.infrastructure.storage import LocalStorageService
from backend.app.processing.compression import COMPRESSION_PROFILES
from backend.app.processing.conversion import (
    FFmpegConversionError,
    FFmpegRunner,
    LocalFFmpegCapabilities,
    OutputContainer,
)
from backend.app.processing.merge_videos import (
    IncompatibleMergeAudioError,
    IncompatibleMergeVideosError,
    InvalidMergeMetadataError,
    MergeMediaHasNoVideoError,
    MergeMediaNotFoundError,
    MergeVideosProfile,
    MergeVideosService,
    MergeVideosSpec,
    MergeTargetAspectRatio,
    UnsupportedMergeContainerError,
    build_concat_manifest,
    build_merge_command,
    build_normalize_command,
    resolve_target_aspect_ratio,
    validate_merge_compatibility,
)
from backend.app.processing.aspect_ratios import OutputAspectRatio
from backend.app.processing.probe import (
    MediaInspection,
    SyncFFprobeInspector,
    parse_ffprobe_payload,
)
from backend.app.schemas.jobs import JobCreateRequest, MergeVideosParameters
from backend.app.workers import tasks as tasks_module
from backend.app.workers.tasks import execute_media_job, process_media_job


def inspection(
    *,
    container: str = "mov,mp4,m4a,3gp,3g2,mj2",
    video: bool = True,
    video_codec: str | None = "h264",
    width: int | None = 96,
    height: int | None = 64,
    pixel_format: str | None = "yuv420p",
    frame_rate: str | None = "25/1",
    audio: bool = True,
    audio_codec: str | None = "aac",
    sample_rate: str | None = "44100",
    channels: int | None = 1,
    channel_layout: str | None = "mono",
) -> MediaInspection:
    streams: list[dict[str, object]] = []
    if video:
        video_stream = {
            "index": 0,
            "codec_type": "video",
            "codec_name": video_codec,
            "width": width,
            "height": height,
            "pix_fmt": pixel_format,
            "avg_frame_rate": frame_rate,
        }
        streams.append({key: value for key, value in video_stream.items() if value is not None})
    if audio:
        audio_stream = {
            "index": 1,
            "codec_type": "audio",
            "codec_name": audio_codec,
            "sample_rate": sample_rate,
            "channels": channels,
            "channel_layout": channel_layout,
        }
        streams.append({key: value for key, value in audio_stream.items() if value is not None})
    return parse_ffprobe_payload({"format": {"format_name": container}, "streams": streams})


@pytest.mark.parametrize("count", [2, 3, 5])
def test_merge_schema_accepts_two_or_more_ordered_ids(count: int) -> None:
    media_ids = [str(uuid4()) for _ in range(count)]
    request = JobCreateRequest.model_validate(
        {"operation": "merge_videos", "parameters": {"media_ids": media_ids, "target_aspect_ratio": "16:9"}}
    )
    assert request.media_id is None
    assert request.to_payload() == {"media_ids": media_ids, "target_aspect_ratio": "16:9"}


@pytest.mark.parametrize("ratio", ["16:9", "9:16", "1:1", "4:5", "first_video"])
def test_merge_schema_accepts_supported_target_ratios(ratio: str) -> None:
    media_ids = [str(uuid4()), str(uuid4())]
    parameters = MergeVideosParameters.model_validate(
        {"media_ids": media_ids, "target_aspect_ratio": ratio}
    )
    assert parameters.to_payload()["target_aspect_ratio"] == ratio


@pytest.mark.parametrize("ratio", ["3:2", "", None, 1])
def test_merge_schema_rejects_invalid_target_ratio(ratio: object) -> None:
    with pytest.raises(ValidationError):
        MergeVideosParameters.model_validate(
            {"media_ids": [str(uuid4()), str(uuid4())], "target_aspect_ratio": ratio}
        )


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (1920, 1080, OutputAspectRatio.LANDSCAPE),
        (1080, 1920, OutputAspectRatio.PORTRAIT),
        (1000, 1000, OutputAspectRatio.SQUARE),
        (1080, 1350, OutputAspectRatio.SOCIAL_PORTRAIT),
    ],
)
def test_first_video_maps_to_nearest_preset(
    width: int, height: int, expected: OutputAspectRatio
) -> None:
    assert resolve_target_aspect_ratio(
        MergeTargetAspectRatio.FIRST_VIDEO,
        inspection(width=width, height=height),
    ) is expected


@pytest.mark.parametrize("media_ids", [[], [str(uuid4())]])
def test_merge_schema_rejects_fewer_than_two_ids(media_ids: list[str]) -> None:
    with pytest.raises(ValidationError):
        MergeVideosParameters.model_validate({"media_ids": media_ids, "target_aspect_ratio": "16:9"})


@pytest.mark.parametrize(
    "parameters",
    [
        {"media_ids": [str(uuid4()), "invalid"], "target_aspect_ratio": "16:9"},
        {"media_ids": [str(uuid4()), str(uuid4())], "target_aspect_ratio": "16:9", "transition": "fade"},
    ],
)
def test_merge_schema_rejects_invalid_ids_and_extra_fields(
    parameters: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        MergeVideosParameters.model_validate(parameters)


def test_merge_parameters_are_rejected_for_another_operation() -> None:
    with pytest.raises(ValidationError):
        JobCreateRequest.model_validate(
            {
                "media_id": str(uuid4()),
                "operation": "mute",
                "parameters": {"media_ids": [str(uuid4()), str(uuid4())], "target_aspect_ratio": "16:9"},
            }
        )


def test_existing_operations_still_require_root_media_id() -> None:
    with pytest.raises(ValidationError):
        JobCreateRequest.model_validate({"operation": "mute", "parameters": {}})


def test_compatible_video_and_audio_streams_resolve_first_container() -> None:
    paths = [Path("a.mp4"), Path("b.mp4")]
    profile = validate_merge_compatibility(paths, [inspection(), inspection()])
    assert profile.extension == "mp4"
    assert profile.muxer == "mp4"
    assert profile.has_audio is True


def test_all_video_only_inputs_are_compatible() -> None:
    paths = [Path("a.webm"), Path("b.webm")]
    profile = validate_merge_compatibility(
        paths,
        [
            inspection(container="matroska,webm", video_codec="vp9", audio=False),
            inspection(container="matroska,webm", video_codec="vp9", audio=False),
        ],
    )
    assert profile.extension == "webm"
    assert profile.has_audio is False


def test_audio_only_input_is_rejected() -> None:
    with pytest.raises(MergeMediaHasNoVideoError):
        validate_merge_compatibility(
            [Path("a.mp4"), Path("b.mp4")],
            [inspection(), inspection(video=False)],
        )


@pytest.mark.parametrize(
    "changed",
    [
        {"width": 128},
        {"height": 72},
        {"video_codec": "hevc"},
        {"pixel_format": "yuv444p"},
        {"frame_rate": "30/1"},
    ],
)
def test_incompatible_video_parameters_are_rejected(changed: dict[str, object]) -> None:
    with pytest.raises(IncompatibleMergeVideosError):
        validate_merge_compatibility(
            [Path("a.mp4"), Path("b.mp4")],
            [inspection(), inspection(**changed)],
        )


@pytest.mark.parametrize("missing", ["video_codec", "width", "height", "pixel_format", "frame_rate"])
def test_missing_required_video_metadata_is_safe_failure(missing: str) -> None:
    with pytest.raises(InvalidMergeMetadataError):
        validate_merge_compatibility(
            [Path("a.mp4"), Path("b.mp4")],
            [inspection(), inspection(**{missing: None})],
        )


def test_mixed_audio_presence_is_rejected() -> None:
    with pytest.raises(IncompatibleMergeAudioError):
        validate_merge_compatibility(
            [Path("a.mp4"), Path("b.mp4")],
            [inspection(), inspection(audio=False)],
        )


@pytest.mark.parametrize(
    "changed",
    [
        {"audio_codec": "mp3"},
        {"sample_rate": "48000"},
        {"channels": 2},
        {"channel_layout": "stereo"},
    ],
)
def test_incompatible_audio_parameters_are_rejected(changed: dict[str, object]) -> None:
    with pytest.raises(IncompatibleMergeAudioError):
        validate_merge_compatibility(
            [Path("a.mp4"), Path("b.mp4")],
            [inspection(), inspection(**changed)],
        )


def test_different_containers_are_rejected() -> None:
    with pytest.raises(UnsupportedMergeContainerError):
        validate_merge_compatibility(
            [Path("a.mp4"), Path("b.mkv")],
            [inspection(), inspection(container="matroska,webm")],
        )


def test_normalization_profile_accepts_mixed_dimensions_fps_audio_and_containers() -> None:
    paths = [Path("a.mp4"), Path("b.mov")]
    service = MergeVideosService(
        "ffmpeg",
        StubInspector([
            inspection(width=1920, height=1080, frame_rate="30/1", sample_rate="44100", channels=1, channel_layout="mono"),
            inspection(width=1080, height=1920, frame_rate="30000/1001", audio=False),
        ]),  # type: ignore[arg-type]
        ManifestCapturingRunner(),  # type: ignore[arg-type]
        LocalFFmpegCapabilities(frozenset({"libx264", "aac"}), frozenset({"mp4"})),
    )
    profile = service.resolve_profile(
        paths,
        MergeVideosSpec((str(uuid4()), str(uuid4())), MergeTargetAspectRatio.PORTRAIT),
    )
    assert (profile.output_width, profile.output_height) == (1080, 1920)
    assert profile.has_audio is True
    assert profile.direct_copy is False
    assert profile.extension == "mp4"


def test_normalize_command_uses_fit_padding_cfr_common_pixel_format_and_silence(tmp_path: Path) -> None:
    media = COMPRESSION_PROFILES[OutputContainer.MP4]
    profile = MergeVideosProfile(
        "mp4", "mp4", media, True, OutputAspectRatio.PORTRAIT,
        1080, 1920, (), False,
    )
    command = build_normalize_command(
        "ffmpeg", tmp_path / "input.mp4", tmp_path / "output.mp4", profile, False
    )
    video_filter = command[command.index("-vf") + 1]
    assert "force_original_aspect_ratio=decrease" in video_filter
    assert "pad=1080:1920" in video_filter
    assert "setsar=1" in video_filter
    assert "fps=30" in video_filter
    assert "format=yuv420p" in video_filter
    assert "anullsrc=r=48000:cl=stereo" in command
    assert command[command.index("-ar") + 1] == "48000"
    assert command[command.index("-ac") + 1] == "2"


def test_manifest_and_command_preserve_request_order(tmp_path: Path) -> None:
    paths = [tmp_path / "B.mp4", tmp_path / "A.mp4", tmp_path / "C.mp4"]
    manifest = build_concat_manifest(paths)
    assert [line.rsplit("/", 1)[-1] for line in manifest.splitlines()] == [
        "B.mp4'",
        "A.mp4'",
        "C.mp4'",
    ]
    profile = MergeVideosProfile("mp4", "mp4", COMPRESSION_PROFILES[OutputContainer.MP4], True)
    command = build_merge_command("ffmpeg", tmp_path / "list.txt", tmp_path / "out.mp4", profile)
    assert command[command.index("-f") + 1] == "concat"
    assert command[command.index("-safe") + 1] == "0"
    assert command[command.index("-c") + 1] == "copy"
    assert "0:v:0" in command and "0:a:0" in command
    assert command[-2:] == ["mp4", str(tmp_path / "out.mp4")]


class StubInspector:
    def __init__(self, results: list[MediaInspection]) -> None:
        self.results = iter(results)
        self.paths: list[Path] = []

    def inspect(self, path: Path) -> MediaInspection:
        self.paths.append(path)
        return next(self.results)


class ManifestCapturingRunner:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.command: list[str] | None = None
        self.manifest_text: str | None = None
        self.manifest_path: Path | None = None

    def run(self, command: list[str]) -> None:
        self.command = command
        self.manifest_path = Path(command[command.index("-i") + 1])
        self.manifest_text = self.manifest_path.read_text(encoding="utf-8")
        if self.error:
            raise self.error


class NormalizationRunner:
    def __init__(self, fail_on_call: int) -> None:
        self.calls = 0
        self.fail_on_call = fail_on_call

    def run(self, command: list[str]) -> None:
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise FFmpegConversionError("private normalization diagnostic")
        Path(command[-1]).write_bytes(b"segment")


def service_with_runner(runner: ManifestCapturingRunner) -> MergeVideosService:
    return MergeVideosService(
        "ffmpeg",
        StubInspector([
            inspection(width=1920, height=1080, frame_rate="30/1", sample_rate="48000", channels=2, channel_layout="stereo"),
            inspection(width=1920, height=1080, frame_rate="30/1", sample_rate="48000", channels=2, channel_layout="stereo"),
        ]),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
        LocalFFmpegCapabilities(frozenset({"libx264", "aac"}), frozenset({"mp4"})),
    )


@pytest.mark.parametrize("fails", [False, True])
def test_manifest_is_job_scoped_and_cleaned_on_success_or_failure(
    fails: bool, tmp_path: Path,
) -> None:
    paths = [tmp_path / "first.mp4", tmp_path / "second.mp4"]
    runner = ManifestCapturingRunner(FFmpegConversionError("private") if fails else None)
    service = service_with_runner(runner)
    profile = service.resolve_profile(paths, MergeVideosSpec(tuple(str(uuid4()) for _ in paths), MergeTargetAspectRatio.LANDSCAPE))
    output = tmp_path / f"{uuid4()}.part.mp4"
    if fails:
        with pytest.raises(FFmpegConversionError):
            service.merge(paths, output, profile)
    else:
        service.merge(paths, output, profile)
    assert runner.manifest_text == build_concat_manifest(paths)
    assert runner.manifest_path is not None
    assert runner.manifest_path.parent == output.parent
    assert not runner.manifest_path.exists()


@pytest.mark.parametrize("fail_on_call", [1, 3])
def test_normalized_segments_and_manifest_are_cleaned_after_failure(
    fail_on_call: int, tmp_path: Path
) -> None:
    paths = [tmp_path / "first.mp4", tmp_path / "second.mp4"]
    inspections = (
        inspection(width=1280, height=720, frame_rate="30000/1001"),
        inspection(width=1080, height=1920, audio=False),
    )
    runner = NormalizationRunner(fail_on_call)
    service = MergeVideosService(
        "ffmpeg",
        StubInspector(list(inspections)),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
        LocalFFmpegCapabilities(frozenset({"libx264", "aac"}), frozenset({"mp4"})),
    )
    spec = MergeVideosSpec((str(uuid4()), str(uuid4())), MergeTargetAspectRatio.LANDSCAPE)
    profile = service.resolve_profile(paths, spec)
    with pytest.raises(FFmpegConversionError):
        service.merge(paths, tmp_path / "output.part.mp4", profile)
    assert not list(tmp_path.glob("*.normalized.mp4"))
    assert not list(tmp_path.glob("*.concat.txt"))


class WritingMergeService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.resolved: list[Path] = []
        self.merged: list[Path] = []

    def resolve_profile(self, paths: list[Path], _spec: MergeVideosSpec) -> MergeVideosProfile:
        self.resolved = paths.copy()
        return MergeVideosProfile("mp4", "mp4", COMPRESSION_PROFILES[OutputContainer.MP4], True)

    def merge(self, paths: list[Path], output: Path, _profile: MergeVideosProfile) -> None:
        self.merged = paths.copy()
        output.write_bytes(b"merged")
        if self.error:
            raise self.error


def test_worker_resolves_in_order_atomically_finalizes_and_cleans_partial(tmp_path: Path) -> None:
    ids = [str(uuid4()) for _ in range(3)]
    job_id = str(uuid4())
    storage = LocalStorageService(tmp_path / "media", tmp_path / "uploads", tmp_path / "outputs")
    storage.initialize()
    for media_id in ids:
        (tmp_path / "uploads" / f"{media_id}.mp4").write_bytes(b"source")
    merger = WritingMergeService()
    result = execute_media_job(
        job_id=job_id,
        media_id=ids[0],
        operation=JobOperation.MERGE_VIDEOS,
        storage=storage,
        converter=None,
        conversion=None,
        merge_videos_service=merger,  # type: ignore[arg-type]
        merge_videos=MergeVideosSpec(tuple(ids), MergeTargetAspectRatio.LANDSCAPE),
        allowed_extensions=[".mp4"],
    )
    assert [path.stem for path in merger.resolved] == ids
    assert [path.stem for path in merger.merged] == ids
    assert (tmp_path / "outputs" / f"{job_id}.mp4").read_bytes() == b"merged"
    assert not (tmp_path / "outputs" / f"{job_id}.part.mp4").exists()
    assert result["format"] == "mp4"

    failed_id = str(uuid4())
    failing = WritingMergeService(FFmpegConversionError("private path"))
    with pytest.raises(FFmpegConversionError):
        execute_media_job(
            job_id=failed_id,
            media_id=ids[0],
            operation=JobOperation.MERGE_VIDEOS,
            storage=storage,
            converter=None,
            conversion=None,
            merge_videos_service=failing,  # type: ignore[arg-type]
            merge_videos=MergeVideosSpec(tuple(ids), MergeTargetAspectRatio.LANDSCAPE),
            allowed_extensions=[".mp4"],
        )
    assert not (tmp_path / "outputs" / f"{failed_id}.part.mp4").exists()
    assert not (tmp_path / "outputs" / f"{failed_id}.mp4").exists()


def test_celery_worker_dispatches_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    job_id, ids = str(uuid4()), [str(uuid4()), str(uuid4())]
    merger, captured = object(), {}

    class StubStorage:
        def initialize(self) -> None:
            pass

    def capture(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"output_id": job_id, "filename": f"{job_id}.mp4", "format": "mp4"}

    monkeypatch.setattr(process_media_job, "update_state", lambda **_kwargs: None)
    monkeypatch.setattr(tasks_module, "get_settings", lambda: SimpleNamespace(allowed_media_extensions=[".mp4"]))
    monkeypatch.setattr(tasks_module, "get_storage_service", StubStorage)
    monkeypatch.setattr(tasks_module, "get_merge_videos_service", lambda: merger)
    monkeypatch.setattr(tasks_module, "execute_media_job", capture)
    process_media_job.push_request(id=job_id)
    try:
        process_media_job.run(ids[0], "merge_videos", {"media_ids": ids, "target_aspect_ratio": "16:9"})
    finally:
        process_media_job.pop_request()
    assert captured["merge_videos_service"] is merger
    assert captured["merge_videos"] == MergeVideosSpec(tuple(ids), MergeTargetAspectRatio.LANDSCAPE)


def test_merge_errors_map_to_safe_messages() -> None:
    assert _public_failure_message(IncompatibleMergeVideosError("detail"), JobOperation.MERGE_VIDEOS) == "Selected videos are not compatible for direct merge"
    assert _public_failure_message(IncompatibleMergeAudioError("detail"), JobOperation.MERGE_VIDEOS) == "Selected videos have incompatible audio streams"
    assert _public_failure_message(FFmpegConversionError(r"C:\\private\\clip.mp4"), JobOperation.MERGE_VIDEOS) == "Video merge failed"
    assert _public_failure_message(MergeMediaNotFoundError(), JobOperation.MERGE_VIDEOS) == "One of the selected media files was not found"


def test_real_ffmpeg_normalizes_mixed_phone_videos(tmp_path: Path) -> None:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg and FFprobe are required for integration tests")
    colors = ["red", "green", "blue"]
    sizes = ["1920x1080", "1280x720", "1080x1920"]
    rates = ["30", "30000/1001", "30"]
    inputs = [tmp_path / f"{index}.mp4" for index in range(len(colors))]
    for index, (path, color, size, rate) in enumerate(zip(inputs, colors, sizes, rates, strict=True)):
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color={color}:size={size}:rate={rate}:duration=0.5",
        ]
        if index != 2:
            sample_rate = "48000" if index == 0 else "44100"
            command.extend(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency=440:sample_rate={sample_rate}:duration=0.5",
                    "-shortest",
                ]
            )
        command.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p"])
        if index != 2:
            command.extend(["-c:a", "aac", "-ac", "2"])
        command.append(str(path))
        subprocess.run(command, check=True, capture_output=True, timeout=30)
    output = tmp_path / "merged.part.mp4"
    service = MergeVideosService(
        ffmpeg,
        SyncFFprobeInspector(ffprobe, 30),
        FFmpegRunner(60),
        LocalFFmpegCapabilities(frozenset({"libx264", "aac"}), frozenset({"mp4"})),
    )
    profile = service.resolve_profile(inputs, MergeVideosSpec(tuple(str(uuid4()) for _ in inputs), MergeTargetAspectRatio.LANDSCAPE))
    service.merge(inputs, output, profile)
    result = SyncFFprobeInspector(ffprobe, 30).inspect(output)
    assert result.video_streams
    assert result.audio_streams
    assert "mp4" in (result.format.name or "")
    assert (result.video_streams[0].width, result.video_streams[0].height) == (1920, 1080)
    assert result.video_streams[0].frame_rate == pytest.approx(30, abs=0.01)
    assert result.video_streams[0].pixel_format == "yuv420p"
    assert result.audio_streams[0].sample_rate == 48000
    assert result.audio_streams[0].channels == 2
    assert result.format.duration_seconds == pytest.approx(1.5, abs=0.25)
    assert output.stat().st_size > 0
    sampled_pixels: list[bytes] = []
    for timestamp in (0.2, 0.7, 1.2):
        sampled = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(timestamp),
                "-i", str(output), "-frames:v", "1", "-vf", "scale=1:1",
                "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        sampled_pixels.append(sampled.stdout[:3])
    red, green, blue = sampled_pixels
    assert red[0] > red[1] and red[0] > red[2]
    assert green[1] > green[0] and green[1] > green[2]
    assert blue[2] > blue[0] and blue[2] > blue[1]
    assert not list(tmp_path.glob("*.normalized.mp4"))
    assert not list(tmp_path.glob("*.concat.txt"))


def test_real_ffmpeg_portrait_canvas_fits_without_stretch_and_preserves_order(
    tmp_path: Path,
) -> None:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg and FFprobe are required for integration tests")
    inputs = [tmp_path / "horizontal.mp4", tmp_path / "vertical.mp4"]
    for path, source in zip(
        inputs,
        [
            "color=red:size=1920x1080:rate=30:duration=0.4",
            "color=green:size=1080x1920:rate=30:duration=0.4",
        ],
        strict=True,
    ):
        subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", source,
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    output = tmp_path / "portrait.part.mp4"
    service = MergeVideosService(
        ffmpeg,
        SyncFFprobeInspector(ffprobe, 30),
        FFmpegRunner(60),
        LocalFFmpegCapabilities(frozenset({"libx264", "aac"}), frozenset({"mp4"})),
    )
    profile = service.resolve_profile(
        inputs,
        MergeVideosSpec(
            (str(uuid4()), str(uuid4())), MergeTargetAspectRatio.PORTRAIT
        ),
    )
    service.merge(inputs, output, profile)
    result = SyncFFprobeInspector(ffprobe, 30).inspect(output)
    assert (result.video_streams[0].width, result.video_streams[0].height) == (1080, 1920)
    assert result.video_streams[0].frame_rate == pytest.approx(30, abs=0.01)
    assert not result.audio_streams

    def pixel(timestamp: float, x: int, y: int) -> bytes:
        sampled = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(timestamp),
                "-i", str(output), "-frames:v", "1",
                "-vf", f"crop=2:2:{x}:{y},scale=1:1", "-pix_fmt", "rgb24",
                "-f", "rawvideo", "-",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return sampled.stdout[:3]

    top_padding = pixel(0.15, 540, 10)
    first_center = pixel(0.15, 540, 960)
    second_center = pixel(0.55, 540, 960)
    assert max(top_padding) < 20
    assert first_center[0] > first_center[1] and first_center[0] > first_center[2]
    assert second_center[1] > second_center[0] and second_center[1] > second_center[2]
