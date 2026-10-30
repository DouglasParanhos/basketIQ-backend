from __future__ import annotations

import base64
import uuid
from collections.abc import Callable
from datetime import datetime
from functools import wraps
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from basketiq.api.deps import ConsentedUser, Db, SettingsDep, csrf_protect
from basketiq.api.errors import ApiError, ErrorBody, FieldError
from basketiq.api.ratelimit import limiter
from basketiq.api.schemas import (
    DuplicateDecisionIn,
    ErrorInfo,
    FileRequirements,
    JobOut,
    LineOut,
    MerchantChoiceIn,
    OriginalUrlOut,
    PhotoAddIn,
    PresignedUploadOut,
    ProcessIn,
    ProcessingInfo,
    ProductRef,
    ReceiptOut,
    ReceiptPage,
    ReceiptPatchIn,
    ReceiptSummaryOut,
    ReviewReasonOut,
    Totals,
    UploadInitIn,
    UploadInitOut,
    VersionIn,
    WarningOut,
)
from basketiq.config import Settings, get_settings
from basketiq.db.models import Receipt, ReceiptLine
from basketiq.domain.status import (
    EDITABLE_STATUSES,
    ERROR_ACTIONS,
    ERROR_STATUSES,
    Merchant,
    ReceiptStatus,
)
from basketiq.pipeline.quality import WARNING_MESSAGES
from basketiq.pipeline.validation import ALLOWED_MIMES
from basketiq.queue import JobQueue, get_queue
from basketiq.services import receipts as svc
from basketiq.storage import ObjectStorage, get_storage

router = APIRouter(
    prefix="/v1/receipts",
    tags=["receipts"],
    dependencies=[Depends(csrf_protect)],
    responses={401: {"model": ErrorBody}, 403: {"model": ErrorBody}, 404: {"model": ErrorBody}},
)

Storage = Annotated[ObjectStorage, Depends(get_storage)]
Queue = Annotated[JobQueue, Depends(get_queue)]


