import asyncio
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.application.ports.storage import AmbiguousMediaError, MediaNotFoundError
from backend.app.core.config import Settings, get_settings
from backend.app.infrastructure.storage import LocalStorageService, get_storage_service
from backend.app.main import app
from backend.app.processing.probe import (
    FFprobeExecutableNotFoundError,
    FFprobeInspector,
    FFprobeTimeoutError,
    InvalidMediaError,
    MediaInspection,
    SyncFFprobeInspector,
    get_sync_ffprobe_inspector,
    parse_ffprobe_payload,
)


VIDEO_AUDIO_PAYLOAD = {
    "format": {
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "format_long_name": "QuickTime / MOV",
        "duration": "128.42",
        "size": "12458230",
        "bit_rate": "776000",
    },
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "h264",
            "codec_long_name": "H.264 / AVC",
            "profile": "High",
            "width": 1920,
            "height": 1080,
            "pix_fmt": "yuv420p",
            "avg_frame_rate": "30000/1001",
            "bit_rate": "640000",
        },
        {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "aac",
            "codec_long_name": "AAC",
            "sample_rate": "48000",
            "channels": 2,
            "channel_layout": "stereo",
            "bit_rate": "128000",
        },
    ],
}


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"{}",
        stderr: bytes = b"",
        returncode: int | None = 0,
        blocks: bool = False,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.blocks = blocks
        self.killed = False
        self.communicate_calls = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        self.communicate_calls += 1
        if self.blocks and not self.killed:
            await asyncio.Event().wait()
        return self.stdout, self.stderr

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def test_ffprobe_inspector_parses_video_and_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(stdout=json.dumps(VIDEO_AUDIO_PAYLOAD).encode())
    captured: dict[str, Any] = {}

    async def create_process(*args: Any, **kwargs: Any) -> FakeProcess:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    result = asyncio.run(FFprobeInspector("configured-ffprobe", 5).inspect(Path("media.mp4")))

    assert captured["args"][0] == "configured-ffprobe"
    assert "-show_entries" in captured["args"]
    assert "shell" not in captured["kwargs"]
    assert result.format.name == "mov,mp4,m4a,3gp,3g2,mj2"
    assert result.format.duration_seconds == 128.42
    assert result.format.size_bytes == 12458230
    assert result.format.bit_rate == 776000
    assert result.stream_count == 2
    assert result.video_streams[0].width == 1920
    assert result.video_streams[0].height == 1080
    assert result.video_streams[0].frame_rate == pytest.approx(29.97, rel=1e-3)
    assert result.audio_streams[0].sample_rate == 48000
    assert result.audio_streams[0].channels == 2


def test_parses_audio_only_media() -> None:
    result = parse_ffprobe_payload(
        {"format": {"format_name": "mp3"}, "streams": [VIDEO_AUDIO_PAYLOAD["streams"][1]]}
    )

    assert result.video_streams == []
    assert len(result.audio_streams) == 1
    assert result.audio_streams[0].codec_name == "aac"


def test_parses_video_only_media() -> None:
    result = parse_ffprobe_payload(
        {"format": {"format_name": "matroska"}, "streams": [VIDEO_AUDIO_PAYLOAD["streams"][0]]}
    )

    assert len(result.video_streams) == 1
    assert result.audio_streams == []


def test_missing_metadata_and_invalid_frame_rate_are_safe() -> None:
    result = parse_ffprobe_payload(
        {
            "format": {"format_name": "webm", "duration": "N/A"},
            "streams": [
                {"index": 0, "codec_type": "video", "avg_frame_rate": "0/0"},
                {"index": 1, "codec_type": "audio", "sample_rate": "N/A"},
                {"index": 2, "codec_type": "subtitle"},
            ],
        }
    )

    assert result.format.duration_seconds is None
    assert result.format.bit_rate is None
    assert result.stream_count == 3
    assert result.video_streams[0].profile is None
    assert result.video_streams[0].frame_rate is None
    assert result.audio_streams[0].sample_rate is None
    assert result.audio_streams[0].channel_layout is None


