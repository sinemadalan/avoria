import math
import tempfile
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence

from backend.app.core.config import get_settings
from backend.app.processing.aspect_ratios import (
    OUTPUT_DIMENSIONS,
    OutputAspectRatio,
    nearest_output_aspect_ratio,
)
from backend.app.processing.compression import (
    CompressionLevel,
    CompressionProfile,
    UnsupportedCompressionContainerError,
    detect_compression_profile,
)
from backend.app.processing.conversion import (
    AudioCodec,
    ConversionSpec,
    FFmpegCapabilityError,
    FFmpegRunner,
    InvalidConversionError,
    LocalFFmpegCapabilities,
    VideoCodec,
    get_ffmpeg_capability_detector,
    validate_input_streams,
)
from backend.app.processing.probe import MediaInspection, SyncFFprobeInspector
from backend.app.processing.tools import get_media_tool_paths

MERGE_FRAME_RATE = 30
MERGE_PIXEL_FORMAT = "yuv420p"
MERGE_AUDIO_SAMPLE_RATE = 48_000
MERGE_AUDIO_CHANNELS = 2
MERGE_AUDIO_LAYOUT = "stereo"


class MergeTargetAspectRatio(str, Enum):
    LANDSCAPE = OutputAspectRatio.LANDSCAPE.value
    PORTRAIT = OutputAspectRatio.PORTRAIT.value
    SQUARE = OutputAspectRatio.SQUARE.value
    SOCIAL_PORTRAIT = OutputAspectRatio.SOCIAL_PORTRAIT.value
    FIRST_VIDEO = "first_video"


class InvalidMergeInputCountError(Exception):
    """Raised when fewer than two video inputs were supplied."""


class InvalidMergeTargetAspectRatioError(Exception):
    """Raised when the requested merge canvas is unsupported."""


class MergeMediaHasNoVideoError(Exception):
    """Raised when one selected input has no video stream."""


class InvalidMergeMetadataError(Exception):
    """Raised when required merge metadata is unavailable."""


class IncompatibleMergeVideosError(Exception):
    """Raised when video streams cannot be concatenated without transcoding."""


class IncompatibleMergeAudioError(Exception):
    """Raised when audio streams cannot be concatenated without transcoding."""


class UnsupportedMergeContainerError(Exception):
    """Raised when the selected output profile is unavailable."""


class MergeMediaNotFoundError(Exception):
    """Raised when a selected upload disappears before worker processing."""


