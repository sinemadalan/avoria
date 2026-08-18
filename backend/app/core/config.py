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

    database_url: str = f"sqlite+aiosqlite:///{(PROJECT_ROOT / 'data' / 'avoria.db').as_posix()}"
    redis_url: str = "redis://localhost:6379/0"
    rq_queue_name: str = "media"

    storage_root: Path = PROJECT_ROOT / "data" / "media"
    log_directory: Path = PROJECT_ROOT / "data" / "logs"
    log_level: str = "INFO"

    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"


@lru_cache
def get_settings() -> Settings:
    return Settings()

