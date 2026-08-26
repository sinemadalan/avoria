from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, status

from backend.app.application.ports.jobs import (
    JobNotFoundError,
    JobOperation,
    JobQueue,
    JobQueueStateError,
    JobQueueUnavailableError,
    JobState,
)
from backend.app.application.ports.storage import (
    AmbiguousMediaError,
    InvalidMediaIdError,
    MediaNotFoundError,
    StorageService,
)
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import AppError
from backend.app.core.logging import get_logger
from backend.app.infrastructure.queue import get_job_queue
from backend.app.infrastructure.storage import get_storage_service
from backend.app.schemas.jobs import (
    JobCreateRequest,
    JobCreateResponse,
    JobOutputReference,
    JobStatusResponse,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = get_logger(__name__)


@router.post("", response_model=JobCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    request: JobCreateRequest,
    settings: Settings = Depends(get_settings),
    storage: StorageService = Depends(get_storage_service),
    job_queue: JobQueue = Depends(get_job_queue),
) -> JobCreateResponse:
    payload = request.to_payload()
    requested_media_ids = (
        payload["media_ids"]
        if request.operation is JobOperation.MERGE_VIDEOS
        else [request.media_id]
    )
    media_ids = [
        _canonical_uuid(value, "media ID", "invalid_media_id")
        for value in requested_media_ids
    ]
    media_id = media_ids[0]
    for candidate_media_id in media_ids:
        try:
            await storage.resolve_upload(
                candidate_media_id,
                settings.allowed_media_extensions,
            )
        except InvalidMediaIdError as exc:
            raise AppError("Invalid media ID", code="invalid_media_id") from exc
        except MediaNotFoundError as exc:
            raise AppError(
                "Media file was not found",
                code="media_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            ) from exc
        except AmbiguousMediaError as exc:
            raise AppError(
                "Multiple media files match this identifier",
                code="ambiguous_media",
                status_code=status.HTTP_409_CONFLICT,
            ) from exc

    if request.operation is JobOperation.REPLACE_AUDIO:
        audio_media_id = payload["audio_media_id"]
        try:
            await storage.resolve_upload(
                audio_media_id,
                settings.allowed_media_extensions,
            )
        except InvalidMediaIdError as exc:
            raise AppError(
                "Invalid external audio media ID",
                code="invalid_audio_media_id",
            ) from exc
        except MediaNotFoundError as exc:
            raise AppError(
                "External audio media file was not found",
                code="audio_media_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            ) from exc
        except AmbiguousMediaError as exc:
            raise AppError(
                "Multiple media files match the external audio identifier",
                code="ambiguous_audio_media",
                status_code=status.HTTP_409_CONFLICT,
            ) from exc

    job_id = str(uuid4())
    try:
        await job_queue.enqueue(
            job_id,
            media_id,
            request.operation,
            payload,
        )
    except JobQueueUnavailableError as exc:
        logger.error(
            "Processing queue unavailable media_id=%s operation=%s",
            media_id,
            request.operation.value,
        )
        raise AppError(
            "Processing queue is temporarily unavailable",
            code="job_queue_unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc

    logger.info(
        "Processing job queued job_id=%s media_id=%s operation=%s",
        job_id,
        media_id,
        request.operation.value,
    )
    return JobCreateResponse(
        job_id=job_id,
        media_id=media_id,
        operation=request.operation,
        status=JobState.QUEUED,
    )


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    response_model_exclude_none=True,
)
async def get_job_status(
    job_id: str,
    job_queue: JobQueue = Depends(get_job_queue),
) -> JobStatusResponse:
    canonical_job_id = _canonical_uuid(job_id, "job ID", "invalid_job_id")
    try:
        record = await job_queue.get(canonical_job_id)
    except JobNotFoundError as exc:
        raise AppError(
            "Processing job was not found",
            code="job_not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from exc
    except JobQueueUnavailableError as exc:
        raise AppError(
            "Processing queue is temporarily unavailable",
            code="job_queue_unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    except JobQueueStateError as exc:
        logger.exception("Invalid processing job state job_id=%s", canonical_job_id)
        raise AppError(
            "Processing job state is unavailable",
            code="invalid_job_state",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from exc

    output = (
        JobOutputReference(
            output_id=record.output_id,
            format=record.output_format,
            compression_level=record.compression_level,
            original_size=record.original_size,
            compressed_size=record.compressed_size,
            saved_bytes=record.saved_bytes,
            reduction_percentage=record.reduction_percentage,
            compression_effective=record.compression_effective,
        )
        if record.status is JobState.COMPLETED
        else None
    )
    return JobStatusResponse(
        job_id=record.job_id,
        media_id=record.media_id,
        operation=record.operation,
        status=record.status,
        output=output,
        progress=record.progress,
        error=record.error,
    )


def _canonical_uuid(value: str, label: str, code: str) -> str:
    try:
        return str(UUID(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise AppError(f"Invalid {label}", code=code) from exc