@dataclass(frozen=True, slots=True)
class MergeVideosSpec:
    media_ids: tuple[str, ...]
    target_aspect_ratio: MergeTargetAspectRatio

    def __post_init__(self) -> None:
        if len(self.media_ids) < 2:
            raise InvalidMergeInputCountError
        try:
            ratio = MergeTargetAspectRatio(self.target_aspect_ratio)
        except (TypeError, ValueError) as exc:
            raise InvalidMergeTargetAspectRatioError from exc
        object.__setattr__(self, "target_aspect_ratio", ratio)

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "MergeVideosSpec":
        values = payload.get("media_ids")
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise InvalidMergeInputCountError
        try:
            ratio = MergeTargetAspectRatio(payload["target_aspect_ratio"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidMergeTargetAspectRatioError from exc
        return cls(tuple(values), ratio)

    def to_payload(self) -> dict[str, object]:
        return {
            "media_ids": list(self.media_ids),
            "target_aspect_ratio": self.target_aspect_ratio.value,
        }


@dataclass(frozen=True, slots=True)
class MergeVideosProfile:
    extension: str
    muxer: str
    container_profile: CompressionProfile
    has_audio: bool
    target_aspect_ratio: OutputAspectRatio = OutputAspectRatio.LANDSCAPE
    output_width: int = 1920
    output_height: int = 1080
    inspections: tuple[MediaInspection, ...] = ()
    direct_copy: bool = True


def resolve_target_aspect_ratio(
    requested: MergeTargetAspectRatio,
    first_inspection: MediaInspection,
) -> OutputAspectRatio:
    if requested is not MergeTargetAspectRatio.FIRST_VIDEO:
        return OutputAspectRatio(requested.value)
    if not first_inspection.video_streams:
        raise MergeMediaHasNoVideoError
    stream = first_inspection.video_streams[0]
    if stream.width is None or stream.height is None:
        raise InvalidMergeMetadataError
    try:
        return nearest_output_aspect_ratio(stream.width, stream.height)
    except ValueError as exc:
        raise InvalidMergeMetadataError from exc


def validate_merge_compatibility(
    input_paths: Sequence[Path],
    inspections: Sequence[MediaInspection],
) -> MergeVideosProfile:
    """Retain the Phase 3H.1 strict direct-copy compatibility check."""
    if len(input_paths) < 2 or len(input_paths) != len(inspections):
        raise InvalidMergeInputCountError
    profiles: list[CompressionProfile] = []
    for input_path, inspection in zip(input_paths, inspections, strict=True):
        if not inspection.video_streams:
            raise MergeMediaHasNoVideoError
        try:
            profiles.append(detect_compression_profile(input_path, inspection))
        except UnsupportedCompressionContainerError as exc:
            raise UnsupportedMergeContainerError from exc
    first_profile = profiles[0]
    if any(profile.container is not first_profile.container for profile in profiles[1:]):
        raise UnsupportedMergeContainerError
    first_video = inspections[0].video_streams[0]
    required_video = (
        first_video.codec_name,
        first_video.width,
        first_video.height,
        first_video.pixel_format,
        first_video.frame_rate,
    )
    if any(value is None for value in required_video):
        raise InvalidMergeMetadataError
    for inspection in inspections[1:]:
        video = inspection.video_streams[0]
        required = (
            video.codec_name,
            video.width,
            video.height,
            video.pixel_format,
            video.frame_rate,
        )
        if any(value is None for value in required):
            raise InvalidMergeMetadataError
        if required[:4] != required_video[:4] or not math.isclose(
            float(required[4]),
            float(required_video[4]),
            rel_tol=1e-6,
            abs_tol=1e-6,
        ):
            raise IncompatibleMergeVideosError
    audio_presence = [bool(item.audio_streams) for item in inspections]
    if any(item != audio_presence[0] for item in audio_presence[1:]):
        raise IncompatibleMergeAudioError
    if audio_presence[0]:
        first_audio = inspections[0].audio_streams[0]
        signature = (
            first_audio.codec_name,
            first_audio.sample_rate,
            first_audio.channels,
            first_audio.channel_layout,
        )
        if any(value is None for value in signature):
            raise InvalidMergeMetadataError
        for inspection in inspections[1:]:
            audio = inspection.audio_streams[0]
            current = (audio.codec_name, audio.sample_rate, audio.channels, audio.channel_layout)
            if any(value is None for value in current):
                raise InvalidMergeMetadataError
            if current != signature:
                raise IncompatibleMergeAudioError
    copy_spec = ConversionSpec(
        container=first_profile.container,
        video_codec=VideoCodec.COPY,
        audio_codec=AudioCodec.COPY if audio_presence[0] else AudioCodec.NONE,
    )
    try:
        for inspection in inspections:
            validate_input_streams(copy_spec, inspection)
    except InvalidConversionError as exc:
        raise UnsupportedMergeContainerError from exc
    width, height = int(required_video[1]), int(required_video[2])
    return MergeVideosProfile(
        first_profile.extension,
        first_profile.muxer,
        first_profile,
        audio_presence[0],
        nearest_output_aspect_ratio(width, height),
        width,
        height,
        tuple(inspections),
        True,
    )


def _can_direct_copy(
    input_paths: Sequence[Path],
    inspections: Sequence[MediaInspection],
    target: OutputAspectRatio,
) -> bool:
    try:
        direct = validate_merge_compatibility(input_paths, inspections)
    except (
        InvalidMergeMetadataError,
        IncompatibleMergeAudioError,
        IncompatibleMergeVideosError,
        UnsupportedMergeContainerError,
    ):
        return False
    video = inspections[0].video_streams[0]
    video_ready = (
        (video.width, video.height) == OUTPUT_DIMENSIONS[target]
        and video.pixel_format == MERGE_PIXEL_FORMAT
        and video.frame_rate is not None
        and math.isclose(video.frame_rate, MERGE_FRAME_RATE, abs_tol=1e-6)
    )
    if not video_ready or not direct.has_audio:
        return video_ready and not direct.has_audio
    audio = inspections[0].audio_streams[0]
    return (
        audio.sample_rate == MERGE_AUDIO_SAMPLE_RATE
        and audio.channels == MERGE_AUDIO_CHANNELS
        and audio.channel_layout == MERGE_AUDIO_LAYOUT
    )


def escape_concat_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "'\\''")


def build_concat_manifest(input_paths: Sequence[Path]) -> str:
    return "".join(f"file '{escape_concat_path(path)}'\n" for path in input_paths)


def build_merge_command(
    executable: str,
    manifest_path: Path,
    output_path: Path,
    profile: MergeVideosProfile,
) -> list[str]:
    command = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(manifest_path),
        "-map",
        "0:v:0",
    ]
    if profile.has_audio:
        command.extend(["-map", "0:a:0"])
    command.extend(["-c", "copy", "-sn", "-dn", "-f", profile.muxer, str(output_path)])
    return command


