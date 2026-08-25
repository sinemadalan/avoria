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
    MediaInspection,
    SyncFFprobeInspector,
    parse_ffprobe_payload,
)
from backend.app.processing.trim import (
    MINIMUM_TRIM_DURATION_SECONDS,
    TRIM_DURATION_TOLERANCE_SECONDS,
    InvalidTrimMediaDurationError,
    InvalidTrimRangeError,
    MediaHasNoTrimStreamError,
    TrimEndExceedsDurationError,
    TrimProfile,
    TrimService,
    TrimSpec,
    TrimStartExceedsDurationError,
    build_trim_command,
    validate_trim_input,
)
from backend.app.schemas.jobs import TrimParameters
from backend.app.workers import tasks as tasks_module
from backend.app.workers.tasks import execute_media_job, process_media_job


def inspection(
    *, duration: float | None = 10.0, video: bool = True, audio_count: int = 1,
    format_name: str = "mov,mp4,m4a,3gp,3g2,mj2",
) -> MediaInspection:
    streams: list[dict[str, object]] = []
    if video:
        streams.append({"index": 0, "codec_type": "video", "codec_name": "h264"})
    streams.extend(
        {"index": index + 1, "codec_type": "audio", "codec_name": "aac"}
        for index in range(audio_count)
    )
    format_payload: dict[str, object] = {"format_name": format_name}
    if duration is not None:
        format_payload["duration"] = str(duration)
    return parse_ffprobe_payload({"format": format_payload, "streams": streams})


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


class WritingTrimmer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[Path, Path, TrimSpec, TrimProfile]] = []

    def resolve_profile(self, input_path: Path, _spec: TrimSpec) -> TrimProfile:
        profile = COMPRESSION_PROFILES[OutputContainer(input_path.suffix[1:])]
        return TrimProfile(profile.extension, profile.muxer, profile, None)

    def trim(
        self, input_path: Path, output_path: Path, spec: TrimSpec,
        profile: TrimProfile,
    ) -> None:
        self.calls.append((input_path, output_path, spec, profile))
        output_path.write_bytes(b"trimmed")
        if self.error:
            raise self.error


@pytest.mark.parametrize("value", [True, False, "1", None, math.nan, math.inf, -math.inf])
def test_trim_schema_rejects_non_finite_or_non_numeric_values(value: object) -> None:
    with pytest.raises((ValidationError, InvalidTrimRangeError)):
        TrimParameters.model_validate({"start_seconds": value, "end_seconds": 2})


def test_trim_spec_supports_millisecond_ranges() -> None:
    spec = TrimSpec(1.25, 1.251)
    assert spec.duration_seconds == pytest.approx(MINIMUM_TRIM_DURATION_SECONDS)


@pytest.mark.parametrize(
    ("spec", "error"),
    [
        (TrimSpec(10, 11), TrimStartExceedsDurationError),
        (TrimSpec(9, 10.002), TrimEndExceedsDurationError),
    ],
)
def test_trim_duration_bounds_are_validated(
    spec: TrimSpec, error: type[Exception],
) -> None:
    with pytest.raises(error):
        validate_trim_input(inspection(), spec)


def test_trim_end_accepts_documented_ffprobe_tolerance() -> None:
    validate_trim_input(
        inspection(duration=30 - TRIM_DURATION_TOLERANCE_SECONDS / 2),
        TrimSpec(10, 30),
    )


def test_trim_rejects_missing_duration_and_missing_streams() -> None:
    with pytest.raises(InvalidTrimMediaDurationError):
        validate_trim_input(inspection(duration=None), TrimSpec(0, 1))
    with pytest.raises(MediaHasNoTrimStreamError):
        validate_trim_input(inspection(video=False, audio_count=0), TrimSpec(0, 1))


@pytest.mark.parametrize("container", list(COMPRESSION_PROFILES))
def test_video_trim_command_reuses_profiles_and_maps_all_audio(
    container: OutputContainer, tmp_path: Path,
) -> None:
    video = COMPRESSION_PROFILES[container]
    profile = TrimProfile(video.extension, video.muxer, video, None)
    command = build_trim_command(
        "ffmpeg", tmp_path / f"input.{video.extension}",
        tmp_path / f"job.part.{video.extension}", TrimSpec(2.25, 6.75), profile,
    )
    assert command.index("-ss") > command.index("-i")
    assert command[command.index("-ss") + 1] == "2.25"
    assert command[command.index("-t") + 1] == "4.5"
    assert "0:v:0" in command and "0:a?" in command
    assert command[command.index("-c:v") + 1] == video.video_encoder
    assert command[command.index("-c:a") + 1] == video.audio_encoder
    assert "copy" not in command
    assert "-sn" in command and "-dn" in command
    assert command[command.index("-avoid_negative_ts") + 1] == "disabled"


