from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

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
from backend.app.processing.probe import (
    FFprobeExecutableNotFoundError,
    FFprobeProcessError,
    FFprobeTimeoutError,
    InvalidMediaError,
    MediaInspection,
    SyncFFprobeInspector,
)
from backend.app.processing.tools import get_media_tool_paths


class ReplaceAudioTargetHasNoVideoError(Exception):
    """Raised when the target media is not a video."""


class ExternalMediaHasVideoError(Exception):
    """Raised when the external source is not audio-only."""


class InvalidReplaceAudioDurationError(Exception):
    """Raised when the target video has no usable duration."""


class TargetMediaInspectionError(Exception):
    """Raised when FFprobe cannot inspect the target video."""


class ExternalAudioInspectionError(Exception):
    """Raised when FFprobe cannot inspect the external audio."""


class ExternalAudioMediaNotFoundError(Exception):
    """Raised when the external audio media ID cannot be resolved."""


_INSPECTION_ERRORS = (
    InvalidMediaError,
    FFprobeExecutableNotFoundError,
    FFprobeProcessError,
    FFprobeTimeoutError,
)


@dataclass(frozen=True, slots=True)
class ReplaceAudioSpec:
    audio_media_id: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, str]) -> "ReplaceAudioSpec":
        return cls(audio_media_id=payload["audio_media_id"])

    def to_payload(self) -> dict[str, str]:
        return {"audio_media_id": self.audio_media_id}


@dataclass(frozen=True, slots=True)
class ReplaceAudioProfile:
    target: CompressionProfile
    external_audio: AudioExtractionProfile
    target_duration_seconds: float


def validate_replace_audio_target(inspection: MediaInspection) -> float:
    if not inspection.video_streams:
        raise ReplaceAudioTargetHasNoVideoError
    duration = inspection.format.duration_seconds
    if duration is None or duration <= 0:
        raise InvalidReplaceAudioDurationError
    return duration


def validate_external_audio(inspection: MediaInspection) -> None:
    if inspection.video_streams:
        raise ExternalMediaHasVideoError
    if not inspection.audio_streams:
        raise MediaHasNoAudioError("The external media does not contain audio")


def build_replace_audio_command(
    executable: str,
    target_path: Path,
    audio_path: Path,
    output_path: Path,
    profile: ReplaceAudioProfile,
) -> list[str]:
    # Phase 3E deliberately selects only the first external audio stream. A
    # future optional looping policy can change the audio input/filter setup
    # without changing the target-video mapping or container profile.
    return [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(target_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-map",
        "1:a:0",
        "-af",
        "apad",
        "-c:a",
        profile.target.audio_encoder,
        *profile.target.audio_options,
        "-t",
        _format_duration(profile.target_duration_seconds),
        "-sn",
        "-dn",
        "-map_metadata",
        "0",
        "-map_metadata:s:a",
        "-1",
        "-f",
        profile.target.muxer,
        str(output_path),
    ]


class ReplaceAudioService:
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
        target_path: Path,
        audio_path: Path,
    ) -> ReplaceAudioProfile:
        try:
            target_inspection = self.inspector.inspect(target_path)
        except _INSPECTION_ERRORS as exc:
            raise TargetMediaInspectionError from exc
        duration = validate_replace_audio_target(target_inspection)
        target_profile = detect_compression_profile(target_path, target_inspection)

        try:
            audio_inspection = self.inspector.inspect(audio_path)
        except _INSPECTION_ERRORS as exc:
            raise ExternalAudioInspectionError from exc
        validate_external_audio(audio_inspection)
        audio_profile = detect_audio_profile(audio_path, audio_inspection)

        if target_profile.muxer not in self.capabilities.muxers:
            raise FFmpegCapabilityError("The replace-audio container is unavailable")
        if target_profile.audio_encoder not in self.capabilities.encoders:
            raise FFmpegCapabilityError("The replace-audio encoder is unavailable")
        return ReplaceAudioProfile(
            target=target_profile,
            external_audio=audio_profile,
            target_duration_seconds=duration,
        )

    def replace(
        self,
        target_path: Path,
        audio_path: Path,
        output_path: Path,
        profile: ReplaceAudioProfile | None = None,
    ) -> None:
        selected_profile = profile or self.resolve_profile(target_path, audio_path)
        self.runner.run(
            build_replace_audio_command(
                self.executable,
                target_path,
                audio_path,
                output_path,
                selected_profile,
            )
        )


def _format_duration(value: float) -> str:
    return format(value, ".9g")


@lru_cache
def get_replace_audio_service() -> ReplaceAudioService:
    settings = get_settings()
    from backend.app.processing.probe import get_sync_ffprobe_inspector

    return ReplaceAudioService(
        executable=get_media_tool_paths().ffmpeg,
        inspector=get_sync_ffprobe_inspector(),
        runner=FFmpegRunner(settings.ffmpeg_timeout_seconds),
        capabilities=get_ffmpeg_capability_detector().detect(),
    )
