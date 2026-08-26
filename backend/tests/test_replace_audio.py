import json
import math
import shutil
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.application.ports.jobs import JobOperation
from backend.app.infrastructure.queue import _public_failure_message
from backend.app.infrastructure.storage import LocalStorageService
from backend.app.processing.audio_extraction import (
    MediaHasNoAudioError,
    UnsupportedAudioContainerError,
)
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
from backend.app.processing.probe import (
    AudioStreamMetadata,
    MediaFormatMetadata,
    MediaInspection,
    SyncFFprobeInspector,
    VideoStreamMetadata,
    InvalidMediaError,
)
from backend.app.processing.replace_audio import (
    ExternalAudioInspectionError,
    ExternalAudioMediaNotFoundError,
    ExternalMediaHasVideoError,
    InvalidReplaceAudioDurationError,
    ReplaceAudioProfile,
    ReplaceAudioService,
    ReplaceAudioSpec,
    ReplaceAudioTargetHasNoVideoError,
    TargetMediaInspectionError,
    build_replace_audio_command,
)
from backend.app.workers import tasks as tasks_module
from backend.app.workers.tasks import execute_media_job, process_media_job


def _format(name: str, duration: float | None = 5.0) -> MediaFormatMetadata:
    return MediaFormatMetadata(name, name, duration, 100, 1000)


def _video(index: int = 0, codec: str = "h264") -> VideoStreamMetadata:
    return VideoStreamMetadata(index, codec, codec, None, 64, 64, "yuv420p", 25, None)


def _audio(index: int = 0, codec: str = "aac") -> AudioStreamMetadata:
    return AudioStreamMetadata(index, codec, codec, 44100, 1, "mono", None)


def _inspection(
    format_name: str,
    *,
    duration: float | None = 5.0,
    videos: int = 0,
    audios: int = 0,
) -> MediaInspection:
    video_streams = [_video(index) for index in range(videos)]
    audio_streams = [_audio(videos + index) for index in range(audios)]
    return MediaInspection(
        _format(format_name, duration),
        videos + audios,
        video_streams,
        audio_streams,
    )


class PathInspector:
    def __init__(self, values: dict[str, MediaInspection]) -> None:
        self.values = values
        self.paths: list[Path] = []

    def inspect(self, path: Path) -> MediaInspection:
        self.paths.append(path)
        return self.values[path.name]


class SelectiveFailingInspector(PathInspector):
    def __init__(
        self,
        values: dict[str, MediaInspection],
        failing_name: str,
    ) -> None:
        super().__init__(values)
        self.failing_name = failing_name

    def inspect(self, path: Path) -> MediaInspection:
        if path.name == self.failing_name:
            raise InvalidMediaError
        return super().inspect(path)


class CapturingRunner:
    def __init__(self, error: Exception | None = None) -> None:
        self.command: list[str] | None = None
        self.error = error

    def run(self, command: list[str]) -> None:
        self.command = command
        if self.error:
            raise self.error


def _profile(
    target: CompressionProfile | None = None,
    duration: float = 5.0,
) -> ReplaceAudioProfile:
    from backend.app.processing.audio_extraction import AUDIO_EXTRACTION_PROFILES, AudioExtractionFormat

    return ReplaceAudioProfile(
        target=target or COMPRESSION_PROFILES[OutputContainer.MP4],
        external_audio=AUDIO_EXTRACTION_PROFILES[AudioExtractionFormat.WAV],
        target_duration_seconds=duration,
    )


@pytest.mark.parametrize("container", list(COMPRESSION_PROFILES))
def test_command_preserves_target_container_and_uses_central_audio_policy(
    container: OutputContainer,
    tmp_path: Path,
) -> None:
    target_profile = COMPRESSION_PROFILES[container]
    command = build_replace_audio_command(
        "ffmpeg",
        tmp_path / f"target.{target_profile.extension}",
        tmp_path / "external.wav",
        tmp_path / f"output.part.{target_profile.extension}",
        _profile(target_profile, 9.25),
    )

    assert command[command.index("-c:v") + 1] == "copy"
    assert command[command.index("-c:a") + 1] == target_profile.audio_encoder
    assert command[command.index("-f") + 1] == target_profile.muxer
    assert command[command.index("-t") + 1] == "9.25"
    assert command[command.index("-af") + 1] == "apad"
    assert [command[index + 1] for index, value in enumerate(command) if value == "-map"] == [
        "0:v:0",
        "1:a:0",
    ]
    assert command[command.index("-i") + 1].endswith(f"target.{target_profile.extension}")
    second_input = command.index("-i", command.index("-i") + 1)
    assert command[second_input + 1].endswith("external.wav")
    assert "-stream_loop" not in command and "aloop" not in command
    assert "-sn" in command and "-dn" in command


