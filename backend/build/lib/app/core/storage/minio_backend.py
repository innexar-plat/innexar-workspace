"""MinIO S3-compatible storage backend."""
import asyncio
import io
from typing import BinaryIO

from app.core.config import settings


def _get_client():
    """Lazy import minio to avoid dependency when not used."""
    try:
        from minio import Minio
    except ImportError:
        raise RuntimeError(
            "minio package not installed. pip install minio"
        ) from None
    secure = getattr(settings, "MINIO_SECURE", False)
    endpoint = settings.MINIO_ENDPOINT.replace("http://", "").replace("https://", "").split("/")[0]
    return Minio(
        endpoint,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=secure,
    )


class MinIOBackend:
    """MinIO implementation of StorageBackend. Sync MinIO calls run in thread pool."""

    def __init__(self) -> None:
        self._client = _get_client()
        self._bucket = settings.MINIO_BUCKET_PROJECTS

    async def ensure_bucket_exists(self) -> None:
        def _exists() -> bool:
            return self._client.bucket_exists(self._bucket)

        def _make() -> None:
            self._client.make_bucket(self._bucket)

        exists = await asyncio.to_thread(_exists)
        if not exists:
            await asyncio.to_thread(_make)

    async def put(self, key: str, body: BinaryIO | bytes, content_type: str | None = None) -> None:
        await self.ensure_bucket_exists()
        data = body.read() if hasattr(body, "read") else body
        length = len(data) if isinstance(data, bytes) else 0
        stream = io.BytesIO(data) if isinstance(data, bytes) else body
        ct = content_type or "application/octet-stream"

        def _put() -> None:
            self._client.put_object(self._bucket, key, stream, length, content_type=ct)

        await asyncio.to_thread(_put)

    async def get(self, key: str) -> tuple[bytes, str | None]:
        def _get() -> tuple[bytes, str | None]:
            response = self._client.get_object(self._bucket, key)
            try:
                content = response.read()
                content_type = getattr(response, "headers", {}).get("Content-Type")
                return (content, content_type)
            finally:
                response.close()
                response.release_conn()

        return await asyncio.to_thread(_get)

    async def list_prefix(self, prefix: str) -> list[str]:
        await self.ensure_bucket_exists()

        def _list() -> list[str]:
            objects = self._client.list_objects(self._bucket, prefix=prefix, recursive=True)
            return [obj.object_name for obj in objects]

        return await asyncio.to_thread(_list)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.remove_object, self._bucket, key)
