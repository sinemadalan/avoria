import asyncio
import shutil
import subprocess
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
    ResolvedCompressionProfile,
    CompressionService,
    CompressionSpec,
    InvalidCompressionInputError,
    UnsupportedCompressionContainerError,
    build_compress_command,
    calculate_compression_statistics,
    detect_compression_profile,
    resolve_compression_profile,
)
from backend.app.processing.conversion import (
    FFmpegConversionError,
    FFmpegRunner,
    LocalFFmpegCapabilities,
    OutputContainer,
)
from backend.app.processing.probe import (
    MediaInspection,
    SyncFFprobeInspector,
    parse_ffprobe_payload,
)
from backend.app.infrastructure.queue import _public_failure_message
from backend.app.workers.tasks import execute_media_job


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


def source_inspection(
    *,
    format_name: str = "mov,mp4,m4a,3gp,3g2,mj2",
    width: int = 3840,
    height: int = 2160,
    frame_rate: str = "60/1",
    video_codec: str = "h264",
    video_bit_rate: int = 20_000_000,
    with_audio: bool = True,
    audio_codec: str = "aac",
    audio_bit_rate: int = 192_000,
) -> MediaInspection:
    streams: list[dict[str, object]] = [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": video_codec,
            "width": width,
            "height": height,
            "avg_frame_rate": frame_rate,
            "bit_rate": video_bit_rate,
        }
    ]
    if with_audio:
        streams.append(
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": audio_codec,
                "bit_rate": audio_bit_rate,
            }
        )
    return parse_ffprobe_payload(
        {
            "format": {
                "format_name": format_name,
                "duration": "12.5",
                "bit_rate": str(video_bit_rate + (audio_bit_rate if with_audio else 0)),
            },
            "streams": streams,
        }
    )


def resolved_command(
    tmp_path: Path,
    container: OutputContainer,
    level: CompressionLevel,
    inspection: MediaInspection,
) -> tuple[ResolvedCompressionProfile, list[str]]:
    profile = resolve_compression_profile(
        COMPRESSION_PROFILES[container],
        inspection,
        CompressionSpec(level),
    )
    command = build_compress_command(
        "ffmpeg",
        tmp_path / f"input.{container.value}",
        tmp_path / f"output.part.{container.value}",
        CompressionSpec(level),
        profile,
    )
    return profile, command


def test_high_quality_preserves_source_dimensions_and_fps(tmp_path: Path) -> None:
    profile, command = resolved_command(
        tmp_path,
        OutputContainer.MP4,
        CompressionLevel.LIGHT,
        source_inspection(),
    )

    assert profile.video_encoder == "libx264"
    assert profile.quality_value == 22
    assert profile.scale_filter is None
    assert profile.output_frame_rate is None
    assert command[command.index("-preset") + 1] == "slow"
    assert command[command.index("-b:a") + 1] == "128k"
    assert "-vf" not in command
    assert "-r" not in command
    assert profile.source.video_codec == "h264"
    assert profile.source.video_bit_rate == 20_000_000
    assert profile.source.audio_codec == "aac"
    assert profile.source.audio_bit_rate == 192_000
    assert profile.source.duration_seconds == 12.5


def test_4k_mp4_balanced_uses_hevc_1080p_and_preserves_fps(tmp_path: Path) -> None:
    profile, command = resolved_command(
        tmp_path,
        OutputContainer.MP4,
        CompressionLevel.BALANCED,
        source_inspection(),
    )

    assert profile.video_encoder == "libx265"
    assert profile.quality_value == 27
    assert profile.scale_filter == "scale=-2:1080"
    assert profile.output_frame_rate is None
    assert command[command.index("-preset") + 1] == "slow"
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"
    assert command[command.index("-tag:v") + 1] == "hvc1"
    assert command[command.index("-b:a") + 1] == "96k"
    assert "-r" not in command


def test_4k_mp4_small_file_uses_hevc_720p_and_caps_fps(tmp_path: Path) -> None:
    profile, command = resolved_command(
        tmp_path,
        OutputContainer.MP4,
        CompressionLevel.STRONG,
        source_inspection(),
    )

    assert profile.video_encoder == "libx265"
    assert profile.quality_value == 30
    assert profile.scale_filter == "scale=-2:720"
    assert profile.output_frame_rate == 30.0
    assert command[command.index("-preset") + 1] == "medium"
    assert command[command.index("-r") + 1] == "30"
    assert command[command.index("-b:a") + 1] == "80k"


@pytest.mark.parametrize(
    ("height", "level"),
    [
        (1080, CompressionLevel.BALANCED),
        (720, CompressionLevel.STRONG),
    ],
)
def test_source_at_preset_height_is_not_rescaled(
    height: int,
    level: CompressionLevel,
    tmp_path: Path,
) -> None:
    width = 1920 if height == 1080 else 1280
    profile, command = resolved_command(
        tmp_path,
        OutputContainer.MP4,
        level,
        source_inspection(width=width, height=height, frame_rate="30/1"),
    )

    assert profile.scale_filter is None
    assert "-vf" not in command


