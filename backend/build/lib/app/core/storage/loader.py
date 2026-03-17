"""Storage backend loader: MinIO or no-op when disabled."""
from functools import lru_cache
from typing import cast

from app.core.config import settings
from app.core.storage.base import StorageBackend
from app.core.storage.minio_backend import MinIOBackend


class NoOpStorageBackend(StorageBackend):
    """No-op backend when storage is disabled: all operations no-op or raise."""

    async def put(self, key: str, body: object, content_type: str | None = None) -> None:
        raise RuntimeError("Storage is not configured. Set STORAGE_PROVIDER=minio and MINIO_* env.")

    async def get(self, key: str) -> tuple[bytes, str | None]:
        raise RuntimeError("Storage is not configured.")

    async def list_prefix(self, prefix: str) -> list[str]:
        return []

    async def delete(self, key: str) -> None:
        pass

    async def ensure_bucket_exists(self) -> None:
        pass


@lru_cache(maxsize=1)
def get_storage_backend() -> StorageBackend:
    """Return storage backend from STORAGE_PROVIDER. Cached singleton."""
    provider = (settings.STORAGE_PROVIDER or "").lower()
    if provider == "minio":
        return cast(StorageBackend, MinIOBackend())
    return cast(StorageBackend, NoOpStorageBackend())
