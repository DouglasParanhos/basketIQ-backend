from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from basketiq.api.deps import AdminUser, Db, SettingsDep, csrf_protect
from basketiq.api.errors import ApiError, ErrorBody
from basketiq.api.routers.config import AppConfigDep
from basketiq.db.models import ProcessingJob, Receipt
from basketiq.domain.status import PROCESSABLE_STATUSES, SUPPORTED_MERCHANTS, ActorType, JobStatus, Merchant
from basketiq.queue import JobQueue, get_queue
from basketiq.services import admin, audit

router = APIRouter(
    prefix="/v1/admin",
    tags=["admin"],
    dependencies=[Depends(csrf_protect)],
    responses={403: {"model": ErrorBody}},
)


class AdminJobOut(BaseModel):
    id: uuid.UUID
    status: str
    receipt_status: str | None
    error_code: str | None
    attempt_count: int
    processing_route: str | None
    ocr_engine: str | None
    external_provider: str | None
    external_reason: str | None
    estimated_cost_cents: int
    parser_version: str | None
    model_version: str | None
    merchant: str | None
    source_type: str | None
    stage_durations_ms: dict[str, int]
    created_at: datetime
    finished_at: datetime | None


class MerchantConfigOut(BaseModel):
    merchant: str
    enabled: bool
    default_threshold: float
    field_thresholds: dict[str, float]
    reconciliation_tolerance_cents: int
    updated_at: datetime | None


class ProductConfigOut(BaseModel):
    """Read-only view of config/app-config.yaml (one field per key in the file)."""

    app_name_for_customer: str
    llm_enabled: bool


class MerchantConfigIn(BaseModel):
    enabled: bool | None = None
    default_threshold: float | None = Field(default=None, ge=0.5, le=1.0)
    field_thresholds: dict[str, Annotated[float, Field(ge=0.5, le=1.0)]] | None = None
    reconciliation_tolerance_cents: int | None = Field(default=None, ge=0, le=100)


@router.get("/jobs", response_model=list[AdminJobOut])
def jobs(
    db: Db,
    _: AdminUser,
    status: JobStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AdminJobOut]:
    return [AdminJobOut.model_validate(j) for j in admin.list_jobs(db, status, limit)]


@router.post("/jobs/{job_id}/reprocess", response_model=AdminJobOut, status_code=202)
def reprocess(
    job_id: uuid.UUID, db: Db, user: AdminUser, queue: Annotated[JobQueue, Depends(get_queue)]
) -> AdminJobOut:
    job = db.get(ProcessingJob, job_id)
    receipt = db.get(Receipt, job.receipt_id) if job and job.receipt_id else None
    if job is None or receipt is None:
        raise ApiError(404, "not_found", "Job não encontrado.")
    if receipt.processing_status not in PROCESSABLE_STATUSES:
        raise ApiError(409, "invalid_state", "A compra não pode ser reprocessada no estado atual.")
    new_job = ProcessingJob(
        receipt_id=receipt.id,
        idempotency_key=f"admin:{uuid.uuid4()}",
        status=JobStatus.QUEUED,
        source_type=receipt.source_type,
    )
    db.add(new_job)
    receipt.version += 1
    db.flush()
    audit.record(
        db,
        "ADMIN_REPROCESS",
        user_id=user.id,
        receipt_id=receipt.id,
        actor=ActorType.ADMIN,
        job_id=new_job.id,
    )
    db.commit()
    queue.enqueue(new_job.id)
    db.refresh(new_job)
    return AdminJobOut.model_validate(admin.job_dict(new_job, receipt.processing_status))


@router.get("/metrics")
def product_metrics(
    db: Db,
    settings: SettingsDep,
    _: AdminUser,
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
) -> dict[str, Any]:
    return admin.metrics(db, date_from, date_to, settings.timezone)


@router.get("/config", response_model=ProductConfigOut, summary="Values loaded from config/app-config.yaml")
def product_config(_: AdminUser, config: AppConfigDep) -> ProductConfigOut:
    return ProductConfigOut(
        app_name_for_customer=config.app_name_for_customer, llm_enabled=config.llm_enabled
    )


@router.get("/merchants", response_model=list[MerchantConfigOut])
def merchants(db: Db, settings: SettingsDep, _: AdminUser) -> list[MerchantConfigOut]:
    return [
        MerchantConfigOut.model_validate(c)
        for c in admin.merchant_configs(
            db, settings.confidence_threshold, settings.reconciliation_tolerance_cents
        )
    ]


@router.put("/merchants/{merchant}", response_model=MerchantConfigOut)
def update_merchant(
    merchant: Merchant, body: MerchantConfigIn, db: Db, settings: SettingsDep, user: AdminUser
) -> MerchantConfigOut:
    if merchant not in SUPPORTED_MERCHANTS:
        raise ApiError(422, "validation_error", "Supermercado inválido.")
    admin.update_merchant_config(db, merchant, body.model_dump(exclude_unset=True), user.id)
    audit.record(
        db, "MERCHANT_CONFIG_UPDATED", user_id=user.id, actor=ActorType.ADMIN, merchant=merchant.value
    )
    configs = admin.merchant_configs(
        db, settings.confidence_threshold, settings.reconciliation_tolerance_cents
    )
    return MerchantConfigOut.model_validate(next(c for c in configs if c["merchant"] == merchant.value))
