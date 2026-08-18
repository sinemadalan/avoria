import pytest

from backend.app.core.errors import AppError
from backend.app.infrastructure.storage import LocalStorageService


def test_storage_paths_stay_under_root(tmp_path) -> None:
    storage = LocalStorageService(tmp_path)

    path = storage.output_directory("user-1", "job-1")

    assert path == (tmp_path / "users" / "user-1" / "outputs" / "job-1").resolve()


def test_storage_rejects_path_traversal(tmp_path) -> None:
    storage = LocalStorageService(tmp_path)

    with pytest.raises(AppError):
        storage.source_directory("../other", "media-1")

