from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from backend.app.core.config import get_settings
from backend.app.processing.compression import (
    CompressionLevel,
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


class CropAspectRatio(str, Enum):
    LANDSCAPE = "16:9"
    PORTRAIT = "9:16"
    SQUARE = "1:1"
    SOCIAL_PORTRAIT = "4:5"

    @property
    def dimensions(self) -> tuple[int, int]:
        width, height = self.value.split(":", maxsplit=1)
        return int(width), int(height)


class CropMode(str, Enum):
    CROP = "crop"
    FIT = "fit"


class InvalidCropSpecError(Exception):
    """Raised when a crop payload is incomplete or unsupported."""


class CropMediaHasNoVideoError(Exception):
    """Raised when crop input does not contain a video stream."""


class InvalidCropDimensionsError(Exception):
    """Raised when FFprobe does not provide usable video dimensions."""


@dataclass(frozen=True, slots=True)
class CropSpec:
    aspect_ratio: CropAspectRatio
    mode: CropMode

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "CropSpec":
        try:
            return cls(
                aspect_ratio=CropAspectRatio(payload["aspect_ratio"]),
                mode=CropMode(payload["mode"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidCropSpecError("Invalid crop parameters") from exc

    def to_payload(self) -> dict[str, str]:
        return {
            "aspect_ratio": self.aspect_ratio.value,
            "mode": self.mode.value,
        }


@dataclass(frozen=True, slots=True)
class CropProfile:
    media: CompressionProfile
    source_width: int
    source_height: int
    output_width: int
    output_height: int
    has_audio: bool


def calculate_output_dimensions(
    source_width: int,
    source_height: int,
    aspect_ratio: CropAspectRatio,
) -> tuple[int, int]:
    if source_width <= 0 or source_height <= 0:
        raise InvalidCropDimensionsError("Media inspection failed")
    ratio_width, ratio_height = aspect_ratio.dimensions
    multiplier = min(source_width // ratio_width, source_height // ratio_height)
    # Every supported ratio has at least one odd component. An even multiplier
    # therefore makes both output dimensions even while retaining the exact ratio.
    multiplier -= multiplier % 2
    if multiplier < 2:
        raise InvalidCropDimensionsError("Video dimensions are too small to crop")
    return ratio_width * multiplier, ratio_height * multiplier


def resolve_crop_profile(
    input_path: Path,
    inspection: MediaInspection,
    spec: CropSpec,
) -> CropProfile:
    if not inspection.video_streams:
        raise CropMediaHasNoVideoError("The input does not contain a video stream")
    stream = inspection.video_streams[0]
    if stream.width is None or stream.height is None:
        raise InvalidCropDimensionsError("Media inspection failed")
    output_width, output_height = calculate_output_dimensions(
        stream.width,
        stream.height,
        spec.aspect_ratio,
    )
    return CropProfile(
        media=detect_compression_profile(input_path, inspection),
        source_width=stream.width,
        source_height=stream.height,
        output_width=output_width,
        output_height=output_height,
        has_audio=bool(inspection.audio_streams),
    )


def build_crop_filter(spec: CropSpec, profile: CropProfile) -> str:
    width, height = profile.output_width, profile.output_height
    if spec.mode is CropMode.CROP:
        x = (profile.source_width - width) // 2
        y = (profile.source_height - height) // 2
        return f"crop={width}:{height}:{x}:{y},setsar=1"
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:"
        "force_divisible_by=2,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
    )


def build_crop_command(
    executable: str,
    input_path: Path,
    output_path: Path,
    spec: CropSpec,
    profile: CropProfile,
) -> list[str]:
    media = profile.media
    command = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-vf",
        build_crop_filter(spec, profile),
        "-c:v",
        media.video_encoder,
        media.quality_option,
        str(media.quality_values[CompressionLevel.BALANCED]),
        *media.video_options,
    ]
    if profile.has_audio:
        command.extend(["-map", "0:a?", "-c:a", "copy"])
    else:
        command.append("-an")
    command.extend(["-sn", "-dn", "-f", media.muxer, str(output_path)])
    return command


class CropService:
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

    def resolve_profile(self, input_path: Path, spec: CropSpec) -> CropProfile:
        profile = resolve_crop_profile(input_path, self.inspector.inspect(input_path), spec)
        if profile.media.muxer not in self.capabilities.muxers:
            raise FFmpegCapabilityError("The crop output container is unavailable")
        if profile.media.video_encoder not in self.capabilities.encoders:
            raise FFmpegCapabilityError("The crop video encoder is unavailable")
        return profile

    def crop(
        self,
        input_path: Path,
        output_path: Path,
        spec: CropSpec,
        profile: CropProfile | None = None,
    ) -> None:
        selected_profile = profile or self.resolve_profile(input_path, spec)
        self.runner.run(
            build_crop_command(
                self.executable,
                input_path,
                output_path,
                spec,
                selected_profile,
            )
        )


@lru_cache
def get_crop_service() -> CropService:
    settings = get_settings()
    from backend.app.processing.probe import get_sync_ffprobe_inspector

    return CropService(
        executable=get_media_tool_paths().ffmpeg,
        inspector=get_sync_ffprobe_inspector(),
        runner=FFmpegRunner(settings.ffmpeg_timeout_seconds),
        capabilities=get_ffmpeg_capability_detector().detect(),
    )
