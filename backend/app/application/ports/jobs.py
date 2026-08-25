from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


class JobOperation(str, Enum):
    CONVERT = "convert"
    TRANSCODE = "transcode"
    COMPRESS = "compress"
    EXTRACT_AUDIO = "extract_audio"
    MUTE = "mute"
    VOLUME = "volume"
    TRIM = "trim"

    @property
    def canonical(self) -> "JobOperation":
        if self is JobOperation.TRANSCODE:
            return JobOperation.CONVERT
        return self


class JobState(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: str
    media_id: str
    operation: JobOperation
    status: JobState
    output_id: str
    output_format: str | None = None
    progress: int | None = None
    error: str | None = None
    compression_level: str | None = None
    original_size: int | None = None
    compressed_size: int | None = None
    saved_bytes: int | None = None
    reduction_percentage: float | None = None
    compression_effective: bool | None = None


class JobNotFoundError(Exception):
    """Raised when a queue job does not exist or has expired."""


class JobQueueUnavailableError(Exception):
    """Raised when Celery cannot publish or retrieve a task."""


class JobQueueStateError(Exception):
    """Raised when a Celery task does not contain valid Avoria metadata."""


class JobQueue(Protocol):
    async def enqueue(
        self,
        job_id: str,
        media_id: str,
        operation: JobOperation,
        parameters: dict[str, Any],
    ) -> None: ...

    async def get(self, job_id: str) -> JobRecord: ...
