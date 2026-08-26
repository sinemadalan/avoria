import asyncio
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from celery import states
from fastapi.testclient import TestClient
from kombu.exceptions import OperationalError

from backend.app.application.ports.jobs import (
    JobNotFoundError,
    JobOperation,
    JobQueueUnavailableError,
    JobRecord,
    JobState,
)
from backend.app.core.config import Settings, get_settings
from backend.app.infrastructure import queue as queue_module
from backend.app.infrastructure.queue import (
    CeleryJobQueue,
    get_job_queue,
    normalize_job_status,
)
from backend.app.infrastructure.storage import LocalStorageService, get_storage_service
from backend.app.main import app
from backend.app.processing.audio_extraction import MediaHasNoAudioError


class FakeJobQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, str, JobOperation, dict[str, str]]] = []
        self.records: dict[str, JobRecord] = {}
        self.enqueue_error: Exception | None = None

    async def enqueue(
        self,
        job_id: str,
        media_id: str,
        operation: JobOperation,
        parameters: dict[str, str],
    ) -> None:
        if self.enqueue_error is not None:
            raise self.enqueue_error
        self.enqueued.append((job_id, media_id, operation, parameters))

    async def get(self, job_id: str) -> JobRecord:
        try:
            return self.records[job_id]
        except KeyError as exc:
            raise JobNotFoundError from exc


@pytest.fixture
def jobs_client(
    tmp_path: Path,
) -> Iterator[tuple[TestClient, Path, Path, FakeJobQueue]]:
    upload_directory = tmp_path / "uploads"
    output_directory = tmp_path / "outputs"
    storage = LocalStorageService(tmp_path / "media", upload_directory, output_directory)
    storage.initialize()
    settings = Settings(
        _env_file=None,
        storage_root=tmp_path / "media",
        upload_directory=upload_directory,
        output_directory=output_directory,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        log_directory=tmp_path / "logs",
    )
    queue = FakeJobQueue()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_storage_service] = lambda: storage
    app.dependency_overrides[get_job_queue] = lambda: queue

    try:
        with TestClient(app) as client:
            yield client, upload_directory, output_directory, queue
    finally:
        app.dependency_overrides.clear()


