import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Iterable, Mapping

from backend.app.core.config import get_settings
from backend.app.processing.probe import MediaInspection, SyncFFprobeInspector
from backend.app.processing.tools import get_media_tool_paths


class OutputContainer(str, Enum):
    MP4 = "mp4"
    MKV = "mkv"
    WEBM = "webm"
    MOV = "mov"
    AVI = "avi"
    MP3 = "mp3"
    WAV = "wav"
    FLAC = "flac"
    OGG = "ogg"
    M4A = "m4a"
    OPUS = "opus"


class VideoCodec(str, Enum):
    H264 = "h264"
    H265 = "h265"
    VP9 = "vp9"
    AV1 = "av1"
    COPY = "copy"
    NONE = "none"


class AudioCodec(str, Enum):
    AAC = "aac"
    MP3 = "mp3"
    OPUS = "opus"
    VORBIS = "vorbis"
    FLAC = "flac"
    PCM = "pcm"
    COPY = "copy"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ConversionSpec:
    container: OutputContainer = OutputContainer.MP4
    video_codec: VideoCodec = VideoCodec.H264
    audio_codec: AudioCodec = AudioCodec.AAC

    @classmethod
    def from_payload(cls, payload: Mapping[str, str]) -> "ConversionSpec":
        spec = cls(
            container=OutputContainer(payload.get("container", OutputContainer.MP4)),
            video_codec=VideoCodec(payload.get("video_codec", VideoCodec.H264)),
            audio_codec=AudioCodec(payload.get("audio_codec", AudioCodec.AAC)),
        )
        validate_compatibility(spec)
        return spec

    def to_payload(self) -> dict[str, str]:
        return {
            "container": self.container.value,
            "video_codec": self.video_codec.value,
            "audio_codec": self.audio_codec.value,
        }


@dataclass(frozen=True, slots=True)
class ContainerCapability:
    id: OutputContainer
    label: str
    extension: str
    muxer: str
    video_codecs: tuple[VideoCodec, ...]
    audio_codecs: tuple[AudioCodec, ...]


def _videos(*codecs: VideoCodec) -> tuple[VideoCodec, ...]:
    return codecs


def _audios(*codecs: AudioCodec) -> tuple[AudioCodec, ...]:
    return codecs


