import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.application.ports.jobs import JobOperation
from backend.app.infrastructure.queue import _public_failure_message
from backend.app.infrastructure.storage import LocalStorageService
from backend.app.processing.audio_extraction import MediaHasNoAudioError
from backend.app.processing.compression import (
    COMPRESSION_PROFILES,
    CompressionProfile,
    UnsupportedCompressionContainerError,
)
from backend.app.processing.conversion import (
    FFmpegConversionError,
    FFmpegRunner,
    LocalFFmpegCapabilities,
    OutputContainer,
)
from backend.app.processing.mute import MediaHasNoVideoError
from backend.app.processing.probe import (
    MediaInspection,
    SyncFFprobeInspector,
    parse_ffprobe_payload,
)
from backend.app.processing.volume import (
    InvalidVolumeError,
    VolumeService,
    VolumeSpec,
    build_volume_command,
    volume_factor,
)
from backend.app.workers import tasks as tasks_module
from backend.app.workers.tasks import execute_media_job, process_media_job


VIDEO_WITH_AUDIO = parse_ffprobe_payload(
    {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
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
    def __init__(self) -> None:
        self.command: list[str] | None = None

    def run(self, command: list[str]) -> None:
        self.command = command


class WritingVolumeAdjuster:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[Path, Path, VolumeSpec, CompressionProfile]] = []

    def resolve_profile(self, input_path: Path) -> CompressionProfile:
        return COMPRESSION_PROFILES[OutputContainer(input_path.suffix[1:].casefold())]

    def adjust(
        self,
        input_path: Path,
        output_path: Path,
        spec: VolumeSpec,
        profile: CompressionProfile,
    ) -> None:
        self.calls.append((input_path, output_path, spec, profile))
        output_path.write_bytes(b"volume-output")
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


@pytest.mark.parametrize(
    ("percentage", "factor"),
    [(0, "0"), (50, "0.5"), (100, "1"), (150, "1.5"), (200, "2")],
)
def test_volume_factor_is_exact_and_deterministic(
    percentage: int,
    factor: str,
) -> None:
    assert volume_factor(percentage) == factor


@pytest.mark.parametrize("value", [-1, 201, "50", None, 50.0, True])
def test_volume_spec_rejects_invalid_values(value: object) -> None:
    with pytest.raises(InvalidVolumeError):
        VolumeSpec(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("container", list(COMPRESSION_PROFILES))
def test_volume_command_uses_container_profile_and_all_audio_streams(
    container: OutputContainer,
    tmp_path: Path,
) -> None:
    profile = COMPRESSION_PROFILES[container]
    command = build_volume_command(
        "configured-ffmpeg",
        tmp_path / f"input.{profile.extension}",
        tmp_path / f"job.part.{profile.extension}",
        VolumeSpec(150),
        profile,
    )

    map_values = [
        command[index + 1]
        for index, item in enumerate(command)
        if item == "-map"
    ]
    assert map_values == ["0:v:0", "0:a"]
    assert command[command.index("-c:v") + 1] == "copy"
    assert command[command.index("-af") + 1] == "volume=1.5"
    assert command[command.index("-c:a") + 1] == profile.audio_encoder
    assert command[command.index("-f") + 1] == profile.muxer
    assert all(option in command for option in profile.audio_options)
    assert "-sn" in command
    assert "-dn" in command
    for forbidden in ("libx264", "libx265", "libvpx-vp9", "-crf", "-b:v"):
        assert forbidden not in command


@pytest.mark.parametrize(
    ("container", "audio_encoder"),
    [
        (OutputContainer.MP4, "aac"),
        (OutputContainer.MOV, "aac"),
        (OutputContainer.MKV, "aac"),
        (OutputContainer.WEBM, "libopus"),
        (OutputContainer.AVI, "libmp3lame"),
    ],
)
def test_volume_audio_encoder_policy_is_centralized(
    container: OutputContainer,
    audio_encoder: str,
) -> None:
    assert COMPRESSION_PROFILES[container].audio_encoder == audio_encoder


@pytest.mark.parametrize(
    ("inspection", "error_type"),
    [(AUDIO_ONLY, MediaHasNoVideoError), (VIDEO_ONLY, MediaHasNoAudioError)],
)
def test_volume_rejects_media_missing_required_stream_before_ffmpeg(
    inspection: MediaInspection,
    error_type: type[Exception],
) -> None:
    runner = CapturingRunner()
    service = VolumeService(
        "ffmpeg",
        StubInspector(inspection),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
        LocalFFmpegCapabilities(frozenset({"aac"}), frozenset({"mp4"})),
    )

    with pytest.raises(error_type):
        service.resolve_profile(Path("input.mp4"))

    assert runner.command is None


def test_volume_rejects_unsupported_container_before_ffmpeg() -> None:
    runner = CapturingRunner()
    service = VolumeService(
        "ffmpeg",
        StubInspector(VIDEO_WITH_AUDIO),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
        LocalFFmpegCapabilities(frozenset({"aac"}), frozenset({"mp4"})),
    )

    with pytest.raises(UnsupportedCompressionContainerError):
        service.resolve_profile(Path("input.flv"))

    assert runner.command is None
    assert (
        _public_failure_message(
            UnsupportedCompressionContainerError(), JobOperation.VOLUME
        )
        == "Volume adjustment is not supported for this container"
    )


@pytest.mark.parametrize(
    ("inspection", "error_type"),
    [(AUDIO_ONLY, MediaHasNoVideoError), (VIDEO_ONLY, MediaHasNoAudioError)],
)
def test_volume_stream_validation_creates_no_worker_output(
    inspection: MediaInspection,
    error_type: type[Exception],
    tmp_path: Path,
) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage = make_storage(tmp_path, media_id)
    runner = CapturingRunner()
    service = VolumeService(
        "ffmpeg",
        StubInspector(inspection),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
        LocalFFmpegCapabilities(frozenset({"aac"}), frozenset({"mp4"})),
    )

    with pytest.raises(error_type):
        execute_media_job(
            job_id=job_id,
            media_id=media_id,
            operation=JobOperation.VOLUME,
            storage=storage,
            converter=None,
            conversion=None,
            volume_adjuster=service,
            volume=VolumeSpec(50),
            allowed_extensions=[".mp4"],
        )

    assert runner.command is None
    assert not (tmp_path / "outputs" / f"{job_id}.part.mp4").exists()
    assert not (tmp_path / "outputs" / f"{job_id}.mp4").exists()


@pytest.mark.parametrize("extension", ["mp4", "mov", "mkv", "webm", "avi"])
def test_worker_preserves_container_and_atomically_finalizes_volume(
    extension: str,
    tmp_path: Path,
) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage = make_storage(tmp_path, media_id, extension)
    adjuster = WritingVolumeAdjuster()

    result = execute_media_job(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.VOLUME,
        storage=storage,
        converter=None,
        conversion=None,
        volume_adjuster=adjuster,  # type: ignore[arg-type]
        volume=VolumeSpec(50),
        allowed_extensions=[f".{extension}"],
    )

    output_path = tmp_path / "outputs" / f"{job_id}.{extension}"
    assert adjuster.calls[0][1].name == f"{job_id}.part.{extension}"
    assert adjuster.calls[0][2] == VolumeSpec(50)
    assert output_path.read_bytes() == b"volume-output"
    assert not (tmp_path / "outputs" / f"{job_id}.part.{extension}").exists()
    assert result == {
        "output_id": job_id,
        "filename": f"{job_id}.{extension}",
        "format": extension,
    }


def test_volume_ffmpeg_failure_cleans_partial_output(tmp_path: Path) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage = make_storage(tmp_path, media_id)
    adjuster = WritingVolumeAdjuster(FFmpegConversionError("private diagnostic"))

    with pytest.raises(FFmpegConversionError):
        execute_media_job(
            job_id=job_id,
            media_id=media_id,
            operation=JobOperation.VOLUME,
            storage=storage,
            converter=None,
            conversion=None,
            volume_adjuster=adjuster,  # type: ignore[arg-type]
            volume=VolumeSpec(50),
            allowed_extensions=[".mp4"],
        )

    assert not (tmp_path / "outputs" / f"{job_id}.part.mp4").exists()
    assert not (tmp_path / "outputs" / f"{job_id}.mp4").exists()
    assert (
        _public_failure_message(
            FFmpegConversionError("private diagnostic"), JobOperation.VOLUME
        )
        == "Volume adjustment failed"
    )


def test_celery_worker_dispatches_volume_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = str(uuid4())
    media_id = str(uuid4())
    adjuster = object()
    captured: dict[str, object] = {}

    class StubStorage:
        def initialize(self) -> None:
            return None

    def capture_execute(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "output_id": job_id,
            "filename": f"{job_id}.mp4",
            "format": "mp4",
        }

    monkeypatch.setattr(process_media_job, "update_state", lambda **_kwargs: None)
    monkeypatch.setattr(
        tasks_module,
        "get_settings",
        lambda: SimpleNamespace(allowed_media_extensions=[".mp4"]),
    )
    monkeypatch.setattr(tasks_module, "get_storage_service", StubStorage)
    monkeypatch.setattr(tasks_module, "get_volume_service", lambda: adjuster)
    monkeypatch.setattr(tasks_module, "execute_media_job", capture_execute)

    process_media_job.push_request(id=job_id)
    try:
        result = process_media_job.run(media_id, "volume", {"volume_percent": 150})
    finally:
        process_media_job.pop_request()

    assert captured["volume_adjuster"] is adjuster
    assert captured["volume"] == VolumeSpec(150)
    assert captured["muter"] is None
    assert result["format"] == "mp4"
    assert result["progress"] == 100


def _mean_volume(ffmpeg: str, media_path: Path, audio_index: int) -> float:
    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(media_path),
            "-map",
            f"0:a:{audio_index}",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=30,
        text=True,
    )
    match = re.search(r"mean_volume:\s+(-?\d+(?:\.\d+)?) dB", completed.stderr)
    if match is None:
        raise AssertionError("FFmpeg did not report mean volume")
    return float(match.group(1))


@pytest.mark.parametrize(
    ("extension", "video_encoder", "audio_encoder", "muxer"),
    [
        ("mp4", "libx264", "aac", "mp4"),
        ("mkv", "libx264", "aac", "matroska"),
        ("webm", "libvpx-vp9", "libopus", "webm"),
    ],
)
def test_real_ffmpeg_volume_preserves_two_audio_streams_and_reduces_levels(
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
            "color=c=blue:s=64x64:d=0.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=0.5",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map",
            "2:a:0",
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
    profile = COMPRESSION_PROFILES[OutputContainer(extension)]
    service = VolumeService(
        ffmpeg,
        inspector,
        FFmpegRunner(30),
        LocalFFmpegCapabilities(
            frozenset({profile.audio_encoder}),
            frozenset({profile.muxer}),
        ),
    )

    resolved_profile = service.resolve_profile(input_path)
    service.adjust(input_path, output_path, VolumeSpec(50), resolved_profile)

    source = inspector.inspect(input_path)
    adjusted = inspector.inspect(output_path)
    assert len(source.audio_streams) == len(adjusted.audio_streams) == 2
    assert source.video_streams[0].codec_name == adjusted.video_streams[0].codec_name
    for audio_index in range(2):
        level_change = _mean_volume(ffmpeg, output_path, audio_index) - _mean_volume(
            ffmpeg, input_path, audio_index
        )
        assert level_change == pytest.approx(-6.02, abs=1.0)