def build_normalize_command(
    executable: str,
    input_path: Path,
    output_path: Path,
    profile: MergeVideosProfile,
    source_has_audio: bool,
    duration_seconds: float | None = None,
) -> list[str]:
    media = profile.container_profile
    command = [executable, "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_path)]
    if profile.has_audio and not source_has_audio:
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r={MERGE_AUDIO_SAMPLE_RATE}:cl={MERGE_AUDIO_LAYOUT}",
            ]
        )
    video_filter = (
        f"scale={profile.output_width}:{profile.output_height}:"
        "force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"pad={profile.output_width}:{profile.output_height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,fps={MERGE_FRAME_RATE},format={MERGE_PIXEL_FORMAT},setpts=PTS-STARTPTS"
    )
    command.extend(
        [
            "-map",
            "0:v:0",
            "-vf",
            video_filter,
            "-c:v",
            media.video_encoder,
            media.quality_option,
            str(media.quality_values[CompressionLevel.BALANCED]),
            *media.video_options,
        ]
    )
    if profile.has_audio:
        command.extend(["-map", "0:a:0" if source_has_audio else "1:a:0"])
        if source_has_audio:
            command.extend(["-af", "aresample=48000:async=1:first_pts=0,apad,asetpts=PTS-STARTPTS"])
        command.extend(
            [
                "-c:a",
                media.audio_encoder,
                *media.audio_options,
                "-ar",
                str(MERGE_AUDIO_SAMPLE_RATE),
                "-ac",
                str(MERGE_AUDIO_CHANNELS),
                "-shortest",
            ]
        )
    else:
        command.append("-an")
    if media.muxer in {"mp4", "mov"}:
        command.extend(["-video_track_timescale", "90000"])
    if duration_seconds is not None and duration_seconds > 0:
        command.extend(["-t", str(duration_seconds)])
    command.extend(["-sn", "-dn", "-f", media.muxer, str(output_path)])
    return command


class MergeVideosService:
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

    def resolve_profile(
        self,
        input_paths: Sequence[Path],
        spec: MergeVideosSpec,
    ) -> MergeVideosProfile:
        if len(input_paths) < 2:
            raise InvalidMergeInputCountError
        inspections = [self.inspector.inspect(path) for path in input_paths]
        profiles: list[CompressionProfile] = []
        for path, inspection in zip(input_paths, inspections, strict=True):
            if not inspection.video_streams:
                raise MergeMediaHasNoVideoError
            stream = inspection.video_streams[0]
            if (
                stream.width is None
                or stream.height is None
                or stream.width <= 0
                or stream.height <= 0
            ):
                raise InvalidMergeMetadataError
            try:
                profiles.append(detect_compression_profile(path, inspection))
            except UnsupportedCompressionContainerError as exc:
                raise UnsupportedMergeContainerError from exc
        output_media = profiles[0]
        target = resolve_target_aspect_ratio(spec.target_aspect_ratio, inspections[0])
        width, height = OUTPUT_DIMENSIONS[target]
        direct_copy = _can_direct_copy(input_paths, inspections, target)
        has_audio = any(inspection.audio_streams for inspection in inspections)
        if output_media.muxer not in self.capabilities.muxers:
            raise UnsupportedMergeContainerError
        if not direct_copy and (
            output_media.video_encoder not in self.capabilities.encoders
            or (
                has_audio
                and output_media.audio_encoder not in self.capabilities.encoders
            )
        ):
            raise FFmpegCapabilityError("A merge normalization encoder is unavailable")
        return MergeVideosProfile(
            output_media.extension,
            output_media.muxer,
            output_media,
            has_audio,
            target,
            width,
            height,
            tuple(inspections),
            direct_copy,
        )

    def merge(
        self,
        input_paths: Sequence[Path],
        output_path: Path,
        profile: MergeVideosProfile,
    ) -> None:
        if profile.direct_copy:
            self._concat(input_paths, output_path, profile)
            return
        normalized: list[Path] = []
        try:
            for source, inspection in zip(input_paths, profile.inspections, strict=True):
                with tempfile.NamedTemporaryFile(
                    prefix=f".{output_path.stem}.",
                    suffix=f".normalized.{profile.extension}",
                    dir=output_path.parent,
                    delete=False,
                ) as temporary:
                    normalized_path = Path(temporary.name)
                normalized.append(normalized_path)
                self.runner.run(
                    build_normalize_command(
                        self.executable,
                        source,
                        normalized_path,
                        profile,
                        bool(inspection.audio_streams),
                        inspection.format.duration_seconds,
                    )
                )
            self._concat(normalized, output_path, profile)
        finally:
            for path in normalized:
                path.unlink(missing_ok=True)

    def _concat(
        self,
        input_paths: Sequence[Path],
        output_path: Path,
        profile: MergeVideosProfile,
    ) -> None:
        manifest_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{output_path.stem}.",
                suffix=".concat.txt",
                dir=output_path.parent,
                delete=False,
            ) as manifest:
                manifest.write(build_concat_manifest(input_paths))
                manifest_path = Path(manifest.name)
            self.runner.run(
                build_merge_command(
                    self.executable,
                    manifest_path,
                    output_path,
                    profile,
                )
            )
        finally:
            if manifest_path is not None:
                manifest_path.unlink(missing_ok=True)


@lru_cache
def get_merge_videos_service() -> MergeVideosService:
    settings = get_settings()
    from backend.app.processing.probe import get_sync_ffprobe_inspector
    return MergeVideosService(
        get_media_tool_paths().ffmpeg,
        get_sync_ffprobe_inspector(),
        FFmpegRunner(settings.ffmpeg_timeout_seconds),
        get_ffmpeg_capability_detector().detect(),
    )