CONVERSION_CAPABILITIES: dict[OutputContainer, ContainerCapability] = {
    OutputContainer.MP4: ContainerCapability(
        OutputContainer.MP4,
        "MP4",
        "mp4",
        "mp4",
        _videos(
            VideoCodec.H264,
            VideoCodec.H265,
            VideoCodec.AV1,
            VideoCodec.COPY,
            VideoCodec.NONE,
        ),
        _audios(
            AudioCodec.AAC,
            AudioCodec.MP3,
            AudioCodec.COPY,
            AudioCodec.NONE,
        ),
    ),
    OutputContainer.MKV: ContainerCapability(
        OutputContainer.MKV,
        "Matroska",
        "mkv",
        "matroska",
        _videos(*VideoCodec),
        _audios(*AudioCodec),
    ),
    OutputContainer.WEBM: ContainerCapability(
        OutputContainer.WEBM,
        "WebM",
        "webm",
        "webm",
        _videos(VideoCodec.VP9, VideoCodec.AV1, VideoCodec.COPY, VideoCodec.NONE),
        _audios(
            AudioCodec.OPUS,
            AudioCodec.VORBIS,
            AudioCodec.COPY,
            AudioCodec.NONE,
        ),
    ),
    OutputContainer.MOV: ContainerCapability(
        OutputContainer.MOV,
        "QuickTime MOV",
        "mov",
        "mov",
        _videos(
            VideoCodec.H264,
            VideoCodec.H265,
            VideoCodec.COPY,
            VideoCodec.NONE,
        ),
        _audios(
            AudioCodec.AAC,
            AudioCodec.PCM,
            AudioCodec.COPY,
            AudioCodec.NONE,
        ),
    ),
    OutputContainer.AVI: ContainerCapability(
        OutputContainer.AVI,
        "AVI",
        "avi",
        "avi",
        _videos(VideoCodec.H264, VideoCodec.COPY, VideoCodec.NONE),
        _audios(
            AudioCodec.MP3,
            AudioCodec.PCM,
            AudioCodec.COPY,
            AudioCodec.NONE,
        ),
    ),
    OutputContainer.MP3: ContainerCapability(
        OutputContainer.MP3,
        "MP3 Audio",
        "mp3",
        "mp3",
        _videos(VideoCodec.NONE),
        _audios(AudioCodec.MP3, AudioCodec.COPY),
    ),
    OutputContainer.WAV: ContainerCapability(
        OutputContainer.WAV,
        "WAV Audio",
        "wav",
        "wav",
        _videos(VideoCodec.NONE),
        _audios(AudioCodec.PCM, AudioCodec.COPY),
    ),
    OutputContainer.FLAC: ContainerCapability(
        OutputContainer.FLAC,
        "FLAC Audio",
        "flac",
        "flac",
        _videos(VideoCodec.NONE),
        _audios(AudioCodec.FLAC, AudioCodec.COPY),
    ),
    OutputContainer.OGG: ContainerCapability(
        OutputContainer.OGG,
        "Ogg Audio",
        "ogg",
        "ogg",
        _videos(VideoCodec.NONE),
        _audios(
            AudioCodec.OPUS,
            AudioCodec.VORBIS,
            AudioCodec.FLAC,
            AudioCodec.COPY,
        ),
    ),
    OutputContainer.M4A: ContainerCapability(
        OutputContainer.M4A,
        "M4A Audio",
        "m4a",
        "ipod",
        _videos(VideoCodec.NONE),
        _audios(AudioCodec.AAC, AudioCodec.COPY),
    ),
    OutputContainer.OPUS: ContainerCapability(
        OutputContainer.OPUS,
        "Opus Audio",
        "opus",
        "opus",
        _videos(VideoCodec.NONE),
        _audios(AudioCodec.OPUS, AudioCodec.COPY),
    ),
}

VIDEO_ENCODERS: dict[VideoCodec, str] = {
    VideoCodec.H264: "libx264",
    VideoCodec.H265: "libx265",
    VideoCodec.VP9: "libvpx-vp9",
    VideoCodec.AV1: "libaom-av1",
    VideoCodec.COPY: "copy",
}
AUDIO_ENCODERS: dict[AudioCodec, str] = {
    AudioCodec.AAC: "aac",
    AudioCodec.MP3: "libmp3lame",
    AudioCodec.OPUS: "libopus",
    AudioCodec.VORBIS: "libvorbis",
    AudioCodec.FLAC: "flac",
    AudioCodec.PCM: "pcm_s16le",
    AudioCodec.COPY: "copy",
}

_COPY_VIDEO_CODECS: dict[OutputContainer, set[str] | None] = {
    OutputContainer.MP4: {"h264", "hevc", "av1"},
    OutputContainer.MKV: None,
    OutputContainer.WEBM: {"vp8", "vp9", "av1"},
    OutputContainer.MOV: {"h264", "hevc"},
    OutputContainer.AVI: {"h264", "mpeg4"},
}
_COPY_AUDIO_CODECS: dict[OutputContainer, set[str] | None] = {
    OutputContainer.MP4: {"aac", "mp3"},
    OutputContainer.MKV: None,
    OutputContainer.WEBM: {"opus", "vorbis"},
    OutputContainer.MOV: {"aac", "pcm"},
    OutputContainer.AVI: {"mp3", "pcm"},
    OutputContainer.MP3: {"mp3"},
    OutputContainer.WAV: {"pcm"},
    OutputContainer.FLAC: {"flac"},
    OutputContainer.OGG: {"opus", "vorbis", "flac"},
    OutputContainer.M4A: {"aac"},
    OutputContainer.OPUS: {"opus"},
}


class InvalidConversionError(Exception):
    """Raised for a safe, user-actionable conversion validation failure."""


class FFmpegExecutableNotFoundError(Exception):
    """Raised when the configured FFmpeg executable cannot be started."""


class FFmpegTimeoutError(Exception):
    """Raised when FFmpeg exceeds the configured processing timeout."""


