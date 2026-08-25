import json
import math
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
from backend.app.processing.audio_extraction import AUDIO_EXTRACTION_PROFILES
from backend.app.processing.compression import COMPRESSION_PROFILES
from backend.app.processing.conversion import (
    FFmpegConversionError,
    FFmpegRunner,
    LocalFFmpegCapabilities,
    OutputContainer,
)
from backend.app.processing.probe import (
    FFprobeProcessError,
    MediaInspection,
    SyncFFprobeInspector,
    parse_ffprobe_payload,
)
from backend.app.processing.speed import (
    InvalidSpeedError,
    MediaHasNoSpeedStreamError,
    SpeedProfile,
    SpeedService,
    SpeedSpec,
    build_atempo_filter,
    build_speed_command,
    build_video_speed_filter,
    validate_speed_input,
)
from backend.app.schemas.jobs import SpeedParameters
from backend.app.workers import tasks as tasks_module
from backend.app.workers.tasks import execute_media_job, process_media_job

SPEED_DURATION_TOLERANCE_SECONDS = 0.12
SPEED_START_SYNC_TOLERANCE_SECONDS = 0.08


def inspection(*, video: bool = True, audio_count: int = 1) -> MediaInspection:
    streams: list[dict[str, object]] = []
    if video:
        streams.append({"index": 0, "codec_type": "video", "codec_name": "h264"})
    streams.extend(
        {"index": index + 1, "codec_type": "audio", "codec_name": "aac"}
        for index in range(audio_count)
    )
    return parse_ffprobe_payload(
        {
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "8"},
            "streams": streams,
        }
    )


@pytest.mark.parametrize(
    ("speed", "expected"),
    [
        (0.25, "atempo=0.5,atempo=0.5"),
        (0.5, "atempo=0.5"),
        (0.75, "atempo=0.75"),
        (1, "atempo=1"),
        (1.5, "atempo=1.5"),
        (2, "atempo=2"),
        (3, "atempo=2,atempo=1.5"),
        (4, "atempo=2,atempo=2"),
    ],
)
def test_atempo_chain_is_valid_and_deterministic(
    speed: float, expected: str,
) -> None:
    assert build_atempo_filter(speed) == expected


@pytest.mark.parametrize(
    "value", [0, -1, 0.24, 4.01, "1.5", None, True, False, math.nan, math.inf, -math.inf]
)
def test_speed_schema_and_spec_reject_invalid_values(value: object) -> None:
    with pytest.raises((ValidationError, InvalidSpeedError)):
        SpeedParameters.model_validate({"speed": value})


def test_speed_schema_rejects_missing_and_extra_parameters() -> None:
    with pytest.raises(ValidationError):
        SpeedParameters.model_validate({})
    with pytest.raises(ValidationError):
        SpeedParameters.model_validate({"speed": 1.5, "codec": "copy"})


@pytest.mark.parametrize("speed", [0.25, 0.5, 1, 1.25, 1.5, 2, 3.5, 4])
def test_speed_spec_accepts_supported_integer_and_decimal_values(speed: float) -> None:
    spec = SpeedSpec(speed)
    assert spec.to_payload() == {"speed": float(speed)}
    assert build_video_speed_filter(speed) == f"setpts=PTS/{speed:g}"


def test_speed_rejects_media_without_audio_or_video() -> None:
    with pytest.raises(MediaHasNoSpeedStreamError):
        validate_speed_input(inspection(video=False, audio_count=0))


def test_speed_errors_are_mapped_without_internal_details() -> None:
    assert _public_failure_message(InvalidSpeedError("detail"), JobOperation.SPEED) == "Invalid speed"
    assert _public_failure_message(
        MediaHasNoSpeedStreamError("detail"), JobOperation.SPEED
    ) == "Media has no audio/video stream"
    assert _public_failure_message(
        FFprobeProcessError("private probe stderr"), JobOperation.SPEED
    ) == "Media inspection failed"


@pytest.mark.parametrize("container", list(COMPRESSION_PROFILES))
def test_video_audio_command_reuses_profile_and_filters_every_audio_stream(
    container: OutputContainer, tmp_path: Path,
) -> None:
    video = COMPRESSION_PROFILES[container]
    profile = SpeedProfile(video.extension, video.muxer, video, None, 2)
    command = build_speed_command(
        "ffmpeg",
        tmp_path / f"input.{video.extension}",
        tmp_path / f"job.part.{video.extension}",
        SpeedSpec(1.5),
        profile,
    )
    assert command[command.index("-vf") + 1] == "setpts=PTS/1.5"
    assert "0:v:0" in command and "0:a:0" in command and "0:a:1" in command
    assert command[command.index("-filter:a:0") + 1] == "atempo=1.5"
    assert command[command.index("-filter:a:1") + 1] == "atempo=1.5"
    assert command[command.index("-c:v") + 1] == video.video_encoder
    assert command[command.index("-c:a") + 1] == video.audio_encoder
    assert "copy" not in command
    assert "-sn" in command and "-dn" in command
    assert command[command.index("-f") + 1] == video.muxer


