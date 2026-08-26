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
    UnsupportedMergeContainerError,
    build_concat_manifest,
    build_merge_command,
    validate_merge_compatibility,
)
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
        {"operation": "merge_videos", "parameters": {"media_ids": media_ids}}
    )
    assert request.media_id is None
    assert request.to_payload() == {"media_ids": media_ids}


@pytest.mark.parametrize("media_ids", [[], [str(uuid4())]])
def test_merge_schema_rejects_fewer_than_two_ids(media_ids: list[str]) -> None:
    with pytest.raises(ValidationError):
        MergeVideosParameters.model_validate({"media_ids": media_ids})


@pytest.mark.parametrize(
    "parameters",
    [
        {"media_ids": [str(uuid4()), "invalid"]},
        {"media_ids": [str(uuid4()), str(uuid4())], "transition": "fade"},
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
                "parameters": {"media_ids": [str(uuid4()), str(uuid4())]},
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


def service_with_runner(runner: ManifestCapturingRunner) -> MergeVideosService:
    return MergeVideosService(
        "ffmpeg",
        StubInspector([inspection(), inspection()]),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
        LocalFFmpegCapabilities(frozenset(), frozenset({"mp4"})),
    )


@pytest.mark.parametrize("fails", [False, True])
def test_manifest_is_job_scoped_and_cleaned_on_success_or_failure(
    fails: bool, tmp_path: Path,
) -> None:
    paths = [tmp_path / "first.mp4", tmp_path / "second.mp4"]
    runner = ManifestCapturingRunner(FFmpegConversionError("private") if fails else None)
    service = service_with_runner(runner)
    profile = service.resolve_profile(paths)
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


class WritingMergeService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.resolved: list[Path] = []
        self.merged: list[Path] = []

    def resolve_profile(self, paths: list[Path]) -> MergeVideosProfile:
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
        merge_videos=MergeVideosSpec(tuple(ids)),
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
            merge_videos=MergeVideosSpec(tuple(ids)),
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
        process_media_job.run(ids[0], "merge_videos", {"media_ids": ids})
    finally:
        process_media_job.pop_request()
    assert captured["merge_videos_service"] is merger
    assert captured["merge_videos"] == MergeVideosSpec(tuple(ids))


def test_merge_errors_map_to_safe_messages() -> None:
    assert _public_failure_message(IncompatibleMergeVideosError("detail"), JobOperation.MERGE_VIDEOS) == "Selected videos are not compatible for direct merge"
    assert _public_failure_message(IncompatibleMergeAudioError("detail"), JobOperation.MERGE_VIDEOS) == "Selected videos have incompatible audio streams"
    assert _public_failure_message(FFmpegConversionError(r"C:\\private\\clip.mp4"), JobOperation.MERGE_VIDEOS) == "Video merge failed"
    assert _public_failure_message(MergeMediaNotFoundError(), JobOperation.MERGE_VIDEOS) == "One of the selected media files was not found"


def test_real_ffmpeg_merge_preserves_audio_and_summed_duration(tmp_path: Path) -> None:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg and FFprobe are required for integration tests")
    colors = ["red", "green", "blue"]
    inputs = [tmp_path / f"{index}.mp4" for index in range(len(colors))]
    for path, color in zip(inputs, colors, strict=True):
        subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"color={color}:size=96x64:rate=25:duration=1",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=1",
                "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-ar", "44100", "-ac", "1", str(path),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    output = tmp_path / "merged.part.mp4"
    service = MergeVideosService(
        ffmpeg,
        SyncFFprobeInspector(ffprobe, 30),
        FFmpegRunner(60),
        LocalFFmpegCapabilities(frozenset(), frozenset({"mp4"})),
    )
    profile = service.resolve_profile(inputs)
    service.merge(inputs, output, profile)
    result = SyncFFprobeInspector(ffprobe, 30).inspect(output)
    assert result.video_streams
    assert result.audio_streams
    assert "mp4" in (result.format.name or "")
    assert result.format.duration_seconds == pytest.approx(3, abs=0.2)
    assert output.stat().st_size > 0
    sampled_pixels: list[bytes] = []
    for timestamp in (0.25, 1.25, 2.25):
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
