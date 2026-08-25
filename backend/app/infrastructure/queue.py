from collections import OrderedDict
from functools import lru_cache, partial
from threading import Lock
from typing import Any

from celery import Celery, states
from celery.exceptions import CeleryError
from celery.result import AsyncResult
from kombu.exceptions import OperationalError
from starlette.concurrency import run_in_threadpool

from backend.app.application.ports.jobs import (
    JobNotFoundError,
    JobOperation,
    JobQueue,
    JobQueueStateError,
    JobQueueUnavailableError,
    JobRecord,
    JobState,
)
from backend.app.core.celery_app import celery_app
from backend.app.processing.audio_extraction import (
    MediaHasNoAudioError,
    UnsupportedAudioContainerError,
)
from backend.app.processing.compression import UnsupportedCompressionContainerError
from backend.app.processing.conversion import FFmpegConversionError, InvalidConversionError
from backend.app.processing.mute import MediaHasNoVideoError
from backend.app.processing.probe import (
    FFprobeExecutableNotFoundError,
    FFprobeProcessError,
    FFprobeTimeoutError,
    InvalidMediaError,
)
from backend.app.processing.trim import (
    InvalidTrimMediaDurationError,
    InvalidTrimRangeError,
    MediaHasNoTrimStreamError,
    TrimEndExceedsDurationError,
    TrimStartExceedsDurationError,
)
from backend.app.processing.volume import InvalidVolumeError

TASK_NAME = "avoria.media.convert"
_MAX_LOCAL_JOBS = 10_000


class CeleryJobQueue(JobQueue):
    """Celery adapter; task state remains in Celery's transient result backend."""

    def __init__(self, app: Celery) -> None:
        self.app = app
        self._submitted: OrderedDict[
            str, tuple[str, JobOperation, dict[str, Any]]
        ] = OrderedDict()
        self._submitted_lock = Lock()

    async def enqueue(
        self,
        job_id: str,
        media_id: str,
        operation: JobOperation,
        parameters: dict[str, Any],
    ) -> None:
        try:
            publish = partial(
                self.app.send_task,
                TASK_NAME,
                args=[media_id, operation.value, parameters],
                task_id=job_id,
            )
            await run_in_threadpool(publish)
        except (CeleryError, OperationalError, OSError) as exc:
            raise JobQueueUnavailableError from exc
        self._remember(job_id, media_id, operation, parameters)

    async def get(self, job_id: str) -> JobRecord:
        result = AsyncResult(job_id, app=self.app)
        try:
            state, info = await run_in_threadpool(_read_result, result)
        except (CeleryError, OperationalError, OSError) as exc:
            raise JobQueueUnavailableError from exc

        local_metadata = self._submitted.get(job_id)
        metadata = info if isinstance(info, dict) else {}
        if state == states.PENDING and local_metadata is None:
            # Celery reports PENDING for both unknown and not-yet-started tasks.
            raise JobNotFoundError

        try:
            media_id = str(
                metadata.get("media_id")
                or (local_metadata[0] if local_metadata is not None else "")
            )
            operation = JobOperation(
                metadata.get("operation")
                or (local_metadata[1].value if local_metadata is not None else "convert")
            )
            if not media_id:
                raise ValueError("missing media_id")
            progress = metadata.get("progress")
            status = normalize_job_status(state)
            return JobRecord(
                job_id=job_id,
                media_id=media_id,
                operation=operation,
                status=status,
                output_id=str(metadata.get("output_id") or job_id),
                output_format=_output_format(metadata, local_metadata),
                progress=(
                    int(progress)
                    if progress is not None
                    else (0 if status is JobState.FAILED else None)
                ),
                error=_error_message(state, info, metadata, operation),
                compression_level=_optional_string(metadata.get("compression_level")),
                original_size=_optional_int(metadata.get("original_size")),
                compressed_size=_optional_int(metadata.get("compressed_size")),
                saved_bytes=_optional_int(metadata.get("saved_bytes")),
                reduction_percentage=_optional_float(
                    metadata.get("reduction_percentage")
                ),
                compression_effective=_optional_bool(
                    metadata.get("compression_effective")
                ),
            )
        except (TypeError, ValueError) as exc:
            raise JobQueueStateError from exc

    def _remember(
        self,
        job_id: str,
        media_id: str,
        operation: JobOperation,
        parameters: dict[str, Any],
    ) -> None:
        # RPC has no record before a worker receives a task. This bounded registry
        # preserves the immediate status contract without persisting job state.
        with self._submitted_lock:
            self._submitted[job_id] = (media_id, operation, parameters.copy())
            self._submitted.move_to_end(job_id)
            if len(self._submitted) > _MAX_LOCAL_JOBS:
                self._submitted.popitem(last=False)


