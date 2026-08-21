from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from backend.app.core.config import get_settings
from backend.app.processing.conversion import (
    FFmpegCapabilityError,
    FFmpegRunner,
    LocalFFmpegCapabilities,
    OutputContainer,
    get_ffmpeg_capability_detector,
)
from backend.app.processing.probe import MediaInspection, SyncFFprobeInspector
from backend.app.processing.tools import get_media_tool_paths


class CompressionLevel(str, Enum):
    LIGHT = "light"
    BALANCED = "balanced"
    STRONG = "strong"


COMPRESSION_CRF: dict[CompressionLevel, int] = {
    CompressionLevel.LIGHT: 20,
    CompressionLevel.BALANCED: 23,
    CompressionLevel.STRONG: 28,
}
VP9_COMPRESSION_CRF: dict[CompressionLevel, int] = {
    CompressionLevel.LIGHT: 26,
    CompressionLevel.BALANCED: 32,
    CompressionLevel.STRONG: 38,
}
AVI_COMPRESSION_QUALITY: dict[CompressionLevel, int] = {
    CompressionLevel.LIGHT: 3,
    CompressionLevel.BALANCED: 5,
    CompressionLevel.STRONG: 8,
}


@dataclass(frozen=True, slots=True)
class CompressionProfile:
    container: OutputContainer
    extension: str
    muxer: str
    video_encoder: str
    audio_encoder: str
    quality_option: str
    quality_values: Mapping[CompressionLevel, int]
    video_options: tuple[str, ...] = ()
    audio_options: tuple[str, ...] = ()


COMPRESSION_PROFILES: dict[OutputContainer, CompressionProfile] = {
    OutputContainer.MP4: CompressionProfile(
        container=OutputContainer.MP4,
        extension="mp4",
        muxer="mp4",
        video_encoder="libx264",
        audio_encoder="aac",
        quality_option="-crf",
        quality_values=COMPRESSION_CRF,
        video_options=("-preset", "medium"),
        audio_options=("-b:a", "128k"),
    ),
    OutputContainer.MOV: CompressionProfile(
        container=OutputContainer.MOV,
        extension="mov",
        muxer="mov",
        video_encoder="libx264",
        audio_encoder="aac",
        quality_option="-crf",
        quality_values=COMPRESSION_CRF,
        video_options=("-preset", "medium"),
        audio_options=("-b:a", "128k"),
    ),
    OutputContainer.MKV: CompressionProfile(
        container=OutputContainer.MKV,
        extension="mkv",
        muxer="matroska",
        video_encoder="libx264",
        audio_encoder="aac",
        quality_option="-crf",
        quality_values=COMPRESSION_CRF,
        video_options=("-preset", "medium"),
        audio_options=("-b:a", "128k"),
    ),
    OutputContainer.WEBM: CompressionProfile(
        container=OutputContainer.WEBM,
        extension="webm",
        muxer="webm",
        video_encoder="libvpx-vp9",
        audio_encoder="libopus",
        quality_option="-crf",
        quality_values=VP9_COMPRESSION_CRF,
        video_options=("-b:v", "0"),
    ),
    OutputContainer.AVI: CompressionProfile(
        container=OutputContainer.AVI,
        extension="avi",
        muxer="avi",
        video_encoder="mpeg4",
        audio_encoder="libmp3lame",
        quality_option="-q:v",
        quality_values=AVI_COMPRESSION_QUALITY,
    ),
}

_CONTAINER_FORMAT_NAMES: dict[OutputContainer, frozenset[str]] = {
    OutputContainer.MP4: frozenset({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}),
    OutputContainer.MOV: frozenset({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}),
    OutputContainer.MKV: frozenset({"matroska", "webm"}),
    OutputContainer.WEBM: frozenset({"matroska", "webm"}),
    OutputContainer.AVI: frozenset({"avi"}),
}


@dataclass(frozen=True, slots=True)
class CompressionSpec:
    level: CompressionLevel = CompressionLevel.BALANCED

    @classmethod
    def from_payload(cls, payload: Mapping[str, str]) -> "CompressionSpec":
        return cls(
            level=CompressionLevel(
                payload.get("compression_level", CompressionLevel.BALANCED)
            )
        )

    def to_payload(self) -> dict[str, str]:
        return {"compression_level": self.level.value}


