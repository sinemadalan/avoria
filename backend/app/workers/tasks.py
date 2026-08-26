import asyncio
import logging
from time import monotonic
from typing import Any

from celery import Task

from backend.app.application.ports.jobs import JobOperation
from backend.app.application.ports.storage import (
    MediaNotFoundError,
    OutputTarget,
    StorageService,
)
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
from backend.app.processing.crop import (
    CropProfile,
    CropService,
    CropSpec,
    get_crop_service,
)
from backend.app.processing.mute import MuteService, get_mute_service
from backend.app.processing.merge_videos import (
    MergeVideosProfile,
    MergeVideosService,
    MergeVideosSpec,
    MergeMediaNotFoundError,
    get_merge_videos_service,
)
from backend.app.processing.replace_audio import (
    ExternalAudioMediaNotFoundError,
    ReplaceAudioProfile,
    ReplaceAudioService,
    ReplaceAudioSpec,
    get_replace_audio_service,
)
from backend.app.processing.speed import (
    SpeedProfile,
    SpeedService,
    SpeedSpec,
    get_speed_service,
)
from backend.app.processing.trim import (
    TrimProfile,
    TrimService,
    TrimSpec,
    get_trim_service,
)
from backend.app.processing.volume import VolumeService, VolumeSpec, get_volume_service

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
    parameters: dict[str, Any],
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
    volume = (
        VolumeSpec.from_payload(parameters)
        if job_operation is JobOperation.VOLUME
        else None
    )
    trim = (
        TrimSpec.from_payload(parameters)
        if job_operation is JobOperation.TRIM
        else None
    )
    speed = (
        SpeedSpec.from_payload(parameters)
        if job_operation is JobOperation.SPEED
        else None
    )
    replace_audio = (
        ReplaceAudioSpec.from_payload(parameters)
        if job_operation is JobOperation.REPLACE_AUDIO
        else None
    )
    crop = (
        CropSpec.from_payload(parameters)
        if job_operation is JobOperation.CROP
        else None
    )
    merge_videos = (
        MergeVideosSpec.from_payload(parameters)
        if job_operation is JobOperation.MERGE_VIDEOS
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
        muter=get_mute_service() if job_operation is JobOperation.MUTE else None,
        volume_adjuster=(
            get_volume_service() if job_operation is JobOperation.VOLUME else None
        ),
        volume=volume,
        trimmer=get_trim_service() if trim else None,
        trim=trim,
        speed_changer=get_speed_service() if speed else None,
        speed=speed,
        audio_replacer=get_replace_audio_service() if replace_audio else None,
        replace_audio=replace_audio,
        cropper=get_crop_service() if crop else None,
        crop=crop,
        merge_videos_service=(
            get_merge_videos_service() if merge_videos else None
        ),
        merge_videos=merge_videos,
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
    muter: MuteService | None = None,
    volume_adjuster: VolumeService | None = None,
    volume: VolumeSpec | None = None,
    trimmer: TrimService | None = None,
    trim: TrimSpec | None = None,
    speed_changer: SpeedService | None = None,
    speed: SpeedSpec | None = None,
    audio_replacer: ReplaceAudioService | None = None,
    replace_audio: ReplaceAudioSpec | None = None,
    cropper: CropService | None = None,
    crop: CropSpec | None = None,
    merge_videos_service: MergeVideosService | None = None,
    merge_videos: MergeVideosSpec | None = None,
) -> dict[str, Any]:
    started_at = monotonic()
    target: OutputTarget | None = None
    statistics: CompressionStatistics | None = None
    compression_profile: CompressionProfile | None = None
    extraction_profile: AudioExtractionProfile | None = None
    mute_profile: CompressionProfile | None = None
    volume_profile: CompressionProfile | None = None
    trim_profile: TrimProfile | None = None
    speed_profile: SpeedProfile | None = None
    replace_audio_profile: ReplaceAudioProfile | None = None
    crop_profile: CropProfile | None = None
    merge_profile: MergeVideosProfile | None = None
    merge_input_paths = None
    external_audio_path = None
    logger.info(
        "Processing job started job_id=%s media_id=%s operation=%s",
        job_id,
        media_id,
        operation.value,
    )
    try:
        if operation is JobOperation.MERGE_VIDEOS:
            if merge_videos_service is None or merge_videos is None:
                raise ValueError("Merge-video dependencies are unavailable")
            try:
                merge_input_paths = [
                    asyncio.run(storage.resolve_upload(item, allowed_extensions))
                    for item in merge_videos.media_ids
                ]
            except MediaNotFoundError as exc:
                raise MergeMediaNotFoundError from exc
            input_path = merge_input_paths[0]
        else:
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
        elif operation is JobOperation.MUTE:
            if muter is None:
                raise ValueError("Mute dependencies are unavailable")
            mute_profile = muter.resolve_profile(input_path)
            extension = mute_profile.extension
        elif operation is JobOperation.VOLUME:
            if volume_adjuster is None or volume is None:
                raise ValueError("Volume dependencies are unavailable")
            volume_profile = volume_adjuster.resolve_profile(input_path)
            extension = volume_profile.extension
        elif operation is JobOperation.TRIM:
            if trimmer is None or trim is None:
                raise ValueError("Trim dependencies are unavailable")
            trim_profile = trimmer.resolve_profile(input_path, trim)
            extension = trim_profile.extension
        elif operation is JobOperation.SPEED:
            if speed_changer is None or speed is None:
                raise ValueError("Speed dependencies are unavailable")
            speed_profile = speed_changer.resolve_profile(input_path)
            extension = speed_profile.extension
        elif operation is JobOperation.REPLACE_AUDIO:
            if audio_replacer is None or replace_audio is None:
                raise ValueError("Replace-audio dependencies are unavailable")
            try:
                external_audio_path = asyncio.run(
                    storage.resolve_upload(
                        replace_audio.audio_media_id,
                        allowed_extensions,
                    )
                )
            except MediaNotFoundError as exc:
                raise ExternalAudioMediaNotFoundError from exc
            replace_audio_profile = audio_replacer.resolve_profile(
                input_path,
                external_audio_path,
            )
            extension = replace_audio_profile.target.extension
        elif operation is JobOperation.CROP:
            if cropper is None or crop is None:
                raise ValueError("Crop dependencies are unavailable")
            crop_profile = cropper.resolve_profile(input_path, crop)
            extension = crop_profile.media.extension
        elif operation is JobOperation.MERGE_VIDEOS:
            if merge_videos_service is None or merge_input_paths is None:
                raise ValueError("Merge-video dependencies are unavailable")
            merge_profile = merge_videos_service.resolve_profile(merge_input_paths)
            extension = merge_profile.extension
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
        elif operation is JobOperation.EXTRACT_AUDIO:
            audio_extractor.extract(
                input_path,
                target.temporary_path,
                extraction,
                extraction_profile,
            )
        elif operation is JobOperation.MUTE:
            muter.mute(input_path, target.temporary_path, mute_profile)
        elif operation is JobOperation.VOLUME:
            volume_adjuster.adjust(
                input_path,
                target.temporary_path,
                volume,
                volume_profile,
            )
        elif operation is JobOperation.TRIM:
            trimmer.trim(input_path, target.temporary_path, trim, trim_profile)
        elif operation is JobOperation.SPEED:
            speed_changer.change_speed(
                input_path,
                target.temporary_path,
                speed,
                speed_profile,
            )
        elif operation is JobOperation.REPLACE_AUDIO:
            audio_replacer.replace(
                input_path,
                external_audio_path,
                target.temporary_path,
                replace_audio_profile,
                loop=replace_audio.loop,
            )
        elif operation is JobOperation.CROP:
            cropper.crop(input_path, target.temporary_path, crop, crop_profile)
        elif operation is JobOperation.MERGE_VIDEOS:
            merge_videos_service.merge(
                merge_input_paths,
                target.temporary_path,
                merge_profile,
            )
        else:
            raise ValueError("Unsupported processing operation")
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