def test_video_only_command_has_no_audio_filter(tmp_path: Path) -> None:
    video = COMPRESSION_PROFILES[OutputContainer.MP4]
    command = build_speed_command(
        "ffmpeg", tmp_path / "input.mp4", tmp_path / "output.part.mp4",
        SpeedSpec(2), SpeedProfile("mp4", "mp4", video, None, 0),
    )
    assert "-vf" in command and "-an" in command
    assert not any(item.startswith("-filter:a") for item in command)
    assert "-c:a" not in command


@pytest.mark.parametrize("audio", list(AUDIO_EXTRACTION_PROFILES.values()))
def test_audio_only_command_reuses_audio_profile(audio: object, tmp_path: Path) -> None:
    profile = SpeedProfile(audio.extension, audio.muxer, None, audio, 1)  # type: ignore[attr-defined]
    command = build_speed_command(
        "ffmpeg", tmp_path / f"input.{audio.extension}",  # type: ignore[attr-defined]
        tmp_path / f"output.part.{audio.extension}", SpeedSpec(0.5), profile,  # type: ignore[attr-defined]
    )
    assert "-vn" in command and "-vf" not in command and "-c:v" not in command
    assert command[command.index("-filter:a:0") + 1] == "atempo=0.5"
    assert command[command.index("-c:a") + 1] == audio.encoder  # type: ignore[attr-defined]


class WritingSpeedChanger:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[Path, Path, SpeedSpec, SpeedProfile]] = []

    def resolve_profile(self, input_path: Path) -> SpeedProfile:
        video = COMPRESSION_PROFILES[OutputContainer(input_path.suffix[1:])]
        return SpeedProfile(video.extension, video.muxer, video, None, 1)

    def change_speed(
        self, input_path: Path, output_path: Path, spec: SpeedSpec, profile: SpeedProfile,
    ) -> None:
        self.calls.append((input_path, output_path, spec, profile))
        output_path.write_bytes(b"speed-output")
        if self.error:
            raise self.error


def _storage(tmp_path: Path, media_id: str) -> LocalStorageService:
    storage = LocalStorageService(
        tmp_path / "media", tmp_path / "uploads", tmp_path / "outputs"
    )
    storage.initialize()
    (tmp_path / "uploads" / f"{media_id}.mp4").write_bytes(b"original-upload")
    return storage


def test_worker_processes_original_upload_and_atomically_finalizes(tmp_path: Path) -> None:
    media_id, job_id = str(uuid4()), str(uuid4())
    storage = _storage(tmp_path, media_id)
    changer = WritingSpeedChanger()
    result = execute_media_job(
        job_id=job_id, media_id=media_id, operation=JobOperation.SPEED,
        storage=storage, converter=None, conversion=None,
        speed_changer=changer, speed=SpeedSpec(2), allowed_extensions=[".mp4"],
    )
    assert changer.calls[0][0].name == f"{media_id}.mp4"
    assert changer.calls[0][1].name == f"{job_id}.part.mp4"
    assert (tmp_path / "outputs" / f"{job_id}.mp4").read_bytes() == b"speed-output"
    assert result["format"] == "mp4"


def test_speed_failure_cleans_partial_and_maps_safe_error(tmp_path: Path) -> None:
    media_id, job_id = str(uuid4()), str(uuid4())
    storage = _storage(tmp_path, media_id)
    changer = WritingSpeedChanger(FFmpegConversionError("private command/stderr"))
    with pytest.raises(FFmpegConversionError):
        execute_media_job(
            job_id=job_id, media_id=media_id, operation=JobOperation.SPEED,
            storage=storage, converter=None, conversion=None,
            speed_changer=changer, speed=SpeedSpec(2), allowed_extensions=[".mp4"],
        )
    assert not (tmp_path / "outputs" / f"{job_id}.part.mp4").exists()
    assert not (tmp_path / "outputs" / f"{job_id}.mp4").exists()
    assert _public_failure_message(changer.error, JobOperation.SPEED) == "Speed processing failed"


def test_celery_worker_dispatches_standalone_speed_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id, media_id = str(uuid4()), str(uuid4())
    changer, captured = object(), {}

    class StubStorage:
        def initialize(self) -> None:
            pass

    def capture(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"output_id": job_id, "filename": f"{job_id}.mp4", "format": "mp4"}

    monkeypatch.setattr(process_media_job, "update_state", lambda **_kwargs: None)
    monkeypatch.setattr(
        tasks_module, "get_settings", lambda: SimpleNamespace(allowed_media_extensions=[".mp4"])
    )
    monkeypatch.setattr(tasks_module, "get_storage_service", StubStorage)
    monkeypatch.setattr(tasks_module, "get_speed_service", lambda: changer)
    monkeypatch.setattr(tasks_module, "execute_media_job", capture)
    process_media_job.push_request(id=job_id)
    try:
        process_media_job.run(media_id, "speed", {"speed": 1.5})
    finally:
        process_media_job.pop_request()
    assert captured["speed_changer"] is changer
    assert captured["speed"] == SpeedSpec(1.5)
    assert captured["trimmer"] is None and captured["trim"] is None