@dataclass(frozen=True, slots=True)
class CompressionStatistics:
    original_size: int
    compressed_size: int
    saved_bytes: int
    reduction_percentage: float
    compression_effective: bool

    def to_payload(self) -> dict[str, int | float | bool]:
        return {
            "original_size": self.original_size,
            "compressed_size": self.compressed_size,
            "saved_bytes": self.saved_bytes,
            "reduction_percentage": self.reduction_percentage,
            "compression_effective": self.compression_effective,
        }


class InvalidCompressionInputError(Exception):
    """Raised when the input does not contain a compressible video stream."""


class UnsupportedCompressionContainerError(Exception):
    """Raised when compression cannot preserve the detected source container."""


def calculate_compression_statistics(
    original_size: int,
    compressed_size: int,
) -> CompressionStatistics:
    saved_bytes = original_size - compressed_size
    reduction_percentage = (
        round((saved_bytes / original_size) * 100, 2) if original_size else 0.0
    )
    return CompressionStatistics(
        original_size=original_size,
        compressed_size=compressed_size,
        saved_bytes=saved_bytes,
        reduction_percentage=reduction_percentage,
        compression_effective=compressed_size < original_size,
    )


def validate_compression_input(inspection: MediaInspection) -> None:
    if not inspection.video_streams:
        raise InvalidCompressionInputError("The input does not contain a video stream")


def detect_compression_profile(
    input_path: Path,
    inspection: MediaInspection,
) -> CompressionProfile:
    extension = input_path.suffix.casefold().removeprefix(".")
    try:
        container = OutputContainer(extension)
    except ValueError as exc:
        raise UnsupportedCompressionContainerError from exc

    profile = COMPRESSION_PROFILES.get(container)
    format_names = {
        item.strip().casefold()
        for item in (inspection.format.name or "").split(",")
        if item.strip()
    }
    if profile is None or not format_names.intersection(
        _CONTAINER_FORMAT_NAMES[container]
    ):
        raise UnsupportedCompressionContainerError
    return profile


def build_compress_command(
    executable: str,
    input_path: Path,
    output_path: Path,
    spec: CompressionSpec,
    profile: CompressionProfile | None = None,
) -> list[str]:
    selected_profile = profile or COMPRESSION_PROFILES[OutputContainer.MP4]
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
        "-c:v",
        selected_profile.video_encoder,
        selected_profile.quality_option,
        str(selected_profile.quality_values[spec.level]),
        *selected_profile.video_options,
        "-map",
        "0:a:0?",
        "-c:a",
        selected_profile.audio_encoder,
        *selected_profile.audio_options,
        "-sn",
        "-dn",
        "-f",
        selected_profile.muxer,
        str(output_path),
    ]
    return command


class CompressionService:
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

    def compress(
        self,
        input_path: Path,
        output_path: Path,
        spec: CompressionSpec,
        profile: CompressionProfile | None = None,
    ) -> None:
        selected_profile = profile or self.resolve_profile(input_path)
        self.runner.run(
            build_compress_command(
                self.executable,
                input_path,
                output_path,
                spec,
                selected_profile,
            )
        )

    def resolve_profile(self, input_path: Path) -> CompressionProfile:
        inspection = self.inspector.inspect(input_path)
        validate_compression_input(inspection)
        profile = detect_compression_profile(input_path, inspection)
        self._validate_capabilities(profile)
        return profile

    def _validate_capabilities(self, profile: CompressionProfile) -> None:
        if profile.muxer not in self.capabilities.muxers:
            raise FFmpegCapabilityError("The compression container is unavailable")
        if (
            profile.video_encoder not in self.capabilities.encoders
            or profile.audio_encoder not in self.capabilities.encoders
        ):
            raise FFmpegCapabilityError("A compression encoder is unavailable")


@lru_cache
def get_compression_service() -> CompressionService:
    settings = get_settings()
    executable = get_media_tool_paths().ffmpeg
    from backend.app.processing.probe import get_sync_ffprobe_inspector

    return CompressionService(
        executable=executable,
        inspector=get_sync_ffprobe_inspector(),
        runner=FFmpegRunner(settings.ffmpeg_timeout_seconds),
        capabilities=get_ffmpeg_capability_detector().detect(),
    )
