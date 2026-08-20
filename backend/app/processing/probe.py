import asyncio
import json
import math
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from backend.app.core.config import get_settings
from backend.app.processing.tools import get_media_tool_paths


class InvalidMediaError(Exception):
    """Raised when FFprobe cannot read the input as valid media."""


class FFprobeExecutableNotFoundError(Exception):
    """Raised when the configured FFprobe executable cannot be started."""


class FFprobeTimeoutError(Exception):
    """Raised when FFprobe does not finish within the configured timeout."""


class FFprobeProcessError(Exception):
    """Raised when FFprobe fails internally or returns unusable output."""


@dataclass(frozen=True, slots=True)
class MediaFormatMetadata:
    name: str | None
    long_name: str | None
    duration_seconds: float | None
    size_bytes: int | None
    bit_rate: int | None


@dataclass(frozen=True, slots=True)
class VideoStreamMetadata:
    index: int | None
    codec_name: str | None
    codec_long_name: str | None
    profile: str | None
    width: int | None
    height: int | None
    pixel_format: str | None
    frame_rate: float | None
    bit_rate: int | None


@dataclass(frozen=True, slots=True)
class AudioStreamMetadata:
    index: int | None
    codec_name: str | None
    codec_long_name: str | None
    sample_rate: int | None
    channels: int | None
    channel_layout: str | None
    bit_rate: int | None


@dataclass(frozen=True, slots=True)
class MediaInspection:
    format: MediaFormatMetadata
    stream_count: int
    video_streams: list[VideoStreamMetadata]
    audio_streams: list[AudioStreamMetadata]


class FFprobeInspector:
    def __init__(self, executable: str, timeout_seconds: float) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    async def inspect(self, media_path: Path) -> MediaInspection:
        try:
            process = await asyncio.create_subprocess_exec(
                *_ffprobe_command(self.executable, media_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise FFprobeExecutableNotFoundError from exc
        except OSError as exc:
            raise FFprobeProcessError from exc

        try:
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            await _stop_process(process)
            raise FFprobeTimeoutError from exc
        except BaseException:
            await _stop_process(process)
            raise

        if process.returncode != 0:
            raise InvalidMediaError

        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FFprobeProcessError from exc
        if not isinstance(payload, dict):
            raise FFprobeProcessError
        return parse_ffprobe_payload(payload)


class SyncFFprobeInspector:
    """Runs FFprobe synchronously so callers can move it to a worker thread."""

    def __init__(self, executable: str, timeout_seconds: float) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    def inspect(self, media_path: Path) -> MediaInspection:
        try:
            completed = subprocess.run(
                _ffprobe_command(self.executable, media_path),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                shell=False,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise FFprobeExecutableNotFoundError from exc
        except subprocess.TimeoutExpired as exc:
            raise FFprobeTimeoutError from exc
        except OSError as exc:
            raise FFprobeProcessError from exc

        if completed.returncode != 0:
            raise InvalidMediaError

        try:
            payload = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FFprobeProcessError from exc
        if not isinstance(payload, dict):
            raise FFprobeProcessError
        return parse_ffprobe_payload(payload)


def get_ffprobe_inspector() -> FFprobeInspector:
    settings = get_settings()
    return FFprobeInspector(
        executable=get_media_tool_paths().ffprobe,
        timeout_seconds=settings.ffprobe_timeout_seconds,
    )


def get_sync_ffprobe_inspector() -> SyncFFprobeInspector:
    settings = get_settings()
    return SyncFFprobeInspector(
        executable=get_media_tool_paths().ffprobe,
        timeout_seconds=settings.ffprobe_timeout_seconds,
    )


def _ffprobe_command(executable: str, media_path: Path) -> list[str]:
    return [
        executable,
        "-v",
        "error",
        "-show_entries",
        (
            "format=format_name,format_long_name,duration,size,bit_rate:"
            "stream=index,codec_type,codec_name,codec_long_name,profile,width,height,"
            "pix_fmt,avg_frame_rate,r_frame_rate,bit_rate,sample_rate,channels,"
            "channel_layout"
        ),
        "-print_format",
        "json",
        str(media_path),
    ]


def parse_ffprobe_payload(payload: dict[str, Any]) -> MediaInspection:
    raw_format = payload.get("format")
    format_data = raw_format if isinstance(raw_format, dict) else {}
    raw_streams = payload.get("streams")
    streams = (
        [stream for stream in raw_streams if isinstance(stream, dict)]
        if isinstance(raw_streams, list)
        else []
    )

    video_streams = [
        _parse_video_stream(stream)
        for stream in streams
        if stream.get("codec_type") == "video"
    ]
    audio_streams = [
        _parse_audio_stream(stream)
        for stream in streams
        if stream.get("codec_type") == "audio"
    ]
    return MediaInspection(
        format=MediaFormatMetadata(
            name=_optional_string(format_data.get("format_name")),
            long_name=_optional_string(format_data.get("format_long_name")),
            duration_seconds=_optional_float(format_data.get("duration")),
            size_bytes=_optional_int(format_data.get("size")),
            bit_rate=_optional_int(format_data.get("bit_rate")),
        ),
        stream_count=len(streams),
        video_streams=video_streams,
        audio_streams=audio_streams,
    )


def _parse_video_stream(stream: dict[str, Any]) -> VideoStreamMetadata:
    frame_rate = _parse_frame_rate(stream.get("avg_frame_rate"))
    if frame_rate is None:
        frame_rate = _parse_frame_rate(stream.get("r_frame_rate"))
    return VideoStreamMetadata(
        index=_optional_int(stream.get("index")),
        codec_name=_optional_string(stream.get("codec_name")),
        codec_long_name=_optional_string(stream.get("codec_long_name")),
        profile=_optional_string(stream.get("profile")),
        width=_optional_int(stream.get("width")),
        height=_optional_int(stream.get("height")),
        pixel_format=_optional_string(stream.get("pix_fmt")),
        frame_rate=frame_rate,
        bit_rate=_optional_int(stream.get("bit_rate")),
    )


def _parse_audio_stream(stream: dict[str, Any]) -> AudioStreamMetadata:
    return AudioStreamMetadata(
        index=_optional_int(stream.get("index")),
        codec_name=_optional_string(stream.get("codec_name")),
        codec_long_name=_optional_string(stream.get("codec_long_name")),
        sample_rate=_optional_int(stream.get("sample_rate")),
        channels=_optional_int(stream.get("channels")),
        channel_layout=_optional_string(stream.get("channel_layout")),
        bit_rate=_optional_int(stream.get("bit_rate")),
    )


def _parse_frame_rate(value: Any) -> float | None:
    text = _optional_string(value)
    if text is None:
        return None
    try:
        frame_rate = float(Fraction(text))
    except (ValueError, ZeroDivisionError):
        return None
    return frame_rate if math.isfinite(frame_rate) and frame_rate > 0 else None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        pass
    await process.communicate()
