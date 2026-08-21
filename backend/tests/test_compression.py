import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.application.ports.jobs import JobOperation
from backend.app.application.ports.storage import MediaNotFoundError
from backend.app.infrastructure.storage import LocalStorageService
from backend.app.processing.compression import (
    AVI_COMPRESSION_QUALITY,
    COMPRESSION_CRF,
    COMPRESSION_PROFILES,
    VP9_COMPRESSION_CRF,
    CompressionLevel,
    CompressionProfile,
    CompressionService,
    CompressionSpec,
    InvalidCompressionInputError,
    UnsupportedCompressionContainerError,
    build_compress_command,
    calculate_compression_statistics,
    detect_compression_profile,
)
from backend.app.processing.conversion import (
    FFmpegConversionError,
    LocalFFmpegCapabilities,
    OutputContainer,
)
from backend.app.processing.probe import MediaInspection, parse_ffprobe_payload
from backend.app.workers.tasks import _public_failure_message, execute_media_job


VIDEO = parse_ffprobe_payload(
    {
        "format": {"format_name": "mov"},
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264"},
            {"index": 1, "codec_type": "audio", "codec_name": "aac"},
        ],
    }
)
VIDEO_ONLY = parse_ffprobe_payload(
    {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        "streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}],
    }
)
AUDIO_ONLY = parse_ffprobe_payload(
    {
        "format": {"format_name": "mp3"},
        "streams": [{"index": 0, "codec_type": "audio", "codec_name": "mp3"}],
    }
)


@pytest.mark.parametrize(
    ("container", "level", "quality_option", "quality", "video", "audio"),
    [
        (OutputContainer.MP4, CompressionLevel.LIGHT, "-crf", 20, "libx264", "aac"),
        (OutputContainer.MOV, CompressionLevel.BALANCED, "-crf", 23, "libx264", "aac"),
        (OutputContainer.MKV, CompressionLevel.STRONG, "-crf", 28, "libx264", "aac"),
        (
            OutputContainer.WEBM,
            CompressionLevel.LIGHT,
            "-crf",
            26,
            "libvpx-vp9",
            "libopus",
        ),
        (
            OutputContainer.WEBM,
            CompressionLevel.BALANCED,
            "-crf",
            32,
            "libvpx-vp9",
            "libopus",
        ),
        (
            OutputContainer.WEBM,
            CompressionLevel.STRONG,
            "-crf",
            38,
            "libvpx-vp9",
            "libopus",
        ),
        (OutputContainer.AVI, CompressionLevel.LIGHT, "-q:v", 3, "mpeg4", "libmp3lame"),
        (
            OutputContainer.AVI,
            CompressionLevel.BALANCED,
            "-q:v",
            5,
            "mpeg4",
            "libmp3lame",
        ),
        (OutputContainer.AVI, CompressionLevel.STRONG, "-q:v", 8, "mpeg4", "libmp3lame"),
    ],
)
def test_build_compress_command_uses_container_encoder_quality_policy(
    container: OutputContainer,
    level: CompressionLevel,
    quality_option: str,
    quality: int,
    video: str,
    audio: str,
    tmp_path: Path,
) -> None:
    profile = COMPRESSION_PROFILES[container]
    input_path = tmp_path / f"input.{profile.extension}"
    output_path = tmp_path / f"job.part.{profile.extension}"

    command = build_compress_command(
        "configured-ffmpeg",
        input_path,
        output_path,
        CompressionSpec(level),
        profile,
    )

    assert command[command.index(quality_option) + 1] == str(quality)
    assert command[command.index("-c:v") + 1] == video
    assert command[command.index("-c:a") + 1] == audio
    assert command[command.index("-f") + 1] == profile.muxer
    assert "-vf" not in command
    assert "-r" not in command
    assert command[-1] == str(output_path)


def test_encoder_quality_mappings_remain_centralized() -> None:
    assert list(COMPRESSION_CRF.values()) == [20, 23, 28]
    assert list(VP9_COMPRESSION_CRF.values()) == [26, 32, 38]
    assert list(AVI_COMPRESSION_QUALITY.values()) == [3, 5, 8]


def test_container_specific_video_options_are_applied(tmp_path: Path) -> None:
    mp4 = build_compress_command(
        "ffmpeg",
        tmp_path / "input.mp4",
        tmp_path / "output.part.mp4",
        CompressionSpec(),
        COMPRESSION_PROFILES[OutputContainer.MP4],
    )
    webm = build_compress_command(
        "ffmpeg",
        tmp_path / "input.webm",
        tmp_path / "output.part.webm",
        CompressionSpec(),
        COMPRESSION_PROFILES[OutputContainer.WEBM],
    )

    assert mp4[mp4.index("-preset") + 1] == "medium"
    assert mp4[mp4.index("-b:a") + 1] == "128k"
    assert webm[webm.index("-b:v") + 1] == "0"


