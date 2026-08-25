from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from backend.app.application.ports.jobs import JobOperation, JobState
from backend.app.processing.audio_extraction import (
    AudioExtractionFormat,
    AudioExtractionSpec,
)
from backend.app.processing.compression import CompressionLevel, CompressionSpec
from backend.app.processing.conversion import (
    AudioCodec,
    ConversionSpec,
    InvalidConversionError,
    OutputContainer,
    VideoCodec,
    validate_compatibility,
)


class ConvertParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    container: OutputContainer = OutputContainer.MP4
    video_codec: VideoCodec = VideoCodec.H264
    audio_codec: AudioCodec = AudioCodec.AAC

    @model_validator(mode="after")
    def validate_combination(self) -> "ConvertParameters":
        try:
            validate_compatibility(self.to_spec())
        except InvalidConversionError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def to_spec(self) -> ConversionSpec:
        return ConversionSpec(
            container=self.container,
            video_codec=self.video_codec,
            audio_codec=self.audio_codec,
        )

    def to_payload(self) -> dict[str, str]:
        return self.to_spec().to_payload()


class CompressParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compression_level: CompressionLevel = CompressionLevel.BALANCED

    def to_spec(self) -> CompressionSpec:
        return CompressionSpec(level=self.compression_level)

    def to_payload(self) -> dict[str, str]:
        return self.to_spec().to_payload()


class ExtractAudioParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def to_payload(self) -> dict[str, str]:
        return {}


class MuteParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def to_payload(self) -> dict[str, str]:
        return {}


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_id: str
    operation: JobOperation
    parameters: (
        ConvertParameters | CompressParameters | ExtractAudioParameters | MuteParameters
    )
    format: AudioExtractionFormat | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_operation_parameters(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        operation = value.get("operation")
        parameters = value.get("parameters", {})
        if operation in {JobOperation.COMPRESS, JobOperation.COMPRESS.value}:
            model = CompressParameters
        elif operation in {
            JobOperation.EXTRACT_AUDIO,
            JobOperation.EXTRACT_AUDIO.value,
        }:
            model = ExtractAudioParameters
        elif operation in {JobOperation.MUTE, JobOperation.MUTE.value}:
            model = MuteParameters
        else:
            model = ConvertParameters
        return {**value, "parameters": model.model_validate(parameters)}

    @model_validator(mode="after")
    def validate_parameter_type(self) -> "JobCreateRequest":
        if self.operation is JobOperation.COMPRESS:
            expected = CompressParameters
        elif self.operation is JobOperation.EXTRACT_AUDIO:
            expected = ExtractAudioParameters
        elif self.operation is JobOperation.MUTE:
            expected = MuteParameters
        else:
            expected = ConvertParameters
        if not isinstance(self.parameters, expected):
            raise ValueError("Parameters do not match the requested operation")
        if self.operation is not JobOperation.EXTRACT_AUDIO and self.format is not None:
            raise ValueError("Format is only supported for audio extraction")
        return self

    def to_payload(self) -> dict[str, str]:
        if self.operation is JobOperation.EXTRACT_AUDIO:
            return AudioExtractionSpec(
                format=self.format or AudioExtractionFormat.MP3
            ).to_payload()
        return self.parameters.to_payload()


class JobCreateResponse(BaseModel):
    job_id: str
    media_id: str
    operation: JobOperation
    status: JobState


class JobOutputReference(BaseModel):
    output_id: str
    format: OutputContainer | None = None
    compression_level: CompressionLevel | None = None
    original_size: int | None = None
    compressed_size: int | None = None
    saved_bytes: int | None = None
    reduction_percentage: float | None = None
    compression_effective: bool | None = None


class JobStatusResponse(BaseModel):
    job_id: str
    media_id: str
    operation: JobOperation
    status: JobState
    output: JobOutputReference | None = None
    progress: int | None = None
    error: str | None = None
