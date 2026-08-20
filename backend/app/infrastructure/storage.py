import os
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from starlette.concurrency import run_in_threadpool

from backend.app.application.ports.storage import (
    AmbiguousMediaError,
    AsyncReadable,
    EmptyUploadError,
    InvalidMediaIdError,
    MediaNotFoundError,
    OutputStorageError,
    OutputTarget,
    StorageService,
    StorageWriteError,
    UploadSizeLimitExceeded,
)
from backend.app.core.config import get_settings
from backend.app.core.errors import AppError


class LocalStorageService(StorageService):
    def __init__(
        self,
        root: Path,
        upload_directory: Path | None = None,
        processing_output_directory: Path | None = None,
    ) -> None:
        self.root = root.resolve()
        self.upload_directory = (upload_directory or root / "uploads").resolve()
        self.processing_outputs_root = (
            processing_output_directory or root / "outputs"
        ).resolve()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.upload_directory.mkdir(parents=True, exist_ok=True)
        self.processing_outputs_root.mkdir(parents=True, exist_ok=True)

    async def save_upload(
        self,
        source: AsyncReadable,
        filename: str,
        *,
        chunk_size: int,
        max_size: int,
    ) -> int:
        safe_filename = self._validate_upload_filename(filename)
        destination = self._resolve_upload_path(safe_filename)
        partial = self._resolve_upload_path(f".{safe_filename}.part")
        handle: BinaryIO | None = None
        total_bytes = 0

        try:
            await run_in_threadpool(self.upload_directory.mkdir, parents=True, exist_ok=True)
            handle = await run_in_threadpool(partial.open, "xb")
            while chunk := await source.read(chunk_size):
                total_bytes += len(chunk)
                if total_bytes > max_size:
                    raise UploadSizeLimitExceeded
                await run_in_threadpool(handle.write, chunk)

            if total_bytes == 0:
                raise EmptyUploadError

            await run_in_threadpool(handle.close)
            handle = None
            await run_in_threadpool(os.replace, partial, destination)
            return total_bytes
        except (EmptyUploadError, UploadSizeLimitExceeded):
            raise
        except Exception as exc:
            raise StorageWriteError from exc
        finally:
            await run_in_threadpool(self._cleanup_partial, handle, partial)

    async def resolve_upload(
        self,
        media_id: str,
        allowed_extensions: list[str],
    ) -> Path:
        canonical_media_id = self._canonical_media_id(media_id)
        return await run_in_threadpool(
            self._find_upload,
            canonical_media_id,
            allowed_extensions,
        )

    def prepare_output(self, output_id: str, extension: str) -> OutputTarget:
        try:
            canonical_output_id = self._canonical_media_id(output_id)
        except InvalidMediaIdError as exc:
            raise OutputStorageError from exc
        safe_extension = extension.casefold()
        if not self._is_safe_media_extension(safe_extension):
            raise OutputStorageError

        filename = f"{canonical_output_id}{safe_extension}"
        temporary_filename = f"{canonical_output_id}.part{safe_extension}"
        target = OutputTarget(
            output_id=canonical_output_id,
            filename=filename,
            temporary_path=self._resolve_processing_output_path(temporary_filename),
            final_path=self._resolve_processing_output_path(filename),
        )
        try:
            self.processing_outputs_root.mkdir(parents=True, exist_ok=True)
            target.temporary_path.unlink(missing_ok=True)
        except OSError as exc:
            raise OutputStorageError from exc
        return target

    def finalize_output(self, target: OutputTarget) -> None:
        try:
            if not target.temporary_path.is_file() or target.temporary_path.stat().st_size == 0:
                raise OutputStorageError
            os.replace(target.temporary_path, target.final_path)
        except OutputStorageError:
            raise
        except OSError as exc:
            raise OutputStorageError from exc

    def cleanup_output(self, target: OutputTarget) -> None:
        try:
            target.temporary_path.unlink(missing_ok=True)
        except OSError as exc:
            raise OutputStorageError from exc

    def source_directory(self, user_id: str, media_id: str) -> Path:
        return self._resolve_user_path(user_id, "sources", media_id)

    def output_directory(self, user_id: str, job_id: str) -> Path:
        return self._resolve_user_path(user_id, "outputs", job_id)

    def temporary_directory(self, user_id: str, job_id: str) -> Path:
        return self._resolve_user_path(user_id, "temp", job_id)

    def _resolve_user_path(self, user_id: str, category: str, resource_id: str) -> Path:
        safe_user_id = self._validate_identifier(user_id, "user_id")
        safe_resource_id = self._validate_identifier(resource_id, "resource_id")
        candidate = (self.root / "users" / safe_user_id / category / safe_resource_id).resolve()
        if self.root not in candidate.parents:
            raise AppError("Storage path is outside the configured root", code="invalid_storage_path")
        return candidate

    def _resolve_upload_path(self, filename: str) -> Path:
        candidate = (self.upload_directory / filename).resolve()
        if candidate.parent != self.upload_directory:
            raise AppError("Invalid upload filename", code="invalid_storage_identifier")
        return candidate

    def _resolve_processing_output_path(self, filename: str) -> Path:
        candidate = (self.processing_outputs_root / filename).resolve()
        if candidate.parent != self.processing_outputs_root:
            raise OutputStorageError
        return candidate

    def _find_upload(self, media_id: str, allowed_extensions: list[str]) -> Path:
        matches: list[Path] = []
        for extension in {item.casefold() for item in allowed_extensions}:
            if not self._is_safe_media_extension(extension):
                continue
            candidate = self._resolve_upload_path(f"{media_id}{extension}")
            if candidate.is_file():
                matches.append(candidate)

        if not matches:
            raise MediaNotFoundError
        if len(matches) > 1:
            raise AmbiguousMediaError
        return matches[0]

    @staticmethod
    def _canonical_media_id(media_id: str) -> str:
        try:
            return str(UUID(media_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise InvalidMediaIdError from exc

    @staticmethod
    def _is_safe_media_extension(extension: str) -> bool:
        return (
            extension.startswith(".")
            and extension != ".part"
            and Path(extension).name == extension
        )

    @staticmethod
    def _cleanup_partial(handle: BinaryIO | None, partial: Path) -> None:
        close_error: OSError | None = None
        if handle is not None:
            try:
                handle.close()
            except OSError as exc:
                close_error = exc

        try:
            partial.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageWriteError from exc
        if close_error is not None:
            raise StorageWriteError from close_error

    @staticmethod
    def _validate_upload_filename(filename: str) -> str:
        path = Path(filename)
        if not filename or path.name != filename or filename in {".", ".."}:
            raise AppError("Invalid upload filename", code="invalid_storage_identifier")
        return filename

    @staticmethod
    def _validate_identifier(value: str, field_name: str) -> str:
        if not value or not all(character.isalnum() or character in "-_" for character in value):
            raise AppError(
                f"Invalid {field_name}",
                code="invalid_storage_identifier",
                details={"field": field_name},
            )
        return value


@lru_cache
def get_storage_service() -> LocalStorageService:
    settings = get_settings()
    return LocalStorageService(
        settings.storage_root,
        settings.upload_directory,
        settings.output_directory,
    )
