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

MINIMUM_SPEED = 0.25
MAXIMUM_SPEED = 4.0


class InvalidSpeedError(Exception):
    """Raised when a speed factor is missing, mistyped, or out of range."""


class MediaHasNoSpeedStreamError(Exception):
    """Raised when media contains neither a video nor an audio stream."""


@dataclass(frozen=True, slots=True)
class SpeedSpec:
    speed: float

    def __post_init__(self) -> None:
        value = self.speed
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not MINIMUM_SPEED <= value <= MAXIMUM_SPEED
        ):
            raise InvalidSpeedError("Speed must be a finite number from 0.25 to 4.0")

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "SpeedSpec":
        if "speed" not in payload:
            raise InvalidSpeedError("Speed is required")
        return cls(payload["speed"])

    def to_payload(self) -> dict[str, float]:
        return {"speed": float(self.speed)}


@dataclass(frozen=True, slots=True)
class SpeedProfile:
    extension: str
    muxer: str
    video: CompressionProfile | None
    audio: AudioExtractionProfile | None
    audio_stream_count: int


def validate_speed_input(inspection: MediaInspection) -> None:
    if not inspection.video_streams and not inspection.audio_streams:
        raise MediaHasNoSpeedStreamError("Media has no audio/video stream")


def resolve_speed_profile(
    input_path: Path,
    inspection: MediaInspection,
) -> SpeedProfile:
    validate_speed_input(inspection)
    if inspection.video_streams:
        video = detect_compression_profile(input_path, inspection)
        return SpeedProfile(
            video.extension,
            video.muxer,
            video,
            None,
            len(inspection.audio_streams),
        )
    audio = detect_audio_profile(input_path, inspection)
    return SpeedProfile(
        audio.extension,
        audio.muxer,
        None,
        audio,
        len(inspection.audio_streams),
    )


def _factor(value: float) -> str:
    return format(value, ".15g")


def build_video_speed_filter(speed: float) -> str:
    spec = SpeedSpec(speed)
    return f"setpts=PTS/{_factor(spec.speed)}"


def build_atempo_filter(speed: float) -> str:
    value = float(SpeedSpec(speed).speed)
    factors: list[float] = []
    while value < 0.5:
        factors.append(0.5)
        value /= 0.5
    while value > 2.0:
        factors.append(2.0)
        value /= 2.0
    factors.append(value)
    return ",".join(f"atempo={_factor(factor)}" for factor in factors)


def build_speed_command(
    executable: str,
    input_path: Path,
    output_path: Path,
    spec: SpeedSpec,
    profile: SpeedProfile,
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
        video = profile.video
        command.extend(
            [
                "-map",
                "0:v:0",
                "-vf",
                build_video_speed_filter(spec.speed),
                "-c:v",
                video.video_encoder,
                video.quality_option,
                str(video.quality_values[CompressionLevel.BALANCED]),
                *video.video_options,
            ]
        )
    else:
        command.append("-vn")

    if profile.audio_stream_count:
        tempo = build_atempo_filter(spec.speed)
        for index in range(profile.audio_stream_count):
            command.extend(["-map", f"0:a:{index}", f"-filter:a:{index}", tempo])
        if profile.video is not None:
            command.extend(
                ["-c:a", profile.video.audio_encoder, *profile.video.audio_options]
            )
        else:
            audio = profile.audio
            if audio is None:
                raise RuntimeError("Speed audio profile is unavailable")
            command.extend(["-c:a", audio.encoder, *audio.encoder_options])
    else:
        command.append("-an")

    command.extend(["-sn", "-dn", "-f", profile.muxer, str(output_path)])
    return command


class SpeedService:
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

    def resolve_profile(self, input_path: Path) -> SpeedProfile:
        inspection = self.inspector.inspect(input_path)
        profile = resolve_speed_profile(input_path, inspection)
        encoders: set[str] = set()
        if profile.video is not None:
            encoders.add(profile.video.video_encoder)
            if profile.audio_stream_count:
                encoders.add(profile.video.audio_encoder)
        elif profile.audio is not None:
            encoders.add(profile.audio.encoder)
        if profile.muxer not in self.capabilities.muxers:
            raise FFmpegCapabilityError("The speed output container is unavailable")
        if not encoders.issubset(self.capabilities.encoders):
            raise FFmpegCapabilityError("A speed encoder is unavailable")
        return profile

    def change_speed(
        self,
        input_path: Path,
        output_path: Path,
        spec: SpeedSpec,
        profile: SpeedProfile | None = None,
    ) -> None:
        selected_profile = profile or self.resolve_profile(input_path)
        self.runner.run(
            build_speed_command(
                self.executable,
                input_path,
                output_path,
                spec,
                selected_profile,
            )
        )


@lru_cache
def get_speed_service() -> SpeedService:
    settings = get_settings()
    from backend.app.processing.probe import get_sync_ffprobe_inspector

    return SpeedService(
        executable=get_media_tool_paths().ffmpeg,
        inspector=get_sync_ffprobe_inspector(),
        runner=FFmpegRunner(settings.ffmpeg_timeout_seconds),
        capabilities=get_ffmpeg_capability_detector().detect(),
    )