def _read_result(result: AsyncResult) -> tuple[str, Any]:
    return result.state, result.info


def normalize_job_status(state: str) -> JobState:
    if state in {states.PENDING, "RECEIVED", "RETRY"}:
        return JobState.QUEUED
    if state in {states.STARTED, "PROGRESS"}:
        return JobState.PROCESSING
    if state == states.SUCCESS:
        return JobState.COMPLETED
    if state in {states.FAILURE, states.REVOKED}:
        return JobState.FAILED
    raise JobQueueStateError


def _output_format(
    metadata: dict[str, Any],
    local_metadata: tuple[str, JobOperation, dict[str, Any]] | None,
) -> str | None:
    value = metadata.get("format")
    if value is None and local_metadata is not None:
        operation = local_metadata[1]
        value = (
            local_metadata[2].get("format", "mp3")
            if operation is JobOperation.EXTRACT_AUDIO
            else local_metadata[2].get("container")
        )
    return str(value) if value else None


def _error_message(
    state: str,
    info: Any,
    metadata: dict[str, Any],
    operation: JobOperation,
) -> str | None:
    if state != states.FAILURE:
        return None
    message = metadata.get("error")
    if message:
        return str(message)
    return _public_failure_message(info, operation)


def _public_failure_message(info: Any, operation: JobOperation) -> str:
    if isinstance(info, UnsupportedCompressionContainerError):
        if operation is JobOperation.MUTE:
            return "Video mute is not supported for this container"
        if operation is JobOperation.VOLUME:
            return "Volume adjustment is not supported for this container"
        if operation is JobOperation.TRIM:
            return "Trim is not supported for this container"
        return "Compression is not supported for this container"
    if isinstance(info, UnsupportedAudioContainerError):
        return "Trim is not supported for this container"
    if isinstance(info, MediaHasNoAudioError):
        return "The input does not contain an audio stream"
    if isinstance(info, MediaHasNoVideoError):
        return "The input does not contain a video stream"
    if isinstance(info, InvalidVolumeError):
        return str(info)
    if isinstance(info, InvalidTrimRangeError):
        return "Invalid trim range"
    if isinstance(info, TrimStartExceedsDurationError):
        return "Start exceeds media duration"
    if isinstance(info, TrimEndExceedsDurationError):
        return "End exceeds media duration"
    if isinstance(info, MediaHasNoTrimStreamError):
        return "Media has no audio/video stream"
    if isinstance(info, InvalidTrimMediaDurationError):
        return "Media inspection failed"
    if operation is JobOperation.TRIM and isinstance(
        info,
        (
            InvalidMediaError,
            FFprobeExecutableNotFoundError,
            FFprobeProcessError,
            FFprobeTimeoutError,
        ),
    ):
        return "Media inspection failed"
    if operation is JobOperation.COMPRESS:
        return "Video compression failed"
    if operation is JobOperation.EXTRACT_AUDIO:
        return "Audio extraction failed"
    if operation is JobOperation.MUTE:
        return "Video mute failed"
    if operation is JobOperation.VOLUME:
        return "Volume adjustment failed"
    if operation is JobOperation.TRIM:
        return "Trim processing failed"
    if isinstance(info, InvalidConversionError):
        return str(info)
    if isinstance(info, FFmpegConversionError):
        return "FFmpeg conversion failed"
    return "Media processing failed"


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _optional_bool(value: Any) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise ValueError("invalid boolean metadata")


@lru_cache
def get_job_queue() -> CeleryJobQueue:
    return CeleryJobQueue(celery_app)
