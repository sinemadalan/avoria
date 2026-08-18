from pathlib import Path
from typing import Protocol


class StorageService(Protocol):
    """Central boundary for resolving Avoria-owned filesystem locations."""

    def initialize(self) -> None: ...

    def source_directory(self, user_id: str, media_id: str) -> Path: ...

    def output_directory(self, user_id: str, job_id: str) -> Path: ...

    def temporary_directory(self, user_id: str, job_id: str) -> Path: ...

