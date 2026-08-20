"""Backward-compatible facade for the former fixed transcode service."""

from pathlib import Path

from backend.app.core.config import get_settings
from backend.app.processing.conversion import (
    ConversionSpec,
    FFmpegConversionError,
    FFmpegExecutableNotFoundError,
    FFmpegRunner,
    FFmpegTimeoutError,
    build_convert_command,
)
from backend.app.processing.tools import get_media_tool_paths

FFmpegTranscodeError = FFmpegConversionError


class FFmpegTranscoder:
    """Legacy adapter that applies the default MP4/H.264/AAC conversion."""

    def __init__(self, executable: str, timeout_seconds: int) -> None:
        self.executable = executable
        self.runner = FFmpegRunner(timeout_seconds)

    def transcode(self, input_path: Path, output_path: Path) -> None:
        self.runner.run(
            build_convert_command(
                self.executable,
                input_path,
                output_path,
                ConversionSpec(),
            )
        )


def get_ffmpeg_transcoder() -> FFmpegTranscoder:
    settings = get_settings()
    return FFmpegTranscoder(
        executable=get_media_tool_paths().ffmpeg,
        timeout_seconds=settings.ffmpeg_timeout_seconds,
    )

