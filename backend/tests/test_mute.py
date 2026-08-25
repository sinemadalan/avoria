import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.application.ports.jobs import JobOperation
from backend.app.infrastructure.queue import _public_failure_message
from backend.app.infrastructure.storage import LocalStorageService
from backend.app.processing.compression import (
    COMPRESSION_PROFILES,
    CompressionProfile,
)
from backend.app.processing.conversion import (
    FFmpegConversionError,
    FFmpegRunner,
    LocalFFmpegCapabilities,
    OutputContainer,
)
from backend.app.processing.mute import (
    MediaHasNoVideoError,
    MuteService,
    build_mute_command,
)
from backend.app.processing.probe import (
    MediaInspection,
    SyncFFprobeInspector,
    parse_ffprobe_payload,
)
from backend.app.workers.tasks import execute_media_job
from backend.app.workers import tasks as tasks_module
from backend.app.workers.tasks import process_media_job


VIDEO_ONLY = parse_ffprobe_payload(
    {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        "streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}],
    }
)
AUDIO_ONLY = parse_ffprobe_payload(
    {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        "streams": [{"index": 0, "codec_type": "audio", "codec_name": "aac"}],
    }
)


class StubInspector:
    def __init__(self, inspection: MediaInspection) -> None:
        self.inspection = inspection

    def inspect(self, _path: Path) -> MediaInspection:
        return self.inspection


class CapturingRunner:
    def __init__(self, error: Exception | None = None) -> None:
        self.command: list[str] | None = None
        self.error = error

    def run(self, command: list[str]) -> None:
        self.command = command
        if self.error is not None:
            raise self.error


class WritingMuter:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[Path, Path, CompressionProfile]] = []

    def resolve_profile(self, input_path: Path) -> CompressionProfile:
        return COMPRESSION_PROFILES[OutputContainer(input_path.suffix[1:].casefold())]

    def mute(
        self,
        input_path: Path,
        output_path: Path,
        profile: CompressionProfile,
    ) -> None:
        self.calls.append((input_path, output_path, profile))
        output_path.write_bytes(b"muted-video")
        if self.error is not None:
            raise self.error


def make_storage(
    tmp_path: Path,
    media_id: str,
    extension: str = "mp4",
) -> LocalStorageService:
    uploads = tmp_path / "uploads"
    storage = LocalStorageService(tmp_path / "media", uploads, tmp_path / "outputs")
    storage.initialize()
    (uploads / f"{media_id}.{extension}").write_bytes(b"source-video")
    return storage


def test_mute_is_a_valid_job_operation() -> None:
    assert JobOperation("mute") is JobOperation.MUTE


@pytest.mark.parametrize("container", list(COMPRESSION_PROFILES))
def test_mute_command_stream_copies_only_primary_video(
    container: OutputContainer,
    tmp_path: Path,
) -> None:
    profile = COMPRESSION_PROFILES[container]
    command = build_mute_command(
        "configured-ffmpeg",
        tmp_path / f"input.{profile.extension}",
        tmp_path / f"job.part.{profile.extension}",
        profile,
    )

    assert command[command.index("-map") + 1] == "0:v:0"
    assert command[command.index("-c:v") + 1] == "copy"
    assert "-an" in command
    assert "-sn" in command
    assert "-dn" in command
    assert command[command.index("-f") + 1] == profile.muxer
    for forbidden in ("libx264", "libx265", "libvpx-vp9", "-crf", "-b:v"):
        assert forbidden not in command


def test_already_muted_video_is_accepted() -> None:
    runner = CapturingRunner()
    service = MuteService(
        "ffmpeg",
        StubInspector(VIDEO_ONLY),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
        LocalFFmpegCapabilities(frozenset(), frozenset({"mp4"})),
    )

    profile = service.resolve_profile(Path("video.mp4"))
    service.mute(Path("video.mp4"), Path("output.part.mp4"), profile)

    assert runner.command is not None
    assert "-an" in runner.command


def test_no_video_is_rejected_before_ffmpeg() -> None:
    runner = CapturingRunner()
    service = MuteService(
        "ffmpeg",
        StubInspector(AUDIO_ONLY),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
        LocalFFmpegCapabilities(frozenset(), frozenset({"mp4"})),
    )

    with pytest.raises(MediaHasNoVideoError, match="video stream"):
        service.resolve_profile(Path("audio.mp4"))

    assert runner.command is None
    assert (
        _public_failure_message(MediaHasNoVideoError(), JobOperation.MUTE)
        == "The input does not contain a video stream"
    )


