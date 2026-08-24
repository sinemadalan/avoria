from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from celery import Celery, states

from backend.app.application.ports.jobs import JobOperation
from backend.app.infrastructure.storage import LocalStorageService
from backend.app.processing.audio_extraction import (
    AUDIO_EXTRACTION_PROFILES,
    AudioExtractionFormat,
    AudioExtractionProfile,
    AudioExtractionService,
    AudioExtractionSpec,
    MediaHasNoAudioError,
    build_extract_audio_command,
)
from backend.app.processing.conversion import FFmpegConversionError
from backend.app.processing.probe import MediaInspection, parse_ffprobe_payload
from backend.app.workers import tasks as tasks_module
from backend.app.workers.tasks import MediaTask, execute_media_job, process_media_job


class CapturingRunner:
    def __init__(self) -> None:
        self.command: list[str] | None = None

    def run(self, command: list[str]) -> None:
        self.command = command


class StubInspector:
    def __init__(self, inspection: MediaInspection) -> None:
        self.inspection = inspection

    def inspect(self, _path: Path) -> MediaInspection:
        return self.inspection


AUDIO_INPUT = parse_ffprobe_payload(
    {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264"},
            {"index": 1, "codec_type": "audio", "codec_name": "aac"},
        ],
    }
)
VIDEO_ONLY_INPUT = parse_ffprobe_payload(
    {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264"},
        ],
    }
)


class WritingExtractor:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[Path, Path, AudioExtractionFormat]] = []

    def resolve_profile(self, spec: AudioExtractionSpec) -> AudioExtractionProfile:
        return AUDIO_EXTRACTION_PROFILES[spec.format]

    def extract(
        self,
        input_path: Path,
        output_path: Path,
        spec: AudioExtractionSpec,
        _profile: AudioExtractionProfile,
    ) -> None:
        self.calls.append((input_path, output_path, spec.format))
        output_path.write_bytes(b"audio-output")
        if self.error is not None:
            raise self.error


def make_storage(tmp_path: Path, media_id: str) -> LocalStorageService:
    upload_directory = tmp_path / "uploads"
    storage = LocalStorageService(
        tmp_path / "media",
        upload_directory,
        tmp_path / "outputs",
    )
    storage.initialize()
    (upload_directory / f"{media_id}.mp4").write_bytes(b"video-input")
    return storage


def test_extract_audio_is_a_valid_job_operation() -> None:
    assert JobOperation("extract_audio") is JobOperation.EXTRACT_AUDIO


def test_extract_audio_command_has_fixed_mp3_policy(tmp_path: Path) -> None:
    output_path = tmp_path / "output.part.mp3"

    command = build_extract_audio_command(
        "configured-ffmpeg",
        tmp_path / "input.mp4",
        output_path,
    )

    assert command[0] == "configured-ffmpeg"
    assert command[command.index("-map") + 1] == "0:a:0"
    assert command[command.index("-c:a") + 1] == "libmp3lame"
    assert command[command.index("-q:a") + 1] == "2"
    assert "-vn" in command
    assert command[-1].endswith(".mp3")


def test_extract_audio_command_has_wav_policy(tmp_path: Path) -> None:
    command = build_extract_audio_command(
        "configured-ffmpeg",
        tmp_path / "input.mp4",
        tmp_path / "output.part.wav",
        AudioExtractionSpec(AudioExtractionFormat.WAV),
    )

    assert "-vn" in command
    assert command[command.index("-map") + 1] == "0:a:0"
    assert command[command.index("-c:a") + 1] == "pcm_s16le"
    assert command[command.index("-f") + 1] == "wav"
    assert "libmp3lame" not in command
    assert "-q:a" not in command
    assert command[-1].endswith(".wav")


def test_extract_audio_command_has_flac_policy(tmp_path: Path) -> None:
    command = build_extract_audio_command(
        "configured-ffmpeg",
        tmp_path / "input.mp4",
        tmp_path / "output.part.flac",
        AudioExtractionSpec(AudioExtractionFormat.FLAC),
    )

    assert "-vn" in command
    assert command[command.index("-map") + 1] == "0:a:0"
    assert command[command.index("-c:a") + 1] == "flac"
    assert command[command.index("-f") + 1] == "flac"
    assert "-sn" in command
    assert "-dn" in command
    assert "libmp3lame" not in command
    assert "-q:a" not in command
    assert "pcm_s16le" not in command
    assert command[-1].endswith(".flac")


def test_m4a_profile_has_fixed_aac_policy() -> None:
    profile = AUDIO_EXTRACTION_PROFILES[AudioExtractionFormat.M4A]

    assert profile.extension == "m4a"
    assert profile.encoder == "aac"
    assert profile.muxer == "ipod"
    assert profile.encoder_options == ("-b:a", "192k")


