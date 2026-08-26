from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app.application.ports.jobs import JobOperation
from backend.app.infrastructure.queue import _public_failure_message
from backend.app.infrastructure.storage import LocalStorageService
from backend.app.processing.compression import COMPRESSION_PROFILES, CompressionProfile
from backend.app.processing.conversion import (
    FFmpegConversionError,
    FFmpegRunner,
    LocalFFmpegCapabilities,
    OutputContainer,
)
from backend.app.processing.crop import (
    BLUR_SIGMA,
    CROP_OUTPUT_DIMENSIONS,
    CropAspectRatio,
    CropBackgroundType,
    CropMediaHasNoVideoError,
    CropMode,
    CropProfile,
    CropService,
    CropSpec,
    InvalidCropDimensionsError,
    InvalidCropSpecError,
    build_crop_command,
    build_crop_filter,
    build_fit_blur_filter,
    build_fit_color_filter,
    calculate_crop_dimensions,
    calculate_output_dimensions,
    normalize_hex_color,
)
from backend.app.processing.probe import (
    MediaInspection,
    SyncFFprobeInspector,
    parse_ffprobe_payload,
)
from backend.app.schemas.jobs import CropParameters, JobCreateRequest
from backend.app.workers import tasks as tasks_module
from backend.app.workers.tasks import execute_media_job, process_media_job


def inspection(
    *, width: int = 1920, height: int = 1080, video: bool = True, audio: bool = True
) -> MediaInspection:
    streams: list[dict[str, object]] = []
    if video:
        streams.append(
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": width,
                "height": height,
            }
        )
    if audio:
        streams.append({"index": 1, "codec_type": "audio", "codec_name": "aac"})
    return parse_ffprobe_payload(
        {
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
            "streams": streams,
        }
    )


class StubInspector:
    def __init__(self, result: MediaInspection) -> None:
        self.result = result

    def inspect(self, _path: Path) -> MediaInspection:
        return self.result


class CapturingRunner:
    def __init__(self, error: Exception | None = None) -> None:
        self.command: list[str] | None = None
        self.error = error

    def run(self, command: list[str]) -> None:
        self.command = command
        if self.error is not None:
            raise self.error


def profile(
    ratio: CropAspectRatio,
    *,
    source_width: int = 1920,
    source_height: int = 1080,
    container: OutputContainer = OutputContainer.MP4,
    has_audio: bool = True,
) -> CropProfile:
    width, height = calculate_output_dimensions(source_width, source_height, ratio)
    return CropProfile(
        media=COMPRESSION_PROFILES[container],
        source_width=source_width,
        source_height=source_height,
        output_width=width,
        output_height=height,
        has_audio=has_audio,
    )


@pytest.mark.parametrize(
    "aspect_ratio",
    ["16:9", "9:16", "1:1", "4:5"],
)
@pytest.mark.parametrize("mode", ["crop", "fit"])
def test_crop_schema_accepts_only_supported_combinations(
    aspect_ratio: str, mode: str
) -> None:
    parameters = CropParameters.model_validate(
        {"aspect_ratio": aspect_ratio, "mode": mode}
    )
    expected = {"aspect_ratio": aspect_ratio, "mode": mode}
    if mode == "fit":
        expected.update(background_type="color", background_color="#000000")
    assert parameters.to_payload() == expected