def test_create_job_enqueues_transcode_with_generated_job_id(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mov").write_bytes(b"media")

    response = client.post(
        "/api/v1/jobs",
        json={"media_id": media_id, "operation": "transcode"},
    )

    assert response.status_code == 202
    body = response.json()
    assert str(UUID(body["job_id"])) == body["job_id"]
    assert body["job_id"] != media_id
    assert body == {
        "job_id": body["job_id"],
        "media_id": media_id,
        "operation": "transcode",
        "status": "queued",
    }
    assert queue.enqueued == [
        (
            body["job_id"],
            media_id,
            JobOperation.TRANSCODE,
            {"container": "mp4", "video_codec": "h264", "audio_codec": "aac"},
        )
    ]


def test_create_convert_job_supports_defaults_and_typed_parameters(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"media")

    default_response = client.post(
        "/api/v1/jobs",
        json={"media_id": media_id, "operation": "convert"},
    )
    webm_response = client.post(
        "/api/v1/jobs",
        json={
            "media_id": media_id,
            "operation": "convert",
            "parameters": {
                "container": "webm",
                "video_codec": "vp9",
                "audio_codec": "opus",
            },
        },
    )

    assert default_response.status_code == 202
    assert default_response.json()["operation"] == "convert"
    assert queue.enqueued[0][3] == {
        "container": "mp4",
        "video_codec": "h264",
        "audio_codec": "aac",
    }
    assert webm_response.status_code == 202
    assert queue.enqueued[1][3] == {
        "container": "webm",
        "video_codec": "vp9",
        "audio_codec": "opus",
    }


@pytest.mark.parametrize("level", ["light", "balanced", "strong"])
def test_create_compress_job_supports_valid_levels(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
    level: str,
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"media")

    response = client.post(
        "/api/v1/jobs",
        json={
            "media_id": media_id,
            "operation": "compress",
            "parameters": {"compression_level": level},
        },
    )

    assert response.status_code == 202
    assert queue.enqueued[-1][2] is JobOperation.COMPRESS
    assert queue.enqueued[-1][3] == {"compression_level": level}


def test_create_compress_job_defaults_to_balanced_and_rejects_invalid_level(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"media")

    default_response = client.post(
        "/api/v1/jobs",
        json={"media_id": media_id, "operation": "compress"},
    )
    invalid_response = client.post(
        "/api/v1/jobs",
        json={
            "media_id": media_id,
            "operation": "compress",
            "parameters": {"compression_level": "extreme"},
        },
    )

    assert default_response.status_code == 202
    assert queue.enqueued[-1][3] == {"compression_level": "balanced"}
    assert invalid_response.status_code == 422
    assert len(queue.enqueued) == 1


def test_create_extract_audio_job_accepts_no_parameters(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"media")

    response = client.post(
        "/api/v1/jobs",
        json={"media_id": media_id, "operation": "extract_audio"},
    )

    assert response.status_code == 202
    assert response.json()["operation"] == "extract_audio"
    assert queue.enqueued == [
        (
            response.json()["job_id"],
            media_id,
            JobOperation.EXTRACT_AUDIO,
            {"format": "mp3"},
        )
    ]


def test_create_mute_job_accepts_no_parameters(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"media")

    response = client.post(
        "/api/v1/jobs",
        json={"media_id": media_id, "operation": "mute"},
    )

    assert response.status_code == 202
    assert response.json()["operation"] == "mute"
    assert queue.enqueued[-1][2] is JobOperation.MUTE
    assert queue.enqueued[-1][3] == {}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("format", "mp4"),
        ("parameters", {"codec": "copy"}),
        ("parameters", {"bitrate": "1M"}),
        ("parameters", {"video_codec": "h264"}),
    ],
)
def test_create_mute_job_rejects_processing_options_before_queue(
    field: str,
    value: object,
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"media")

    response = client.post(
        "/api/v1/jobs",
        json={"media_id": media_id, "operation": "mute", field: value},
    )

    assert response.status_code == 422
    assert queue.enqueued == []


@pytest.mark.parametrize("volume_percent", [0, 50, 100, 150, 200])
def test_create_volume_job_accepts_supported_percentages(
    volume_percent: int,
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"media")

    response = client.post(
        "/api/v1/jobs",
        json={
            "media_id": media_id,
            "operation": "volume",
            "parameters": {"volume_percent": volume_percent},
        },
    )

    assert response.status_code == 202
    assert response.json()["operation"] == "volume"
    assert queue.enqueued[-1][2] is JobOperation.VOLUME
    assert queue.enqueued[-1][3] == {"volume_percent": volume_percent}


@pytest.mark.parametrize("volume_percent", [-1, 201, "50", None, 50.0, True])
def test_create_volume_job_rejects_invalid_percentage(
    volume_percent: object,
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"media")

    response = client.post(
        "/api/v1/jobs",
        json={
            "media_id": media_id,
            "operation": "volume",
            "parameters": {"volume_percent": volume_percent},
        },
    )

    assert response.status_code == 422
    assert queue.enqueued == []


@pytest.mark.parametrize(
    "parameters",
    [{}, {"volume_percent": 50, "codec": "aac"}],
)
def test_create_volume_job_rejects_missing_or_extra_parameters(
    parameters: dict[str, object],
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"media")

    response = client.post(
        "/api/v1/jobs",
        json={"media_id": media_id, "operation": "volume", "parameters": parameters},
    )

    assert response.status_code == 422
    assert queue.enqueued == []