def test_extract_audio_command_has_m4a_aac_policy(tmp_path: Path) -> None:
    command = build_extract_audio_command(
        "configured-ffmpeg",
        tmp_path / "input.mov",
        tmp_path / "output.part.m4a",
        AudioExtractionSpec(AudioExtractionFormat.M4A),
    )

    assert "-vn" in command
    assert command[command.index("-map") + 1] == "0:a:0"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-b:a") + 1] == "192k"
    assert command[command.index("-f") + 1] == "ipod"
    assert "libmp3lame" not in command
    assert "-q:a" not in command
    assert "pcm_s16le" not in command
    assert "flac" not in command
    assert not command[-1].endswith(".aac")
    assert command[-1].endswith(".m4a")


def test_opus_profile_has_fixed_ogg_policy() -> None:
    profile = AUDIO_EXTRACTION_PROFILES[AudioExtractionFormat.OPUS]

    assert profile.extension == "opus"
    assert profile.encoder == "libopus"
    assert profile.muxer == "ogg"
    assert profile.encoder_options == ("-b:a", "128k")


def test_extract_audio_command_has_opus_ogg_policy(tmp_path: Path) -> None:
    command = build_extract_audio_command(
        "configured-ffmpeg",
        tmp_path / "input.mkv",
        tmp_path / "output.part.opus",
        AudioExtractionSpec(AudioExtractionFormat.OPUS),
    )

    assert "-vn" in command
    assert command[command.index("-map") + 1] == "0:a:0"
    assert command[command.index("-c:a") + 1] == "libopus"
    assert command[command.index("-b:a") + 1] == "128k"
    assert command[command.index("-f") + 1] == "ogg"
    assert "libmp3lame" not in command
    assert "-q:a" not in command
    assert "pcm_s16le" not in command
    assert "flac" not in command
    assert "aac" not in command
    assert "ipod" not in command
    assert not command[-1].endswith(".ogg")
    assert command[-1].endswith(".opus")


def test_ogg_profile_has_fixed_vorbis_policy() -> None:
    profile = AUDIO_EXTRACTION_PROFILES[AudioExtractionFormat.OGG]

    assert profile.extension == "ogg"
    assert profile.encoder == "libvorbis"
    assert profile.muxer == "ogg"
    assert profile.encoder_options == ("-q:a", "5")


def test_extract_audio_command_has_ogg_vorbis_policy(tmp_path: Path) -> None:
    command = build_extract_audio_command(
        "configured-ffmpeg",
        tmp_path / "input.webm",
        tmp_path / "output.part.ogg",
        AudioExtractionSpec(AudioExtractionFormat.OGG),
    )

    assert "-vn" in command
    assert command[command.index("-map") + 1] == "0:a:0"
    assert command[command.index("-c:a") + 1] == "libvorbis"
    assert command[command.index("-q:a") + 1] == "5"
    assert command[command.index("-f") + 1] == "ogg"
    assert "libmp3lame" not in command
    assert "pcm_s16le" not in command
    assert "flac" not in command
    assert "aac" not in command
    assert "ipod" not in command
    assert "libopus" not in command
    assert not command[-1].endswith(".opus")
    assert command[-1].endswith(".ogg")


def test_opus_and_ogg_profiles_keep_distinct_codecs_and_extensions() -> None:
    opus = AUDIO_EXTRACTION_PROFILES[AudioExtractionFormat.OPUS]
    ogg = AUDIO_EXTRACTION_PROFILES[AudioExtractionFormat.OGG]

    assert (opus.extension, opus.encoder, opus.muxer) == (
        "opus",
        "libopus",
        "ogg",
    )
    assert (ogg.extension, ogg.encoder, ogg.muxer) == (
        "ogg",
        "libvorbis",
        "ogg",
    )


def test_audio_extraction_service_uses_shared_runner(tmp_path: Path) -> None:
    runner = CapturingRunner()
    service = AudioExtractionService(
        "ffmpeg",
        StubInspector(AUDIO_INPUT),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
    )

    service.extract(
        tmp_path / "input.mp4",
        tmp_path / "output.part.mp3",
        AudioExtractionSpec(),
    )

    assert runner.command is not None
    assert "libmp3lame" in runner.command


def test_worker_dispatches_extract_audio_and_completes_mp3(tmp_path: Path) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage = make_storage(tmp_path, media_id)
    extractor = WritingExtractor()

    result = execute_media_job(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.EXTRACT_AUDIO,
        storage=storage,
        converter=None,
        conversion=None,
        audio_extractor=extractor,  # type: ignore[arg-type]
        extraction=AudioExtractionSpec(),
        allowed_extensions=[".mp4"],
    )

    output_path = tmp_path / "outputs" / f"{job_id}.mp3"
    assert len(extractor.calls) == 1
    assert extractor.calls[0][1].name == f"{job_id}.part.mp3"
    assert extractor.calls[0][2] is AudioExtractionFormat.MP3
    assert output_path.read_bytes() == b"audio-output"
    assert result == {
        "output_id": job_id,
        "filename": f"{job_id}.mp3",
        "format": "mp3",
    }


