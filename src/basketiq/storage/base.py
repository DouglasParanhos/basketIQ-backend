from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class StorageUnavailableError(RuntimeError):
    pass


class ObjectNotFoundError(KeyError):
    pass


@dataclass
class PresignedUpload:
    url: str
    fields: dict[str, str]
    expires_at: datetime


class ObjectStorage(Protocol):
    def ensure_bucket(self, allowed_origins: list[str]) -> None: ...

    def presign_upload(
        self, key: str, content_type: str, max_bytes: int, ttl_seconds: int
    ) -> PresignedUpload: ...

    def presign_download(self, key: str, ttl_seconds: int, content_type: str | None = None) -> str: ...

    def exists(self, key: str) -> bool: ...

    def size(self, key: str) -> int: ...

    def get(self, key: str, max_bytes: int) -> bytes: ...

    def delete_prefix(self, prefix: str) -> int: ...


def receipt_prefix(user_id: object, receipt_id: object) -> str:
    return f"users/{user_id}/receipts/{receipt_id}/"


def original_key(user_id: object, receipt_id: object) -> str:
    # No user-provided file name in the key: names can contain personal data.
    return receipt_prefix(user_id, receipt_id) + "original"


def user_prefix(user_id: object) -> str:
    return f"users/{user_id}/"
