from pydantic import BaseModel, ConfigDict

from backend.app.processing.conversion import AudioCodec, OutputContainer, VideoCodec


class MediaUploadResponse(BaseModel):
    media_id: str
    original_filename: str
    filename: str
    content_type: str | None
    size_bytes: int


class MediaFormatInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str | None
    long_name: str | None
    duration_seconds: float | None
    size_bytes: int | None
    bit_rate: int | None


class VideoStreamInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int | None
    codec_name: str | None
    codec_long_name: str | None
    profile: str | None
    width: int | None
    height: int | None
    pixel_format: str | None
    frame_rate: float | None
    bit_rate: int | None


class AudioStreamInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int | None
    codec_name: str | None
    codec_long_name: str | None
    sample_rate: int | None
    channels: int | None
    channel_layout: str | None
    bit_rate: int | None


class MediaInspectionResponse(BaseModel):
    media_id: str
    format: MediaFormatInfo
    stream_count: int
    video_streams: list[VideoStreamInfo]
    audio_streams: list[AudioStreamInfo]


class ConversionContainerOption(BaseModel):
    id: OutputContainer
    label: str
    extension: str
    video_codecs: list[VideoCodec]
    audio_codecs: list[AudioCodec]


class ConversionOptionsResponse(BaseModel):
    defaults: dict[str, str]
    containers: list[ConversionContainerOption]
