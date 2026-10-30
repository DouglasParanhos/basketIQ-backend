"""Periodic maintenance: retention (30 days), stuck-job recovery, abandoned uploads.

Run as its own process (`python -m basketiq.workers.scheduler`). Every task is idempotent, so a
second scheduler instance or a missed tick is harmless.
"""

from __future__ import annotations

import logging
import signal
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from basketiq.config import Settings, get_settings
from basketiq.db.models import ProcessingJob, Receipt
from basketiq.db.session import session_scope
from basketiq.domain.status import ActorType, ReceiptStatus
from basketiq.observability.logging import configure_logging
from basketiq.queue import JobQueue, get_queue
from basketiq.services.processing import requeue_stuck_jobs
from basketiq.services.receipts import erase_original
from basketiq.storage import ObjectStorage, StorageUnavailableError, get_storage, receipt_prefix

log = logging.getLogger(__name__)


def purge_expired_originals(db: Session, storage: ObjectStorage, now: datetime | None = None) -> int:
    """F-22b: delete originals (and derivatives) 30 days after confirmation; keep structured data."""
    now = now or datetime.now(UTC)
    due = db.scalars(
        select(Receipt)
        .where(Receipt.original_expires_at <= now, Receipt.original_deleted_at.is_(None))
        .limit(500)
        .with_for_update(skip_locked=True)
    ).all()
    for receipt in due:
        erase_original(db, storage, receipt, reason="retention", actor=ActorType.SYSTEM, now=now)
    return len(due)


def purge_abandoned_uploads(
    db: Session, storage: ObjectStorage, settings: Settings, now: datetime | None = None
) -> int:
    """Receipts created by upload-init but never processed (no job) are removed after a grace period."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=settings.abandoned_upload_hours)
    has_job = select(ProcessingJob.id).where(ProcessingJob.receipt_id == Receipt.id).exists()
    abandoned = db.scalars(
        select(Receipt)
        .where(Receipt.processing_status == ReceiptStatus.RECEIVED, Receipt.created_at < cutoff, ~has_job)
        .limit(500)
    ).all()
    for receipt in abandoned:
        storage.delete_prefix(receipt_prefix(receipt.user_id, receipt.id))
    if abandoned:
        db.execute(delete(Receipt).where(Receipt.id.in_([r.id for r in abandoned])))
    return len(abandoned)


def run_once(settings: Settings, storage: ObjectStorage, queue: JobQueue) -> dict[str, int]:
    result: dict[str, int] = {}
    with session_scope() as db:
        result["originals_purged"] = purge_expired_originals(db, storage)
    with session_scope() as db:
        result["abandoned_purged"] = purge_abandoned_uploads(db, storage, settings)
    with session_scope() as db:
        result["jobs_requeued"] = requeue_stuck_jobs(db, settings, queue)
    return result


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    storage, queue = get_storage(), get_queue()
    running = True

    def stop(*_: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while running:
        try:
            log.info("scheduler_tick", extra=run_once(settings, storage, queue))
        except StorageUnavailableError:
            log.warning("scheduler_storage_unavailable")
        except Exception:
            log.exception("scheduler_tick_failed")
        for _ in range(60):
            if not running:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
