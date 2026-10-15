"""Worker side of processing: atomic job claim, pipeline execution, persistence and retries."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from basketiq.config import Settings
from basketiq.db.models import ProcessingJob, Receipt, ReceiptLine
from basketiq.domain.status import (
    ERROR_STATUSES,
    ActorType,
    ConfirmedBy,
    JobStatus,
    Merchant,
    ReceiptStatus,
)
from basketiq.observability import metrics
from basketiq.pipeline.external import ExternalExtractor
from basketiq.pipeline.llm import AmbiguityResolver
from basketiq.pipeline.ocr import OcrEngine
from basketiq.pipeline.orchestrator import (
    PipelineDeps,
    PipelineOutcome,
    PipelineRequest,
    TemporaryProcessingError,
    run_pipeline,
)
from basketiq.queue import JobQueue
from basketiq.services import audit, products
from basketiq.services.receipts import apply_evaluation, evaluate_receipt, mark_confirmed
from basketiq.services.thresholds import merchant_enabled, thresholds_for
from basketiq.storage import ObjectNotFoundError, ObjectStorage, StorageUnavailableError

log = logging.getLogger(__name__)


@dataclass
class Components:
    ocr: OcrEngine
    resolver: AmbiguityResolver | None
    external: ExternalExtractor | None


def external_spent_this_month(db: Session, tz: str) -> int:
    now = datetime.now(ZoneInfo(tz))
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = db.scalar(
        select(func.coalesce(func.sum(ProcessingJob.estimated_cost_cents), 0)).where(
            ProcessingJob.external_provider.is_not(None), ProcessingJob.created_at >= start
        )
    )
    return int(spent or 0)


def claim(db: Session, job_id: uuid.UUID) -> ProcessingJob | None:
    """Atomic: only one worker can move a job to RUNNING; replayed messages get None."""
    row = db.execute(
        update(ProcessingJob)
        .where(ProcessingJob.id == job_id, ProcessingJob.status.in_([JobStatus.QUEUED, JobStatus.RETRY]))
        .values(
            status=JobStatus.RUNNING, attempt_count=ProcessingJob.attempt_count + 1, started_at=func.now()
        )
        .returning(ProcessingJob.id)
    ).first()
    db.commit()
    return db.get(ProcessingJob, job_id) if row else None


def process_job(
    job_id: uuid.UUID,
    *,
    sessions: sessionmaker[Session],
    settings: Settings,
    storage: ObjectStorage,
    queue: JobQueue,
    components: Components,
) -> None:
    with sessions() as db:
        job = claim(db, job_id)
        if job is None:
            return
        receipt = db.get(Receipt, job.receipt_id) if job.receipt_id else None
        if receipt is None:
            job.status, job.error_code, job.finished_at = (
                JobStatus.FAILED,
                "receipt_deleted",
                datetime.now(UTC),
            )
            db.commit()
            return
        receipt.processing_status = ReceiptStatus.VALIDATING
        db.commit()

        try:
            data = storage.get(receipt.object_key, settings.upload_max_bytes)
        except ObjectNotFoundError:
            _fail(db, job, receipt, ReceiptStatus.PERMANENT_FAILURE, "original_missing")
            return
        except StorageUnavailableError:
            _retry_or_fail(db, settings, queue, job, receipt, "storage_unavailable")
            return

        receipt.processing_status = ReceiptStatus.EXTRACTING
        db.commit()

        deps = PipelineDeps(
            ocr=components.ocr,
            resolver=components.resolver,
            external=components.external,
            external_budget_remaining_cents=max(
                settings.external_monthly_budget_cents - external_spent_this_month(db, settings.timezone), 0
            ),
            thresholds_for=lambda m: thresholds_for(db, settings, m),
            merchant_enabled=lambda m: merchant_enabled(db, m),
            max_bytes=settings.upload_max_bytes,
            max_pages=settings.pdf_max_pages,
            external_timeout=settings.external_timeout_seconds,
        )
        request = PipelineRequest(
            data=data,
            declared_mime=receipt.content_type,
            merchant_hint=Merchant(receipt.merchant_hint) if receipt.merchant_hint else None,
            external_requested=job.external_requested,
        )
        try:
            outcome = run_pipeline(request, deps)
        except TemporaryProcessingError:
            db.rollback()
            _retry_or_fail(db, settings, queue, job, receipt, "temporary_error")
            return
        except Exception:
            db.rollback()
            log.exception("pipeline_crashed", extra={"job_id": str(job.id)})
            _retry_or_fail(db, settings, queue, job, receipt, "unexpected_error")
            return

        file_hash = hashlib.sha256(data).hexdigest()
        persist_outcome(db, settings, job, receipt, outcome, file_hash)
        db.commit()


def persist_outcome(
    db: Session,
    settings: Settings,
    job: ProcessingJob,
    receipt: Receipt,
    outcome: PipelineOutcome,
    file_hash: str,
) -> None:
    now = datetime.now(UTC)
    job.processing_route = outcome.route
    job.ocr_engine = outcome.ocr_engine
    job.llm_used = outcome.llm_used
    job.model_version = outcome.model_version
    job.external_provider = outcome.external_provider
    job.external_reason = outcome.external_reason
    job.estimated_cost_cents = outcome.estimated_cost_cents
    job.stage_durations_ms = outcome.stage_ms
    job.source_type = outcome.source_type
    job.finished_at = now
    receipt.file_hash = file_hash
    receipt.quality_warnings = outcome.quality_warnings
    for stage, ms in outcome.stage_ms.items():
        metrics.STAGE_SECONDS.labels(stage=stage).observe(ms / 1000)
    if outcome.external_provider:
        metrics.EXTERNAL_CALLS.labels(
            provider=outcome.external_provider, reason=outcome.external_reason or ""
        ).inc()
        metrics.EXTERNAL_COST_CENTS.labels(provider=outcome.external_provider).inc(
            outcome.estimated_cost_cents
        )
        audit.record(
            db,
            "EXTERNAL_CALL",
            user_id=receipt.user_id,
            receipt_id=receipt.id,
            actor=ActorType.SYSTEM,
            provider=outcome.external_provider,
            reason=outcome.external_reason,
            cost_cents=outcome.estimated_cost_cents,
            job_id=job.id,
        )
    if outcome.llm_used:
        metrics.LLM_CALLS.inc()

    if outcome.status in ERROR_STATUSES or outcome.parsed is None:
        # An invalid document never creates a partial purchase.
        receipt.lines = []
        receipt.processing_status = outcome.status
        receipt.error_code = outcome.error_code
        receipt.review_reasons = [{"code": outcome.error_code, "message": outcome.error_message}]
        receipt.version += 1
        job.status = JobStatus.SUCCEEDED
        job.error_code = outcome.error_code
        metrics.JOBS.labels(outcome=outcome.status, route=outcome.route, merchant="none").inc()
        audit.record(
            db,
            "PROCESSING_REJECTED",
            user_id=receipt.user_id,
            receipt_id=receipt.id,
            actor=ActorType.SYSTEM,
            error_code=outcome.error_code,
        )
        return

    parsed = outcome.parsed
    job.parser_version = parsed.parser_version
    job.merchant = parsed.merchant
    receipt.merchant = parsed.merchant
    receipt.purchase_at = parsed.purchase_at
    receipt.receipt_ref = parsed.receipt_ref
    receipt.subtotal_cents = parsed.subtotal_cents
    receipt.tax_cents = parsed.tax_cents
    receipt.total_cents = parsed.total_cents
    receipt.field_confidence = parsed.field_confidence
    receipt.manually_edited_fields = []
    receipt.error_code = None

    lines: list[ReceiptLine] = []
    for i, pl in enumerate(parsed.lines, start=1):
        product_id = (
            None
            if pl.unresolved
            else products.resolve_product(db, receipt.user_id, pl.raw_description, pl.unit)
        )
        lines.append(
            ReceiptLine(
                receipt_id=receipt.id,
                line_number=i,
                raw_description=pl.raw_description,
                normalized_product_id=product_id,
                quantity_decimal=pl.quantity,
                unit=pl.unit,
                unit_price_cents=pl.unit_price_cents,
                line_total_cents=pl.line_total_cents,
                discount_cents=pl.discount_cents,
                tax_code=pl.tax_code,
                confidence=pl.confidence,
                field_confidence=pl.field_confidence,
                evidence={"lines": pl.evidence, "unresolved": pl.unresolved, "resolved_by": pl.resolved_by},
                manually_edited=False,
                edited_fields=[],
            )
        )
        if pl.unresolved:
            metrics.PARSER_ERRORS.labels(parser=parsed.parser_version).inc()
    receipt.lines = lines
    db.flush()

    receipt.duplicate_of_receipt_id = find_attribute_duplicate(db, receipt)
    evaluation = evaluate_receipt(db, settings, receipt)
    apply_evaluation(receipt, evaluation)
    receipt.version += 1
    metrics.RECEIPT_CONFIDENCE.observe(evaluation.overall_confidence)
    if not evaluation.reconciliation.within_tolerance:
        metrics.TOTAL_DIVERGENCE.labels(merchant=parsed.merchant).inc()

    if evaluation.reasons:
        receipt.processing_status = ReceiptStatus.REVIEW_REQUIRED
    elif settings.auto_confirm_enabled:
        receipt.processing_status = ReceiptStatus.COMPLETED
        mark_confirmed(receipt, settings, ConfirmedBy.AUTO)  # F-15a
    else:
        receipt.processing_status = ReceiptStatus.COMPLETED
    job.status = JobStatus.SUCCEEDED
    metrics.JOBS.labels(
        outcome=receipt.processing_status, route=outcome.route, merchant=parsed.merchant
    ).inc()
    audit.record(
        db,
        "PROCESSED",
        user_id=receipt.user_id,
        receipt_id=receipt.id,
        actor=ActorType.SYSTEM,
        status=receipt.processing_status,
        route=outcome.route,
        lines=len(lines),
        reasons=[r.code for r in evaluation.reasons],
    )


def find_attribute_duplicate(db: Session, receipt: Receipt) -> uuid.UUID | None:
    """RF-08: same file hash, or same merchant + purchase minute + total, among the user's receipts."""
    conditions = []
    if receipt.file_hash:
        conditions.append(Receipt.file_hash == receipt.file_hash)
    if receipt.merchant and receipt.purchase_at and receipt.total_cents is not None:
        conditions.append(
            (Receipt.merchant == receipt.merchant)
            & (
                func.date_trunc("minute", Receipt.purchase_at)
                == func.date_trunc("minute", receipt.purchase_at)
            )
            & (Receipt.total_cents == receipt.total_cents)
        )
    if not conditions:
        return None
    return db.scalar(
        select(Receipt.id)
        .where(
            Receipt.user_id == receipt.user_id,
            Receipt.id != receipt.id,
            Receipt.processing_status.notin_([s.value for s in ERROR_STATUSES]),
            or_(*conditions),
        )
        .order_by(Receipt.created_at)
        .limit(1)
    )