@pytest.mark.parametrize(
    "parameters",
    [
        {"aspect_ratio": "3:2", "mode": "crop"},
        {"aspect_ratio": "9:16", "mode": "fill"},
        {"mode": "crop"},
        {"aspect_ratio": "9:16"},
        {"aspect_ratio": "9:16", "mode": "crop", "width": 720},
    ],
)
def test_crop_schema_rejects_unsupported_missing_and_extra_parameters(
    parameters: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        CropParameters.model_validate(parameters)


def test_crop_parameters_are_rejected_for_another_operation() -> None:
    with pytest.raises(ValidationError):
        JobCreateRequest.model_validate(
            {
                "media_id": str(uuid4()),
                "operation": "compress",
                "parameters": {"aspect_ratio": "9:16", "mode": "crop"},
            }
        )


def test_crop_request_serializes_primitive_worker_payload() -> None:
    request = JobCreateRequest.model_validate(
        {
            "media_id": str(uuid4()),
            "operation": "crop",
            "parameters": {"aspect_ratio": "4:5", "mode": "fit"},
        }
    )
    assert request.operation is JobOperation.CROP
    assert request.to_payload() == {
        "aspect_ratio": "4:5",
        "mode": "fit",
        "background_type": "color",
        "background_color": "#000000",
    }


@pytest.mark.parametrize("color", ["#7a4fd8", "#7A4FD8", "#000000", "#FFFFFF"])
def test_fit_color_accepts_and_normalizes_valid_hex(color: str) -> None:
    parameters = CropParameters.model_validate(
        {
            "aspect_ratio": "9:16",
            "mode": "fit",
            "background_type": "color",
            "background_color": color,
        }
    )
    assert parameters.background_color == color.upper()
    assert parameters.to_payload()["background_color"] == color.upper()


def test_fit_blur_is_valid_without_color() -> None:
    parameters = CropParameters.model_validate(
        {"aspect_ratio": "9:16", "mode": "fit", "background_type": "blur"}
    )
    assert parameters.to_payload() == {
        "aspect_ratio": "9:16",
        "mode": "fit",
        "background_type": "blur",
    }


@pytest.mark.parametrize(
    "parameters",
    [
        {"aspect_ratio": "9:16", "mode": "fit", "background_type": "color"},
        {
            "aspect_ratio": "9:16",
            "mode": "fit",
            "background_type": "color",
            "background_color": "#FFF",
        },
        {
            "aspect_ratio": "9:16",
            "mode": "fit",
            "background_type": "color",
            "background_color": "red",
        },
        {
            "aspect_ratio": "9:16",
            "mode": "fit",
            "background_type": "blur",
            "background_color": "#FFFFFF",
        },
        {"aspect_ratio": "9:16", "mode": "crop", "background_type": "blur"},
        {"aspect_ratio": "9:16", "mode": "crop", "background_color": "#FFFFFF"},
        {
            "aspect_ratio": "9:16",
            "mode": "fit",
            "background_type": "blur",
            "blur_strength": 10,
        },
    ],
)
def test_background_schema_rejects_invalid_combinations(
    parameters: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        CropParameters.model_validate(parameters)


@pytest.mark.parametrize("value", ["#12345G", "123456", "rgb(1,2,3)", " #123456"])
def test_hex_normalizer_rejects_non_rrggbb_values(value: str) -> None:
    with pytest.raises(InvalidCropSpecError, match="RRGGBB"):
        normalize_hex_color(value)


@pytest.mark.parametrize(
    ("aspect_ratio", "expected"),
    [
        (CropAspectRatio.LANDSCAPE, (1920, 1080)),
        (CropAspectRatio.PORTRAIT, (1080, 1920)),
        (CropAspectRatio.SQUARE, (1080, 1080)),
        (CropAspectRatio.SOCIAL_PORTRAIT, (1080, 1350)),
    ],
)
def test_output_dimensions_use_central_aspect_ratio_presets(
    aspect_ratio: CropAspectRatio, expected: tuple[int, int]
) -> None:
    result = calculate_output_dimensions(1920, 1080, aspect_ratio)
    assert result == expected
    assert CROP_OUTPUT_DIMENSIONS[aspect_ratio] == expected
    assert result[0] % 2 == 0 and result[1] % 2 == 0


def test_crop_area_is_normalized_down_to_even_exact_ratio() -> None:
    assert calculate_crop_dimensions(1919, 1079, CropAspectRatio.LANDSCAPE) == (
        1888,
        1062,
    )


def test_small_source_still_resolves_to_full_preset_for_upscaling() -> None:
    assert calculate_output_dimensions(640, 360, CropAspectRatio.PORTRAIT) == (
        1080,
        1920,
    )


@pytest.mark.parametrize("aspect_ratio", list(CropAspectRatio))
@pytest.mark.parametrize("mode", list(CropMode))
def test_command_builder_supports_every_ratio_and_mode(
    aspect_ratio: CropAspectRatio, mode: CropMode, tmp_path: Path
) -> None:
    spec = CropSpec(aspect_ratio, mode)
    selected = profile(aspect_ratio)
    command = build_crop_command(
        "configured-ffmpeg",
        tmp_path / "input.mp4",
        tmp_path / "output.part.mp4",
        spec,
        selected,
    )
    filter_value = command[command.index("-vf") + 1]
    if mode is CropMode.CROP:
        crop_width, crop_height = calculate_crop_dimensions(
            selected.source_width, selected.source_height, aspect_ratio
        )
        assert filter_value.startswith(f"crop={crop_width}:{crop_height}:")
        assert f"scale={selected.output_width}:{selected.output_height}" in filter_value
        assert "pad=" not in filter_value
    else:
        assert filter_value.startswith(
            f"scale={selected.output_width}:{selected.output_height}:"
        )
        assert (
            f"pad={selected.output_width}:{selected.output_height}:"
            in filter_value
        )
        assert "force_original_aspect_ratio=decrease" in filter_value
        assert ":0x000000" in filter_value


def test_crop_filter_is_centered_for_landscape_to_portrait() -> None:
    selected = profile(CropAspectRatio.PORTRAIT)
    assert build_crop_filter(
        CropSpec(CropAspectRatio.PORTRAIT, CropMode.CROP), selected
    ) == "crop=594:1056:663:12,scale=1080:1920,setsar=1"


def test_crop_filter_centers_portrait_source_for_landscape_output() -> None:
    selected = profile(
        CropAspectRatio.LANDSCAPE, source_width=1080, source_height=1920
    )
    assert build_crop_filter(
        CropSpec(CropAspectRatio.LANDSCAPE, CropMode.CROP), selected
    ) == "crop=1056:594:12:663,scale=1920:1080,setsar=1"


def test_square_crop_scales_to_exact_square_preset() -> None:
    selected = profile(CropAspectRatio.SQUARE)
    assert build_crop_filter(
        CropSpec(CropAspectRatio.SQUARE, CropMode.CROP), selected
    ) == "crop=1080:1080:420:0,scale=1080:1080,setsar=1"


def test_color_fit_uses_normalized_safe_pad_color() -> None:
    selected = profile(CropAspectRatio.PORTRAIT)
    spec = CropSpec(
        CropAspectRatio.PORTRAIT,
        CropMode.FIT,
        CropBackgroundType.COLOR,
        "#7a4fd8",
    )
    filter_value = build_fit_color_filter(spec, selected)
    assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:0x7A4FD8" in filter_value
    assert "#" not in filter_value
    assert filter_value.endswith("setsar=1")


def test_blur_fit_builds_split_fill_blur_fit_and_center_overlay_graph() -> None:
    selected = profile(CropAspectRatio.PORTRAIT)
    graph = build_fit_blur_filter(selected)
    assert "[0:v:0]split=2[background][foreground]" in graph
    assert (
        "[background]scale=1080:1920:force_original_aspect_ratio=increase:"
        "force_divisible_by=2,crop=1080:1920" in graph
    )
    assert f"gblur=sigma={BLUR_SIGMA}" in graph
    assert (
        "[foreground]scale=1080:1920:force_original_aspect_ratio=decrease:"
        "force_divisible_by=2" in graph
    )
    assert "overlay=(W-w)/2:(H-h)/2:shortest=1" in graph
    assert graph.endswith("setsar=1[vout]")


def test_blur_command_uses_complex_video_output_and_preserves_audio(tmp_path: Path) -> None:
    command = build_crop_command(
        "ffmpeg",
        tmp_path / "input.mp4",
        tmp_path / "output.part.mp4",
        CropSpec(
            CropAspectRatio.PORTRAIT, CropMode.FIT, CropBackgroundType.BLUR
        ),
        profile(CropAspectRatio.PORTRAIT),
    )
    graph = command[command.index("-filter_complex") + 1]
    assert "-vf" not in command
    assert command[command.index("-map") + 1] == "[vout]"
    assert "crop=1080:1920" in graph and "setsar=1[vout]" in graph
    assert "0:a?" in command
    assert command[command.index("-c:a") + 1] == "copy"


def test_blur_video_only_command_has_no_audio_mapping(tmp_path: Path) -> None:
    command = build_crop_command(
        "ffmpeg",
        tmp_path / "input.mp4",
        tmp_path / "output.part.mp4",
        CropSpec(CropAspectRatio.SQUARE, CropMode.FIT, CropBackgroundType.BLUR),
        profile(CropAspectRatio.SQUARE, has_audio=False),
    )
    assert "-an" in command and "-c:a" not in command and "0:a?" not in command


@pytest.mark.parametrize("container", list(COMPRESSION_PROFILES))
def test_command_reuses_container_video_policy_and_copies_audio(
    container: OutputContainer, tmp_path: Path
) -> None:
    media = COMPRESSION_PROFILES[container]
    command = build_crop_command(
        "ffmpeg",
        tmp_path / f"input.{media.extension}",
        tmp_path / f"output.part.{media.extension}",
        CropSpec(CropAspectRatio.SQUARE, CropMode.FIT),
        profile(CropAspectRatio.SQUARE, container=container),
    )
    assert command[command.index("-c:v") + 1] == media.video_encoder
    assert command[command.index(media.quality_option) + 1] == str(
        media.quality_values[next(level for level in media.quality_values if level.value == "balanced")]
    )
    assert command[command.index("-c:a") + 1] == "copy"
    assert "0:a?" in command
    assert command[command.index("-f") + 1] == media.muxer


def test_video_only_command_does_not_emit_audio_mapping(tmp_path: Path) -> None:
    command = build_crop_command(
        "ffmpeg",
        tmp_path / "input.mp4",
        tmp_path / "output.part.mp4",
        CropSpec(CropAspectRatio.SQUARE, CropMode.CROP),
        profile(CropAspectRatio.SQUARE, has_audio=False),
    )
    assert "-an" in command and "-c:a" not in command and "0:a?" not in command


def test_audio_only_media_is_rejected_before_ffmpeg() -> None:
    runner = CapturingRunner()
    service = CropService(
        "ffmpeg",
        StubInspector(inspection(video=False)),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
        LocalFFmpegCapabilities(frozenset({"libx264"}), frozenset({"mp4"})),
    )
    with pytest.raises(CropMediaHasNoVideoError):
        service.resolve_profile(
            Path("audio.mp4"),
            CropSpec(CropAspectRatio.SQUARE, CropMode.CROP),
        )
    assert runner.command is None
    assert _public_failure_message(
        CropMediaHasNoVideoError("private"), JobOperation.CROP
    ) == "The input does not contain a video stream"


@pytest.mark.parametrize(
    ("background_type", "expected_option"),
    [
        (CropBackgroundType.COLOR, "-vf"),
        (CropBackgroundType.BLUR, "-filter_complex"),
    ],
)
def test_fit_service_executes_color_and_blur_filters(
    background_type: CropBackgroundType, expected_option: str
) -> None:
    runner = CapturingRunner()
    service = CropService(
        "configured-ffmpeg",
        StubInspector(inspection()),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
        LocalFFmpegCapabilities(frozenset({"libx264"}), frozenset({"mp4"})),
    )
    spec = CropSpec(
        CropAspectRatio.PORTRAIT,
        CropMode.FIT,
        background_type,
        "#4F46E5" if background_type is CropBackgroundType.COLOR else None,
    )
    selected = service.resolve_profile(Path("video.mp4"), spec)
    service.crop(Path("video.mp4"), Path("output.part.mp4"), spec, selected)
    assert runner.command is not None and expected_option in runner.command


class WritingCropper:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[Path, Path, CropSpec, CropProfile]] = []
        self.error = error

    def resolve_profile(self, _input_path: Path, spec: CropSpec) -> CropProfile:
        return profile(spec.aspect_ratio)

    def crop(
        self,
        input_path: Path,
        output_path: Path,
        spec: CropSpec,
        selected: CropProfile,
    ) -> None:
        self.calls.append((input_path, output_path, spec, selected))
        output_path.write_bytes(b"cropped-video")
        if self.error is not None:
            raise self.error


def test_worker_atomically_finalizes_crop_output(tmp_path: Path) -> None:
    media_id, job_id = str(uuid4()), str(uuid4())
    storage = LocalStorageService(
        tmp_path / "media", tmp_path / "uploads", tmp_path / "outputs"
    )
    storage.initialize()
    (tmp_path / "uploads" / f"{media_id}.mp4").write_bytes(b"source")
    cropper = WritingCropper()
    result = execute_media_job(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.CROP,
        storage=storage,
        converter=None,
        conversion=None,
        cropper=cropper,  # type: ignore[arg-type]
        crop=CropSpec(CropAspectRatio.PORTRAIT, CropMode.CROP),
        allowed_extensions=[".mp4"],
    )
    assert cropper.calls[0][1].name == f"{job_id}.part.mp4"
    assert (tmp_path / "outputs" / f"{job_id}.mp4").read_bytes() == b"cropped-video"
    assert not (tmp_path / "outputs" / f"{job_id}.part.mp4").exists()
    assert result["format"] == "mp4"


def test_crop_ffmpeg_failure_cleans_partial_output(tmp_path: Path) -> None:
    media_id, job_id = str(uuid4()), str(uuid4())
    storage = LocalStorageService(
        tmp_path / "media", tmp_path / "uploads", tmp_path / "outputs"
    )
    storage.initialize()
    (tmp_path / "uploads" / f"{media_id}.mp4").write_bytes(b"source")
    cropper = WritingCropper(FFmpegConversionError("private command and stderr"))
    with pytest.raises(FFmpegConversionError):
        execute_media_job(
            job_id=job_id,
            media_id=media_id,
            operation=JobOperation.CROP,
            storage=storage,
            converter=None,
            conversion=None,
            cropper=cropper,  # type: ignore[arg-type]
            crop=CropSpec(
                CropAspectRatio.PORTRAIT,
                CropMode.FIT,
                CropBackgroundType.BLUR,
            ),
            allowed_extensions=[".mp4"],
        )
    assert not (tmp_path / "outputs" / f"{job_id}.part.mp4").exists()
    assert not (tmp_path / "outputs" / f"{job_id}.mp4").exists()
    assert _public_failure_message(
        cropper.error, JobOperation.CROP
    ) == "Crop processing failed"


def test_celery_worker_dispatches_crop_service(monkeypatch: pytest.MonkeyPatch) -> None:
    job_id, media_id = str(uuid4()), str(uuid4())
    cropper, captured = object(), {}

    class StubStorage:
        def initialize(self) -> None:
            pass

    def capture(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"output_id": job_id, "filename": f"{job_id}.mp4", "format": "mp4"}

    monkeypatch.setattr(process_media_job, "update_state", lambda **_kwargs: None)
    monkeypatch.setattr(
        tasks_module,
        "get_settings",
        lambda: SimpleNamespace(allowed_media_extensions=[".mp4"]),
    )
    monkeypatch.setattr(tasks_module, "get_storage_service", StubStorage)
    monkeypatch.setattr(tasks_module, "get_crop_service", lambda: cropper)
    monkeypatch.setattr(tasks_module, "execute_media_job", capture)
    process_media_job.push_request(id=job_id)
    try:
        result = process_media_job.run(
            media_id, "crop", {"aspect_ratio": "9:16", "mode": "fit"}
        )
    finally:
        process_media_job.pop_request()
    assert captured["cropper"] is cropper
    assert captured["crop"] == CropSpec(CropAspectRatio.PORTRAIT, CropMode.FIT)
    assert captured["speed_changer"] is None and captured["speed"] is None
    assert result["progress"] == 100


def test_missing_video_dimensions_are_rejected_safely() -> None:
    no_dimensions = parse_ffprobe_payload(
        {
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
            "streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}],
        }
    )
    service = CropService(
        "ffmpeg",
        StubInspector(no_dimensions),  # type: ignore[arg-type]
        CapturingRunner(),  # type: ignore[arg-type]
        LocalFFmpegCapabilities(frozenset({"libx264"}), frozenset({"mp4"})),
    )
    with pytest.raises(InvalidCropDimensionsError):
        service.resolve_profile(
            Path("video.mp4"), CropSpec(CropAspectRatio.SQUARE, CropMode.CROP)
        )


@pytest.mark.parametrize(
    "spec",
    [
        CropSpec(CropAspectRatio.PORTRAIT, CropMode.CROP),
        CropSpec(
            CropAspectRatio.PORTRAIT,
            CropMode.FIT,
            CropBackgroundType.COLOR,
            "#7A4FD8",
        ),
        CropSpec(
            CropAspectRatio.PORTRAIT,
            CropMode.FIT,
            CropBackgroundType.BLUR,
        ),
    ],
    ids=["crop", "fit-color", "fit-blur"],
)
def test_real_ffmpeg_landscape_to_portrait_uses_exact_preset(
    tmp_path: Path, spec: CropSpec
) -> None:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg and FFprobe are required for the integration test")
    source = tmp_path / "source.mp4"
    output = tmp_path / "output.part.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=10:duration=0.2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.2",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    inspector = SyncFFprobeInspector(ffprobe, 30)
    service = CropService(
        ffmpeg,
        inspector,
        FFmpegRunner(30),
        LocalFFmpegCapabilities(frozenset({"libx264"}), frozenset({"mp4"})),
    )
    selected = service.resolve_profile(source, spec)
    service.crop(source, output, spec, selected)
    result = inspector.inspect(output)
    assert (selected.output_width, selected.output_height) == (1080, 1920)
    assert (result.video_streams[0].width, result.video_streams[0].height) == (
        1080,
        1920,
    )
    assert result.video_streams[0].codec_name == "h264"
    assert result.audio_streams[0].codec_name == "aac"
    assert "mp4" in (result.format.name or "")