def test_loop_command_scopes_infinite_repeat_to_external_input_only(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.mp4"
    external = tmp_path / "external.wav"
    command = build_replace_audio_command(
        "ffmpeg",
        target,
        external,
        tmp_path / "output.part.mp4",
        _profile(duration=5),
        loop=True,
    )

    input_indexes = [index for index, value in enumerate(command) if value == "-i"]
    loop_index = command.index("-stream_loop")
    assert len(input_indexes) == 2
    assert command[input_indexes[0] + 1] == str(target)
    assert input_indexes[0] < loop_index < input_indexes[1]
    assert command[loop_index + 1] == "-1"
    assert command[input_indexes[1] + 1] == str(external)
    assert "apad" not in command and "-af" not in command
    assert command[command.index("-t") + 1] == "5"
    assert command[command.index("-c:v") + 1] == "copy"
    assert [command[index + 1] for index, value in enumerate(command) if value == "-map"] == [
        "0:v:0",
        "1:a:0",
    ]


def test_service_inspects_both_inputs_and_accepts_video_with_or_without_audio(
    tmp_path: Path,
) -> None:
    for target_audio_count in (0, 3):
        target, external = tmp_path / "target.mp4", tmp_path / "external.wav"
        inspector = PathInspector(
            {
                target.name: _inspection("mov,mp4,m4a,3gp,3g2,mj2", videos=1, audios=target_audio_count),
                external.name: _inspection("wav", audios=1),
            }
        )
        service = ReplaceAudioService(
            "ffmpeg",
            inspector,  # type: ignore[arg-type]
            CapturingRunner(),  # type: ignore[arg-type]
            LocalFFmpegCapabilities(frozenset({"aac"}), frozenset({"mp4"})),
        )

        profile = service.resolve_profile(target, external)

        assert inspector.paths == [target, external]
        assert profile.target is COMPRESSION_PROFILES[OutputContainer.MP4]
        assert profile.target_duration_seconds == 5


@pytest.mark.parametrize(
    ("target_name", "target_inspection", "error"),
    [
        ("target.mp3", _inspection("mp3", audios=1), ReplaceAudioTargetHasNoVideoError),
        ("target.mp4", _inspection("mov,mp4", duration=None, videos=1), InvalidReplaceAudioDurationError),
        ("target.mp4", _inspection("mov,mp4", duration=0, videos=1), InvalidReplaceAudioDurationError),
        ("target.xyz", _inspection("unknown", videos=1), UnsupportedCompressionContainerError),
    ],
)
def test_target_validation_rejects_invalid_media(
    target_name: str,
    target_inspection: MediaInspection,
    error: type[Exception],
    tmp_path: Path,
) -> None:
    target, external = tmp_path / target_name, tmp_path / "external.wav"
    service = ReplaceAudioService(
        "ffmpeg",
        PathInspector({target.name: target_inspection, external.name: _inspection("wav", audios=1)}),  # type: ignore[arg-type]
        CapturingRunner(),  # type: ignore[arg-type]
        LocalFFmpegCapabilities(frozenset({"aac"}), frozenset({"mp4"})),
    )
    with pytest.raises(error):
        service.resolve_profile(target, external)


@pytest.mark.parametrize(
    ("audio_name", "audio_inspection", "error"),
    [
        ("external.wav", _inspection("wav"), MediaHasNoAudioError),
        ("external.mp4", _inspection("mov,mp4", videos=1, audios=1), ExternalMediaHasVideoError),
        ("external.aac", _inspection("aac", audios=1), UnsupportedAudioContainerError),
    ],
)
def test_external_validation_requires_supported_audio_only_media(
    audio_name: str,
    audio_inspection: MediaInspection,
    error: type[Exception],
    tmp_path: Path,
) -> None:
    target, external = tmp_path / "target.mp4", tmp_path / audio_name
    service = ReplaceAudioService(
        "ffmpeg",
        PathInspector(
            {
                target.name: _inspection("mov,mp4", videos=1),
                external.name: audio_inspection,
            }
        ),  # type: ignore[arg-type]
        CapturingRunner(),  # type: ignore[arg-type]
        LocalFFmpegCapabilities(frozenset({"aac"}), frozenset({"mp4"})),
    )
    with pytest.raises(error):
        service.resolve_profile(target, external)


@pytest.mark.parametrize(
    ("failing_name", "error"),
    [
        ("target.mp4", TargetMediaInspectionError),
        ("external.wav", ExternalAudioInspectionError),
    ],
)
def test_probe_failures_identify_which_input_failed(
    failing_name: str,
    error: type[Exception],
    tmp_path: Path,
) -> None:
    target, external = tmp_path / "target.mp4", tmp_path / "external.wav"
    inspector = SelectiveFailingInspector(
        {
            target.name: _inspection("mov,mp4", videos=1),
            external.name: _inspection("wav", audios=1),
        },
        failing_name,
    )
    service = ReplaceAudioService(
        "ffmpeg",
        inspector,  # type: ignore[arg-type]
        CapturingRunner(),  # type: ignore[arg-type]
        LocalFFmpegCapabilities(frozenset({"aac"}), frozenset({"mp4"})),
    )
    with pytest.raises(error):
        service.resolve_profile(target, external)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (ReplaceAudioTargetHasNoVideoError(), "The target media does not contain a video stream"),
        (ExternalMediaHasVideoError(), "The external audio media must be audio-only"),
        (ExternalAudioMediaNotFoundError(), "The external audio media file was not found"),
        (TargetMediaInspectionError(), "Target media inspection failed"),
        (ExternalAudioInspectionError(), "External audio inspection failed"),
        (MediaHasNoAudioError(), "The external media does not contain an audio stream"),
        (UnsupportedAudioContainerError(), "The external audio format is not supported"),
        (UnsupportedCompressionContainerError(), "Audio replacement is not supported for the target container"),
    ],
)
def test_replace_audio_errors_are_mapped_without_diagnostics(
    error: Exception,
    message: str,
) -> None:
    assert _public_failure_message(error, JobOperation.REPLACE_AUDIO) == message


