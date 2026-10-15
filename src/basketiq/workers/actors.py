"""Dramatiq worker entrypoint: `dramatiq basketiq.workers.actors`."""

from __future__ import annotations

import uuid
from functools import lru_cache

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from basketiq.config import get_settings
from basketiq.db.session import get_sessionmaker
from basketiq.observability.logging import configure_logging
from basketiq.pipeline.external import build_external
from basketiq.pipeline.llm import build_resolver
from basketiq.pipeline.ocr import build_ocr_engine
from basketiq.queue import ACTOR_NAME, QUEUE_NAME, get_queue
from basketiq.services.processing import Components, process_job
from basketiq.storage import get_storage

settings = get_settings()
configure_logging(settings.log_level)
broker = RedisBroker(url=settings.redis_url)
dramatiq.set_broker(broker)


@lru_cache
def components() -> Components:
    # Built once per worker process: PaddleOCR models load lazily on first use.
    return Components(
        ocr=build_ocr_engine(settings),
        resolver=build_resolver(settings),
        external=build_external(settings.external_fallback),
    )


# Retries are handled by our own bookkeeping in PostgreSQL (attempt_count, backoff, sweeper).
@dramatiq.actor(actor_name=ACTOR_NAME, queue_name=QUEUE_NAME, max_retries=0, time_limit=10 * 60 * 1000)
def process_job_actor(job_id: str) -> None:
    process_job(
        uuid.UUID(job_id),
        sessions=get_sessionmaker(),
        settings=settings,
        storage=get_storage(),
        queue=get_queue(),
        components=components(),
    )
