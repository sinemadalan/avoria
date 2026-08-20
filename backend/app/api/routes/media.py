from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, UploadFile, status
from starlette.concurrency import run_in_threadpool

from backend.app.application.ports.storage import (
    AmbiguousMediaError,
    EmptyUploadError,
    InvalidMediaIdError,
    MediaNotFoundError,
    StorageService,
    StorageWriteError,
    UploadSizeLimitExceeded,
)
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import AppError
from backend.app.core.logging import get_logger
from backend.app.infrastructure.storage import get_storage_service
from backend.app.processing.probe import (
    FFprobeExecutableNotFoundError,
    FFprobeProcessError,
    FFprobeTimeoutError,
    InvalidMediaError,
    SyncFFprobeInspector,
    get_sync_ffprobe_inspector,
)
from backend.app.processing.conversion import (
    FFmpegCapabilityDetector,
    FFmpegCapabilityError,
    FFmpegExecutableNotFoundError as ConversionFFmpegNotFoundError,
    ConversionSpec,
    available_conversion_capabilities,
    get_ffmpeg_capability_detector,
)
from backend.app.schemas.media import (
    ConversionContainerOption,
    ConversionOptionsResponse,
    MediaInspectionResponse,
    MediaUploadResponse,
)

router = APIRouter(prefix="/media", tags=["media"])
logger = get_logger(__name__)


@router.post(
    "/upload",
    response_model=MediaUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_media(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    storage: StorageService = Depends(get_storage_service),
) -> MediaUploadResponse:
    original_filename = file.filename
    if not original_filename or not original_filename.strip():
        logger.warning("Media upload rejected: missing filename")
        raise AppError("A filename is required", code="missing_filename")

    extension = Path(original_filename).suffix.casefold()
    allowed_extensions = {item.casefold() for item in settings.allowed_media_extensions}
    if extension not in allowed_extensions:
        logger.warning(
            "Media upload rejected: unsupported extension original_filename=%r extension=%r",
            original_filename,
            extension,
        )
        raise AppError(
            "Unsupported media extension",
            code="unsupported_media_extension",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            details={"extension": extension or None},
        )

    media_id = str(uuid4())
    stored_filename = f"{media_id}{extension}"
    try:
        size_bytes = await storage.save_upload(
            file,
            stored_filename,
            chunk_size=settings.upload_chunk_size_bytes,
            max_size=settings.max_upload_size_bytes,
        )
    except EmptyUploadError as exc:
        logger.warning(
            "Media upload rejected: empty file media_id=%s original_filename=%r",
            media_id,
            original_filename,
        )
        raise AppError("Uploaded file is empty", code="empty_file") from exc
    except UploadSizeLimitExceeded as exc:
        logger.warning(
            "Media upload rejected: size limit exceeded media_id=%s original_filename=%r",
            media_id,
            original_filename,
        )
        raise AppError(
            "Upload exceeds the maximum allowed size",
            code="upload_too_large",
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            details={"max_size_bytes": settings.max_upload_size_bytes},
        ) from exc
    except StorageWriteError as exc:
        logger.exception(
            "Media upload storage failure media_id=%s original_filename=%r",
            media_id,
            original_filename,
        )
        raise AppError(
            "The upload could not be stored",
            code="storage_write_failure",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from exc
    finally:
        await file.close()

    logger.info(
        "Media upload completed media_id=%s original_filename=%r stored_filename=%s size_bytes=%d",
        media_id,
        original_filename,
        stored_filename,
        size_bytes,
    )
    return MediaUploadResponse(
        media_id=media_id,
        original_filename=original_filename,
        filename=stored_filename,
        content_type=file.content_type,
        size_bytes=size_bytes,
    )


@router.get(
    "/conversion-options",
    response_model=ConversionOptionsResponse,
)
def get_conversion_options(
    detector: FFmpegCapabilityDetector = Depends(get_ffmpeg_capability_detector),
) -> ConversionOptionsResponse:
    try:
        capabilities = available_conversion_capabilities(detector.detect())
    except ConversionFFmpegNotFoundError as exc:
        raise AppError(
            "Conversion options are temporarily unavailable",
            code="ffmpeg_unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    except FFmpegCapabilityError as exc:
        raise AppError(
            "Conversion options are temporarily unavailable",
            code="ffmpeg_capabilities_unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc

    return ConversionOptionsResponse(
        defaults=ConversionSpec().to_payload(),
        containers=[
            ConversionContainerOption(
                id=item.id,
                label=item.label,
                extension=item.extension,
                video_codecs=list(item.video_codecs),
                audio_codecs=list(item.audio_codecs),
            )
            for item in capabilities
        ],
    )


@router.post(
    "/{media_id}/inspect",
    response_model=MediaInspectionResponse,
)
async def inspect_media(
    media_id: str,
    settings: Settings = Depends(get_settings),
    storage: StorageService = Depends(get_storage_service),
    inspector: SyncFFprobeInspector = Depends(get_sync_ffprobe_inspector),
) -> MediaInspectionResponse:
    canonical_media_id = _canonical_media_id(media_id)
    try:
        media_path = await storage.resolve_upload(
            canonical_media_id,
            settings.allowed_media_extensions,
        )
    except InvalidMediaIdError as exc:
        raise _invalid_media_id_error() from exc
    except MediaNotFoundError as exc:
        raise AppError(
            "Media file was not found",
            code="media_not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from exc
    except AmbiguousMediaError as exc:
        logger.error("Media inspection found ambiguous uploads media_id=%s", canonical_media_id)
        raise AppError(
            "Multiple media files match this identifier",
            code="ambiguous_media",
            status_code=status.HTTP_409_CONFLICT,
        ) from exc

    try:
        inspection = await run_in_threadpool(inspector.inspect, media_path)
    except InvalidMediaError as exc:
        logger.warning("Media inspection rejected invalid media media_id=%s", canonical_media_id)
        raise AppError(
            "The file is not valid or readable media",
            code="invalid_media",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from exc
    except FFprobeExecutableNotFoundError as exc:
        logger.error("FFprobe executable is unavailable media_id=%s", canonical_media_id)
        raise AppError(
            "Media inspection is temporarily unavailable",
            code="ffprobe_unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    except FFprobeTimeoutError as exc:
        logger.warning("Media inspection timed out media_id=%s", canonical_media_id)
        raise AppError(
            "Media inspection timed out",
            code="ffprobe_timeout",
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        ) from exc
    except FFprobeProcessError as exc:
        logger.exception("FFprobe internal failure media_id=%s", canonical_media_id)
        raise AppError(
            "Media inspection failed",
            code="ffprobe_process_failure",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from exc

    logger.info(
        (
            "Media inspection completed media_id=%s format=%s duration_seconds=%s "
            "video_streams=%d audio_streams=%d"
        ),
        canonical_media_id,
        inspection.format.name,
        inspection.format.duration_seconds,
        len(inspection.video_streams),
        len(inspection.audio_streams),
    )
    return MediaInspectionResponse.model_validate(
        {
            "media_id": canonical_media_id,
            "format": inspection.format,
            "stream_count": inspection.stream_count,
            "video_streams": inspection.video_streams,
            "audio_streams": inspection.audio_streams,
        }
    )


def _canonical_media_id(media_id: str) -> str:
    try:
        return str(UUID(media_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise _invalid_media_id_error() from exc


def _invalid_media_id_error() -> AppError:
    return AppError("Invalid media ID", code="invalid_media_id")
