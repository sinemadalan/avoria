from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings, get_settings
from backend.app.infrastructure.storage import LocalStorageService, get_storage_service
from backend.app.main import app


@pytest.fixture
def upload_client(tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    upload_directory = tmp_path / "uploads"
    storage = LocalStorageService(tmp_path / "media", upload_directory)
    storage.initialize()
    settings = Settings(
        _env_file=None,
        storage_root=tmp_path / "media",
        upload_directory=upload_directory,
        max_upload_size_bytes=1024,
        upload_chunk_size_bytes=4,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        log_directory=tmp_path / "logs",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_storage_service] = lambda: storage

    try:
        with TestClient(app) as client:
            yield client, upload_directory
    finally:
        app.dependency_overrides.clear()


def test_successful_media_upload(upload_client: tuple[TestClient, Path]) -> None:
    client, upload_directory = upload_client
    content = b"fake-video-content"

    response = client.post(
        "/api/v1/media/upload",
        files={"file": ("holiday_video.mp4", content, "video/mp4")},
    )

    assert response.status_code == 201
    body = response.json()
    assert UUID(body["media_id"])
    assert body == {
        "media_id": body["media_id"],
        "original_filename": "holiday_video.mp4",
        "filename": f"{body['media_id']}.mp4",
        "content_type": "video/mp4",
        "size_bytes": len(content),
    }
    assert (upload_directory / body["filename"]).read_bytes() == content


def test_opus_input_is_accepted_for_conversion(
    upload_client: tuple[TestClient, Path],
) -> None:
    client, upload_directory = upload_client

    response = client.post(
        "/api/v1/media/upload",
        files={"file": ("audio.opus", b"fake-opus-content", "audio/opus")},
    )

    assert response.status_code == 201
    assert response.json()["filename"].endswith(".opus")
    assert (upload_directory / response.json()["filename"]).is_file()


def test_rejects_unsupported_extension(upload_client: tuple[TestClient, Path]) -> None:
    client, upload_directory = upload_client

    response = client.post(
        "/api/v1/media/upload",
        files={"file": ("notes.txt", b"not-media", "text/plain")},
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_extension"
    assert list(upload_directory.iterdir()) == []


def test_rejects_empty_file(upload_client: tuple[TestClient, Path]) -> None:
    client, upload_directory = upload_client

    response = client.post(
        "/api/v1/media/upload",
        files={"file": ("empty.mp4", b"", "video/mp4")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_file"
    assert list(upload_directory.iterdir()) == []


def test_rejects_upload_over_size_limit_without_partial_file(
    upload_client: tuple[TestClient, Path],
) -> None:
    client, upload_directory = upload_client
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        storage_root=upload_directory.parent / "media",
        upload_directory=upload_directory,
        max_upload_size_bytes=5,
        upload_chunk_size_bytes=4,
        database_url=f"sqlite+aiosqlite:///{(upload_directory.parent / 'test.db').as_posix()}",
        log_directory=upload_directory.parent / "logs",
    )

    response = client.post(
        "/api/v1/media/upload",
        files={"file": ("too-large.mp4", b"123456", "video/mp4")},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "upload_too_large"
    assert list(upload_directory.iterdir()) == []


def test_client_filename_cannot_control_storage_path(
    upload_client: tuple[TestClient, Path],
) -> None:
    client, upload_directory = upload_client

    response = client.post(
        "/api/v1/media/upload",
        files={"file": ("../../dangerous.mp4", b"safe", "video/mp4")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["original_filename"] == "../../dangerous.mp4"
    assert body["filename"] == f"{body['media_id']}.mp4"
    assert (upload_directory / body["filename"]).is_file()
    assert not (upload_directory.parent / "dangerous.mp4").exists()