@pytest.mark.parametrize("audio", list(AUDIO_EXTRACTION_PROFILES.values()))
def test_audio_trim_command_reuses_audio_profiles(audio: object, tmp_path: Path) -> None:
    profile = TrimProfile(audio.extension, audio.muxer, None, audio)  # type: ignore[attr-defined]
    command = build_trim_command(
        "ffmpeg", tmp_path / f"input.{audio.extension}",  # type: ignore[attr-defined]
        tmp_path / f"job.part.{audio.extension}", TrimSpec(1, 2), profile,  # type: ignore[attr-defined]
    )
    assert "-vn" in command and "0:a" in command
    assert command[command.index("-c:a") + 1] == audio.encoder  # type: ignore[attr-defined]


def _storage(tmp_path: Path, media_id: str, extension: str = "mp4") -> LocalStorageService:
    storage = LocalStorageService(
        tmp_path / "media", tmp_path / "uploads", tmp_path / "outputs"
    )
    storage.initialize()
    (tmp_path / "uploads" / f"{media_id}.{extension}").write_bytes(b"source")
    return storage


def test_worker_atomically_finalizes_trim_and_cleans_failure(tmp_path: Path) -> None:
    media_id, job_id = str(uuid4()), str(uuid4())
    storage = _storage(tmp_path, media_id)
    trimmer = WritingTrimmer()
    result = execute_media_job(
        job_id=job_id, media_id=media_id, operation=JobOperation.TRIM,
        storage=storage, converter=None, conversion=None,
        trimmer=trimmer, trim=TrimSpec(1, 2), allowed_extensions=[".mp4"],
    )
    assert trimmer.calls[0][1].name == f"{job_id}.part.mp4"
    assert (tmp_path / "outputs" / f"{job_id}.mp4").read_bytes() == b"trimmed"
    assert result["format"] == "mp4"

    failed_job = str(uuid4())
    failing = WritingTrimmer(FFmpegConversionError("private stderr"))
    with pytest.raises(FFmpegConversionError):
        execute_media_job(
            job_id=failed_job, media_id=media_id, operation=JobOperation.TRIM,
            storage=storage, converter=None, conversion=None,
            trimmer=failing, trim=TrimSpec(1, 2), allowed_extensions=[".mp4"],
        )
    assert not (tmp_path / "outputs" / f"{failed_job}.part.mp4").exists()
    assert not (tmp_path / "outputs" / f"{failed_job}.mp4").exists()
    assert _public_failure_message(failing.error, JobOperation.TRIM) == "Trim processing failed"


def test_celery_worker_dispatches_trim(monkeypatch: pytest.MonkeyPatch) -> None:
    job_id, media_id = str(uuid4()), str(uuid4())
    trimmer, captured = object(), {}

    class StubStorage:
        def initialize(self) -> None:
            pass

    def capture(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"output_id": job_id, "filename": f"{job_id}.mp4", "format": "mp4"}

    monkeypatch.setattr(process_media_job, "update_state", lambda **_kwargs: None)
    monkeypatch.setattr(tasks_module, "get_settings", lambda: SimpleNamespace(allowed_media_extensions=[".mp4"]))
    monkeypatch.setattr(tasks_module, "get_storage_service", StubStorage)
    monkeypatch.setattr(tasks_module, "get_trim_service", lambda: trimmer)
    monkeypatch.setattr(tasks_module, "execute_media_job", capture)
    process_media_job.push_request(id=job_id)
    try:
        process_media_job.run(media_id, "trim", {"start_seconds": 1.5, "end_seconds": 3})
    finally:
        process_media_job.pop_request()
    assert captured["trimmer"] is trimmer
    assert captured["trim"] == TrimSpec(1.5, 3)


def _tools() -> tuple[str, str]:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg and FFprobe are required for integration tests")
    return ffmpeg, ffprobe


def _service(ffmpeg: str, ffprobe: str, encoders: set[str], muxer: str) -> TrimService:
    return TrimService(
        ffmpeg, SyncFFprobeInspector(ffprobe, 30), FFmpegRunner(60),
        LocalFFmpegCapabilities(frozenset(encoders), frozenset({muxer})),
    )