@pytest.mark.parametrize(
    ("source_rate", "expected_rate"),
    [("24/1", None), ("60/1", 30.0)],
)
def test_small_file_only_caps_frame_rates_above_30(
    source_rate: str,
    expected_rate: float | None,
    tmp_path: Path,
) -> None:
    profile, command = resolved_command(
        tmp_path,
        OutputContainer.MP4,
        CompressionLevel.STRONG,
        source_inspection(width=1280, height=720, frame_rate=source_rate),
    )

    assert profile.output_frame_rate == expected_rate
    assert ("-r" in command) is (expected_rate is not None)


def test_webm_preserves_vp9_and_applies_source_aware_limits(tmp_path: Path) -> None:
    profile, command = resolved_command(
        tmp_path,
        OutputContainer.WEBM,
        CompressionLevel.STRONG,
        source_inspection(format_name="matroska,webm", video_codec="vp9"),
    )

    assert profile.video_encoder == "libvpx-vp9"
    assert "libx265" not in command
    assert profile.scale_filter == "scale=-2:720"
    assert profile.output_frame_rate == 30.0
    assert profile.audio_encoder == "libopus"
    assert command[command.index("-b:a") + 1] == "80k"


def test_no_audio_source_does_not_add_an_audio_mapping(tmp_path: Path) -> None:
    profile, command = resolved_command(
        tmp_path,
        OutputContainer.MP4,
        CompressionLevel.BALANCED,
        source_inspection(with_audio=False),
    )

    assert profile.audio_encoder is None
    assert "0:a:0?" not in command
    assert "-c:a" not in command


def test_portrait_video_keeps_orientation_when_scaled(tmp_path: Path) -> None:
    profile, command = resolved_command(
        tmp_path,
        OutputContainer.MP4,
        CompressionLevel.BALANCED,
        source_inspection(width=1080, height=1920, frame_rate="30/1"),
    )

    assert profile.source.width == 1080
    assert profile.source.height == 1920
    assert profile.scale_filter == "scale=-2:1080"
    assert command[command.index("-vf") + 1] == "scale=-2:1080"


def test_avi_keeps_legacy_safe_codec_policy(tmp_path: Path) -> None:
    profile, command = resolved_command(
        tmp_path,
        OutputContainer.AVI,
        CompressionLevel.STRONG,
        source_inspection(format_name="avi"),
    )

    assert profile.video_encoder == "mpeg4"
    assert profile.audio_encoder == "libmp3lame"
    assert profile.scale_filter is None
    assert profile.output_frame_rate is None
    assert "libx265" not in command


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
            encoders=frozenset({"libx265", "aac"}),
            muxers=frozenset({"mp4"}),
        ),
    )

    service.compress(
        tmp_path / "video.mp4",
        tmp_path / "output.part.mp4",
        CompressionSpec(),
    )

    assert runner.command is not None
    assert "0:a:0?" not in runner.command
    assert "-c:a" not in runner.command


class WritingCompressor:
    def __init__(
        self,
        output: bytes,
        error: Exception | None = None,
    ) -> None:
        self.output = output
        self.error = error

    def resolve_profile(
        self,
        input_path: Path,
        _spec: CompressionSpec | None = None,
    ) -> CompressionProfile:
        return COMPRESSION_PROFILES[OutputContainer(input_path.suffix[1:].casefold())]

    def compress(
        self,
        _input_path: Path,
        output_path: Path,
        _compression: CompressionSpec,
        _profile: CompressionProfile | ResolvedCompressionProfile,
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

    assert (
        _public_failure_message(error, JobOperation.COMPRESS)
        == "Video compression failed"
    )


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
        _public_failure_message(error.value, JobOperation.COMPRESS)
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


def test_real_ffmpeg_small_file_mp4_uses_hevc_caps_fps_and_keeps_audio(
    tmp_path: Path,
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
            "testsrc2=size=320x240:rate=60:duration=0.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.5",
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
    service = CompressionService(
        ffmpeg,
        inspector,
        FFmpegRunner(60),
        LocalFFmpegCapabilities(
            encoders=frozenset({"libx265", "aac"}),
            muxers=frozenset({"mp4"}),
        ),
    )
    spec = CompressionSpec(CompressionLevel.STRONG)
    profile = service.resolve_profile(source, spec)
    service.compress(source, output, spec, profile)
    result = inspector.inspect(output)

    assert result.video_streams[0].codec_name == "hevc"
    assert result.video_streams[0].frame_rate == pytest.approx(30, abs=0.1)
    assert (result.video_streams[0].width, result.video_streams[0].height) == (320, 240)
    assert result.audio_streams[0].codec_name == "aac"
    assert "mp4" in (result.format.name or "")