@pytest.mark.parametrize(
    ("extension", "format_name", "container"),
    [
        ("mp4", "mov,mp4,m4a,3gp,3g2,mj2", OutputContainer.MP4),
        ("mov", "mov,mp4,m4a,3gp,3g2,mj2", OutputContainer.MOV),
        ("mkv", "matroska,webm", OutputContainer.MKV),
        ("webm", "matroska,webm", OutputContainer.WEBM),
        ("avi", "avi", OutputContainer.AVI),
    ],
)
def test_detect_compression_profile_uses_suffix_and_normalized_probe_format(
    extension: str,
    format_name: str,
    container: OutputContainer,
    tmp_path: Path,
) -> None:
    inspection = parse_ffprobe_payload(
        {
            "format": {"format_name": format_name},
            "streams": [{"codec_type": "video", "codec_name": "h264"}],
        }
    )

    profile = detect_compression_profile(tmp_path / f"input.{extension}", inspection)

    assert profile.container is container
    assert profile.extension == extension


@pytest.mark.parametrize(
    ("filename", "format_name"),
    [("input.flv", "flv"), ("input.avi", "mov,mp4,m4a,3gp,3g2,mj2")],
)
def test_detect_compression_profile_rejects_unsupported_or_mismatched_container(
    filename: str,
    format_name: str,
    tmp_path: Path,
) -> None:
    inspection = parse_ffprobe_payload(
        {
            "format": {"format_name": format_name},
            "streams": [{"codec_type": "video", "codec_name": "h264"}],
        }
    )

    with pytest.raises(UnsupportedCompressionContainerError):
        detect_compression_profile(tmp_path / filename, inspection)


class StubInspector:
    def __init__(self, inspection: MediaInspection) -> None:
        self.inspection = inspection

    def inspect(self, _path: Path) -> MediaInspection:
        return self.inspection


class CapturingRunner:
    def __init__(self) -> None:
        self.command: list[str] | None = None

    def run(self, command: list[str]) -> None:
        self.command = command


def test_compression_service_rejects_non_video_before_ffmpeg(tmp_path: Path) -> None:
    runner = CapturingRunner()
    service = CompressionService(
        "ffmpeg",
        StubInspector(AUDIO_ONLY),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
        LocalFFmpegCapabilities(
            encoders=frozenset({"libx264", "aac"}),
            muxers=frozenset({"mp4"}),
        ),
    )

    with pytest.raises(InvalidCompressionInputError, match="video stream"):
        service.compress(
            tmp_path / "audio.mp3",
            tmp_path / "output.part.mp4",
            CompressionSpec(),
        )

    assert runner.command is None


def test_compression_service_accepts_video_without_audio(tmp_path: Path) -> None:
    runner = CapturingRunner()
    service = CompressionService(
        "ffmpeg",
        StubInspector(VIDEO_ONLY),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
        LocalFFmpegCapabilities(
            encoders=frozenset({"libx264", "aac"}),
            muxers=frozenset({"mp4"}),
        ),
    )

    service.compress(
        tmp_path / "video.mp4",
        tmp_path / "output.part.mp4",
        CompressionSpec(),
    )

    assert runner.command is not None
    assert "0:a:0?" in runner.command


class WritingCompressor:
    def __init__(
        self,
        output: bytes,
        error: Exception | None = None,
    ) -> None:
        self.output = output
        self.error = error

    def resolve_profile(self, input_path: Path) -> CompressionProfile:
        return COMPRESSION_PROFILES[OutputContainer(input_path.suffix[1:].casefold())]

    def compress(
        self,
        _input_path: Path,
        output_path: Path,
        _compression: CompressionSpec,
        _profile: CompressionProfile,
    ) -> None:
        output_path.write_bytes(self.output)
        if self.error is not None:
            raise self.error


def make_storage(
    tmp_path: Path,
    media_id: str,
    input_bytes: bytes = b"original-video",
    extension: str = "mov",
) -> tuple[LocalStorageService, Path]:
    upload_directory = tmp_path / "uploads"
    storage = LocalStorageService(
        tmp_path / "media",
        upload_directory,
        tmp_path / "outputs",
    )
    storage.initialize()
    input_path = upload_directory / f"{media_id}.{extension}"
    input_path.write_bytes(input_bytes)
    return storage, input_path


def run_compression(
    tmp_path: Path,
    output: bytes,
    extension: str = "mov",
) -> tuple[dict[str, object], Path, Path]:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage, input_path = make_storage(tmp_path, media_id, extension=extension)
    result = execute_media_job(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.COMPRESS,
        storage=storage,
        converter=None,
        conversion=None,
        compressor=WritingCompressor(output),  # type: ignore[arg-type]
        compression=CompressionSpec(),
        allowed_extensions=[f".{extension}"],
    )
    return result, input_path, tmp_path / "outputs" / f"{job_id}.{extension}"


