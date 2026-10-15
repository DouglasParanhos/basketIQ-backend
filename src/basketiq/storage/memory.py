from __future__ import annotations

from datetime import UTC, datetime, timedelta

from basketiq.storage.base import ObjectNotFoundError, PresignedUpload, StorageUnavailableError


class MemoryStorage:
    """In-process storage for tests. `available=False` simulates an outage."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.available = True

    def _check(self) -> None:
        if not self.available:
            raise StorageUnavailableError("storage offline")

    def ensure_bucket(self, allowed_origins: list[str]) -> None:
        self._check()

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._check()
        self.objects[key] = (data, content_type)

    def presign_upload(
        self, key: str, content_type: str, max_bytes: int, ttl_seconds: int
    ) -> PresignedUpload:
        self._check()
        return PresignedUpload(
            url="memory://upload",
            fields={"key": key, "Content-Type": content_type},
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )

    def presign_download(self, key: str, ttl_seconds: int, content_type: str | None = None) -> str:
        self._check()
        return f"memory://download/{key}"

    def exists(self, key: str) -> bool:
        self._check()
        return key in self.objects

    def size(self, key: str) -> int:
        self._check()
        if key not in self.objects:
            raise ObjectNotFoundError(key)
        return len(self.objects[key][0])

    def get(self, key: str, max_bytes: int) -> bytes:
        self._check()
        if key not in self.objects:
            raise ObjectNotFoundError(key)
        return self.objects[key][0][: max_bytes + 1]

    def delete_prefix(self, prefix: str) -> int:
        self._check()
        keys = [k for k in self.objects if k.startswith(prefix)]
        for k in keys:
            del self.objects[k]
        return len(keys)
