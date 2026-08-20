import asyncio
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from backend.app.application.ports.jobs import JobOperation
from backend.app.application.ports.storage import OutputStorageError
from backend.app.infrastructure.storage import LocalStorageService
from backend.app.processing.conversion import (
    AUDIO_ENCODERS,
    CONVERSION_CAPABILITIES,
    VIDEO_ENCODERS,
    AudioCodec,
    ConversionService,
    ConversionSpec,
    FFmpegCapabilityError,
    FFmpegCapabilityDetector,
    FFmpegConversionError,
    FFmpegExecutableNotFoundError,
    FFmpegRunner,
    FFmpegTimeoutError,
    InvalidConversionError,
    LocalFFmpegCapabilities,
    OutputContainer,
    VideoCodec,
    build_convert_command,
    validate_input_streams,
)
from backend.app.processing.probe import MediaInspection, parse_ffprobe_payload
from backend.app.workers.tasks import execute_media_job


def spec(
    container: OutputContainer = OutputContainer.MP4,
    video: VideoCodec = VideoCodec.H264,
    audio: AudioCodec = AudioCodec.AAC,
) -> ConversionSpec:
    return ConversionSpec(container, video, audio)


@pytest.mark.parametrize(
    ("conversion", "expected"),
    [
        (
            spec(),
            ["-map", "0:v:0", "-c:v", "libx264", "-map", "0:a:0?", "-c:a", "aac"],
        ),
        (
            spec(OutputContainer.WEBM, VideoCodec.VP9, AudioCodec.OPUS),
            [
                "-map",
                "0:v:0",
                "-c:v",
                "libvpx-vp9",
                "-map",
                "0:a:0?",
                "-c:a",
                "libopus",
            ],
        ),
        (
            spec(OutputContainer.MKV, VideoCodec.H265, AudioCodec.FLAC),
            ["-c:v", "libx265", "-c:a", "flac"],
        ),
        (
            spec(OutputContainer.MOV, VideoCodec.H264, AudioCodec.PCM),
            ["-c:v", "libx264", "-c:a", "pcm_s16le"],
        ),
        (
            spec(OutputContainer.MKV, VideoCodec.COPY, AudioCodec.COPY),
            ["-c:v", "copy", "-c:a", "copy"],
        ),
    ],
)
def test_build_convert_command_uses_safe_mappings(
    conversion: ConversionSpec,
    expected: list[str],
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.file"
    output_path = tmp_path / f"job.part.{conversion.container.value}"

    command = build_convert_command("configured-ffmpeg", input_path, output_path, conversion)

    assert command[0] == "configured-ffmpeg"
    assert command[command.index("-i") + 1] == str(input_path)
    assert command[-1] == str(output_path)
    assert "-sn" in command
    assert "-dn" in command
    for value in expected:
        assert value in command


def test_build_convert_command_handles_disabled_streams(tmp_path: Path) -> None:
    audio_only = build_convert_command(
        "ffmpeg",
        tmp_path / "input.mp4",
        tmp_path / "output.part.mp3",
        spec(OutputContainer.MP3, VideoCodec.NONE, AudioCodec.MP3),
    )
    video_only = build_convert_command(
        "ffmpeg",
        tmp_path / "input.mp4",
        tmp_path / "output.part.mp4",
        spec(OutputContainer.MP4, VideoCodec.H264, AudioCodec.NONE),
    )

    assert "-vn" in audio_only
    assert "-c:v" not in audio_only
    assert "-an" in video_only
    assert "-c:a" not in video_only


def test_ffmpeg_runner_never_uses_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    FFmpegRunner(60).run(["ffmpeg", "-version"])

    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
    assert captured["kwargs"]["stdout"] is subprocess.DEVNULL


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (FileNotFoundError(), FFmpegExecutableNotFoundError),
        (subprocess.TimeoutExpired(["ffmpeg"], 1), FFmpegTimeoutError),
    ],
)
def test_ffmpeg_runner_maps_process_start_failures(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: type[Exception],
) -> None:
    def run(_command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise error

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(expected):
        FFmpegRunner(1).run(["ffmpeg"])


def test_capability_detection_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        output = " V..... libx264" if command[-1] == "-encoders" else " E mp4"
        return subprocess.CompletedProcess(command, 0, stdout=output)

    monkeypatch.setattr(subprocess, "run", run)
    detector = FFmpegCapabilityDetector("ffmpeg")

    first = detector.detect()
    second = detector.detect()

    assert first is second
    assert len(calls) == 2


def test_ffmpeg_failure_has_bounded_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(_command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        kwargs["stderr"].write(b"x" * 10000 + b" useful error")
        return subprocess.CompletedProcess([], 1)

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(FFmpegConversionError) as error:
        FFmpegRunner(60).run(["ffmpeg"])

    assert error.value.diagnostic is not None
    assert len(error.value.diagnostic) <= 8192
    assert error.value.diagnostic.endswith("useful error")


VIDEO_AUDIO = parse_ffprobe_payload(
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
        "format": {"format_name": "mov"},
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
    ("conversion", "inspection"),
    [
        (spec(), VIDEO_AUDIO),
        (spec(), VIDEO_ONLY),
        (spec(OutputContainer.MP3, VideoCodec.NONE, AudioCodec.MP3), AUDIO_ONLY),
    ],
)
def test_input_stream_contract_accepts_supported_media(
    conversion: ConversionSpec,
    inspection: MediaInspection,
) -> None:
    validate_input_streams(conversion, inspection)


def test_input_stream_contract_rejects_missing_or_incompatible_copy() -> None:
    with pytest.raises(InvalidConversionError, match="video stream"):
        validate_input_streams(spec(), AUDIO_ONLY)
    with pytest.raises(InvalidConversionError, match="cannot be copied"):
        validate_input_streams(
            spec(OutputContainer.WEBM, VideoCodec.COPY, AudioCodec.NONE),
            VIDEO_ONLY,
        )


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


def all_local_capabilities() -> LocalFFmpegCapabilities:
    return LocalFFmpegCapabilities(
        encoders=frozenset({*VIDEO_ENCODERS.values(), *AUDIO_ENCODERS.values()}),
        muxers=frozenset(item.muxer for item in CONVERSION_CAPABILITIES.values()),
    )


def test_conversion_service_inspects_validates_and_builds_command(tmp_path: Path) -> None:
    runner = CapturingRunner()
    service = ConversionService(
        executable="configured-ffmpeg",
        inspector=StubInspector(VIDEO_ONLY),  # type: ignore[arg-type]
        runner=runner,  # type: ignore[arg-type]
        capabilities=all_local_capabilities(),
    )

    service.convert(tmp_path / "input.mov", tmp_path / "output.part.mp4", spec())

    assert runner.command is not None
    assert "libx264" in runner.command
    assert "0:a:0?" in runner.command


def test_local_encoder_availability_is_enforced() -> None:
    capabilities = LocalFFmpegCapabilities(
        encoders=frozenset({"aac"}),
        muxers=frozenset({"mp4"}),
    )

    with pytest.raises(FFmpegCapabilityError, match="encoder"):
        capabilities.validate(spec())


def test_local_muxer_availability_is_enforced() -> None:
    capabilities = LocalFFmpegCapabilities(
        encoders=frozenset({"libx264", "aac"}),
        muxers=frozenset(),
    )

    with pytest.raises(FFmpegCapabilityError, match="format"):
        capabilities.validate(spec())


class WritingConverter:
    def convert(
        self,
        _input_path: Path,
        output_path: Path,
        _conversion: ConversionSpec,
    ) -> None:
        output_path.write_bytes(b"converted")


class FailingConverter:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def convert(
        self,
        _input_path: Path,
        output_path: Path,
        _conversion: ConversionSpec,
    ) -> None:
        output_path.write_bytes(b"partial")
        raise self.error


def make_storage(tmp_path: Path, media_id: str) -> LocalStorageService:
    upload_directory = tmp_path / "uploads"
    storage = LocalStorageService(
        tmp_path / "media",
        upload_directory,
        tmp_path / "outputs",
    )
    storage.initialize()
    (upload_directory / f"{media_id}.mov").write_bytes(b"input")
    return storage


def test_worker_conversion_uses_dynamic_extension_and_atomic_finalize(
    tmp_path: Path,
) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage = make_storage(tmp_path, media_id)
    conversion = spec(OutputContainer.WEBM, VideoCodec.VP9, AudioCodec.OPUS)

    result = execute_media_job(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.CONVERT,
        storage=storage,
        converter=WritingConverter(),  # type: ignore[arg-type]
        conversion=conversion,
        allowed_extensions=[".mov"],
    )

    assert result == {
        "output_id": job_id,
        "filename": f"{job_id}.webm",
        "format": "webm",
    }
    assert (tmp_path / "outputs" / f"{job_id}.webm").read_bytes() == b"converted"
    assert not (tmp_path / "outputs" / f"{job_id}.part.webm").exists()


@pytest.mark.parametrize(
    "error",
    [
        FFmpegConversionError("bad input"),
        FFmpegTimeoutError(),
        InvalidConversionError("invalid streams"),
        OutputStorageError(),
    ],
)
def test_worker_failure_cleans_partial_output(
    tmp_path: Path,
    error: Exception,
) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage = make_storage(tmp_path, media_id)

    with pytest.raises(type(error)):
        execute_media_job(
            job_id=job_id,
            media_id=media_id,
            operation=JobOperation.CONVERT,
            storage=storage,
            converter=FailingConverter(error),  # type: ignore[arg-type]
            conversion=spec(),
            allowed_extensions=[".mov"],
        )

    assert not (tmp_path / "outputs" / f"{job_id}.part.mp4").exists()
    assert not (tmp_path / "outputs" / f"{job_id}.mp4").exists()


def test_finalize_failure_cleans_partial_output(tmp_path: Path) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage = make_storage(tmp_path, media_id)

    def fail_finalize(_target: object) -> None:
        raise OutputStorageError

    storage.finalize_output = fail_finalize  # type: ignore[method-assign]

    with pytest.raises(OutputStorageError):
        execute_media_job(
            job_id=job_id,
            media_id=media_id,
            operation=JobOperation.CONVERT,
            storage=storage,
            converter=WritingConverter(),  # type: ignore[arg-type]
            conversion=spec(),
            allowed_extensions=[".mov"],
        )

    assert not (tmp_path / "outputs" / f"{job_id}.part.mp4").exists()
    assert not (tmp_path / "outputs" / f"{job_id}.mp4").exists()


def test_legacy_transcode_operation_uses_convert_pipeline(tmp_path: Path) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage = make_storage(tmp_path, media_id)

    result = execute_media_job(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.TRANSCODE,
        storage=storage,
        converter=WritingConverter(),  # type: ignore[arg-type]
        conversion=spec(),
        allowed_extensions=[".mov"],
    )

    assert result["format"] == "mp4"