class WritingReplacer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[Path, Path, Path, object, bool]] = []

    def resolve_profile(self, target: Path, audio: Path) -> ReplaceAudioProfile:
        return _profile(duration=2)

    def replace(
        self,
        target: Path,
        audio: Path,
        output: Path,
        profile: object,
        *,
        loop: bool = False,
    ) -> None:
        self.calls.append((target, audio, output, profile, loop))
        output.write_bytes(b"replaced-output")
        if self.error:
            raise self.error


def _storage(tmp_path: Path, video_id: str, audio_id: str) -> LocalStorageService:
    storage = LocalStorageService(tmp_path / "media", tmp_path / "uploads", tmp_path / "outputs")
    storage.initialize()
    (tmp_path / "uploads" / f"{video_id}.mp4").write_bytes(b"video")
    (tmp_path / "uploads" / f"{audio_id}.wav").write_bytes(b"audio")
    return storage


def test_worker_resolves_two_uploads_and_atomically_finalizes(tmp_path: Path) -> None:
    video_id, audio_id, job_id = str(uuid4()), str(uuid4()), str(uuid4())
    storage = _storage(tmp_path, video_id, audio_id)
    replacer = WritingReplacer()

    result = execute_media_job(
        job_id=job_id,
        media_id=video_id,
        operation=JobOperation.REPLACE_AUDIO,
        storage=storage,
        converter=None,
        conversion=None,
        audio_replacer=replacer,  # type: ignore[arg-type]
        replace_audio=ReplaceAudioSpec(audio_id),
        allowed_extensions=[".mp4", ".wav"],
    )

    assert replacer.calls[0][0].name == f"{video_id}.mp4"
    assert replacer.calls[0][1].name == f"{audio_id}.wav"
    assert replacer.calls[0][2].name == f"{job_id}.part.mp4"
    assert replacer.calls[0][4] is False
    assert (tmp_path / "outputs" / f"{job_id}.mp4").read_bytes() == b"replaced-output"
    assert not (tmp_path / "outputs" / f"{job_id}.part.mp4").exists()
    assert result["format"] == "mp4"


