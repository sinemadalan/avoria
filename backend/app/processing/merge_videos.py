import math
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence

from backend.app.core.config import get_settings
from backend.app.processing.compression import (
    CompressionProfile,
    UnsupportedCompressionContainerError,
    detect_compression_profile,
)
from backend.app.processing.conversion import (
    AudioCodec,
    ConversionSpec,
    FFmpegRunner,
    InvalidConversionError,
    LocalFFmpegCapabilities,
    VideoCodec,
    get_ffmpeg_capability_detector,
    validate_input_streams,
)
from backend.app.processing.probe import MediaInspection, SyncFFprobeInspector
from backend.app.processing.tools import get_media_tool_paths


class InvalidMergeInputCountError(Exception):
    """Raised when fewer than two video inputs were supplied."""


class MergeMediaHasNoVideoError(Exception):
    """Raised when one selected input has no video stream."""


class InvalidMergeMetadataError(Exception):
    """Raised when direct merge requirements cannot be established safely."""


class IncompatibleMergeVideosError(Exception):
    """Raised when video streams cannot be concatenated without transcoding."""


class IncompatibleMergeAudioError(Exception):
    """Raised when audio presence or stream parameters differ."""


class UnsupportedMergeContainerError(Exception):
    """Raised when the input container cannot be used for direct concat."""


class MergeMediaNotFoundError(Exception):
    """Raised when a selected upload disappears before worker processing."""


@dataclass(frozen=True, slots=True)
class MergeVideosSpec:
    media_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.media_ids) < 2:
            raise InvalidMergeInputCountError

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "MergeVideosSpec":
        values = payload.get("media_ids")
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise InvalidMergeInputCountError
        return cls(tuple(values))

    def to_payload(self) -> dict[str, list[str]]:
        return {"media_ids": list(self.media_ids)}


@dataclass(frozen=True, slots=True)
class MergeVideosProfile:
    extension: str
    muxer: str
    container_profile: CompressionProfile
    has_audio: bool


def validate_merge_compatibility(
    input_paths: Sequence[Path],
    inspections: Sequence[MediaInspection],
) -> MergeVideosProfile:
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

    first = inspections[0]
    first_video = first.video_streams[0]
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
            float(required[4]), float(required_video[4]), rel_tol=1e-6, abs_tol=1e-6
        ):
            raise IncompatibleMergeVideosError

    audio_presence = [bool(item.audio_streams) for item in inspections]
    if any(item != audio_presence[0] for item in audio_presence[1:]):
        raise IncompatibleMergeAudioError
    if audio_presence[0]:
        first_audio = first.audio_streams[0]
        audio_signature = (
            first_audio.codec_name,
            first_audio.sample_rate,
            first_audio.channels,
            first_audio.channel_layout,
        )
        if any(value is None for value in audio_signature):
            raise InvalidMergeMetadataError
        for inspection in inspections[1:]:
            audio = inspection.audio_streams[0]
            signature = (
                audio.codec_name,
                audio.sample_rate,
                audio.channels,
                audio.channel_layout,
            )
            if any(value is None for value in signature):
                raise InvalidMergeMetadataError
            if signature != audio_signature:
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

    return MergeVideosProfile(
        extension=first_profile.extension,
        muxer=first_profile.muxer,
        container_profile=first_profile,
        has_audio=audio_presence[0],
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

    def resolve_profile(self, input_paths: Sequence[Path]) -> MergeVideosProfile:
        inspections = [self.inspector.inspect(path) for path in input_paths]
        profile = validate_merge_compatibility(input_paths, inspections)
        if profile.muxer not in self.capabilities.muxers:
            raise UnsupportedMergeContainerError
        return profile

    def merge(
        self,
        input_paths: Sequence[Path],
        output_path: Path,
        profile: MergeVideosProfile | None = None,
    ) -> None:
        selected_profile = profile or self.resolve_profile(input_paths)
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
                    selected_profile,
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
        executable=get_media_tool_paths().ffmpeg,
        inspector=get_sync_ffprobe_inspector(),
        runner=FFmpegRunner(settings.ffmpeg_timeout_seconds),
        capabilities=get_ffmpeg_capability_detector().detect(),
    )