def test_nonzero_ffprobe_exit_rejects_invalid_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def create_process(*_args: Any, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stderr=b"invalid data", returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(InvalidMediaError):
        asyncio.run(FFprobeInspector("ffprobe", 5).inspect(Path("fake.mp4")))


def test_ffprobe_timeout_kills_and_reaps_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(returncode=None, blocks=True)

    async def create_process(*_args: Any, **_kwargs: Any) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(FFprobeTimeoutError):
        asyncio.run(FFprobeInspector("ffprobe", 0.01).inspect(Path("slow.mp4")))

    assert process.killed is True
    assert process.communicate_calls == 2


def test_missing_ffprobe_executable_is_distinct_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def create_process(*_args: Any, **_kwargs: Any) -> FakeProcess:
        raise FileNotFoundError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(FFprobeExecutableNotFoundError):
        asyncio.run(FFprobeInspector("missing-ffprobe", 5).inspect(Path("media.mp4")))


def test_sync_ffprobe_inspector_is_safe_for_windows_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(VIDEO_AUDIO_PAYLOAD).encode(),
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", run)

    result = SyncFFprobeInspector("configured-ffprobe", 5).inspect(Path("media.mp4"))

    assert captured["command"][0] == "configured-ffprobe"
    assert captured["command"][-1] == "media.mp4"
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
    assert captured["kwargs"]["timeout"] == 5
    assert result.stream_count == 2


def test_sync_ffprobe_timeout_is_mapped(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(command, timeout=0.01)

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(FFprobeTimeoutError):
        SyncFFprobeInspector("ffprobe", 0.01).inspect(Path("slow.mp4"))


def test_sync_ffprobe_missing_executable_is_mapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(_command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(FFprobeExecutableNotFoundError):
        SyncFFprobeInspector("missing-ffprobe", 5).inspect(Path("media.mp4"))


def test_storage_ignores_partial_and_unsupported_uploads(tmp_path: Path) -> None:
    upload_directory = tmp_path / "uploads"
    storage = LocalStorageService(tmp_path / "media", upload_directory)
    storage.initialize()
    media_id = str(uuid4())
    (upload_directory / f".{media_id}.mp4.part").write_bytes(b"partial")
    (upload_directory / f"{media_id}.txt").write_bytes(b"unsupported")

    with pytest.raises(MediaNotFoundError):
        asyncio.run(storage.resolve_upload(media_id, [".mp4"]))


def test_storage_rejects_multiple_supported_uploads_for_same_id(tmp_path: Path) -> None:
    upload_directory = tmp_path / "uploads"
    storage = LocalStorageService(tmp_path / "media", upload_directory)
    storage.initialize()
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"video")
    (upload_directory / f"{media_id}.mov").write_bytes(b"video")

    with pytest.raises(AmbiguousMediaError):
        asyncio.run(storage.resolve_upload(media_id, [".mp4", ".mov"]))


class StubInspector:
    def __init__(self, result: MediaInspection | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def inspect(self, _media_path: Path) -> MediaInspection:
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


@pytest.fixture
def inspection_client(
    tmp_path: Path,
) -> Iterator[tuple[TestClient, Path, dict[str, StubInspector]]]:
    upload_directory = tmp_path / "uploads"
    storage = LocalStorageService(tmp_path / "media", upload_directory)
    storage.initialize()
    settings = Settings(
        _env_file=None,
        storage_root=tmp_path / "media",
        upload_directory=upload_directory,
        log_directory=tmp_path / "logs",
    )
    inspector_holder = {"value": StubInspector(parse_ffprobe_payload(VIDEO_AUDIO_PAYLOAD))}
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_storage_service] = lambda: storage
    app.dependency_overrides[get_sync_ffprobe_inspector] = lambda: inspector_holder["value"]

    try:
        with TestClient(app) as client:
            yield client, upload_directory, inspector_holder
    finally:
        app.dependency_overrides.clear()


def test_media_inspection_endpoint_returns_clean_response(
    inspection_client: tuple[TestClient, Path, dict[str, StubInspector]],
) -> None:
    client, upload_directory, _ = inspection_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"test")

    response = client.post(f"/api/v1/media/{media_id}/inspect")

    assert response.status_code == 200
    body = response.json()
    assert body["media_id"] == media_id
    assert body["format"]["duration_seconds"] == 128.42
    assert body["stream_count"] == 2
    assert body["video_streams"][0]["frame_rate"] == pytest.approx(29.97, rel=1e-3)
    assert body["audio_streams"][0]["sample_rate"] == 48000
    assert "path" not in body


def test_media_inspection_returns_not_found(
    inspection_client: tuple[TestClient, Path, dict[str, StubInspector]],
) -> None:
    client, _, _ = inspection_client

    response = client.post(f"/api/v1/media/{uuid4()}/inspect")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "media_not_found"


@pytest.mark.parametrize("media_id", ["not-a-uuid", "..%2F..%2Fsecret"])
def test_media_inspection_rejects_invalid_media_id(
    inspection_client: tuple[TestClient, Path, dict[str, StubInspector]],
    media_id: str,
) -> None:
    client, _, _ = inspection_client

    response = client.post(f"/api/v1/media/{media_id}/inspect")

    assert response.status_code in {400, 404}
    if response.status_code == 400:
        assert response.json()["error"]["code"] == "invalid_media_id"


def test_media_inspection_maps_invalid_media_error(
    inspection_client: tuple[TestClient, Path, dict[str, StubInspector]],
) -> None:
    client, upload_directory, inspector_holder = inspection_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"not really media")
    inspector_holder["value"] = StubInspector(error=InvalidMediaError())

    response = client.post(f"/api/v1/media/{media_id}/inspect")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_media"


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (FFprobeTimeoutError(), 504, "ffprobe_timeout"),
        (FFprobeExecutableNotFoundError(), 503, "ffprobe_unavailable"),
    ],
)
def test_media_inspection_maps_ffprobe_availability_errors(
    inspection_client: tuple[TestClient, Path, dict[str, StubInspector]],
    error: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    client, upload_directory, inspector_holder = inspection_client
    media_id = str(uuid4())
    (upload_directory / f"{media_id}.mp4").write_bytes(b"media")
    inspector_holder["value"] = StubInspector(error=error)

    response = client.post(f"/api/v1/media/{media_id}/inspect")

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code
