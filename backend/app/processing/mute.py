from functools import lru_cache
from pathlib import Path

from backend.app.core.config import get_settings
from backend.app.processing.compression import (
    CompressionProfile,
    detect_compression_profile,
)
from backend.app.processing.conversion import (
    FFmpegCapabilityError,
    FFmpegRunner,
    LocalFFmpegCapabilities,
    get_ffmpeg_capability_detector,
)
from backend.app.processing.probe import MediaInspection, SyncFFprobeInspector
from backend.app.processing.tools import get_media_tool_paths


class MediaHasNoVideoError(Exception):
    """Raised when mute input does not contain a video stream."""


def validate_mute_input(inspection: MediaInspection) -> None:
    if not inspection.video_streams:
        raise MediaHasNoVideoError("The input does not contain a video stream")


def build_mute_command(
    executable: str,
    input_path: Path,
    output_path: Path,
    profile: CompressionProfile,
) -> list[str]:
    return [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-an",
        "-sn",
        "-dn",
        "-f",
        profile.muxer,
        str(output_path),
    ]


class MuteService:
    def __init__(
        self,
        executable: str,
        inspector: SyncFFprobeInspector,
        runner: FFmpegRunner,
        capabilities: LocalFFmpegCapabilities,
    ) -> None:
        self.executable = executable
        self.inspector = inspector
        self.runner = runner
        self.capabilities = capabilities

    def resolve_profile(self, input_path: Path) -> CompressionProfile:
        inspection = self.inspector.inspect(input_path)
        validate_mute_input(inspection)
        profile = detect_compression_profile(input_path, inspection)
        if profile.muxer not in self.capabilities.muxers:
            raise FFmpegCapabilityError("The mute output container is unavailable")
        return profile

    def mute(
        self,
        input_path: Path,
        output_path: Path,
        profile: CompressionProfile | None = None,
    ) -> None:
        selected_profile = profile or self.resolve_profile(input_path)
        self.runner.run(
            build_mute_command(
                self.executable,
                input_path,
                output_path,
                selected_profile,
            )
        )


@lru_cache
def get_mute_service() -> MuteService:
    settings = get_settings()
    from backend.app.processing.probe import get_sync_ffprobe_inspector

    return MuteService(
        executable=get_media_tool_paths().ffmpeg,
        inspector=get_sync_ffprobe_inspector(),
        runner=FFmpegRunner(settings.ffmpeg_timeout_seconds),
        capabilities=get_ffmpeg_capability_detector().detect(),
    )