def test_replace_audio_failure_cleans_partial_and_maps_safe_error(tmp_path: Path) -> None:
    video_id, audio_id, job_id = str(uuid4()), str(uuid4()), str(uuid4())
    storage = _storage(tmp_path, video_id, audio_id)
    error = FFmpegConversionError(r"private C:\uploads\audio.wav stderr")
    replacer = WritingReplacer(error)

    with pytest.raises(FFmpegConversionError):
        execute_media_job(
            job_id=job_id,
            media_id=video_id,
            operation=JobOperation.REPLACE_AUDIO,
            storage=storage,
            converter=None,
            conversion=None,
            audio_replacer=replacer,  # type: ignore[arg-type]
            replace_audio=ReplaceAudioSpec(audio_id, loop=True),
            allowed_extensions=[".mp4", ".wav"],
        )

    assert not (tmp_path / "outputs" / f"{job_id}.part.mp4").exists()
    assert not (tmp_path / "outputs" / f"{job_id}.mp4").exists()
    assert replacer.calls[0][4] is True
    assert _public_failure_message(error, JobOperation.REPLACE_AUDIO) == "Audio replacement failed"


def test_celery_dispatches_standalone_replace_audio_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id, video_id, audio_id = str(uuid4()), str(uuid4()), str(uuid4())
    replacer, captured = object(), {}

    class StubStorage:
        def initialize(self) -> None:
            pass

    def capture(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"output_id": job_id, "filename": f"{job_id}.mp4", "format": "mp4"}

    monkeypatch.setattr(process_media_job, "update_state", lambda **_kwargs: None)
    monkeypatch.setattr(tasks_module, "get_settings", lambda: SimpleNamespace(allowed_media_extensions=[".mp4", ".wav"]))
    monkeypatch.setattr(tasks_module, "get_storage_service", StubStorage)
    monkeypatch.setattr(tasks_module, "get_replace_audio_service", lambda: replacer)
    monkeypatch.setattr(tasks_module, "execute_media_job", capture)
    process_media_job.push_request(id=job_id)
    try:
        process_media_job.run(
            video_id,
            "replace_audio",
            {"audio_media_id": audio_id, "loop": True},
        )
    finally:
        process_media_job.pop_request()

    assert captured["audio_replacer"] is replacer
    assert captured["replace_audio"] == ReplaceAudioSpec(audio_id, loop=True)
    assert captured["muter"] is None and captured["trimmer"] is None


def _tools() -> tuple[str, str]:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg and FFprobe are required for integration tests")
    return ffmpeg, ffprobe


def _run(command: list[str], timeout: int = 60) -> None:
    subprocess.run(command, check=True, capture_output=True, timeout=timeout)


def _real_service(ffmpeg: str, ffprobe: str, encoder: str, muxer: str) -> ReplaceAudioService:
    return ReplaceAudioService(
        ffmpeg,
        SyncFFprobeInspector(ffprobe, 30),
        FFmpegRunner(90),
        LocalFFmpegCapabilities(frozenset({encoder}), frozenset({muxer})),
    )


def _make_video(
    ffmpeg: str,
    path: Path,
    duration: float,
    video_encoder: str,
    muxer: str,
    audio_encoder: str | None = None,
    frequencies: tuple[int, ...] = (),
) -> None:
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=64x64:rate=25:duration={duration}",
    ]
    for frequency in frequencies:
        command.extend(["-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration}"])
    command.extend(["-map", "0:v:0", "-c:v", video_encoder])
    for index in range(len(frequencies)):
        command.extend(["-map", f"{index + 1}:a:0"])
    if frequencies and audio_encoder:
        command.extend(["-c:a", audio_encoder])
    command.extend(["-f", muxer, str(path)])
    _run(command)


def _make_audio(ffmpeg: str, path: Path, duration: float, frequency: int = 880) -> None:
    _run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
        f"sine=frequency={frequency}:duration={duration}", str(path),
    ])


