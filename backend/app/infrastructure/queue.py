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

TASK_NAME = "avoria.media.convert"
_MAX_LOCAL_JOBS = 10_000


class CeleryJobQueue(JobQueue):
    """Celery adapter; task state remains in Celery's transient result backend."""

    def __init__(self, app: Celery) -> None:
        self.app = app
        self._submitted: OrderedDict[
            str, tuple[str, JobOperation, dict[str, str]]
        ] = OrderedDict()
        self._submitted_lock = Lock()

    async def enqueue(
        self,
        job_id: str,
        media_id: str,
        operation: JobOperation,
        parameters: dict[str, str],
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
            return JobRecord(
                job_id=job_id,
                media_id=media_id,
                operation=operation,
                status=normalize_job_status(state),
                output_id=str(metadata.get("output_id") or job_id),
                output_format=str(
                    metadata.get("format")
                    or (local_metadata[2].get("container") if local_metadata else "")
                )
                or None,
                progress=int(progress) if progress is not None else None,
                error=_error_message(state, info, metadata),
            )
        except (TypeError, ValueError) as exc:
            raise JobQueueStateError from exc

    def _remember(
        self,
        job_id: str,
        media_id: str,
        operation: JobOperation,
        parameters: dict[str, str],
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


def _error_message(state: str, info: Any, metadata: dict[str, Any]) -> str | None:
    if state != states.FAILURE:
        return None
    message = metadata.get("error")
    if message:
        return str(message)
    if isinstance(info, BaseException):
        return "Media processing failed"
    return "Media processing failed"


@lru_cache
def get_job_queue() -> CeleryJobQueue:
    return CeleryJobQueue(celery_app)