@pytest.mark.parametrize(
    ("extension", "video_encoder", "audio_encoder", "muxer"),
    [("mp4", "libx264", "aac", "mp4"), ("mkv", "libx264", "aac", "matroska"),
     ("webm", "libvpx-vp9", "libopus", "webm")],
)
def test_real_video_trim_preserves_container_duration_and_two_audio_streams(
    extension: str, video_encoder: str, audio_encoder: str, muxer: str, tmp_path: Path,
) -> None:
    ffmpeg, ffprobe = _tools()
    source, output = tmp_path / f"source.{extension}", tmp_path / f"output.part.{extension}"
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=25:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=3",
        "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0",
        "-c:v", video_encoder, "-c:a", audio_encoder, "-f", muxer, str(source),
    ], check=True, capture_output=True, timeout=60)
    service = _service(ffmpeg, ffprobe, {video_encoder, audio_encoder}, muxer)
    profile = service.resolve_profile(source, TrimSpec(0.75, 2.25))
    service.trim(source, output, TrimSpec(0.75, 2.25), profile)
    result = SyncFFprobeInspector(ffprobe, 30).inspect(output)
    assert len(result.video_streams) == 1 and len(result.audio_streams) == 2
    assert result.format.duration_seconds == pytest.approx(1.5, abs=0.08)
    assert result.video_streams[0].codec_name == ("vp9" if extension == "webm" else "h264")
    timing = json.loads(
        subprocess.run(
            [
                ffprobe, "-v", "error", "-show_entries",
                "format=start_time:stream=codec_type,start_time", "-of", "json",
                str(output),
            ],
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout
    )
    assert float(timing["format"]["start_time"]) == pytest.approx(0, abs=0.001)
    stream_starts = [
        float(stream["start_time"])
        for stream in timing["streams"]
        if "start_time" in stream
    ]
    assert max(stream_starts) - min(stream_starts) <= 0.05


def test_real_video_only_trim_succeeds(tmp_path: Path) -> None:
    ffmpeg, ffprobe = _tools()
    source, output = tmp_path / "video.mp4", tmp_path / "video.part.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=64x64:rate=25:duration=2", "-an", "-c:v", "libx264", str(source)],
                   check=True, capture_output=True, timeout=30)
    service = _service(ffmpeg, ffprobe, {"libx264"}, "mp4")
    profile = service.resolve_profile(source, TrimSpec(0.5, 1.5))
    service.trim(source, output, TrimSpec(0.5, 1.5), profile)
    result = SyncFFprobeInspector(ffprobe, 30).inspect(output)
    assert result.video_streams and not result.audio_streams
    assert result.format.duration_seconds == pytest.approx(1, abs=0.08)


@pytest.mark.parametrize(
    ("extension", "format_name", "encoder", "muxer"),
    [("mp3", "mp3", "libmp3lame", "mp3"), ("flac", "flac", "flac", "flac")],
)
def test_real_audio_only_trim_preserves_format(
    extension: str, format_name: str, encoder: str, muxer: str, tmp_path: Path,
) -> None:
    ffmpeg, ffprobe = _tools()
    source, output = tmp_path / f"audio.{extension}", tmp_path / f"audio.part.{extension}"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=3", "-c:a", encoder, "-f", muxer, str(source)],
                   check=True, capture_output=True, timeout=30)
    service = _service(ffmpeg, ffprobe, {encoder}, muxer)
    profile = service.resolve_profile(source, TrimSpec(0.5, 2.5))
    service.trim(source, output, TrimSpec(0.5, 2.5), profile)
    result = SyncFFprobeInspector(ffprobe, 30).inspect(output)
    assert not result.video_streams and len(result.audio_streams) == 1
    assert format_name in (result.format.name or "")
    assert result.format.duration_seconds == pytest.approx(2, abs=0.08)


def test_real_frame_accurate_start_does_not_snap_to_keyframe(tmp_path: Path) -> None:
    ffmpeg, ffprobe = _tools()
    source, output, frame = tmp_path / "colors.mp4", tmp_path / "colors.part.mp4", tmp_path / "first.rgb"
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=red:size=32x32:rate=25:duration=1",
        "-f", "lavfi", "-i", "color=blue:size=32x32:rate=25:duration=1",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]",
        "-c:v", "libx264", "-g", "250", "-pix_fmt", "yuv420p", str(source),
    ], check=True, capture_output=True, timeout=30)
    service = _service(ffmpeg, ffprobe, {"libx264", "aac"}, "mp4")
    profile = service.resolve_profile(source, TrimSpec(1.24, 1.8))
    service.trim(source, output, TrimSpec(1.24, 1.8), profile)
    completed = subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(output), "-frames:v", "1",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ], check=True, capture_output=True, timeout=30)
    pixel = completed.stdout[:3]
    assert len(pixel) == 3
    assert pixel[2] > 180 and pixel[0] < 80  # blue, not the preceding red keyframe