def _probe_json(ffprobe: str, path: Path) -> dict[str, object]:
    return json.loads(subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, timeout=30,
    ).stdout)


def _pcm(ffmpeg: str, path: Path, start: float, duration: float) -> list[int]:
    output = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(start), "-t", str(duration),
         "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"],
        check=True, capture_output=True, timeout=30,
    ).stdout
    return list(struct.unpack(f"<{len(output) // 2}h", output))


def _rms(samples: list[int]) -> float:
    return math.sqrt(sum(sample * sample for sample in samples) / max(len(samples), 1))


def _tone_power(samples: list[int], frequency: int, sample_rate: int = 8000) -> float:
    sine = sum(sample * math.sin(2 * math.pi * frequency * index / sample_rate) for index, sample in enumerate(samples))
    cosine = sum(sample * math.cos(2 * math.pi * frequency * index / sample_rate) for index, sample in enumerate(samples))
    return sine * sine + cosine * cosine


@pytest.mark.parametrize(
    ("extension", "video_encoder", "source_audio_encoder", "output_audio_encoder", "muxer"),
    [
        ("mp4", "libx264", "aac", "aac", "mp4"),
        ("mkv", "libx264", "aac", "aac", "matroska"),
        ("webm", "libvpx-vp9", "libopus", "libopus", "webm"),
    ],
)
def test_real_replace_removes_all_original_audio_and_stream_copies_video(
    extension: str,
    video_encoder: str,
    source_audio_encoder: str,
    output_audio_encoder: str,
    muxer: str,
    tmp_path: Path,
) -> None:
    ffmpeg, ffprobe = _tools()
    target, external, output = tmp_path / f"target.{extension}", tmp_path / "external.wav", tmp_path / f"output.part.{extension}"
    _make_video(ffmpeg, target, 3, video_encoder, muxer, source_audio_encoder, (440, 660))
    _make_audio(ffmpeg, external, 1, 880)
    service = _real_service(ffmpeg, ffprobe, output_audio_encoder, muxer)
    service.replace(
        target,
        external,
        output,
        service.resolve_profile(target, external),
        loop=True,
    )

    source_info, output_info = _probe_json(ffprobe, target), _probe_json(ffprobe, output)
    source_video = next(stream for stream in source_info["streams"] if stream["codec_type"] == "video")  # type: ignore[index]
    output_streams = output_info["streams"]  # type: ignore[index]
    output_video = next(stream for stream in output_streams if stream["codec_type"] == "video")
    assert source_video["codec_name"] == output_video["codec_name"]
    assert sum(stream["codec_type"] == "audio" for stream in output_streams) == 1
    samples = _pcm(ffmpeg, output, 0.1, 0.7)
    repeated_samples = _pcm(ffmpeg, output, 2.1, 0.7)
    assert _tone_power(samples, 880) > _tone_power(samples, 440) * 20
    assert _tone_power(repeated_samples, 880) > _tone_power(repeated_samples, 440) * 20
    assert float(output_info["format"]["duration"]) == pytest.approx(3, abs=0.2)  # type: ignore[index]


@pytest.mark.parametrize("extension", ["mp3", "wav", "flac"])
def test_real_video_only_target_accepts_supported_external_audio_formats(
    extension: str,
    tmp_path: Path,
) -> None:
    ffmpeg, ffprobe = _tools()
    target, external, output = tmp_path / "silent.mp4", tmp_path / f"external.{extension}", tmp_path / "output.part.mp4"
    _make_video(ffmpeg, target, 2, "libx264", "mp4")
    _make_audio(ffmpeg, external, 0.75)
    service = _real_service(ffmpeg, ffprobe, "aac", "mp4")
    service.replace(
        target,
        external,
        output,
        service.resolve_profile(target, external),
        loop=True,
    )
    inspection = SyncFFprobeInspector(ffprobe, 30).inspect(output)
    assert len(inspection.video_streams) == 1 and len(inspection.audio_streams) == 1
    assert inspection.audio_streams[0].codec_name == "aac"
    assert _rms(_pcm(ffmpeg, output, 1.25, 0.5)) > 1000


