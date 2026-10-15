"""Operator views: technical metadata and aggregated metrics only — never receipt content."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Float, cast, func, select
from sqlalchemy.orm import Session

from basketiq.db.models import MerchantConfig, ProcessingJob, Receipt, ReceiptLine, User
from basketiq.domain.status import ERROR_STATUSES, SUPPORTED_MERCHANTS, ConfirmedBy, ReceiptStatus


def list_jobs(db: Session, status: str | None, limit: int) -> list[dict[str, Any]]:
    stmt = (
        select(ProcessingJob, Receipt.processing_status)
        .outerjoin(Receipt, Receipt.id == ProcessingJob.receipt_id)
        .order_by(ProcessingJob.created_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(ProcessingJob.status == status)
    return [job_dict(job, receipt_status) for job, receipt_status in db.execute(stmt).all()]


def job_dict(job: ProcessingJob, receipt_status: str | None) -> dict[str, Any]:
    return {
        "id": job.id,
        "status": job.status,
        "receipt_status": receipt_status,
        "error_code": job.error_code,
        "attempt_count": job.attempt_count,
        "processing_route": job.processing_route,
        "ocr_engine": job.ocr_engine,
        "external_provider": job.external_provider,
        "external_reason": job.external_reason,
        "estimated_cost_cents": job.estimated_cost_cents,
        "parser_version": job.parser_version,
        "model_version": job.model_version,
        "merchant": job.merchant,
        "source_type": job.source_type,
        "stage_durations_ms": job.stage_durations_ms,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
    }


def metrics(db: Session, date_from: date, date_to: date, tz_name: str) -> dict[str, Any]:
    tz = ZoneInfo(tz_name)
    start = datetime.combine(date_from, time(0), tz)
    end = datetime.combine(date_to + timedelta(days=1), time(0), tz)
    in_period = (Receipt.created_at >= start) & (Receipt.created_at < end)

    status_rows = db.execute(
        select(Receipt.processing_status, func.count()).where(in_period).group_by(Receipt.processing_status)
    ).all()
    by_status = {s: int(c) for s, c in status_rows}
    total = sum(by_status.values())
    confirmed = by_status.get(ReceiptStatus.CONFIRMED, 0)

    median_review = db.scalar(
        select(
            func.percentile_cont(0.5).within_group(
                func.extract("epoch", Receipt.confirmed_at - Receipt.created_at)
            )
        ).where(in_period, Receipt.confirmed_by == ConfirmedBy.USER)
    )
    lines_row = db.execute(
        select(func.count(ReceiptLine.id), func.count().filter(ReceiptLine.manually_edited.is_(False)))
        .join(Receipt, Receipt.id == ReceiptLine.receipt_id)
        .where(in_period, Receipt.processing_status == ReceiptStatus.CONFIRMED)
    ).one()
    duplicates = (
        db.scalar(select(func.count()).where(in_period, Receipt.duplicate_of_receipt_id.is_not(None))) or 0
    )
    mau = db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.last_login_at >= datetime.now(UTC) - timedelta(days=30))
    )

    per_merchant = db.execute(
        select(Receipt.merchant, func.count())
        .where(in_period, Receipt.merchant.is_not(None))
        .group_by(Receipt.merchant)
    ).all()
    error_rate = db.execute(
        select(
            Receipt.source_type,
            func.count(),
            func.count().filter(Receipt.processing_status.in_([s.value for s in ERROR_STATUSES])),
        )
        .where(in_period)
        .group_by(Receipt.source_type)
    ).all()

    jobs_in_period = (ProcessingJob.created_at >= start) & (ProcessingJob.created_at < end)
    route_rows = db.execute(
        select(ProcessingJob.processing_route, func.count())
        .where(jobs_in_period, ProcessingJob.processing_route.is_not(None))
        .group_by(ProcessingJob.processing_route)
    ).all()
    jobs_total = sum(int(c) for _, c in route_rows)
    ext = db.execute(
        select(func.count(), func.coalesce(func.sum(ProcessingJob.estimated_cost_cents), 0)).where(
            jobs_in_period, ProcessingJob.external_provider.is_not(None)
        )
    ).one()
    llm_jobs = db.scalar(select(func.count()).where(jobs_in_period, ProcessingJob.llm_used.is_(True))) or 0
    resolved_by = db.execute(
        select(
            func.count(ReceiptLine.id),
            func.count().filter(ReceiptLine.evidence["resolved_by"].astext == "llm"),
        )
        .join(Receipt, Receipt.id == ReceiptLine.receipt_id)
        .where(in_period)
    ).one()
    avg_stage = db.execute(
        select(
            func.avg(cast(ProcessingJob.stage_durations_ms["text"].astext, Float)),
            func.avg(cast(ProcessingJob.stage_durations_ms["parse"].astext, Float)),
            func.percentile_cont(0.95).within_group(
                func.extract("epoch", ProcessingJob.finished_at - ProcessingJob.started_at)
            ),
        ).where(jobs_in_period, ProcessingJob.finished_at.is_not(None))
    ).one()

    def pct(part: int, whole: int) -> float | None:
        return round(part / whole * 100, 1) if whole else None

    return {
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "uploads": {
            "total": total,
            "by_status": by_status,
            "confirmed_pct": pct(confirmed, total),
            "auto_confirmed": db.scalar(
                select(func.count()).where(in_period, Receipt.confirmed_by == ConfirmedBy.AUTO)
            )
            or 0,
        },
        "median_seconds_to_user_confirmation": float(median_review) if median_review is not None else None,
        "lines_confirmed_without_change_pct": pct(int(lines_row[1]), int(lines_row[0])),
        "duplicate_rate_pct": pct(int(duplicates), total),
        "monthly_active_users": int(mau or 0),
        "receipts_per_merchant": {m: int(c) for m, c in per_merchant},
        "error_rate_by_source_type": {s: pct(int(e), int(t)) for s, t, e in error_rate},
        "routes": {r: int(c) for r, c in route_rows},
        "without_external_pct": pct(jobs_total - int(ext[0]), jobs_total),
        "external_calls": int(ext[0]),
        "external_cost_cents": int(ext[1]),
        "jobs_with_llm": int(llm_jobs),
        "lines_resolved_without_llm_pct": pct(int(resolved_by[0]) - int(resolved_by[1]), int(resolved_by[0])),
        "avg_text_stage_ms": round(avg_stage[0]) if avg_stage[0] is not None else None,
        "avg_parse_stage_ms": round(avg_stage[1]) if avg_stage[1] is not None else None,
        "p95_processing_seconds": round(float(avg_stage[2]), 2) if avg_stage[2] is not None else None,
    }


def merchant_configs(db: Session, default_threshold: float, default_tolerance: int) -> list[dict[str, Any]]:
    stored = {c.merchant: c for c in db.scalars(select(MerchantConfig)).all()}
    out = []
    for m in SUPPORTED_MERCHANTS:
        c = stored.get(m)
        out.append(
            {
                "merchant": m.value,
                "enabled": c.enabled if c else True,
                "default_threshold": c.default_threshold
                if c and c.default_threshold is not None
                else default_threshold,
                "field_thresholds": dict(c.field_thresholds) if c else {},
                "reconciliation_tolerance_cents": c.reconciliation_tolerance_cents
                if c and c.reconciliation_tolerance_cents is not None
                else default_tolerance,
                "updated_at": c.updated_at if c else None,
            }
        )
    return out


def update_merchant_config(
    db: Session, merchant: str, data: dict[str, Any], admin_id: uuid.UUID
) -> MerchantConfig:
    cfg = db.get(MerchantConfig, merchant)
    if cfg is None:
        cfg = MerchantConfig(merchant=merchant, field_thresholds={})
        db.add(cfg)
    for key in ("enabled", "default_threshold", "field_thresholds", "reconciliation_tolerance_cents"):
        if key in data:
            setattr(cfg, key, data[key])
    cfg.updated_by = admin_id
    db.flush()
    return cfg