def test_worker_dispatches_wav_and_completes_dynamic_output(tmp_path: Path) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage = make_storage(tmp_path, media_id)
    extractor = WritingExtractor()

    result = execute_media_job(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.EXTRACT_AUDIO,
        storage=storage,
        converter=None,
        conversion=None,
        audio_extractor=extractor,  # type: ignore[arg-type]
        extraction=AudioExtractionSpec(AudioExtractionFormat.WAV),
        allowed_extensions=[".mp4"],
    )

    output_path = tmp_path / "outputs" / f"{job_id}.wav"
    assert extractor.calls[0][1].name == f"{job_id}.part.wav"
    assert extractor.calls[0][2] is AudioExtractionFormat.WAV
    assert output_path.read_bytes() == b"audio-output"
    assert result["filename"] == f"{job_id}.wav"
    assert result["format"] == "wav"


def test_worker_dispatches_flac_and_completes_dynamic_output(tmp_path: Path) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage = make_storage(tmp_path, media_id)
    extractor = WritingExtractor()

    result = execute_media_job(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.EXTRACT_AUDIO,
        storage=storage,
        converter=None,
        conversion=None,
        audio_extractor=extractor,  # type: ignore[arg-type]
        extraction=AudioExtractionSpec(AudioExtractionFormat.FLAC),
        allowed_extensions=[".mp4"],
    )

    output_path = tmp_path / "outputs" / f"{job_id}.flac"
    assert extractor.calls[0][1].name == f"{job_id}.part.flac"
    assert extractor.calls[0][2] is AudioExtractionFormat.FLAC
    assert output_path.read_bytes() == b"audio-output"
    assert not (tmp_path / "outputs" / f"{job_id}.part.flac").exists()
    assert result["filename"] == f"{job_id}.flac"
    assert result["format"] == "flac"


def test_worker_dispatches_m4a_and_completes_dynamic_output(tmp_path: Path) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage = make_storage(tmp_path, media_id)
    extractor = WritingExtractor()

    result = execute_media_job(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.EXTRACT_AUDIO,
        storage=storage,
        converter=None,
        conversion=None,
        audio_extractor=extractor,  # type: ignore[arg-type]
        extraction=AudioExtractionSpec(AudioExtractionFormat.M4A),
        allowed_extensions=[".mp4"],
    )

    output_path = tmp_path / "outputs" / f"{job_id}.m4a"
    assert extractor.calls[0][1].name == f"{job_id}.part.m4a"
    assert extractor.calls[0][2] is AudioExtractionFormat.M4A
    assert output_path.read_bytes() == b"audio-output"
    assert not (tmp_path / "outputs" / f"{job_id}.part.m4a").exists()
    assert result["filename"] == f"{job_id}.m4a"
    assert result["format"] == "m4a"


def test_worker_dispatches_opus_and_completes_dynamic_output(tmp_path: Path) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage = make_storage(tmp_path, media_id)
    extractor = WritingExtractor()

    result = execute_media_job(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.EXTRACT_AUDIO,
        storage=storage,
        converter=None,
        conversion=None,
        audio_extractor=extractor,  # type: ignore[arg-type]
        extraction=AudioExtractionSpec(AudioExtractionFormat.OPUS),
        allowed_extensions=[".mp4"],
    )

    output_path = tmp_path / "outputs" / f"{job_id}.opus"
    assert extractor.calls[0][1].name == f"{job_id}.part.opus"
    assert extractor.calls[0][2] is AudioExtractionFormat.OPUS
    assert output_path.read_bytes() == b"audio-output"
    assert not (tmp_path / "outputs" / f"{job_id}.part.opus").exists()
    assert result["filename"] == f"{job_id}.opus"
    assert result["format"] == "opus"


def test_worker_dispatches_ogg_and_completes_dynamic_output(tmp_path: Path) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage = make_storage(tmp_path, media_id)
    extractor = WritingExtractor()

    result = execute_media_job(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.EXTRACT_AUDIO,
        storage=storage,
        converter=None,
        conversion=None,
        audio_extractor=extractor,  # type: ignore[arg-type]
        extraction=AudioExtractionSpec(AudioExtractionFormat.OGG),
        allowed_extensions=[".mp4"],
    )

    output_path = tmp_path / "outputs" / f"{job_id}.ogg"
    assert extractor.calls[0][1].name == f"{job_id}.part.ogg"
    assert extractor.calls[0][2] is AudioExtractionFormat.OGG
    assert output_path.read_bytes() == b"audio-output"
    assert not (tmp_path / "outputs" / f"{job_id}.part.ogg").exists()
    assert result["filename"] == f"{job_id}.ogg"
    assert result["format"] == "ogg"


