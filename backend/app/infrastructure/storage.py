from functools import lru_cache
from pathlib import Path

from backend.app.application.ports.storage import StorageService
from backend.app.core.config import get_settings
from backend.app.core.errors import AppError


class LocalStorageService(StorageService):
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

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
    return LocalStorageService(get_settings().storage_root)

