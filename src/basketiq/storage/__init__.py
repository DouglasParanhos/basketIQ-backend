from __future__ import annotations

from functools import lru_cache

from basketiq.config import get_settings
from basketiq.storage.base import (
    ObjectNotFoundError,
    ObjectStorage,
    PresignedUpload,
    StorageUnavailableError,
    original_key,
    photo_key,
    receipt_prefix,
    user_prefix,
)


@lru_cache
def get_storage() -> ObjectStorage:
    settings = get_settings()
    if settings.storage_backend == "memory":
        from basketiq.storage.memory import MemoryStorage

        return MemoryStorage()
    from basketiq.storage.s3 import S3Storage

    return S3Storage(settings)


__all__ = [
    "ObjectNotFoundError",
    "ObjectStorage",
    "PresignedUpload",
    "StorageUnavailableError",
    "get_storage",
    "original_key",
    "photo_key",
    "receipt_prefix",
    "user_prefix",
]
