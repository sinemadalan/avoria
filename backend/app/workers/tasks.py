import asyncio
import logging
from time import monotonic
from typing import Any

from celery import Task

from backend.app.application.ports.jobs import JobOperation
from backend.app.application.ports.storage import OutputTarget, StorageService
from backend.app.core.config import get_settings
from backend.app.core.celery_app import celery_app
from backend.app.infrastructure.storage import get_storage_service
from backend.app.processing.conversion import (
    CONVERSION_CAPABILITIES,
    ConversionService,
    ConversionSpec,
    FFmpegConversionError,
    InvalidConversionError,
    get_conversion_service,
)

logger = logging.getLogger(__name__)


class MediaTask(Task):
    def on_failure(
        self,
        exc: BaseException,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        media_id = str(args[0]) if args else "unknown"
        operation = str(args[1]) if len(args) > 1 else "convert"
        parameters = args[2] if len(args) > 2 and isinstance(args[2], dict) else {}
        self.update_state(
            task_id=task_id,
            state="FAILURE",
            meta={
                "media_id": media_id,
                "operation": operation,
                "output_id": task_id,
                "format": str(parameters.get("container", "mp4")),
                "progress": 0,
                "error": _public_failure_message(exc),
            },
        )
        logger.error(
            "Processing task failed task_id=%s media_id=%s operation=%s error=%s",
            task_id,
            media_id,
            operation,
            exc.__class__.__name__,
        )
        super().on_failure(exc, task_id, args, kwargs, einfo)


@celery_app.task(bind=True, base=MediaTask, name="avoria.media.convert")
def process_media_job(
    self: MediaTask,
    media_id: str,
    operation: str,
    parameters: dict[str, str],
) -> dict[str, str | int]:
    job_id = str(self.request.id)
    conversion = ConversionSpec.from_payload(parameters)
    logger.info(
        "Processing task received task_id=%s media_id=%s operation=%s",
        job_id,
        media_id,
        operation,
    )
    self.update_state(
        state="PROGRESS",
        meta={
            "media_id": media_id,
            "operation": operation,
            "output_id": job_id,
            "format": conversion.container.value,
            "progress": 0,
        },
    )
    settings = get_settings()
    storage = get_storage_service()
    storage.initialize()
    output = execute_media_job(
        job_id=job_id,
        media_id=media_id,
        operation=JobOperation(operation),
        storage=storage,
        converter=get_conversion_service(),
        conversion=conversion,
        allowed_extensions=settings.allowed_media_extensions,
    )
    logger.info(
        "Processing task completed task_id=%s media_id=%s operation=%s",
        job_id,
        media_id,
        operation,
    )
    return {
        **output,
        "media_id": media_id,
        "operation": operation,
        "format": conversion.container.value,
        "progress": 100,
    }


def execute_media_job(
    *,
    job_id: str,
    media_id: str,
    operation: JobOperation,
    storage: StorageService,
    converter: ConversionService,
    conversion: ConversionSpec,
    allowed_extensions: list[str],
) -> dict[str, str]:
    started_at = monotonic()
    target: OutputTarget | None = None
    logger.info(
        "Processing job started job_id=%s media_id=%s operation=%s",
        job_id,
        media_id,
        operation.value,
    )
    try:
        input_path = asyncio.run(storage.resolve_upload(media_id, allowed_extensions))
        extension = CONVERSION_CAPABILITIES[conversion.container].extension
        target = storage.prepare_output(job_id, f".{extension}")
        if operation.canonical is not JobOperation.CONVERT:
            raise ValueError("Unsupported processing operation")
        converter.convert(input_path, target.temporary_path, conversion)
        storage.finalize_output(target)
    except FFmpegConversionError as exc:
        _cleanup_partial(storage, target, job_id)
        logger.error(
            "FFmpeg conversion failed job_id=%s media_id=%s diagnostic=%r",
            job_id,
            media_id,
            exc.diagnostic,
        )
        raise
    except Exception:
        _cleanup_partial(storage, target, job_id)
        logger.exception("Processing job failed job_id=%s media_id=%s", job_id, media_id)
        raise

    duration_seconds = monotonic() - started_at
    logger.info(
        "FFmpeg completed job_id=%s media_id=%s output_id=%s duration_seconds=%.3f",
        job_id,
        media_id,
        target.output_id,
        duration_seconds,
    )
    return {
        "output_id": target.output_id,
        "filename": target.filename,
        "format": conversion.container.value,
    }


def _cleanup_partial(
    storage: StorageService,
    target: OutputTarget | None,
    job_id: str,
) -> None:
    if target is None:
        return
    try:
        storage.cleanup_output(target)
    except Exception:
        logger.exception("Partial output cleanup failed job_id=%s", job_id)


def _public_failure_message(exc: BaseException) -> str:
    if isinstance(exc, InvalidConversionError):
        return str(exc)
    if isinstance(exc, FFmpegConversionError):
        return "FFmpeg conversion failed"
    return "Media processing failed"