def _fail(db: Session, job: ProcessingJob, receipt: Receipt, status: ReceiptStatus, code: str) -> None:
    job.status, job.error_code, job.finished_at = JobStatus.FAILED, code, datetime.now(UTC)
    receipt.processing_status, receipt.error_code = status, code
    receipt.lines = []
    receipt.review_reasons = [{"code": code, "message": "Falha no processamento."}]
    receipt.version += 1
    metrics.JOBS.labels(outcome=status, route="NONE", merchant="none").inc()
    db.commit()


def _retry_or_fail(
    db: Session, settings: Settings, queue: JobQueue, job: ProcessingJob, receipt: Receipt, code: str
) -> None:
    job = db.get(ProcessingJob, job.id) or job
    receipt = db.get(Receipt, receipt.id) or receipt
    if job.attempt_count < settings.job_max_attempts:
        job.status, job.error_code = JobStatus.RETRY, code
        job.queued_at = datetime.now(UTC)
        receipt.processing_status = ReceiptStatus.RECEIVED
        db.commit()
        metrics.JOB_RETRIES.inc()
        delay_ms = int(timedelta(seconds=5 * 2 ** (job.attempt_count - 1)).total_seconds() * 1000)
        queue.enqueue(job.id, delay_ms=delay_ms)
        return
    status = (
        ReceiptStatus.PERMANENT_FAILURE if code == "unexpected_error" else ReceiptStatus.TEMPORARY_FAILURE
    )
    _fail(db, job, receipt, status, code)