@pytest.mark.parametrize(("video_duration", "audio_duration"), [(3, 3), (3, 5)])
def test_real_output_duration_is_video_master_for_equal_or_long_audio(
    video_duration: int,
    audio_duration: int,
    tmp_path: Path,
) -> None:
    ffmpeg, ffprobe = _tools()
    target, external, output = tmp_path / "target.mp4", tmp_path / "external.wav", tmp_path / "output.part.mp4"
    _make_video(ffmpeg, target, video_duration, "libx264", "mp4")
    _make_audio(ffmpeg, external, audio_duration)
    service = _real_service(ffmpeg, ffprobe, "aac", "mp4")
    service.replace(target, external, output, service.resolve_profile(target, external))
    assert SyncFFprobeInspector(ffprobe, 30).inspect(output).format.duration_seconds == pytest.approx(video_duration, abs=0.2)


def test_real_short_audio_is_padded_with_silence_and_never_looped(tmp_path: Path) -> None:
    ffmpeg, ffprobe = _tools()
    target, external, output = tmp_path / "target.mp4", tmp_path / "external.wav", tmp_path / "output.part.mp4"
    _make_video(ffmpeg, target, 5, "libx264", "mp4")
    _make_audio(ffmpeg, external, 2, 880)
    service = _real_service(ffmpeg, ffprobe, "aac", "mp4")
    service.replace(
        target,
        external,
        output,
        service.resolve_profile(target, external),
        loop=False,
    )

    inspection = SyncFFprobeInspector(ffprobe, 30).inspect(output)
    probe = _probe_json(ffprobe, output)
    audio_stream = next(stream for stream in probe["streams"] if stream["codec_type"] == "audio")  # type: ignore[index]
    head = _pcm(ffmpeg, output, 0.5, 0.75)
    middle = _pcm(ffmpeg, output, 2.5, 0.75)
    tail = _pcm(ffmpeg, output, 4.25, 0.5)
    assert inspection.format.duration_seconds == pytest.approx(5, abs=0.2)
    assert float(audio_stream["duration"]) == pytest.approx(5, abs=0.2)
    assert _rms(head) > 1000
    assert _rms(middle) < _rms(head) / 50
    assert _rms(tail) < _rms(head) / 50


@pytest.mark.parametrize(
    ("video_duration", "audio_duration", "sample_starts"),
    [
        (6, 2, (0.5, 2.5, 4.5)),
        (5, 2, (0.5, 2.5, 4.25)),
    ],
)
def test_real_loop_repeats_exactly_and_supports_partial_final_iteration(
    video_duration: int,
    audio_duration: int,
    sample_starts: tuple[float, ...],
    tmp_path: Path,
) -> None:
    ffmpeg, ffprobe = _tools()
    target = tmp_path / "target.mp4"
    external = tmp_path / "external.wav"
    output = tmp_path / "output.part.mp4"
    _make_video(ffmpeg, target, video_duration, "libx264", "mp4")
    _make_audio(ffmpeg, external, audio_duration, 880)
    service = _real_service(ffmpeg, ffprobe, "aac", "mp4")
    service.replace(
        target,
        external,
        output,
        service.resolve_profile(target, external),
        loop=True,
    )

    inspection = SyncFFprobeInspector(ffprobe, 30).inspect(output)
    assert inspection.format.duration_seconds == pytest.approx(video_duration, abs=0.2)
    for start in sample_starts:
        samples = _pcm(ffmpeg, output, start, 0.5)
        assert _rms(samples) > 1000
        assert _tone_power(samples, 880) > _tone_power(samples, 440) * 20


def test_real_loop_with_long_audio_still_stops_at_video_duration(tmp_path: Path) -> None:
    ffmpeg, ffprobe = _tools()
    target = tmp_path / "target.mp4"
    external = tmp_path / "external.wav"
    output = tmp_path / "output.part.mp4"
    _make_video(ffmpeg, target, 4, "libx264", "mp4")
    _make_audio(ffmpeg, external, 8, 880)
    service = _real_service(ffmpeg, ffprobe, "aac", "mp4")
    service.replace(
        target,
        external,
        output,
        service.resolve_profile(target, external),
        loop=True,
    )

    assert SyncFFprobeInspector(ffprobe, 30).inspect(
        output
    ).format.duration_seconds == pytest.approx(4, abs=0.2)
