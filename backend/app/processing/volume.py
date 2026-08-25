from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from backend.app.core.config import get_settings
from backend.app.processing.audio_extraction import MediaHasNoAudioError
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
from backend.app.processing.mute import MediaHasNoVideoError
from backend.app.processing.probe import MediaInspection, SyncFFprobeInspector
from backend.app.processing.tools import get_media_tool_paths


class InvalidVolumeError(Exception):
    """Raised when a volume percentage is missing, mistyped, or out of range."""


@dataclass(frozen=True, slots=True)
class VolumeSpec:
    volume_percent: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.volume_percent, bool)
            or not isinstance(self.volume_percent, int)
            or not 0 <= self.volume_percent <= 200
        ):
            raise InvalidVolumeError("Volume percent must be an integer from 0 to 200")

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "VolumeSpec":
        if "volume_percent" not in payload:
            raise InvalidVolumeError("Volume percent is required")
        return cls(volume_percent=payload["volume_percent"])

    def to_payload(self) -> dict[str, int]:
        return {"volume_percent": self.volume_percent}


def validate_volume_input(inspection: MediaInspection) -> None:
    if not inspection.video_streams:
        raise MediaHasNoVideoError("The input does not contain a video stream")
    if not inspection.audio_streams:
        raise MediaHasNoAudioError("The input does not contain an audio stream")


def volume_factor(volume_percent: int) -> str:
    VolumeSpec(volume_percent)
    whole, remainder = divmod(volume_percent, 100)
    if remainder == 0:
        return str(whole)
    return f"{whole}.{remainder:02d}".rstrip("0")


def build_volume_command(
    executable: str,
    input_path: Path,
    output_path: Path,
    spec: VolumeSpec,
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
        "-map",
        "0:a",
        "-af",
        f"volume={volume_factor(spec.volume_percent)}",
        "-c:a",
        profile.audio_encoder,
        *profile.audio_options,
        "-sn",
        "-dn",
        "-f",
        profile.muxer,
        str(output_path),
    ]


class VolumeService:
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
        validate_volume_input(inspection)
        profile = detect_compression_profile(input_path, inspection)
        if profile.muxer not in self.capabilities.muxers:
            raise FFmpegCapabilityError("The volume output container is unavailable")
        if profile.audio_encoder not in self.capabilities.encoders:
            raise FFmpegCapabilityError("The volume audio encoder is unavailable")
        return profile

    def adjust(
        self,
        input_path: Path,
        output_path: Path,
        spec: VolumeSpec,
        profile: CompressionProfile | None = None,
    ) -> None:
        selected_profile = profile or self.resolve_profile(input_path)
        self.runner.run(
            build_volume_command(
                self.executable,
                input_path,
                output_path,
                spec,
                selected_profile,
            )
        )


@lru_cache
def get_volume_service() -> VolumeService:
    settings = get_settings()
    from backend.app.processing.probe import get_sync_ffprobe_inspector

    return VolumeService(
        executable=get_media_tool_paths().ffmpeg,
        inspector=get_sync_ffprobe_inspector(),
        runner=FFmpegRunner(settings.ffmpeg_timeout_seconds),
        capabilities=get_ffmpeg_capability_detector().detect(),
    )
