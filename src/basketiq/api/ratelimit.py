from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from basketiq.config import get_settings


def _key(request: Request) -> str:
    # Authenticated limits are per user; anonymous ones (login) per client address.
    user_id = getattr(request.state, "user_id", None)
    return f"user:{user_id}" if user_id else f"ip:{get_remote_address(request)}"


def _build() -> Limiter:
    settings = get_settings()
    storage = "memory://" if settings.env == "test" else settings.redis_url
    return Limiter(
        key_func=_key, storage_uri=storage, enabled=settings.rate_limit_enabled, headers_enabled=False
    )


limiter = _build()
