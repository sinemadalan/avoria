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
from backend.app.processing.probe import (
    AudioStreamMetadata,
    MediaInspection,
    SyncFFprobeInspector,
)
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


@dataclass(frozen=True, slots=True)
class CompressionSourceMetadata:
    container: OutputContainer
    video_codec: str | None
    width: int | None
    height: int | None
    frame_rate: float | None
    video_bit_rate: int | None
    audio_codec: str | None
    audio_bit_rate: int | None
    duration_seconds: float | None

    @property
    def has_audio(self) -> bool:
        return self.audio_codec is not None


@dataclass(frozen=True, slots=True)
class ResolvedCompressionProfile:
    container: OutputContainer
    extension: str
    muxer: str
    video_encoder: str
    quality_option: str
    quality_value: int
    video_options: tuple[str, ...]
    audio_encoder: str | None
    audio_options: tuple[str, ...]
    scale_filter: str | None
    output_frame_rate: float | None
    source: CompressionSourceMetadata


_SOURCE_AWARE_CRF: dict[CompressionLevel, int] = {
    CompressionLevel.LIGHT: 22,
    CompressionLevel.BALANCED: 27,
    CompressionLevel.STRONG: 30,
}

_SOURCE_AWARE_PRESET: dict[CompressionLevel, str] = {
    CompressionLevel.LIGHT: "slow",
    CompressionLevel.BALANCED: "slow",
    CompressionLevel.STRONG: "medium",
}

_MAX_HEIGHT: dict[CompressionLevel, int | None] = {
    CompressionLevel.LIGHT: None,
    CompressionLevel.BALANCED: 1080,
    CompressionLevel.STRONG: 720,
}

_MAX_FRAME_RATE: dict[CompressionLevel, float | None] = {
    CompressionLevel.LIGHT: None,
    CompressionLevel.BALANCED: None,
    CompressionLevel.STRONG: 30.0,
}

_AUDIO_BIT_RATE_KBPS: dict[CompressionLevel, int] = {
    CompressionLevel.LIGHT: 128,
    CompressionLevel.BALANCED: 96,
    CompressionLevel.STRONG: 80,
}

_HEVC_CONTAINERS = frozenset(
    {OutputContainer.MP4, OutputContainer.MOV, OutputContainer.MKV}
)


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


def resolve_compression_profile(
    container_profile: CompressionProfile,
    inspection: MediaInspection,
    spec: CompressionSpec,
) -> ResolvedCompressionProfile:
    validate_compression_input(inspection)
    video = inspection.video_streams[0]
    audio = inspection.audio_streams[0] if inspection.audio_streams else None
    source = CompressionSourceMetadata(
        container=container_profile.container,
        video_codec=video.codec_name,
        width=video.width,
        height=video.height,
        frame_rate=video.frame_rate,
        video_bit_rate=video.bit_rate,
        audio_codec=audio.codec_name if audio else None,
        audio_bit_rate=audio.bit_rate if audio else None,
        duration_seconds=inspection.format.duration_seconds,
    )

    if container_profile.container in _HEVC_CONTAINERS:
        video_encoder = (
            "libx264" if spec.level is CompressionLevel.LIGHT else "libx265"
        )
        quality_option = "-crf"
        quality_value = _SOURCE_AWARE_CRF[spec.level]
        video_options: tuple[str, ...] = (
            "-preset",
            _SOURCE_AWARE_PRESET[spec.level],
            "-pix_fmt",
            "yuv420p",
        )
        if (
            video_encoder == "libx265"
            and container_profile.container in {OutputContainer.MP4, OutputContainer.MOV}
        ):
            video_options += ("-tag:v", "hvc1")
        audio_encoder = "aac" if audio else None
        audio_options = _resolved_audio_options(audio, spec.level)
        apply_size_policy = True
    elif container_profile.container is OutputContainer.WEBM:
        video_encoder = "libvpx-vp9"
        quality_option = "-crf"
        quality_value = VP9_COMPRESSION_CRF[spec.level]
        video_options = ("-b:v", "0")
        audio_encoder = "libopus" if audio else None
        audio_options = _resolved_audio_options(audio, spec.level)
        apply_size_policy = True
    else:
        # AVI keeps its established MPEG-4/MP3 policy. HEVC, resizing, and FPS
        # limiting are intentionally not forced into this legacy container.
        video_encoder = container_profile.video_encoder
        quality_option = container_profile.quality_option
        quality_value = container_profile.quality_values[spec.level]
        video_options = container_profile.video_options
        audio_encoder = container_profile.audio_encoder if audio else None
        audio_options = container_profile.audio_options if audio else ()
        apply_size_policy = False

    max_height = _MAX_HEIGHT[spec.level] if apply_size_policy else None
    scale_filter = (
        f"scale=-2:{max_height}"
        if max_height is not None
        and source.height is not None
        and source.height > max_height
        else None
    )
    max_frame_rate = _MAX_FRAME_RATE[spec.level] if apply_size_policy else None
    output_frame_rate = (
        max_frame_rate
        if max_frame_rate is not None
        and source.frame_rate is not None
        and source.frame_rate > max_frame_rate
        else None
    )

    return ResolvedCompressionProfile(
        container=container_profile.container,
        extension=container_profile.extension,
        muxer=container_profile.muxer,
        video_encoder=video_encoder,
        quality_option=quality_option,
        quality_value=quality_value,
        video_options=video_options,
        audio_encoder=audio_encoder,
        audio_options=audio_options,
        scale_filter=scale_filter,
        output_frame_rate=output_frame_rate,
        source=source,
    )


