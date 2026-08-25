import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from backend.app.core.config import get_settings
from backend.app.processing.audio_extraction import (
    AudioExtractionProfile,
    detect_audio_profile,
)
from backend.app.processing.compression import (
    COMPRESSION_PROFILES,
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

# FFprobe container durations can differ from their nominal endpoint by a fraction
# of a millisecond. This tolerance is only used for the upper endpoint comparison.
TRIM_DURATION_TOLERANCE_SECONDS = 0.001
# Keep millisecond-scale edits valid while rejecting effectively empty ranges.
MINIMUM_TRIM_DURATION_SECONDS = 0.001


class InvalidTrimRangeError(Exception):
    """Raised when trim timestamps do not form a usable finite range."""


class TrimStartExceedsDurationError(Exception):
    """Raised when the requested start is outside the media timeline."""


class TrimEndExceedsDurationError(Exception):
    """Raised when the requested end is outside the media timeline."""


class MediaHasNoTrimStreamError(Exception):
    """Raised when media contains neither a video nor an audio stream."""


class InvalidTrimMediaDurationError(Exception):
    """Raised when FFprobe does not provide a usable media duration."""


@dataclass(frozen=True, slots=True)
class TrimSpec:
    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        for value in (self.start_seconds, self.end_seconds):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InvalidTrimRangeError("Trim timestamps must be finite numbers")
            if not math.isfinite(value):
                raise InvalidTrimRangeError("Trim timestamps must be finite numbers")
        if self.start_seconds < 0 or self.end_seconds <= 0:
            raise InvalidTrimRangeError("Invalid trim range")
        if (
            self.end_seconds - self.start_seconds + 1e-12
            < MINIMUM_TRIM_DURATION_SECONDS
        ):
            raise InvalidTrimRangeError("Invalid trim range")

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "TrimSpec":
        if "start_seconds" not in payload or "end_seconds" not in payload:
            raise InvalidTrimRangeError("Both trim timestamps are required")
        return cls(payload["start_seconds"], payload["end_seconds"])

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    def to_payload(self) -> dict[str, float]:
        return {
            "start_seconds": float(self.start_seconds),
            "end_seconds": float(self.end_seconds),
        }


@dataclass(frozen=True, slots=True)
class TrimProfile:
    extension: str
    muxer: str
    video: CompressionProfile | None
    audio: AudioExtractionProfile | None
    has_audio: bool = True


def validate_trim_input(inspection: MediaInspection, spec: TrimSpec) -> None:
    if not inspection.video_streams and not inspection.audio_streams:
        raise MediaHasNoTrimStreamError("Media has no audio/video stream")
    duration = inspection.format.duration_seconds
    if duration is None or not math.isfinite(duration) or duration <= 0:
        raise InvalidTrimMediaDurationError("Media inspection failed")
    if spec.start_seconds >= duration:
        raise TrimStartExceedsDurationError("Start exceeds media duration")
    if spec.end_seconds > duration + TRIM_DURATION_TOLERANCE_SECONDS:
        raise TrimEndExceedsDurationError("End exceeds media duration")


def resolve_trim_profile(
    input_path: Path,
    inspection: MediaInspection,
) -> TrimProfile:
    if inspection.video_streams:
        profile = detect_compression_profile(input_path, inspection)
        return TrimProfile(
            profile.extension,
            profile.muxer,
            profile,
            None,
            bool(inspection.audio_streams),
        )
    audio_profile = detect_audio_profile(input_path, inspection)
    return TrimProfile(
        audio_profile.extension,
        audio_profile.muxer,
        None,
        audio_profile,
    )


def _timestamp(value: float) -> str:
    return format(value, ".15g")


def build_trim_command(
    executable: str,
    input_path: Path,
    output_path: Path,
    spec: TrimSpec,
    profile: TrimProfile,
) -> list[str]:
    # Output-side seek decodes through the requested point instead of snapping to
    # an earlier keyframe. Re-encoding makes the first emitted frame accurate.
    command = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-ss",
        _timestamp(spec.start_seconds),
        "-t",
        _timestamp(spec.duration_seconds),
    ]
    if profile.video is not None:
        video = profile.video
        command.extend(
            [
                "-map",
                "0:v:0",
                "-c:v",
                video.video_encoder,
                video.quality_option,
                str(video.quality_values[CompressionLevel.BALANCED]),
                *video.video_options,
                "-map",
                "0:a?",
                "-c:a",
                video.audio_encoder,
                *video.audio_options,
            ]
        )
    else:
        audio = profile.audio
        if audio is None:
            raise RuntimeError("Trim audio profile is unavailable")
        command.extend(
            [
                "-vn",
                "-map",
                "0:a",
                "-c:a",
                audio.encoder,
                *audio.encoder_options,
            ]
        )
    command.extend(
        [
            "-sn",
            "-dn",
            # Output-side seek already rebases the presentation timeline. Keeping
            # the muxer's native timestamp handling avoids extending MKV/WebM by
            # an encoder's decode delay while their public start remains zero.
            "-avoid_negative_ts",
            "disabled",
            "-f",
            profile.muxer,
            str(output_path),
        ]
    )
    return command


class TrimService:
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

    def resolve_profile(self, input_path: Path, spec: TrimSpec) -> TrimProfile:
        inspection = self.inspector.inspect(input_path)
        validate_trim_input(inspection, spec)
        profile = resolve_trim_profile(input_path, inspection)
        encoders = set()
        if profile.video is not None:
            encoders.add(profile.video.video_encoder)
            if profile.has_audio:
                encoders.add(profile.video.audio_encoder)
        elif profile.audio is not None:
            encoders.add(profile.audio.encoder)
        if profile.muxer not in self.capabilities.muxers:
            raise FFmpegCapabilityError("The trim output container is unavailable")
        if not encoders.issubset(self.capabilities.encoders):
            raise FFmpegCapabilityError("A trim encoder is unavailable")
        return profile

    def trim(
        self,
        input_path: Path,
        output_path: Path,
        spec: TrimSpec,
        profile: TrimProfile | None = None,
    ) -> None:
        selected_profile = profile or self.resolve_profile(input_path, spec)
        self.runner.run(
            build_trim_command(
                self.executable,
                input_path,
                output_path,
                spec,
                selected_profile,
            )
        )


@lru_cache
def get_trim_service() -> TrimService:
    settings = get_settings()
    from backend.app.processing.probe import get_sync_ffprobe_inspector

    return TrimService(
        executable=get_media_tool_paths().ffmpeg,
        inspector=get_sync_ffprobe_inspector(),
        runner=FFmpegRunner(settings.ffmpeg_timeout_seconds),
        capabilities=get_ffmpeg_capability_detector().detect(),
    )