def translate_errors[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Map service errors to the uniform API error body."""

    @wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except svc.ServiceError as exc:
            raise ApiError(
                exc.status_code,
                exc.code,
                exc.message,
                field_errors=[FieldError(field=f, message=m) for f, m in exc.field_errors]
                if exc.field_errors
                else None,
                details=exc.details,
            ) from exc

    return wrapper


def requirements(settings: Settings) -> FileRequirements:
    return FileRequirements(
        max_bytes=settings.upload_max_bytes,
        accepted_types=sorted(ALLOWED_MIMES),
        max_pdf_pages=settings.pdf_max_pages,
        max_photos=settings.upload_max_photos,
    )


@router.post(
    "/upload-init",
    response_model=UploadInitOut,
    status_code=201,
    responses={409: {"model": ErrorBody}, 422: {"model": ErrorBody}},
)
@limiter.limit(lambda: get_settings().rate_limit_upload)
@translate_errors
def upload_init(
    request: Request, body: UploadInitIn, db: Db, settings: SettingsDep, storage: Storage, user: ConsentedUser
) -> UploadInitOut:
    result = svc.upload_init(
        db,
        settings,
        storage,
        user,
        filename=body.filename,
        content_type=body.content_type,
        size_bytes=body.size_bytes,
        sha256=body.sha256,
        merchant_hint=body.merchant_hint,
        allow_duplicate=body.allow_duplicate,
    )
    return UploadInitOut(
        receipt_id=result.receipt.id,
        upload=PresignedUploadOut(
            url=result.upload.url, fields=result.upload.fields, expires_at=result.upload.expires_at
        ),
        requirements=requirements(settings),
    )


@router.post(
    "/{receipt_id}/upload-url",
    response_model=PresignedUploadOut,
    summary="New upload URL after a failed upload (position 0 is the first photo)",
)
@translate_errors
def renew_upload_url(
    receipt_id: uuid.UUID,
    db: Db,
    settings: SettingsDep,
    storage: Storage,
    user: ConsentedUser,
    position: Annotated[int, Query(ge=0, le=20)] = 0,
) -> PresignedUploadOut:
    up = svc.new_upload_url(db, settings, storage, user, receipt_id, position)
    return PresignedUploadOut(url=up.url, fields=up.fields, expires_at=up.expires_at)


@router.post(
    "/{receipt_id}/photos",
    response_model=PresignedUploadOut,
    status_code=201,
    summary="Another photo of a receipt too long for one",
    responses={409: {"model": ErrorBody}, 422: {"model": ErrorBody}},
)
@translate_errors
def add_photo(
    receipt_id: uuid.UUID,
    body: PhotoAddIn,
    db: Db,
    settings: SettingsDep,
    storage: Storage,
    user: ConsentedUser,
) -> PresignedUploadOut:
    up = svc.add_photo(
        db,
        settings,
        storage,
        user,
        receipt_id,
        filename=body.filename,
        content_type=body.content_type,
        size_bytes=body.size_bytes,
        sha256=body.sha256,
    )
    return PresignedUploadOut(url=up.url, fields=up.fields, expires_at=up.expires_at)


@router.post(
    "/{receipt_id}/process", response_model=JobOut, status_code=202, responses={409: {"model": ErrorBody}}
)
@limiter.limit(lambda: get_settings().rate_limit_process)
@translate_errors
def process(
    request: Request,
    receipt_id: uuid.UUID,
    db: Db,
    settings: SettingsDep,
    storage: Storage,
    queue: Queue,
    user: ConsentedUser,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    body: Annotated[ProcessIn | None, Body()] = None,
) -> JobOut:
    job, created = svc.request_processing(
        db, settings, storage, queue, user, receipt_id, idempotency_key, external=bool(body and body.external)
    )
    db.commit()  # the worker must see the job before the message arrives
    if created:
        queue.enqueue(job.id)
    return JobOut(
        id=job.id,
        status=job.status,
        attempt_count=job.attempt_count,
        processing_route=job.processing_route,
        created_at=job.created_at,
    )


@router.get("", response_model=ReceiptPage)
def list_receipts(
    db: Db,
    user: ConsentedUser,
    status: Annotated[list[ReceiptStatus] | None, Query()] = None,
    merchant: Merchant | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ReceiptPage:
    line_count = (
        select(func.count(ReceiptLine.id))
        .where(ReceiptLine.receipt_id == Receipt.id)
        .correlate(Receipt)
        .scalar_subquery()
    )
    stmt = select(Receipt, line_count).where(Receipt.user_id == user.id)
    if status:
        stmt = stmt.where(Receipt.processing_status.in_([s.value for s in status]))
    if merchant:
        stmt = stmt.where(Receipt.merchant == merchant)
    if cursor:
        try:
            created_s, rid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
            created, last_id = datetime.fromisoformat(created_s), uuid.UUID(rid)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ApiError(422, "invalid_cursor", "Cursor inválido.") from exc
        stmt = stmt.where(
            (Receipt.created_at < created) | ((Receipt.created_at == created) & (Receipt.id < last_id))
        )
    rows = db.execute(stmt.order_by(Receipt.created_at.desc(), Receipt.id.desc()).limit(limit + 1)).all()
    items = [
        ReceiptSummaryOut(
            id=r.id,
            status=ReceiptStatus(r.processing_status),
            merchant=r.merchant,
            merchant_name=r.merchant_name,
            store_label=r.store_label,
            purchase_at=r.purchase_at,
            total_cents=r.total_cents,
            line_count=count,
            overall_confidence=r.overall_confidence,
            needs_action=r.processing_status in (*EDITABLE_STATUSES, *ERROR_STATUSES)
            or (r.processing_status == ReceiptStatus.RECEIVED),
            created_at=r.created_at,
        )
        for r, count in rows[:limit]
    ]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1][0]
        next_cursor = base64.urlsafe_b64encode(f"{last.created_at.isoformat()}|{last.id}".encode()).decode()
    return ReceiptPage(items=items, next_cursor=next_cursor)


@router.get("/{receipt_id}", response_model=ReceiptOut)
@translate_errors
def get_receipt(receipt_id: uuid.UUID, db: Db, settings: SettingsDep, user: ConsentedUser) -> ReceiptOut:
    return serialize(db, settings, svc.get_owned(db, user, receipt_id))


@router.patch("/{receipt_id}", response_model=ReceiptOut, responses={409: {"model": ErrorBody}})
@translate_errors
def patch_receipt(
    receipt_id: uuid.UUID, body: ReceiptPatchIn, db: Db, settings: SettingsDep, user: ConsentedUser
) -> ReceiptOut:
    patch: dict[str, Any] = body.model_dump(exclude_unset=True)
    if body.lines is not None:
        patch["lines"] = [line.model_dump(exclude_unset=True) for line in body.lines]
    receipt = svc.edit_receipt(db, settings, user, receipt_id, patch)
    return serialize(db, settings, receipt)


@router.post("/{receipt_id}/confirm", response_model=ReceiptOut, responses={409: {"model": ErrorBody}})
@translate_errors
def confirm(
    receipt_id: uuid.UUID, body: VersionIn, db: Db, settings: SettingsDep, user: ConsentedUser
) -> ReceiptOut:
    return serialize(db, settings, svc.confirm(db, settings, user, receipt_id, body.version))


@router.post("/{receipt_id}/duplicate-decision", response_model=ReceiptOut | None)
@translate_errors
def duplicate_decision(
    receipt_id: uuid.UUID,
    body: DuplicateDecisionIn,
    db: Db,
    settings: SettingsDep,
    storage: Storage,
    user: ConsentedUser,
) -> ReceiptOut | None:
    receipt = svc.duplicate_decision(db, settings, storage, user, receipt_id, keep=body.action == "keep")
    return serialize(db, settings, receipt) if receipt else None


@router.post(
    "/{receipt_id}/merchant",
    response_model=ReceiptOut | JobOut,
    summary="Choose the merchant or mark unsupported (F-10)",
)
@translate_errors
def choose_merchant(
    receipt_id: uuid.UUID,
    body: MerchantChoiceIn,
    db: Db,
    settings: SettingsDep,
    storage: Storage,
    queue: Queue,
    user: ConsentedUser,
) -> ReceiptOut | JobOut:
    if body.merchant == "UNSUPPORTED":
        return serialize(db, settings, svc.mark_unsupported(db, user, receipt_id))
    receipt = svc.get_owned(db, user, receipt_id, for_update=True)
    receipt.merchant_hint = body.merchant
    job, created = svc.request_processing(
        db, settings, storage, queue, user, receipt_id, f"merchant-{uuid.uuid4()}"
    )
    db.commit()
    if created:
        queue.enqueue(job.id)
    return JobOut(
        id=job.id,
        status=job.status,
        attempt_count=job.attempt_count,
        processing_route=job.processing_route,
        created_at=job.created_at,
    )


@router.get("/{receipt_id}/original", response_model=OriginalUrlOut)
@translate_errors
def original_url(
    receipt_id: uuid.UUID,
    db: Db,
    settings: SettingsDep,
    storage: Storage,
    user: ConsentedUser,
    photo: Annotated[int, Query(ge=1, le=20, description="Photo number, counted from 1")] = 1,
) -> OriginalUrlOut:
    receipt = svc.get_owned(db, user, receipt_id)
    if receipt.original_deleted_at is not None:
        raise ApiError(410, "original_deleted", "O ficheiro original foi eliminado.")
    key, content_type = svc.photo_file(receipt, photo - 1)
    url = storage.presign_download(key, settings.original_url_ttl_seconds, content_type)
    return OriginalUrlOut(
        url=url, expires_in_seconds=settings.original_url_ttl_seconds, content_type=content_type
    )


@router.delete("/{receipt_id}", status_code=204, response_class=Response)
@translate_errors
def delete_receipt(receipt_id: uuid.UUID, db: Db, storage: Storage, user: ConsentedUser) -> Response:
    svc.delete_receipt(db, storage, user, receipt_id)
    return Response(status_code=204)


@router.delete("/{receipt_id}/original", response_model=ReceiptOut)
@translate_errors
def delete_original(
    receipt_id: uuid.UUID, db: Db, settings: SettingsDep, storage: Storage, user: ConsentedUser
) -> ReceiptOut:
    return serialize(db, settings, svc.delete_original(db, storage, user, receipt_id))


# ---------------------------------------------------------------- serialisation


def serialize(db: Session, settings: Settings, receipt: Receipt) -> ReceiptOut:
    status = ReceiptStatus(receipt.processing_status)
    has_content = status not in ERROR_STATUSES and status not in (
        ReceiptStatus.RECEIVED,
        ReceiptStatus.VALIDATING,
        ReceiptStatus.EXTRACTING,
    )
    job = svc.latest_job(db, receipt.id)
    evaluation = svc.evaluate_receipt(db, settings, receipt, job) if has_content else None
    rec = evaluation.reconciliation if evaluation else None

    error = None
    if status in ERROR_STATUSES:
        reason = (receipt.review_reasons or [{}])[0]
        error = ErrorInfo(
            code=receipt.error_code, message=reason.get("message"), action=ERROR_ACTIONS.get(status)
        )

    lines = []
    for i, line in enumerate(receipt.lines):
        flags = evaluation.line_flags.get(i, []) if evaluation else []
        evidence = line.evidence or {}
        lines.append(
            LineOut(
                id=line.id,
                line_number=line.line_number,
                raw_description=line.raw_description,
                product=ProductRef(id=line.product.id, name=line.product.canonical_name)
                if line.product
                else None,
                quantity=line.quantity_decimal,
                unit=line.unit,
                unit_price_cents=line.unit_price_cents,
                line_total_cents=line.line_total_cents,
                discount_cents=line.discount_cents or 0,
                net_cents=(line.line_total_cents - (line.discount_cents or 0))
                if line.line_total_cents is not None
                else None,
                tax_code=line.tax_code,
                confidence=line.confidence,
                field_confidence=line.field_confidence or {},
                review_status=line.review_status,
                flags=flags,
                unresolved=bool(evidence.get("unresolved")),
                manually_edited=line.manually_edited,
                edited_fields=list(line.edited_fields or []),
                evidence=list(evidence.get("lines", [])),
            )
        )

    reasons = evaluation.reasons if evaluation else []
    return ReceiptOut(
        id=receipt.id,
        status=status,
        error=error,
        merchant=receipt.merchant,
        merchant_hint=receipt.merchant_hint,
        merchant_name=receipt.merchant_name,
        purchase_at=receipt.purchase_at,
        receipt_ref=receipt.receipt_ref,
        store_label=receipt.store_label,
        currency=receipt.currency,
        source_type=receipt.source_type,
        totals=Totals(
            subtotal_cents=receipt.subtotal_cents,
            tax_cents=receipt.tax_cents,
            printed_total_cents=receipt.total_cents,
            lines_total_cents=rec.lines_total_cents if rec else 0,
            discounts_cents=rec.discounts_cents if rec else 0,
            recalculated_total_cents=rec.recalculated_total_cents if rec else 0,
            difference_cents=rec.difference_cents if rec else None,
            reconciled=bool(rec and rec.within_tolerance),
        ),
        overall_confidence=receipt.overall_confidence,
        field_confidence=receipt.field_confidence or {},
        review_reasons=[ReviewReasonOut(**r.model_dump()) for r in reasons],
        quality_warnings=[
            WarningOut(code=w, message=WARNING_MESSAGES.get(w, w)) for w in receipt.quality_warnings or []
        ],
        manually_edited_fields=list(receipt.manually_edited_fields or []),
        lines=lines,
        duplicate_of_receipt_id=receipt.duplicate_of_receipt_id,
        duplicate_acknowledged=receipt.duplicate_acknowledged,
        can_confirm=bool(evaluation and status in EDITABLE_STATUSES and not evaluation.blocking),
        confirmed_at=receipt.confirmed_at,
        confirmed_by=receipt.confirmed_by,
        original_available=receipt.original_deleted_at is None and status != ReceiptStatus.RECEIVED,
        photo_count=1 + len(receipt.photos),
        original_expires_at=receipt.original_expires_at,
        original_deleted_at=receipt.original_deleted_at,
        processing=ProcessingInfo(
            job_id=job.id, status=job.status, route=job.processing_route, attempts=job.attempt_count
        )
        if job
        else None,
        version=receipt.version,
        created_at=receipt.created_at,
        updated_at=receipt.updated_at,
    )
