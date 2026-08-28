from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from backend.app.core.config import get_settings
from backend.app.processing.audio_extraction import (
    AudioExtractionProfile,
    MediaHasNoAudioError,
    detect_audio_profile,
)
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


@dataclass(frozen=True, slots=True)
class VolumeProfile:
    extension: str
    muxer: str
    video: CompressionProfile | None
    audio: AudioExtractionProfile | None
    audio_stream_count: int


def validate_volume_input(inspection: MediaInspection) -> None:
    if not inspection.audio_streams:
        raise MediaHasNoAudioError("The input does not contain an audio stream")


def resolve_volume_profile(
    input_path: Path,
    inspection: MediaInspection,
) -> VolumeProfile:
    validate_volume_input(inspection)
    if inspection.video_streams:
        video = detect_compression_profile(input_path, inspection)
        return VolumeProfile(
            video.extension,
            video.muxer,
            video,
            None,
            len(inspection.audio_streams),
        )
    audio = detect_audio_profile(input_path, inspection)
    return VolumeProfile(
        audio.extension,
        audio.muxer,
        None,
        audio,
        len(inspection.audio_streams),
    )


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
    profile: VolumeProfile,
) -> list[str]:
    command = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
    ]
    if profile.video is not None:
        command.extend(["-map", "0:v:0", "-c:v", "copy"])
        audio_encoder = profile.video.audio_encoder
        audio_options = profile.video.audio_options
    else:
        command.append("-vn")
        if profile.audio is None:
            raise RuntimeError("Volume audio profile is unavailable")
        audio_encoder = profile.audio.encoder
        audio_options = profile.audio.encoder_options
    command.extend([
        "-map", "0:a",
        "-af",
        f"volume={volume_factor(spec.volume_percent)}",
        "-c:a",
        audio_encoder,
        *audio_options,
        "-sn",
        "-dn",
        "-f",
        profile.muxer,
        str(output_path),
    ])
    return command


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

    def resolve_profile(self, input_path: Path) -> VolumeProfile:
        inspection = self.inspector.inspect(input_path)
        profile = resolve_volume_profile(input_path, inspection)
        audio_encoder = (
            profile.video.audio_encoder
            if profile.video is not None
            else profile.audio.encoder if profile.audio is not None else None
        )
        if profile.muxer not in self.capabilities.muxers:
            raise FFmpegCapabilityError("The volume output container is unavailable")
        if audio_encoder not in self.capabilities.encoders:
            raise FFmpegCapabilityError("The volume audio encoder is unavailable")
        return profile

    def adjust(
        self,
        input_path: Path,
        output_path: Path,
        spec: VolumeSpec,
        profile: VolumeProfile | None = None,
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
