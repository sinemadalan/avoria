from typing import Any

from pydantic import BaseModel, ConfigDict, StrictFloat, StrictInt, model_validator

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
from backend.app.processing.trim import InvalidTrimRangeError, TrimSpec
from backend.app.processing.volume import InvalidVolumeError, VolumeSpec


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


class VolumeParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    volume_percent: StrictInt

    @model_validator(mode="after")
    def validate_percentage(self) -> "VolumeParameters":
        try:
            self.to_spec()
        except InvalidVolumeError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def to_spec(self) -> VolumeSpec:
        return VolumeSpec(volume_percent=self.volume_percent)

    def to_payload(self) -> dict[str, int]:
        return self.to_spec().to_payload()


class TrimParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    start_seconds: StrictInt | StrictFloat
    end_seconds: StrictInt | StrictFloat

    @model_validator(mode="after")
    def validate_range(self) -> "TrimParameters":
        try:
            self.to_spec()
        except InvalidTrimRangeError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def to_spec(self) -> TrimSpec:
        return TrimSpec(
            start_seconds=self.start_seconds,
            end_seconds=self.end_seconds,
        )

    def to_payload(self) -> dict[str, float]:
        return self.to_spec().to_payload()


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_id: str
    operation: JobOperation
    parameters: (
        ConvertParameters
        | CompressParameters
        | ExtractAudioParameters
        | MuteParameters
        | VolumeParameters
        | TrimParameters
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
        elif operation in {JobOperation.VOLUME, JobOperation.VOLUME.value}:
            model = VolumeParameters
        elif operation in {JobOperation.TRIM, JobOperation.TRIM.value}:
            model = TrimParameters
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
        elif self.operation is JobOperation.VOLUME:
            expected = VolumeParameters
        elif self.operation is JobOperation.TRIM:
            expected = TrimParameters
        else:
            expected = ConvertParameters
        if not isinstance(self.parameters, expected):
            raise ValueError("Parameters do not match the requested operation")
        if self.operation is not JobOperation.EXTRACT_AUDIO and self.format is not None:
            raise ValueError("Format is only supported for audio extraction")
        return self

    def to_payload(self) -> dict[str, Any]:
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
