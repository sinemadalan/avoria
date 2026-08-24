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
from backend.app.processing.audio_extraction import (
    AudioExtractionProfile,
    AudioExtractionService,
    AudioExtractionSpec,
    get_audio_extraction_service,
)
from backend.app.processing.compression import (
    CompressionProfile,
    CompressionService,
    CompressionSpec,
    CompressionStatistics,
    calculate_compression_statistics,
    get_compression_service,
)
from backend.app.processing.conversion import (
    CONVERSION_CAPABILITIES,
    ConversionService,
    ConversionSpec,
    FFmpegConversionError,
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
) -> dict[str, Any]:
    job_id = str(self.request.id)
    job_operation = JobOperation(operation)
    conversion = (
        ConversionSpec.from_payload(parameters)
        if job_operation.canonical is JobOperation.CONVERT
        else None
    )
    compression = (
        CompressionSpec.from_payload(parameters)
        if job_operation is JobOperation.COMPRESS
        else None
    )
    extraction = (
        AudioExtractionSpec.from_payload(parameters)
        if job_operation is JobOperation.EXTRACT_AUDIO
        else None
    )
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
            "format": (
                conversion.container.value
                if conversion
                else (extraction.format.value if extraction else None)
            ),
            "compression_level": compression.level.value if compression else None,
            "progress": 0,
        },
    )
    settings = get_settings()
    storage = get_storage_service()
    storage.initialize()
    output = execute_media_job(
        job_id=job_id,
        media_id=media_id,
        operation=job_operation,
        storage=storage,
        converter=get_conversion_service() if conversion else None,
        conversion=conversion,
        compressor=get_compression_service() if compression else None,
        compression=compression,
        audio_extractor=(
            get_audio_extraction_service() if extraction else None
        ),
        extraction=extraction,
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
        "format": output["format"],
        "progress": 100,
    }


def execute_media_job(
    *,
    job_id: str,
    media_id: str,
    operation: JobOperation,
    storage: StorageService,
    converter: ConversionService | None,
    conversion: ConversionSpec | None,
    allowed_extensions: list[str],
    compressor: CompressionService | None = None,
    compression: CompressionSpec | None = None,
    audio_extractor: AudioExtractionService | None = None,
    extraction: AudioExtractionSpec | None = None,
) -> dict[str, Any]:
    started_at = monotonic()
    target: OutputTarget | None = None
    statistics: CompressionStatistics | None = None
    compression_profile: CompressionProfile | None = None
    extraction_profile: AudioExtractionProfile | None = None
    logger.info(
        "Processing job started job_id=%s media_id=%s operation=%s",
        job_id,
        media_id,
        operation.value,
    )
    try:
        input_path = asyncio.run(storage.resolve_upload(media_id, allowed_extensions))
        if operation.canonical is JobOperation.CONVERT:
            if converter is None or conversion is None:
                raise ValueError("Conversion dependencies are unavailable")
            extension = CONVERSION_CAPABILITIES[conversion.container].extension
        elif operation is JobOperation.COMPRESS:
            if compressor is None or compression is None:
                raise ValueError("Compression dependencies are unavailable")
            compression_profile = compressor.resolve_profile(input_path)
            extension = compression_profile.extension
        elif operation is JobOperation.EXTRACT_AUDIO:
            if audio_extractor is None or extraction is None:
                raise ValueError("Audio extraction dependencies are unavailable")
            extraction_profile = audio_extractor.resolve_profile(extraction)
            extension = extraction_profile.extension
        else:
            raise ValueError("Unsupported processing operation")
        target = storage.prepare_output(job_id, f".{extension}")
        if operation.canonical is JobOperation.CONVERT:
            converter.convert(input_path, target.temporary_path, conversion)
        elif operation is JobOperation.COMPRESS:
            compressor.compress(
                input_path,
                target.temporary_path,
                compression,
                compression_profile,
            )
            statistics = calculate_compression_statistics(
                input_path.stat().st_size,
                target.temporary_path.stat().st_size,
            )
        else:
            audio_extractor.extract(
                input_path,
                target.temporary_path,
                extraction,
                extraction_profile,
            )
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
    result: dict[str, Any] = {
        "output_id": target.output_id,
        "filename": target.filename,
        "format": extension,
    }
    if operation is JobOperation.COMPRESS and compression is not None:
        if statistics is None:
            raise RuntimeError("Compression statistics are unavailable")
        result.update(
            compression_level=compression.level.value,
            **statistics.to_payload(),
        )
    return result


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
