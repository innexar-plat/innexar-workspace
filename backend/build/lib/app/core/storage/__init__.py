"""Storage abstraction: S3-compatible backends (MinIO, S3, R2)."""
from app.core.storage.base import StorageBackend
from app.core.storage.loader import get_storage_backend

__all__ = ["StorageBackend", "get_storage_backend"]