@pytest.mark.parametrize(
    ("start", "end"),
    [(0, 10), (1.5, 10.25)],
)
def test_create_trim_job_accepts_integer_and_decimal_timestamps(
    start: int | float,
    end: int | float,
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"media")

    response = client.post(
        "/api/v1/jobs",
        json={
            "media_id": media_id,
            "operation": "trim",
            "parameters": {"start_seconds": start, "end_seconds": end},
        },
    )

    assert response.status_code == 202
    assert queue.enqueued[-1][2] is JobOperation.TRIM
    assert queue.enqueued[-1][3] == {
        "start_seconds": float(start),
        "end_seconds": float(end),
    }


@pytest.mark.parametrize(
    "parameters",
    [
        {"start_seconds": -1, "end_seconds": 10},
        {"start_seconds": 0, "end_seconds": 0},
        {"start_seconds": 5, "end_seconds": 5},
        {"start_seconds": 6, "end_seconds": 5},
        {"start_seconds": True, "end_seconds": 5},
        {"start_seconds": 0, "end_seconds": False},
        {"start_seconds": "1", "end_seconds": 5},
        {"start_seconds": 0, "end_seconds": "5"},
        {"start_seconds": None, "end_seconds": 5},
        {"start_seconds": 0, "end_seconds": None},
        {"end_seconds": 5},
        {"start_seconds": 0},
        {"start_seconds": 0, "end_seconds": 5, "codec": "copy"},
    ],
)
def test_create_trim_job_rejects_invalid_parameters_before_queue(
    parameters: dict[str, object],
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"media")

    response = client.post(
        "/api/v1/jobs",
        json={"media_id": media_id, "operation": "trim", "parameters": parameters},
    )

    assert response.status_code == 422
    assert queue.enqueued == []


@pytest.mark.parametrize("speed", [0.25, 0.5, 1, 1.5, 2, 4])
def test_create_speed_job_accepts_supported_factors_on_original_upload(
    speed: int | float,
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"original-upload")

    response = client.post(
        "/api/v1/jobs",
        json={
            "media_id": media_id,
            "operation": "speed",
            "parameters": {"speed": speed},
        },
    )

    assert response.status_code == 202
    assert response.json()["operation"] == "speed"
    assert queue.enqueued[-1][1] == media_id
    assert queue.enqueued[-1][2] is JobOperation.SPEED
    assert queue.enqueued[-1][3] == {"speed": float(speed)}


@pytest.mark.parametrize(
    "parameters",
    [
        {"speed": 0},
        {"speed": -1},
        {"speed": 0.24},
        {"speed": 4.01},
        {"speed": "1.5"},
        {"speed": None},
        {"speed": True},
        {"speed": False},
        {},
        {"speed": 1.5, "codec": "copy"},
    ],
)
def test_create_speed_job_rejects_invalid_parameters_before_queue(
    parameters: dict[str, object],
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"original-upload")

    response = client.post(
        "/api/v1/jobs",
        json={"media_id": media_id, "operation": "speed", "parameters": parameters},
    )

    assert response.status_code == 422
    assert queue.enqueued == []


