from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class AsyncReadable(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


class UploadSizeLimitExceeded(Exception):
    """Raised when a streamed upload exceeds its configured byte limit."""


class EmptyUploadError(Exception):
    """Raised when an upload stream contains no bytes."""


class StorageWriteError(Exception):
    """Raised when storage cannot persist an upload."""


class InvalidMediaIdError(Exception):
    """Raised when a media identifier is not a valid UUID."""


class MediaNotFoundError(Exception):
    """Raised when no supported upload exists for a media identifier."""


class AmbiguousMediaError(Exception):
    """Raised when multiple supported uploads share a media identifier."""


class OutputStorageError(Exception):
    """Raised when a processing output cannot be prepared or finalized."""


@dataclass(frozen=True, slots=True)
class OutputTarget:
    output_id: str
    filename: str
    temporary_path: Path
    final_path: Path


class StorageService(Protocol):
    """Central boundary for resolving Avoria-owned filesystem locations."""

    def initialize(self) -> None: ...

    async def save_upload(
        self,
        source: AsyncReadable,
        filename: str,
        *,
        chunk_size: int,
        max_size: int,
    ) -> int: ...

    async def resolve_upload(
        self,
        media_id: str,
        allowed_extensions: list[str],
    ) -> Path: ...

    def prepare_output(self, output_id: str, extension: str) -> OutputTarget: ...

    def finalize_output(self, target: OutputTarget) -> None: ...

    def cleanup_output(self, target: OutputTarget) -> None: ...

    def source_directory(self, user_id: str, media_id: str) -> Path: ...

    def output_directory(self, user_id: str, job_id: str) -> Path: ...

    def temporary_directory(self, user_id: str, job_id: str) -> Path: ...
