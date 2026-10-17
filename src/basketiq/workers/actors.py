"""Dramatiq worker entrypoint: `dramatiq basketiq.workers.actors`."""

from __future__ import annotations

import uuid
from functools import lru_cache

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from basketiq.app_config import get_app_config
from basketiq.config import get_settings
from basketiq.db.session import get_sessionmaker
from basketiq.observability.logging import configure_logging
from basketiq.queue import ACTOR_NAME, QUEUE_NAME, get_queue
from basketiq.services.processing import Components, process_job
from basketiq.storage import get_storage
from basketiq.workers.components import build_components

settings = get_settings()
app_config = get_app_config()  # fails fast if config/app-config.yaml is missing or invalid
configure_logging(settings.log_level)
broker = RedisBroker(url=settings.redis_url)
dramatiq.set_broker(broker)


@lru_cache
def components() -> Components:
    # Built once per worker process.
    return build_components(settings, app_config)


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