class FFmpegConversionError(Exception):
    """Raised when FFmpeg exits without producing a valid conversion."""

    def __init__(self, diagnostic: str | None = None) -> None:
        super().__init__("FFmpeg conversion failed")
        self.diagnostic = diagnostic


class FFmpegCapabilityError(Exception):
    """Raised when the local FFmpeg build cannot satisfy a conversion."""


def validate_compatibility(spec: ConversionSpec) -> None:
    if spec.video_codec is VideoCodec.NONE and spec.audio_codec is AudioCodec.NONE:
        raise InvalidConversionError("At least one output stream must be enabled")
    capability = CONVERSION_CAPABILITIES[spec.container]
    if spec.video_codec not in capability.video_codecs:
        raise InvalidConversionError(
            f"Video codec {spec.video_codec.value} is incompatible with {spec.container.value}"
        )
    if spec.audio_codec not in capability.audio_codecs:
        raise InvalidConversionError(
            f"Audio codec {spec.audio_codec.value} is incompatible with {spec.container.value}"
        )


def validate_input_streams(spec: ConversionSpec, inspection: MediaInspection) -> None:
    has_video = bool(inspection.video_streams)
    has_audio = bool(inspection.audio_streams)
    if spec.video_codec is not VideoCodec.NONE and not has_video:
        raise InvalidConversionError("The input does not contain a video stream")
    if spec.video_codec is VideoCodec.NONE and not has_audio:
        raise InvalidConversionError("The input does not contain an audio stream")

    if spec.video_codec is VideoCodec.COPY and has_video:
        source_codec = _normalize_codec(inspection.video_streams[0].codec_name)
        _validate_copy_codec(
            source_codec,
            _COPY_VIDEO_CODECS.get(spec.container),
            "video",
            spec.container,
        )
    if spec.audio_codec is AudioCodec.COPY and has_audio:
        source_codec = _normalize_codec(inspection.audio_streams[0].codec_name)
        _validate_copy_codec(
            source_codec,
            _COPY_AUDIO_CODECS.get(spec.container),
            "audio",
            spec.container,
        )


def _validate_copy_codec(
    source_codec: str | None,
    supported_codecs: set[str] | None,
    stream_type: str,
    container: OutputContainer,
) -> None:
    if supported_codecs is None:
        return
    if source_codec not in supported_codecs:
        raise InvalidConversionError(
            f"The source {stream_type} codec cannot be copied into {container.value}"
        )


def _normalize_codec(codec: str | None) -> str | None:
    if codec is None:
        return None
    if codec.startswith("pcm_"):
        return "pcm"
    return codec.casefold()


def build_convert_command(
    executable: str,
    input_path: Path,
    output_path: Path,
    spec: ConversionSpec,
) -> list[str]:
    validate_compatibility(spec)
    capability = CONVERSION_CAPABILITIES[spec.container]
    command = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
    ]
    if spec.video_codec is VideoCodec.NONE:
        command.append("-vn")
    else:
        command.extend(["-map", "0:v:0", "-c:v", VIDEO_ENCODERS[spec.video_codec]])
    if spec.audio_codec is AudioCodec.NONE:
        command.append("-an")
    else:
        command.extend(["-map", "0:a:0?", "-c:a", AUDIO_ENCODERS[spec.audio_codec]])
    command.extend(["-sn", "-dn", "-f", capability.muxer, str(output_path)])
    return command


@dataclass(frozen=True, slots=True)
class LocalFFmpegCapabilities:
    encoders: frozenset[str]
    muxers: frozenset[str]

    def validate(self, spec: ConversionSpec) -> None:
        capability = CONVERSION_CAPABILITIES[spec.container]
        if capability.muxer not in self.muxers:
            raise FFmpegCapabilityError("The requested output format is unavailable")
        for codec, mapping in (
            (spec.video_codec, VIDEO_ENCODERS),
            (spec.audio_codec, AUDIO_ENCODERS),
        ):
            if codec.value in {"copy", "none"}:
                continue
            encoder = mapping[codec]  # type: ignore[index]
            if encoder not in self.encoders:
                raise FFmpegCapabilityError("The requested encoder is unavailable")


