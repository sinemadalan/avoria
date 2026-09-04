from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="AVORIA_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Avoria API"
    environment: str = "development"
    debug: bool = False
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    celery_broker_url: str = "amqp://guest:guest@localhost:5672//"
    celery_result_backend: str = "rpc://"
    celery_task_default_queue: str = "media"
    celery_result_expires_seconds: int = Field(default=24 * 60 * 60, gt=0)

    storage_root: Path = PROJECT_ROOT / "data" / "media"
    upload_directory: Path = PROJECT_ROOT / "data" / "uploads"
    output_directory: Path = PROJECT_ROOT / "data" / "outputs"
    max_upload_size_bytes: int = Field(default=2 * 1024 * 1024 * 1024, gt=0)
    upload_chunk_size_bytes: int = Field(default=4 * 1024 * 1024, gt=0)
    allowed_media_extensions: list[str] = Field(
        default_factory=lambda: [
            ".mp4",
            ".mov",
            ".mkv",
            ".webm",
            ".avi",
            ".mp3",
            ".wav",
            ".m4a",
            ".aac",
            ".flac",
            ".ogg",
            ".opus",
        ]
    )
    log_directory: Path = PROJECT_ROOT / "data" / "logs"
    log_level: str = "INFO"

    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    ffprobe_timeout_seconds: float = Field(default=30.0, gt=0)
    ffmpeg_timeout_seconds: int = Field(default=6 * 60 * 60, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