def _tools() -> tuple[str, str]:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg and FFprobe are required for integration tests")
    return ffmpeg, ffprobe


def _service(ffmpeg: str, ffprobe: str, encoders: set[str], muxer: str) -> SpeedService:
    return SpeedService(
        ffmpeg, SyncFFprobeInspector(ffprobe, 30), FFmpegRunner(90),
        LocalFFmpegCapabilities(frozenset(encoders), frozenset({muxer})),
    )


def _timing(ffprobe: str, path: Path) -> tuple[float, list[float]]:
    payload = json.loads(
        subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,duration,start_time", "-of", "json", str(path)],
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout
    )
    return float(payload["format"]["duration"]), [
        float(stream["start_time"])
        for stream in payload["streams"]
        if "start_time" in stream
    ]


@pytest.mark.parametrize(
    ("extension", "video_encoder", "audio_encoder", "muxer"),
    [("mp4", "libx264", "aac", "mp4"), ("mkv", "libx264", "aac", "matroska"),
     ("webm", "libvpx-vp9", "libopus", "webm")],
)
def test_real_speed_preserves_video_container_two_audio_streams_and_sync(
    extension: str, video_encoder: str, audio_encoder: str, muxer: str, tmp_path: Path,
) -> None:
    ffmpeg, ffprobe = _tools()
    source = tmp_path / f"source.{extension}"
    output = tmp_path / f"output.part.{extension}"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=25:duration=4",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=4",
         "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0",
         "-c:v", video_encoder, "-c:a", audio_encoder, "-f", muxer, str(source)],
        check=True, capture_output=True, timeout=60,
    )
    service = _service(ffmpeg, ffprobe, {video_encoder, audio_encoder}, muxer)
    profile = service.resolve_profile(source)
    service.change_speed(source, output, SpeedSpec(2), profile)
    result = SyncFFprobeInspector(ffprobe, 30).inspect(output)
    assert len(result.video_streams) == 1 and len(result.audio_streams) == 2
    assert result.format.duration_seconds == pytest.approx(
        2, abs=SPEED_DURATION_TOLERANCE_SECONDS
    )
    assert result.video_streams[0].codec_name == ("vp9" if extension == "webm" else "h264")
    _, starts = _timing(ffprobe, output)
    assert starts and max(starts) - min(starts) <= SPEED_START_SYNC_TOLERANCE_SECONDS


def test_real_mp4_slowdown_doubles_duration(tmp_path: Path) -> None:
    ffmpeg, ffprobe = _tools()
    source, output = tmp_path / "slow.mp4", tmp_path / "slow.part.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=size=64x64:rate=25:duration=2", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=2", "-c:v", "libx264", "-c:a", "aac", str(source)],
        check=True, capture_output=True, timeout=30,
    )
    service = _service(ffmpeg, ffprobe, {"libx264", "aac"}, "mp4")
    service.change_speed(source, output, SpeedSpec(0.5), service.resolve_profile(source))
    result = SyncFFprobeInspector(ffprobe, 30).inspect(output)
    assert result.format.duration_seconds == pytest.approx(
        4, abs=SPEED_DURATION_TOLERANCE_SECONDS
    )


def test_real_video_only_speed_succeeds(tmp_path: Path) -> None:
    ffmpeg, ffprobe = _tools()
    source, output = tmp_path / "video.mp4", tmp_path / "video.part.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=size=64x64:rate=25:duration=2", "-an", "-c:v", "libx264", str(source)],
        check=True, capture_output=True, timeout=30,
    )
    service = _service(ffmpeg, ffprobe, {"libx264"}, "mp4")
    service.change_speed(source, output, SpeedSpec(2), service.resolve_profile(source))
    result = SyncFFprobeInspector(ffprobe, 30).inspect(output)
    assert result.video_streams and not result.audio_streams
    assert result.format.duration_seconds == pytest.approx(
        1, abs=SPEED_DURATION_TOLERANCE_SECONDS
    )


@pytest.mark.parametrize(
    ("extension", "encoder", "muxer", "speed", "expected"),
    [("mp3", "libmp3lame", "mp3", 2, 1), ("flac", "flac", "flac", 0.5, 4)],
)
def test_real_audio_only_speed_preserves_format_and_duration(
    extension: str, encoder: str, muxer: str, speed: float, expected: float,
    tmp_path: Path,
) -> None:
    ffmpeg, ffprobe = _tools()
    source = tmp_path / f"audio.{extension}"
    output = tmp_path / f"audio.part.{extension}"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=2", "-c:a", encoder, "-f", muxer, str(source)],
        check=True, capture_output=True, timeout=30,
    )
    service = _service(ffmpeg, ffprobe, {encoder}, muxer)
    service.change_speed(source, output, SpeedSpec(speed), service.resolve_profile(source))
    result = SyncFFprobeInspector(ffprobe, 30).inspect(output)
    assert not result.video_streams and len(result.audio_streams) == 1
    assert extension in (result.format.name or "")
    assert result.format.duration_seconds == pytest.approx(
        expected, abs=SPEED_DURATION_TOLERANCE_SECONDS
    )
