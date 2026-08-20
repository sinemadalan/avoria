from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.application.ports.jobs import JobOperation, JobState
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


class JobCreateRequest(BaseModel):
    media_id: str
    operation: JobOperation
    parameters: ConvertParameters = Field(default_factory=ConvertParameters)


class JobCreateResponse(BaseModel):
    job_id: str
    media_id: str
    operation: JobOperation
    status: JobState


class JobOutputReference(BaseModel):
    output_id: str
    format: OutputContainer | None = None


class JobStatusResponse(BaseModel):
    job_id: str
    media_id: str
    operation: JobOperation
    status: JobState
    output: JobOutputReference | None = None
    progress: int | None = None
    error: str | None = None
