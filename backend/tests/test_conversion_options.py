from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings, get_settings
from backend.app.main import app
from backend.app.processing.conversion import (
    FFmpegCapabilityError,
    LocalFFmpegCapabilities,
    get_ffmpeg_capability_detector,
)


class StaticDetector:
    def __init__(
        self,
        capabilities: LocalFFmpegCapabilities | None = None,
        error: Exception | None = None,
    ) -> None:
        self.capabilities = capabilities
        self.error = error

    def detect(self) -> LocalFFmpegCapabilities:
        if self.error is not None:
            raise self.error
        assert self.capabilities is not None
        return self.capabilities


@pytest.fixture
def options_client(tmp_path: Path) -> Iterator[tuple[TestClient, dict[str, StaticDetector]]]:
    settings = Settings(
        _env_file=None,
        storage_root=tmp_path / "media",
        upload_directory=tmp_path / "uploads",
        output_directory=tmp_path / "outputs",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        log_directory=tmp_path / "logs",
    )
    detector_holder = {
        "value": StaticDetector(
            LocalFFmpegCapabilities(
                encoders=frozenset(
                    {
                        "libx264",
                        "libx265",
                        "libvpx-vp9",
                        "libaom-av1",
                        "aac",
                        "libmp3lame",
                        "libopus",
                        "libvorbis",
                        "flac",
                        "pcm_s16le",
                    }
                ),
                muxers=frozenset(
                    {
                        "mp4",
                        "matroska",
                        "webm",
                        "mov",
                        "avi",
                        "mp3",
                        "wav",
                        "flac",
                        "ogg",
                        "ipod",
                        "opus",
                    }
                ),
            )
        )
    }
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_ffmpeg_capability_detector] = (
        lambda: detector_holder["value"]
    )
    try:
        with TestClient(app) as client:
            yield client, detector_holder
    finally:
        app.dependency_overrides.clear()


def test_conversion_options_exposes_stable_compatible_choices(
    options_client: tuple[TestClient, dict[str, StaticDetector]],
) -> None:
    client, _ = options_client

    response = client.get("/api/v1/media/conversion-options")

    assert response.status_code == 200
    body = response.json()
    assert body["defaults"] == {
        "container": "mp4",
        "video_codec": "h264",
        "audio_codec": "aac",
    }
    containers = {item["id"]: item for item in body["containers"]}
    assert set(containers) == {
        "mp4",
        "mkv",
        "webm",
        "mov",
        "avi",
        "mp3",
        "wav",
        "flac",
        "ogg",
        "m4a",
        "opus",
    }
    assert containers["mp4"]["video_codecs"] == [
        "h264",
        "h265",
        "av1",
        "copy",
        "none",
    ]
    assert containers["webm"]["video_codecs"] == ["vp9", "av1", "copy", "none"]
    assert containers["webm"]["audio_codecs"] == [
        "opus",
        "vorbis",
        "copy",
        "none",
    ]


def test_conversion_options_filters_unavailable_local_encoders(
    options_client: tuple[TestClient, dict[str, StaticDetector]],
) -> None:
    client, detector_holder = options_client
    detector_holder["value"] = StaticDetector(
        LocalFFmpegCapabilities(
            encoders=frozenset({"libx264", "aac"}),
            muxers=frozenset({"mp4"}),
        )
    )

    response = client.get("/api/v1/media/conversion-options")

    assert response.status_code == 200
    assert response.json()["containers"] == [
        {
            "id": "mp4",
            "label": "MP4",
            "extension": "mp4",
            "video_codecs": ["h264", "copy", "none"],
            "audio_codecs": ["aac", "copy", "none"],
        }
    ]


def test_conversion_options_maps_capability_failure(
    options_client: tuple[TestClient, dict[str, StaticDetector]],
) -> None:
    client, detector_holder = options_client
    detector_holder["value"] = StaticDetector(error=FFmpegCapabilityError())

    response = client.get("/api/v1/media/conversion-options")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ffmpeg_capabilities_unavailable"

