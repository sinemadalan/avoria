from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app.application.ports.jobs import JobOperation
from backend.app.infrastructure.queue import _public_failure_message
from backend.app.infrastructure.storage import LocalStorageService
from backend.app.processing.compression import COMPRESSION_PROFILES, CompressionProfile
from backend.app.processing.conversion import LocalFFmpegCapabilities, OutputContainer
from backend.app.processing.crop import (
    CropAspectRatio,
    CropMediaHasNoVideoError,
    CropMode,
    CropProfile,
    CropService,
    CropSpec,
    InvalidCropDimensionsError,
    build_crop_command,
    build_crop_filter,
    calculate_output_dimensions,
)
from backend.app.processing.probe import MediaInspection, parse_ffprobe_payload
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
    def __init__(self) -> None:
        self.command: list[str] | None = None

    def run(self, command: list[str]) -> None:
        self.command = command


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
    assert parameters.to_payload() == {"aspect_ratio": aspect_ratio, "mode": mode}


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
    assert request.to_payload() == {"aspect_ratio": "4:5", "mode": "fit"}


@pytest.mark.parametrize(
    ("aspect_ratio", "expected"),
    [
        (CropAspectRatio.LANDSCAPE, (1920, 1080)),
        (CropAspectRatio.PORTRAIT, (594, 1056)),
        (CropAspectRatio.SQUARE, (1080, 1080)),
        (CropAspectRatio.SOCIAL_PORTRAIT, (864, 1080)),
    ],
)
def test_dimensions_are_largest_exact_even_rectangle_within_source(
    aspect_ratio: CropAspectRatio, expected: tuple[int, int]
) -> None:
    result = calculate_output_dimensions(1920, 1080, aspect_ratio)
    assert result == expected
    assert result[0] % 2 == 0 and result[1] % 2 == 0


def test_odd_source_dimensions_are_normalized_down_to_even_exact_ratio() -> None:
    assert calculate_output_dimensions(1919, 1079, CropAspectRatio.LANDSCAPE) == (
        1888,
        1062,
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
        assert filter_value.startswith(
            f"crop={selected.output_width}:{selected.output_height}:"
        )
        assert "scale=" not in filter_value and "pad=" not in filter_value
    else:
        assert filter_value.startswith(
            f"scale={selected.output_width}:{selected.output_height}:"
        )
        assert (
            f"pad={selected.output_width}:{selected.output_height}:"
            in filter_value
        )
        assert "force_original_aspect_ratio=decrease" in filter_value
        assert ":black" in filter_value


def test_crop_filter_is_centered_for_landscape_to_portrait() -> None:
    selected = profile(CropAspectRatio.PORTRAIT)
    assert build_crop_filter(
        CropSpec(CropAspectRatio.PORTRAIT, CropMode.CROP), selected
    ) == "crop=594:1056:663:12,setsar=1"


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


class WritingCropper:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path, CropSpec, CropProfile]] = []

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