@pytest.mark.parametrize("extension", ["mp4", "mov", "mkv", "webm", "avi"])
def test_worker_preserves_container_with_atomic_output_and_statistics(
    extension: str,
    tmp_path: Path,
) -> None:
    result, input_path, output_path = run_compression(
        tmp_path,
        b"small",
        extension,
    )

    assert input_path.read_bytes() == b"original-video"
    assert output_path.read_bytes() == b"small"
    assert not output_path.with_name(
        f"{output_path.stem}.part.{extension}"
    ).exists()
    assert result == {
        "output_id": output_path.stem,
        "filename": output_path.name,
        "format": extension,
        "compression_level": "balanced",
        "original_size": 14,
        "compressed_size": 5,
        "saved_bytes": 9,
        "reduction_percentage": 64.29,
        "compression_effective": True,
    }


def test_larger_compressed_output_still_completes(tmp_path: Path) -> None:
    result, input_path, output_path = run_compression(tmp_path, b"x" * 20)

    assert output_path.is_file()
    assert input_path.read_bytes() == b"original-video"
    assert result["saved_bytes"] == -6
    assert result["reduction_percentage"] == -42.86
    assert result["compression_effective"] is False


def test_compression_statistics_formula() -> None:
    result = calculate_compression_statistics(52_428_800, 18_350_080)

    assert result.saved_bytes == 34_078_720
    assert result.reduction_percentage == 65.0
    assert result.compression_effective is True


@pytest.mark.parametrize("extension", ["mp4", "mov", "mkv", "webm", "avi"])
def test_ffmpeg_failure_cleans_container_specific_partial_output(
    extension: str,
    tmp_path: Path,
) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage, input_path = make_storage(tmp_path, media_id, extension=extension)

    with pytest.raises(FFmpegConversionError):
        execute_media_job(
            job_id=job_id,
            media_id=media_id,
            operation=JobOperation.COMPRESS,
            storage=storage,
            converter=None,
            conversion=None,
            compressor=WritingCompressor(
                b"partial", FFmpegConversionError("technical details")
            ),  # type: ignore[arg-type]
            compression=CompressionSpec(CompressionLevel.STRONG),
            allowed_extensions=[f".{extension}"],
        )

    assert input_path.read_bytes() == b"original-video"
    assert not (tmp_path / "outputs" / f"{job_id}.part.{extension}").exists()
    assert not (tmp_path / "outputs" / f"{job_id}.{extension}").exists()


def test_compression_failure_message_does_not_expose_ffmpeg_diagnostic() -> None:
    error = FFmpegConversionError(r"private input path: C:\\media\\secret.mov")

    assert _public_failure_message(error, "compress") == "Video compression failed"


def test_unsupported_container_fails_without_mp4_fallback(tmp_path: Path) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage, input_path = make_storage(tmp_path, media_id, extension="flv")
    inspection = parse_ffprobe_payload(
        {
            "format": {"format_name": "flv"},
            "streams": [{"codec_type": "video", "codec_name": "h264"}],
        }
    )
    runner = CapturingRunner()
    service = CompressionService(
        "ffmpeg",
        StubInspector(inspection),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
        LocalFFmpegCapabilities(
            encoders=frozenset({"libx264", "aac"}),
            muxers=frozenset({"mp4"}),
        ),
    )

    with pytest.raises(UnsupportedCompressionContainerError) as error:
        execute_media_job(
            job_id=job_id,
            media_id=media_id,
            operation=JobOperation.COMPRESS,
            storage=storage,
            converter=None,
            conversion=None,
            compressor=service,
            compression=CompressionSpec(),
            allowed_extensions=[".flv"],
        )

    assert input_path.read_bytes() == b"original-video"
    assert runner.command is None
    assert not (tmp_path / "outputs" / f"{job_id}.flv").exists()
    assert not (tmp_path / "outputs" / f"{job_id}.mp4").exists()
    assert (
        _public_failure_message(error.value, "compress")
        == "Compression is not supported for this container"
    )


def test_worker_reports_nonexistent_media(tmp_path: Path) -> None:
    storage = LocalStorageService(tmp_path / "media")
    storage.initialize()

    with pytest.raises(MediaNotFoundError):
        execute_media_job(
            job_id=str(uuid4()),
            media_id=str(uuid4()),
            operation=JobOperation.COMPRESS,
            storage=storage,
            converter=None,
            conversion=None,
            compressor=WritingCompressor(b"output"),  # type: ignore[arg-type]
            compression=CompressionSpec(),
            allowed_extensions=[".mov"],
        )
