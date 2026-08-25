from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from backend.app.core.config import get_settings
from backend.app.processing.conversion import FFmpegRunner
from backend.app.processing.probe import MediaInspection, SyncFFprobeInspector
from backend.app.processing.tools import get_media_tool_paths


class AudioExtractionFormat(str, Enum):
    MP3 = "mp3"
    WAV = "wav"
    FLAC = "flac"
    M4A = "m4a"
    OPUS = "opus"
    OGG = "ogg"


@dataclass(frozen=True, slots=True)
class AudioExtractionProfile:
    format: AudioExtractionFormat
    extension: str
    encoder: str
    muxer: str
    encoder_options: tuple[str, ...] = ()


AUDIO_EXTRACTION_PROFILES: dict[
    AudioExtractionFormat, AudioExtractionProfile
] = {
    AudioExtractionFormat.MP3: AudioExtractionProfile(
        format=AudioExtractionFormat.MP3,
        extension="mp3",
        encoder="libmp3lame",
        muxer="mp3",
        encoder_options=("-q:a", "2"),
    ),
    AudioExtractionFormat.WAV: AudioExtractionProfile(
        format=AudioExtractionFormat.WAV,
        extension="wav",
        encoder="pcm_s16le",
        muxer="wav",
    ),
    AudioExtractionFormat.FLAC: AudioExtractionProfile(
        format=AudioExtractionFormat.FLAC,
        extension="flac",
        encoder="flac",
        muxer="flac",
    ),
    AudioExtractionFormat.M4A: AudioExtractionProfile(
        format=AudioExtractionFormat.M4A,
        extension="m4a",
        encoder="aac",
        muxer="ipod",
        encoder_options=("-b:a", "192k"),
    ),
    AudioExtractionFormat.OPUS: AudioExtractionProfile(
        format=AudioExtractionFormat.OPUS,
        extension="opus",
        encoder="libopus",
        muxer="ogg",
        encoder_options=("-b:a", "128k"),
    ),
    AudioExtractionFormat.OGG: AudioExtractionProfile(
        format=AudioExtractionFormat.OGG,
        extension="ogg",
        encoder="libvorbis",
        muxer="ogg",
        encoder_options=("-q:a", "5"),
    ),
}


@dataclass(frozen=True, slots=True)
class AudioExtractionSpec:
    format: AudioExtractionFormat = AudioExtractionFormat.MP3

    @classmethod
    def from_payload(cls, payload: Mapping[str, str]) -> "AudioExtractionSpec":
        return cls(
            format=AudioExtractionFormat(
                payload.get("format", AudioExtractionFormat.MP3)
            )
        )

    def to_payload(self) -> dict[str, str]:
        return {"format": self.format.value}


class MediaHasNoAudioError(Exception):
    """Raised when extraction input does not contain an audio stream."""


class UnsupportedAudioContainerError(Exception):
    """Raised when a source audio container cannot be preserved."""


def detect_audio_profile(
    input_path: Path,
    inspection: MediaInspection,
) -> AudioExtractionProfile:
    """Resolve a source-preserving audio profile from the central profile registry."""
    extension = input_path.suffix.casefold().removeprefix(".")
    profile = next(
        (
            candidate
            for candidate in AUDIO_EXTRACTION_PROFILES.values()
            if candidate.extension == extension
        ),
        None,
    )
    format_names = {
        item.strip().casefold()
        for item in (inspection.format.name or "").split(",")
        if item.strip()
    }
    if profile is None or not _audio_format_matches(profile, format_names):
        raise UnsupportedAudioContainerError
    return profile


def _audio_format_matches(
    profile: AudioExtractionProfile,
    format_names: set[str],
) -> bool:
    if profile.format is AudioExtractionFormat.M4A:
        return bool(format_names.intersection({"mov", "mp4", "m4a"}))
    if profile.format in {AudioExtractionFormat.OPUS, AudioExtractionFormat.OGG}:
        return bool(format_names.intersection({"ogg", "opus"}))
    return profile.muxer in format_names or profile.extension in format_names


def validate_audio_stream(inspection: MediaInspection) -> None:
    if not inspection.audio_streams:
        raise MediaHasNoAudioError("The input does not contain an audio stream")


def build_extract_audio_command(
    executable: str,
    input_path: Path,
    output_path: Path,
    spec: AudioExtractionSpec | None = None,
    profile: AudioExtractionProfile | None = None,
) -> list[str]:
    selected_spec = spec or AudioExtractionSpec()
    selected_profile = profile or AUDIO_EXTRACTION_PROFILES[selected_spec.format]
    return [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-map",
        "0:a:0",
        "-c:a",
        selected_profile.encoder,
        *selected_profile.encoder_options,
        "-sn",
        "-dn",
        "-f",
        selected_profile.muxer,
        str(output_path),
    ]


class AudioExtractionService:
    def __init__(
        self,
        executable: str,
        inspector: SyncFFprobeInspector,
        runner: FFmpegRunner,
    ) -> None:
        self.executable = executable
        self.inspector = inspector
        self.runner = runner

    def resolve_profile(self, spec: AudioExtractionSpec) -> AudioExtractionProfile:
        return AUDIO_EXTRACTION_PROFILES[spec.format]

    def extract(
        self,
        input_path: Path,
        output_path: Path,
        spec: AudioExtractionSpec,
        profile: AudioExtractionProfile | None = None,
    ) -> None:
        inspection = self.inspector.inspect(input_path)
        validate_audio_stream(inspection)
        selected_profile = profile or self.resolve_profile(spec)
        self.runner.run(
            build_extract_audio_command(
                self.executable,
                input_path,
                output_path,
                spec,
                selected_profile,
            )
        )


@lru_cache
def get_audio_extraction_service() -> AudioExtractionService:
    settings = get_settings()
    from backend.app.processing.probe import get_sync_ffprobe_inspector

    return AudioExtractionService(
        executable=get_media_tool_paths().ffmpeg,
        inspector=get_sync_ffprobe_inspector(),
        runner=FFmpegRunner(settings.ffmpeg_timeout_seconds),
    )