class FFmpegCapabilityDetector:
    def __init__(self, executable: str, timeout_seconds: float = 15.0) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    @lru_cache(maxsize=1)
    def detect(self) -> LocalFFmpegCapabilities:
        encoders = self._query("-encoders")
        muxers = self._query("-muxers")
        return LocalFFmpegCapabilities(
            encoders=frozenset(_parse_capability_names(encoders)),
            muxers=frozenset(_parse_capability_names(muxers)),
        )

    def _query(self, flag: str) -> str:
        try:
            result = subprocess.run(
                [self.executable, "-hide_banner", flag],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=False,
                check=False,
                timeout=self.timeout_seconds,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise FFmpegExecutableNotFoundError from exc
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise FFmpegCapabilityError("FFmpeg capabilities are unavailable") from exc
        if result.returncode != 0:
            raise FFmpegCapabilityError("FFmpeg capabilities are unavailable")
        return result.stdout


def _parse_capability_names(output: str) -> Iterable[str]:
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] not in {"--", "="}:
            flags = parts[0]
            if "E" in flags or flags.startswith(("V", "A", "S")):
                yield parts[1]


class FFmpegRunner:
    def __init__(self, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds

    def run(self, command: list[str]) -> None:
        with tempfile.TemporaryFile() as stderr_file:
            try:
                result = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=stderr_file,
                    shell=False,
                    check=False,
                    timeout=self.timeout_seconds,
                )
            except FileNotFoundError as exc:
                raise FFmpegExecutableNotFoundError from exc
            except subprocess.TimeoutExpired as exc:
                raise FFmpegTimeoutError from exc
            except OSError as exc:
                raise FFmpegConversionError from exc
            if result.returncode != 0:
                raise FFmpegConversionError(_read_diagnostic_tail(stderr_file))


class ConversionService:
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

    def convert(
        self,
        input_path: Path,
        output_path: Path,
        spec: ConversionSpec,
    ) -> None:
        inspection = self.inspector.inspect(input_path)
        validate_input_streams(spec, inspection)
        self.capabilities.validate(spec)
        self.runner.run(
            build_convert_command(self.executable, input_path, output_path, spec)
        )


def available_conversion_capabilities(
    local: LocalFFmpegCapabilities,
) -> list[ContainerCapability]:
    available: list[ContainerCapability] = []
    for capability in CONVERSION_CAPABILITIES.values():
        if capability.muxer not in local.muxers:
            continue
        video_codecs = tuple(
            codec
            for codec in capability.video_codecs
            if codec in {VideoCodec.COPY, VideoCodec.NONE}
            or VIDEO_ENCODERS[codec] in local.encoders
        )
        audio_codecs = tuple(
            codec
            for codec in capability.audio_codecs
            if codec in {AudioCodec.COPY, AudioCodec.NONE}
            or AUDIO_ENCODERS[codec] in local.encoders
        )
        available.append(
            ContainerCapability(
                id=capability.id,
                label=capability.label,
                extension=capability.extension,
                muxer=capability.muxer,
                video_codecs=video_codecs,
                audio_codecs=audio_codecs,
            )
        )
    return available


@lru_cache
def get_ffmpeg_capability_detector() -> FFmpegCapabilityDetector:
    return FFmpegCapabilityDetector(get_media_tool_paths().ffmpeg)


@lru_cache
def get_conversion_service() -> ConversionService:
    settings = get_settings()
    executable = get_media_tool_paths().ffmpeg
    from backend.app.processing.probe import get_sync_ffprobe_inspector

    return ConversionService(
        executable=executable,
        inspector=get_sync_ffprobe_inspector(),
        runner=FFmpegRunner(settings.ffmpeg_timeout_seconds),
        capabilities=get_ffmpeg_capability_detector().detect(),
    )


def _read_diagnostic_tail(stderr_file: BinaryIO) -> str | None:
    stderr_file.seek(0, 2)
    size = stderr_file.tell()
    stderr_file.seek(max(0, size - 8192))
    diagnostic = stderr_file.read().decode("utf-8", errors="replace").strip()
    return diagnostic or None