@pytest.mark.parametrize("non_finite", ["NaN", "Infinity", "-Infinity"])
def test_create_speed_job_rejects_non_finite_json_numbers(
    non_finite: str,
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"original-upload")
    payload = (
        f'{{"media_id":"{media_id}","operation":"speed",'
        f'"parameters":{{"speed":{non_finite}}}}}'
    )

    response = client.post(
        "/api/v1/jobs",
        content=payload,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert queue.enqueued == []


@pytest.mark.parametrize(
    "output_format",
    ["mp3", "wav", "flac", "m4a", "opus", "ogg"],
)
def test_create_extract_audio_job_accepts_supported_format(
    output_format: str,
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"media")

    response = client.post(
        "/api/v1/jobs",
        json={
            "media_id": media_id,
            "operation": "extract_audio",
            "format": output_format,
        },
    )

    assert response.status_code == 202
    assert queue.enqueued[-1][3] == {"format": output_format}


@pytest.mark.parametrize("output_format", ["aac", "vorbis", "abc"])
def test_create_extract_audio_job_rejects_unsupported_format(
    output_format: str,
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"media")

    response = client.post(
        "/api/v1/jobs",
        json={
            "media_id": media_id,
            "operation": "extract_audio",
            "format": output_format,
        },
    )

    assert response.status_code == 422
    assert queue.enqueued == []


@pytest.mark.parametrize(
    "parameters",
    [
        {"format": "wav"},
        {"codec": "pcm_s16le"},
        {"encoder": "aac"},
        {"bitrate": "320k"},
        {"quality": 1},
        {"sample_rate": 48000},
        {"channels": 1},
        {"profile": "aac_low"},
        {"compression_level": 5},
        {"application": "audio"},
        {"vbr": "on"},
    ],
)
def test_create_extract_audio_job_rejects_parameters(
    parameters: dict[str, object],
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"media")

    response = client.post(
        "/api/v1/jobs",
        json={
            "media_id": media_id,
            "operation": "extract_audio",
            "parameters": parameters,
        },
    )

    assert response.status_code == 422
    assert queue.enqueued == []


@pytest.mark.parametrize("field", ["output_format", "output_container"])
def test_create_compress_job_rejects_user_selected_output_format(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
    field: str,
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"media")

    response = client.post(
        "/api/v1/jobs",
        json={
            "media_id": media_id,
            "operation": "compress",
            "parameters": {
                "compression_level": "balanced",
                field: "webm",
            },
        },
    )

    assert response.status_code == 422
    assert queue.enqueued == []


@pytest.mark.parametrize(
    "parameters",
    [
        {"container": "invalid"},
        {"video_codec": "mpeg2"},
        {"audio_codec": "wma"},
        {"container": "webm", "video_codec": "h264", "audio_codec": "opus"},
        {"video_codec": "none", "audio_codec": "none"},
    ],
)
def test_create_convert_job_rejects_invalid_parameters(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
    parameters: dict[str, str],
) -> None:
    client, _, _, queue = jobs_client

    response = client.post(
        "/api/v1/jobs",
        json={
            "media_id": str(uuid4()),
            "operation": "convert",
            "parameters": parameters,
        },
    )

    assert response.status_code == 422
    assert queue.enqueued == []


def test_create_job_rejects_invalid_media_id(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, _, _, queue = jobs_client

    response = client.post(
        "/api/v1/jobs",
        json={"media_id": "../../secret", "operation": "transcode"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_media_id"
    assert queue.enqueued == []


def test_create_job_rejects_missing_media(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, _, _, queue = jobs_client

    response = client.post(
        "/api/v1/jobs",
        json={"media_id": str(uuid4()), "operation": "transcode"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "media_not_found"
    assert queue.enqueued == []


def test_create_job_rejects_unsupported_operation(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, _, _, queue = jobs_client

    response = client.post(
        "/api/v1/jobs",
        json={"media_id": str(uuid4()), "operation": "resize"},
    )

    assert response.status_code == 422
    assert queue.enqueued == []


def test_create_job_does_not_fake_queued_when_broker_is_unavailable(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"media")
    queue.enqueue_error = JobQueueUnavailableError()

    response = client.post(
        "/api/v1/jobs",
        json={"media_id": media_id, "operation": "transcode"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "job_queue_unavailable"
    assert queue.enqueued == []


@pytest.mark.parametrize(
    ("celery_status", "expected"),
    [
        (states.PENDING, JobState.QUEUED),
        ("RECEIVED", JobState.QUEUED),
        (states.STARTED, JobState.PROCESSING),
        ("PROGRESS", JobState.PROCESSING),
        (states.SUCCESS, JobState.COMPLETED),
        (states.FAILURE, JobState.FAILED),
        (states.REVOKED, JobState.FAILED),
    ],
)
def test_celery_statuses_are_normalized(
    celery_status: str, expected: JobState
) -> None:
    assert normalize_job_status(celery_status) is expected


def test_completed_job_status_includes_output_reference(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, _, _, queue = jobs_client
    job_id = str(uuid4())
    media_id = str(uuid4())
    queue.records[job_id] = JobRecord(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.TRANSCODE,
        status=JobState.COMPLETED,
        output_id=job_id,
    )

    response = client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json() == {
        "job_id": job_id,
        "media_id": media_id,
        "operation": "transcode",
        "status": "completed",
        "output": {"output_id": job_id},
    }


def test_completed_compression_status_includes_statistics(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, _, _, queue = jobs_client
    job_id = str(uuid4())
    media_id = str(uuid4())
    queue.records[job_id] = JobRecord(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.COMPRESS,
        status=JobState.COMPLETED,
        output_id=job_id,
        output_format="webm",
        progress=100,
        compression_level="balanced",
        original_size=100,
        compressed_size=80,
        saved_bytes=20,
        reduction_percentage=20.0,
        compression_effective=True,
    )

    response = client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["output"] == {
        "output_id": job_id,
        "format": "webm",
        "compression_level": "balanced",
        "original_size": 100,
        "compressed_size": 80,
        "saved_bytes": 20,
        "reduction_percentage": 20.0,
        "compression_effective": True,
    }


def test_completed_mute_status_reports_preserved_container(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, _, _, queue = jobs_client
    job_id = str(uuid4())
    media_id = str(uuid4())
    queue.records[job_id] = JobRecord(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.MUTE,
        status=JobState.COMPLETED,
        output_id=job_id,
        output_format="mkv",
        progress=100,
    )

    response = client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["progress"] == 100
    assert response.json()["output"] == {"output_id": job_id, "format": "mkv"}


def test_completed_volume_status_reports_preserved_container(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, _, _, queue = jobs_client
    job_id = str(uuid4())
    media_id = str(uuid4())
    queue.records[job_id] = JobRecord(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.VOLUME,
        status=JobState.COMPLETED,
        output_id=job_id,
        output_format="webm",
        progress=100,
    )

    response = client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["progress"] == 100
    assert response.json()["output"] == {"output_id": job_id, "format": "webm"}


def test_completed_audio_extraction_status_reports_mp3(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, _, _, queue = jobs_client
    job_id = str(uuid4())
    media_id = str(uuid4())
    queue.records[job_id] = JobRecord(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.EXTRACT_AUDIO,
        status=JobState.COMPLETED,
        output_id=job_id,
        output_format="mp3",
        progress=100,
    )

    response = client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["output"] == {"output_id": job_id, "format": "mp3"}


def test_completed_wav_extraction_status_reports_wav(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, _, _, queue = jobs_client
    job_id = str(uuid4())
    media_id = str(uuid4())
    queue.records[job_id] = JobRecord(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.EXTRACT_AUDIO,
        status=JobState.COMPLETED,
        output_id=job_id,
        output_format="wav",
        progress=100,
    )

    response = client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["output"] == {"output_id": job_id, "format": "wav"}


def test_completed_flac_extraction_status_reports_flac(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, _, _, queue = jobs_client
    job_id = str(uuid4())
    media_id = str(uuid4())
    queue.records[job_id] = JobRecord(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.EXTRACT_AUDIO,
        status=JobState.COMPLETED,
        output_id=job_id,
        output_format="flac",
        progress=100,
    )

    response = client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["output"] == {"output_id": job_id, "format": "flac"}


def test_completed_m4a_extraction_status_reports_m4a(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, _, _, queue = jobs_client
    job_id = str(uuid4())
    media_id = str(uuid4())
    queue.records[job_id] = JobRecord(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.EXTRACT_AUDIO,
        status=JobState.COMPLETED,
        output_id=job_id,
        output_format="m4a",
        progress=100,
    )

    response = client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["output"] == {"output_id": job_id, "format": "m4a"}


def test_completed_opus_extraction_status_reports_opus(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, _, _, queue = jobs_client
    job_id = str(uuid4())
    media_id = str(uuid4())
    queue.records[job_id] = JobRecord(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.EXTRACT_AUDIO,
        status=JobState.COMPLETED,
        output_id=job_id,
        output_format="opus",
        progress=100,
    )

    response = client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["output"] == {"output_id": job_id, "format": "opus"}


def test_completed_ogg_extraction_status_reports_ogg(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, _, _, queue = jobs_client
    job_id = str(uuid4())
    media_id = str(uuid4())
    queue.records[job_id] = JobRecord(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.EXTRACT_AUDIO,
        status=JobState.COMPLETED,
        output_id=job_id,
        output_format="ogg",
        progress=100,
    )

    response = client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["output"] == {"output_id": job_id, "format": "ogg"}


def test_failed_job_status_includes_safe_error(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, _, _, queue = jobs_client
    job_id = str(uuid4())
    media_id = str(uuid4())
    queue.records[job_id] = JobRecord(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.TRANSCODE,
        status=JobState.FAILED,
        output_id=job_id,
        progress=0,
        error="FFmpeg transcode failed",
    )

    response = client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["progress"] == 0
    assert response.json()["error"] == "FFmpeg transcode failed"
    assert "output" not in response.json()


def test_failed_audio_extraction_status_is_controlled(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, _, _, queue = jobs_client
    job_id = str(uuid4())
    media_id = str(uuid4())
    queue.records[job_id] = JobRecord(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation.EXTRACT_AUDIO,
        status=JobState.FAILED,
        output_id=job_id,
        output_format="mp3",
        progress=0,
        error="Audio extraction failed",
    )

    response = client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json() == {
        "job_id": job_id,
        "media_id": media_id,
        "operation": "extract_audio",
        "status": "failed",
        "progress": 0,
        "error": "Audio extraction failed",
    }


def test_missing_job_returns_404(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, _, _, _ = jobs_client

    response = client.get(f"/api/v1/jobs/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"


def test_celery_adapter_uses_application_job_id_and_primitive_payload() -> None:
    class CapturingApp:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def send_task(self, *args: object, **kwargs: object) -> object:
            self.kwargs["name"] = args[0]
            self.kwargs = kwargs
            self.kwargs["name"] = args[0]
            return object()

    app = CapturingApp()
    adapter = CeleryJobQueue(app)  # type: ignore[arg-type]
    job_id = str(uuid4())
    media_id = str(uuid4())

    parameters = {"container": "webm", "video_codec": "vp9", "audio_codec": "opus"}
    asyncio.run(
        adapter.enqueue(job_id, media_id, JobOperation.CONVERT, parameters)
    )

    assert app.kwargs == {
        "name": "avoria.media.convert",
        "args": [media_id, "convert", parameters],
        "task_id": job_id,
    }


def test_celery_adapter_maps_broker_connection_failure() -> None:
    class UnavailableApp:
        def send_task(self, *_args: object, **_kwargs: object) -> object:
            raise OperationalError("unavailable")

    adapter = CeleryJobQueue(UnavailableApp())  # type: ignore[arg-type]

    with pytest.raises(JobQueueUnavailableError):
        asyncio.run(
            adapter.enqueue(
                str(uuid4()),
                str(uuid4()),
                JobOperation.CONVERT,
                {"container": "mp4", "video_codec": "h264", "audio_codec": "aac"},
            )
        )


def test_celery_adapter_reads_progress_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = str(uuid4())
    media_id = str(uuid4())

    class FakeResult:
        state = "PROGRESS"
        info = {
            "media_id": media_id,
            "operation": "transcode",
            "output_id": job_id,
            "format": "webm",
            "progress": 37,
        }

    monkeypatch.setattr(
        queue_module,
        "AsyncResult",
        lambda *_args, **_kwargs: FakeResult(),
    )
    adapter = CeleryJobQueue(object())  # type: ignore[arg-type]

    record = asyncio.run(adapter.get(job_id))

    assert record.status is JobState.PROCESSING
    assert record.media_id == media_id
    assert record.progress == 37
    assert record.output_format == "webm"


def test_celery_adapter_reads_completed_compression_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = str(uuid4())
    media_id = str(uuid4())

    class FakeResult:
        state = states.SUCCESS
        info = {
            "media_id": media_id,
            "operation": "compress",
            "output_id": job_id,
            "format": "mp4",
            "progress": 100,
            "compression_level": "strong",
            "original_size": 100,
            "compressed_size": 120,
            "saved_bytes": -20,
            "reduction_percentage": -20.0,
            "compression_effective": False,
        }

    monkeypatch.setattr(
        queue_module,
        "AsyncResult",
        lambda *_args, **_kwargs: FakeResult(),
    )
    adapter = CeleryJobQueue(object())  # type: ignore[arg-type]

    record = asyncio.run(adapter.get(job_id))

    assert record.status is JobState.COMPLETED
    assert record.operation is JobOperation.COMPRESS
    assert record.compression_level == "strong"
    assert record.saved_bytes == -20
    assert record.reduction_percentage == -20.0
    assert record.compression_effective is False


def test_celery_adapter_does_not_expose_raw_failure_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = str(uuid4())

    class FakeResult:
        state = states.FAILURE
        info = OSError(r"Access denied: C:\\private\\media.mp4")

    monkeypatch.setattr(queue_module, "AsyncResult", lambda *_args, **_kwargs: FakeResult())
    adapter = CeleryJobQueue(object())  # type: ignore[arg-type]
    adapter._remember(
        job_id,
        str(uuid4()),
        JobOperation.CONVERT,
        {"container": "mp4", "video_codec": "h264", "audio_codec": "aac"},
    )

    record = asyncio.run(adapter.get(job_id))

    assert record.status is JobState.FAILED
    assert record.error == "Media processing failed"


def test_celery_adapter_maps_audio_exception_to_safe_failed_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = str(uuid4())
    media_id = str(uuid4())

    class FakeResult:
        state = states.FAILURE
        info = RuntimeError(r"private path: C:\\uploads\\silent.mp4")

    monkeypatch.setattr(queue_module, "AsyncResult", lambda *_args, **_kwargs: FakeResult())
    adapter = CeleryJobQueue(object())  # type: ignore[arg-type]
    adapter._remember(job_id, media_id, JobOperation.EXTRACT_AUDIO, {})

    record = asyncio.run(adapter.get(job_id))

    assert record.status is JobState.FAILED
    assert record.media_id == media_id
    assert record.operation is JobOperation.EXTRACT_AUDIO
    assert record.output_format == "mp3"
    assert record.progress == 0
    assert record.error == "Audio extraction failed"


def test_celery_adapter_maps_no_audio_to_controlled_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = str(uuid4())
    media_id = str(uuid4())

    class FakeResult:
        state = states.FAILURE
        info = MediaHasNoAudioError("technical detail")

    monkeypatch.setattr(queue_module, "AsyncResult", lambda *_args, **_kwargs: FakeResult())
    adapter = CeleryJobQueue(object())  # type: ignore[arg-type]
    adapter._remember(
        job_id,
        media_id,
        JobOperation.EXTRACT_AUDIO,
        {"format": "wav"},
    )

    record = asyncio.run(adapter.get(job_id))

    assert record.status is JobState.FAILED
    assert record.output_format == "wav"
    assert record.error == "The input does not contain an audio stream"


def test_create_replace_audio_job_accepts_two_original_uploads(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    video_id, audio_id = str(uuid4()), str(uuid4())
    (upload_directory / f"{video_id}.mp4").write_bytes(b"video-upload")
    (upload_directory / f"{audio_id}.mp3").write_bytes(b"audio-upload")

    response = client.post(
        "/api/v1/jobs",
        json={
            "media_id": video_id,
            "operation": "replace_audio",
            "parameters": {"audio_media_id": audio_id},
        },
    )

    assert response.status_code == 202
    assert response.json()["operation"] == "replace_audio"
    assert queue.enqueued[-1][1:] == (
        video_id,
        JobOperation.REPLACE_AUDIO,
        {"audio_media_id": audio_id, "loop": False},
    )


@pytest.mark.parametrize("loop", [False, True])
def test_create_replace_audio_job_accepts_explicit_strict_loop_boolean(
    loop: bool,
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    video_id, audio_id = str(uuid4()), str(uuid4())
    (upload_directory / f"{video_id}.mp4").write_bytes(b"video-upload")
    (upload_directory / f"{audio_id}.wav").write_bytes(b"audio-upload")

    response = client.post(
        "/api/v1/jobs",
        json={
            "media_id": video_id,
            "operation": "replace_audio",
            "parameters": {"audio_media_id": audio_id, "loop": loop},
        },
    )

    assert response.status_code == 202
    assert queue.enqueued[-1][3] == {
        "audio_media_id": audio_id,
        "loop": loop,
    }


@pytest.mark.parametrize(
    "parameters",
    [
        {},
        {"audio_media_id": None},
        {"audio_media_id": ""},
        {"audio_media_id": "not-a-uuid"},
        {"audio_media_id": "../../audio.mp3"},
        {"audio_media_id": 123},
        {"audio_media_id": True},
        {"audio_media_id": str(uuid4()), "loop": 1},
        {"audio_media_id": str(uuid4()), "loop": 0},
        {"audio_media_id": str(uuid4()), "loop": "true"},
        {"audio_media_id": str(uuid4()), "loop": "false"},
        {"audio_media_id": str(uuid4()), "loop": None},
        {"audio_media_id": str(uuid4()), "loop": []},
        {"audio_media_id": str(uuid4()), "loop": {}},
        {"audio_media_id": str(uuid4()), "codec": "aac"},
    ],
)
def test_create_replace_audio_job_rejects_invalid_parameters_before_queue(
    parameters: dict[str, object],
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    video_id = str(uuid4())
    (upload_directory / f"{video_id}.mp4").write_bytes(b"video-upload")

    response = client.post(
        "/api/v1/jobs",
        json={
            "media_id": video_id,
            "operation": "replace_audio",
            "parameters": parameters,
        },
    )

    assert response.status_code == 422
    assert queue.enqueued == []


def test_create_replace_audio_job_rejects_missing_external_upload(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    video_id = str(uuid4())
    (upload_directory / f"{video_id}.mp4").write_bytes(b"video-upload")

    response = client.post(
        "/api/v1/jobs",
        json={
            "media_id": video_id,
            "operation": "replace_audio",
            "parameters": {"audio_media_id": str(uuid4())},
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "audio_media_not_found"
    assert queue.enqueued == []


def test_create_merge_job_preserves_requested_order(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    media_ids = [str(uuid4()) for _ in range(3)]
    for media_id in media_ids:
        (upload_directory / f"{media_id}.mp4").write_bytes(b"video")

    response = client.post(
        "/api/v1/jobs",
        json={
            "operation": "merge_videos",
            "parameters": {
                "media_ids": media_ids,
                "target_aspect_ratio": "16:9",
            },
        },
    )

    assert response.status_code == 202
    assert response.json()["media_id"] == media_ids[0]
    assert queue.enqueued[-1][1:] == (
        media_ids[0],
        JobOperation.MERGE_VIDEOS,
        {"media_ids": media_ids, "target_aspect_ratio": "16:9"},
    )


def test_create_merge_job_rejects_missing_media_before_queue(
    jobs_client: tuple[TestClient, Path, Path, FakeJobQueue],
) -> None:
    client, upload_directory, _, queue = jobs_client
    existing, missing = str(uuid4()), str(uuid4())
    (upload_directory / f"{existing}.mp4").write_bytes(b"video")

    response = client.post(
        "/api/v1/jobs",
        json={
            "operation": "merge_videos",
            "parameters": {
                "media_ids": [existing, missing],
                "target_aspect_ratio": "16:9",
            },
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "media_not_found"
    assert queue.enqueued == []
