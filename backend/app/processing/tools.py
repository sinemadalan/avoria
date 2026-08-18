from dataclasses import dataclass

from backend.app.core.config import get_settings


@dataclass(frozen=True, slots=True)
class MediaToolPaths:
    """Executable locations consumed by future processing adapters."""

    ffmpeg: str
    ffprobe: str


def get_media_tool_paths() -> MediaToolPaths:
    settings = get_settings()
    return MediaToolPaths(
        ffmpeg=settings.ffmpeg_path,
        ffprobe=settings.ffprobe_path,
    )