def test_no_video_worker_failure_creates_no_partial_output(tmp_path: Path) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage = make_storage(tmp_path, media_id)
    runner = CapturingRunner()
    service = MuteService(
        "ffmpeg",
        StubInspector(AUDIO_ONLY),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
        LocalFFmpegCapabilities(frozenset(), frozenset({"mp4"})),
    )

    with pytest.raises(MediaHasNoVideoError):
        execute_media_job(
            job_id=job_id,
            media_id=media_id,
            operation=JobOperation.MUTE,
            storage=storage,
            converter=None,
            conversion=None,
            muter=service,
            allowed_extensions=[".mp4"],
        )

    assert runner.command is None
    assert not (tmp_path / "outputs" / f"{job_id}.part.mp4").exists()
    assert not (tmp_path / "outputs" / f"{job_id}.mp4").exists()


@pytest.mark.parametrize("extension", ["mp4", "mov", "mkv", "webm", "avi"])
def test_worker_preserves_container_and_atomically_finalizes(
    extension: str,
    tmp_path: Path,
) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage = make_storage(tmp_path, media_id, extension)
    muter = WritingMuter()

    result = execute_media_job(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.MUTE,
        storage=storage,
        converter=None,
        conversion=None,
        muter=muter,  # type: ignore[arg-type]
        allowed_extensions=[f".{extension}"],
    )

    output_path = tmp_path / "outputs" / f"{job_id}.{extension}"
    assert muter.calls[0][1].name == f"{job_id}.part.{extension}"
    assert output_path.read_bytes() == b"muted-video"
    assert not (tmp_path / "outputs" / f"{job_id}.part.{extension}").exists()
    assert result == {
        "output_id": job_id,
        "filename": f"{job_id}.{extension}",
        "format": extension,
    }


def test_ffmpeg_failure_cleans_partial_and_never_creates_final(tmp_path: Path) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage = make_storage(tmp_path, media_id)
    muter = WritingMuter(FFmpegConversionError("private diagnostic"))

    with pytest.raises(FFmpegConversionError):
        execute_media_job(
            job_id=job_id,
            media_id=media_id,
            operation=JobOperation.MUTE,
            storage=storage,
            converter=None,
            conversion=None,
            muter=muter,  # type: ignore[arg-type]
            allowed_extensions=[".mp4"],
        )

    assert not (tmp_path / "outputs" / f"{job_id}.part.mp4").exists()
    assert not (tmp_path / "outputs" / f"{job_id}.mp4").exists()
    assert (
        _public_failure_message(
            FFmpegConversionError("private diagnostic"), JobOperation.MUTE
        )
        == "Video mute failed"
    )


def test_celery_worker_dispatches_mute_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = str(uuid4())
    media_id = str(uuid4())
    muter = object()
    captured: dict[str, object] = {}

    class StubStorage:
        def initialize(self) -> None:
            return None

    def capture_execute(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "output_id": job_id,
            "filename": f"{job_id}.mkv",
            "format": "mkv",
        }

    monkeypatch.setattr(process_media_job, "update_state", lambda **_kwargs: None)
    monkeypatch.setattr(
        tasks_module,
        "get_settings",
        lambda: SimpleNamespace(allowed_media_extensions=[".mkv"]),
    )
    monkeypatch.setattr(tasks_module, "get_storage_service", StubStorage)
    monkeypatch.setattr(tasks_module, "get_mute_service", lambda: muter)
    monkeypatch.setattr(tasks_module, "execute_media_job", capture_execute)

    process_media_job.push_request(id=job_id)
    try:
        result = process_media_job.run(media_id, "mute", {})
    finally:
        process_media_job.pop_request()

    assert captured["muter"] is muter
    assert captured["converter"] is None
    assert captured["compressor"] is None
    assert captured["audio_extractor"] is None
    assert result["format"] == "mkv"
    assert result["progress"] == 100


@pytest.mark.parametrize(
    ("extension", "video_encoder", "audio_encoder", "muxer"),
    [
        ("mp4", "libx264", "aac", "mp4"),
        ("mkv", "libx264", "aac", "matroska"),
        ("webm", "libvpx-vp9", "libopus", "webm"),
    ],
)
def test_real_ffmpeg_mute_preserves_video_codec_and_removes_audio(
    extension: str,
    video_encoder: str,
    audio_encoder: str,
    muxer: str,
    tmp_path: Path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg and FFprobe are required for the integration test")

    input_path = tmp_path / f"input.{extension}"
    output_path = tmp_path / f"output.part.{extension}"
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
            "color=c=blue:s=64x64:d=0.25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=0.25",
            "-shortest",
            "-c:v",
            video_encoder,
            "-c:a",
            audio_encoder,
            "-f",
            muxer,
            str(input_path),
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
    )
    inspector = SyncFFprobeInspector(ffprobe, 30)
    service = MuteService(
        ffmpeg,
        inspector,
        FFmpegRunner(30),
        LocalFFmpegCapabilities(frozenset(), frozenset({muxer})),
    )

    profile = service.resolve_profile(input_path)
    service.mute(input_path, output_path, profile)

    source = inspector.inspect(input_path)
    muted = inspector.inspect(output_path)
    assert source.video_streams[0].codec_name == muted.video_streams[0].codec_name
    assert source.audio_streams
    assert not muted.audio_streams
    assert muted.video_streams
