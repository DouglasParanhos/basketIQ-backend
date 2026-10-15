"""Audit trail. Payloads hold identifiers, codes and counts only — never receipt content or e-mail."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from basketiq.db.models import AuditEvent
from basketiq.domain.status import ActorType
from basketiq.observability.logging import redact


def record(
    db: Session,
    event_type: str,
    *,
    user_id: uuid.UUID | None = None,
    receipt_id: uuid.UUID | None = None,
    actor: ActorType = ActorType.USER,
    **payload: Any,
) -> None:
    db.add(
        AuditEvent(
            user_id=user_id,
            receipt_id=receipt_id,
            actor_type=actor,
            event_type=event_type,
            payload_redacted=redact({k: _jsonable(v) for k, v in payload.items()}),
        )
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    return value
