"""Job queue adapter. Messages carry only the job id; all state lives in PostgreSQL, so duplicate
or replayed messages are harmless (the worker claims the job atomically)."""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Protocol

from basketiq.config import get_settings

QUEUE_NAME = "extraction"
ACTOR_NAME = "process_job"


class JobQueue(Protocol):
    def enqueue(self, job_id: uuid.UUID, delay_ms: int = 0) -> None: ...


class DramatiqQueue:
    def __init__(self, redis_url: str) -> None:
        from dramatiq.brokers.redis import RedisBroker

        self._broker = RedisBroker(url=redis_url)
        self._broker.declare_queue(QUEUE_NAME)

    def enqueue(self, job_id: uuid.UUID, delay_ms: int = 0) -> None:
        from dramatiq import Message

        message: Message = Message(
            queue_name=QUEUE_NAME, actor_name=ACTOR_NAME, args=(str(job_id),), kwargs={}, options={}
        )
        self._broker.enqueue(message, delay=delay_ms or None)


class InlineQueue:
    """Collects job ids; tests decide when (and whether) to run them."""

    def __init__(self) -> None:
        self.pending: list[tuple[uuid.UUID, int]] = []

    def enqueue(self, job_id: uuid.UUID, delay_ms: int = 0) -> None:
        self.pending.append((job_id, delay_ms))

    def drain(self) -> list[uuid.UUID]:
        ids = [job_id for job_id, _ in self.pending]
        self.pending.clear()
        return ids


@lru_cache
def get_queue() -> JobQueue:
    settings = get_settings()
    if settings.queue_backend == "inline":
        return InlineQueue()
    return DramatiqQueue(settings.redis_url)