@pytest.mark.parametrize(
    "output_format",
    [
        AudioExtractionFormat.WAV,
        AudioExtractionFormat.FLAC,
        AudioExtractionFormat.M4A,
        AudioExtractionFormat.OPUS,
        AudioExtractionFormat.OGG,
    ],
)
def test_celery_worker_dispatches_audio_profile(
    output_format: AudioExtractionFormat,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = str(uuid4())
    media_id = str(uuid4())
    extractor = object()
    captured: dict[str, object] = {}

    class StubStorage:
        def initialize(self) -> None:
            return None

    def capture_execute(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "output_id": job_id,
            "filename": f"{job_id}.{output_format.value}",
            "format": output_format.value,
        }

    monkeypatch.setattr(
        process_media_job,
        "update_state",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        tasks_module,
        "get_settings",
        lambda: SimpleNamespace(allowed_media_extensions=[".mp4"]),
    )
    monkeypatch.setattr(tasks_module, "get_storage_service", StubStorage)
    monkeypatch.setattr(tasks_module, "get_audio_extraction_service", lambda: extractor)
    monkeypatch.setattr(tasks_module, "execute_media_job", capture_execute)

    process_media_job.push_request(id=job_id)
    try:
        result = process_media_job.run(
            media_id,
            "extract_audio",
            {"format": output_format.value},
        )
    finally:
        process_media_job.pop_request()

    assert captured["audio_extractor"] is extractor
    assert captured["extraction"] == AudioExtractionSpec(output_format)
    assert captured["converter"] is None
    assert captured["compressor"] is None
    assert result["format"] == output_format.value
    assert result["progress"] == 100


@pytest.mark.parametrize("output_format", list(AudioExtractionFormat))
def test_ffmpeg_failure_cleans_partial_audio(
    output_format: AudioExtractionFormat,
    tmp_path: Path,
) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage = make_storage(tmp_path, media_id)
    extractor = WritingExtractor(FFmpegConversionError("private diagnostic"))

    with pytest.raises(FFmpegConversionError):
        execute_media_job(
            job_id=job_id,
            media_id=media_id,
            operation=JobOperation.EXTRACT_AUDIO,
            storage=storage,
            converter=None,
            conversion=None,
            audio_extractor=extractor,  # type: ignore[arg-type]
            extraction=AudioExtractionSpec(output_format),
            allowed_extensions=[".mp4"],
        )

    assert not (
        tmp_path / "outputs" / f"{job_id}.part.{output_format.value}"
    ).exists()
    assert not (tmp_path / "outputs" / f"{job_id}.{output_format.value}").exists()


@pytest.mark.parametrize("output_format", list(AudioExtractionFormat))
def test_no_audio_validation_skips_ffmpeg_and_output(
    output_format: AudioExtractionFormat,
    tmp_path: Path,
) -> None:
    media_id = str(uuid4())
    job_id = str(uuid4())
    storage = make_storage(tmp_path, media_id)
    runner = CapturingRunner()
    service = AudioExtractionService(
        "ffmpeg",
        StubInspector(VIDEO_ONLY_INPUT),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
    )

    with pytest.raises(MediaHasNoAudioError, match="audio stream"):
        execute_media_job(
            job_id=job_id,
            media_id=media_id,
            operation=JobOperation.EXTRACT_AUDIO,
            storage=storage,
            converter=None,
            conversion=None,
            audio_extractor=service,
            extraction=AudioExtractionSpec(output_format),
            allowed_extensions=[".mp4"],
        )

    assert runner.command is None
    assert not (
        tmp_path / "outputs" / f"{job_id}.part.{output_format.value}"
    ).exists()
    assert not (tmp_path / "outputs" / f"{job_id}.{output_format.value}").exists()


def test_failure_hook_preserves_celery_exception_result() -> None:
    app = Celery("audio-extraction-test", broker="memory://", backend="cache+memory://")
    app.conf.update(
        task_always_eager=True,
        task_store_eager_result=True,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
    )

    @app.task(bind=True, base=MediaTask, name=f"test.audio-extraction.{uuid4()}")
    def fail_audio_extraction(
        _task: MediaTask,
        _media_id: str,
        _operation: str,
        _parameters: dict[str, str],
    ) -> None:
        raise MediaHasNoAudioError("The input does not contain an audio stream")

    job_id = str(uuid4())
    media_id = str(uuid4())

    fail_audio_extraction.apply(
        args=(media_id, "extract_audio", {}),
        task_id=job_id,
        throw=False,
    )
    stored_result = app.AsyncResult(job_id)

    assert stored_result.state == states.FAILURE
    assert isinstance(stored_result.info, MediaHasNoAudioError)