def requeue_stuck_jobs(db: Session, settings: Settings, queue: JobQueue) -> int:
    """F-25: recover jobs whose worker died (RUNNING too long) or whose message was lost."""
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.job_stuck_after_seconds)
    stuck = db.scalars(
        select(ProcessingJob).where(
            ((ProcessingJob.status == JobStatus.RUNNING) & (ProcessingJob.started_at < cutoff))
            | (
                ProcessingJob.status.in_([JobStatus.QUEUED, JobStatus.RETRY])
                & (ProcessingJob.queued_at < cutoff)
            )
        )
    ).all()
    requeued = 0
    for job in stuck:
        receipt = db.get(Receipt, job.receipt_id) if job.receipt_id else None
        if job.attempt_count >= settings.job_max_attempts and job.status == JobStatus.RUNNING:
            job.status, job.error_code, job.finished_at = JobStatus.FAILED, "stuck", datetime.now(UTC)
            if receipt is not None:
                receipt.processing_status, receipt.error_code = ReceiptStatus.TEMPORARY_FAILURE, "stuck"
                receipt.version += 1
            continue
        job.status = JobStatus.RETRY
        job.queued_at = datetime.now(UTC)
        if receipt is not None:
            receipt.processing_status = ReceiptStatus.RECEIVED
        requeued += 1
    db.commit()
    for job in stuck:
        if job.status == JobStatus.RETRY:
            queue.enqueue(job.id)
    return requeued
