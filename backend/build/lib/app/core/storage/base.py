"""Storage backend interface (S3-compatible)."""
from abc import ABC, abstractmethod
from typing import BinaryIO


class StorageBackend(ABC):
    """Abstract storage backend: put, get, list, delete. All methods are async."""

    @abstractmethod
    async def put(self, key: str, body: BinaryIO | bytes, content_type: str | None = None) -> None:
        """Upload object. Key is full path (e.g. projects/123/file.pdf)."""

    @abstractmethod
    async def get(self, key: str) -> tuple[bytes, str | None]:
        """Download object. Returns (content, content_type)."""

    @abstractmethod
    async def list_prefix(self, prefix: str) -> list[str]:
        """List object keys under prefix."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete object by key."""

    @abstractmethod
    async def ensure_bucket_exists(self) -> None:
        """Create bucket if it does not exist."""