def _resolved_audio_options(
    audio: AudioStreamMetadata | None,
    level: CompressionLevel,
) -> tuple[str, ...]:
    if audio is None:
        return ()
    target_kbps = _AUDIO_BIT_RATE_KBPS[level]
    source_bit_rate = audio.bit_rate
    if isinstance(source_bit_rate, int) and source_bit_rate > 0:
        target_kbps = min(target_kbps, max(32, round(source_bit_rate / 1000)))
    return ("-b:a", f"{target_kbps}k")


def build_compress_command(
    executable: str,
    input_path: Path,
    output_path: Path,
    spec: CompressionSpec,
    profile: CompressionProfile | ResolvedCompressionProfile | None = None,
) -> list[str]:
    selected_profile = profile or COMPRESSION_PROFILES[OutputContainer.MP4]
    if isinstance(selected_profile, ResolvedCompressionProfile):
        return _build_resolved_compress_command(
            executable,
            input_path,
            output_path,
            selected_profile,
        )
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


def _build_resolved_compress_command(
    executable: str,
    input_path: Path,
    output_path: Path,
    profile: ResolvedCompressionProfile,
) -> list[str]:
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
        profile.video_encoder,
        profile.quality_option,
        str(profile.quality_value),
        *profile.video_options,
    ]
    if profile.scale_filter is not None:
        command.extend(["-vf", profile.scale_filter])
    if profile.output_frame_rate is not None:
        command.extend(["-r", _format_frame_rate(profile.output_frame_rate)])
    if profile.audio_encoder is not None:
        command.extend(
            [
                "-map",
                "0:a:0?",
                "-c:a",
                profile.audio_encoder,
                *profile.audio_options,
            ]
        )
    command.extend(["-sn", "-dn", "-f", profile.muxer, str(output_path)])
    return command


def _format_frame_rate(frame_rate: float) -> str:
    return str(int(frame_rate)) if frame_rate.is_integer() else f"{frame_rate:g}"


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
        profile: CompressionProfile | ResolvedCompressionProfile | None = None,
    ) -> None:
        selected_profile = profile or self.resolve_profile(input_path, spec)
        self.runner.run(
            build_compress_command(
                self.executable,
                input_path,
                output_path,
                spec,
                selected_profile,
            )
        )

    def resolve_profile(
        self,
        input_path: Path,
        spec: CompressionSpec | None = None,
    ) -> ResolvedCompressionProfile:
        inspection = self.inspector.inspect(input_path)
        validate_compression_input(inspection)
        container_profile = detect_compression_profile(input_path, inspection)
        profile = resolve_compression_profile(
            container_profile,
            inspection,
            spec or CompressionSpec(),
        )
        self._validate_capabilities(profile)
        return profile

    def _validate_capabilities(self, profile: ResolvedCompressionProfile) -> None:
        if profile.muxer not in self.capabilities.muxers:
            raise FFmpegCapabilityError("The compression container is unavailable")
        if (
            profile.video_encoder not in self.capabilities.encoders
            or (
                profile.audio_encoder is not None
                and profile.audio_encoder not in self.capabilities.encoders
            )
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
