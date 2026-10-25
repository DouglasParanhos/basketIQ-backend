from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from basketiq.config import Settings
from basketiq.db.models import ProcessingJob, Receipt, ReceiptLine, ReceiptPhoto, User
from basketiq.domain.confidence import Evaluation, LineView, ReceiptView, evaluate
from basketiq.domain.money import multiply_cents
from basketiq.domain.status import (
    EDITABLE_STATUSES,
    ERROR_STATUSES,
    PROCESSABLE_STATUSES,
    ConfirmedBy,
    JobStatus,
    LineReviewStatus,
    Merchant,
    ReceiptStatus,
    SourceType,
)
from basketiq.pipeline.validation import ALLOWED_MIMES, EXTENSIONS, IMAGE_MIMES, source_type_for
from basketiq.queue import JobQueue
from basketiq.services import audit, products
from basketiq.services.thresholds import thresholds_for
from basketiq.storage import ObjectStorage, PresignedUpload, original_key, photo_key, receipt_prefix


class ServiceError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        field_errors: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code, self.code, self.message = status_code, code, message
        self.details = details
        self.field_errors = field_errors


# ---------------------------------------------------------------- loading


def get_owned(db: Session, user: User, receipt_id: uuid.UUID, *, for_update: bool = False) -> Receipt:
    stmt = (
        select(Receipt)
        .where(Receipt.id == receipt_id, Receipt.user_id == user.id)  # ownership in every query
        .options(selectinload(Receipt.lines).selectinload(ReceiptLine.product))
    )
    if for_update:
        stmt = stmt.with_for_update(of=Receipt)
    receipt = db.scalar(stmt)
    if receipt is None:
        raise ServiceError(404, "not_found", "Compra não encontrada.")
    return receipt


def latest_job(db: Session, receipt_id: uuid.UUID) -> ProcessingJob | None:
    return db.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.receipt_id == receipt_id)
        .order_by(ProcessingJob.created_at.desc())
        .limit(1)
    )


# ---------------------------------------------------------------- evaluation


def view_of(receipt: Receipt) -> ReceiptView:
    edited = frozenset(receipt.manually_edited_fields or [])
    return ReceiptView(
        merchant=receipt.merchant,
        purchase_at=receipt.purchase_at,
        total_cents=receipt.total_cents,
        subtotal_cents=receipt.subtotal_cents,
        tax_cents=receipt.tax_cents,
        field_confidence=dict(receipt.field_confidence or {}),
        lines=[
            LineView(
                raw_description=line.raw_description,
                quantity=line.quantity_decimal,
                line_total_cents=line.line_total_cents,
                discount_cents=line.discount_cents or 0,
                field_confidence=dict(line.field_confidence or {}),
                unresolved=bool((line.evidence or {}).get("unresolved")),
                edited_fields=frozenset(line.edited_fields or []),
            )
            for line in receipt.lines
        ],
        edited_fields=edited,
        duplicate_suspected=receipt.duplicate_of_receipt_id is not None
        and not receipt.duplicate_acknowledged,
        merchant_hint_conflict=bool(
            receipt.merchant_hint
            and receipt.merchant
            and receipt.merchant_hint != receipt.merchant
            and "merchant" not in edited
        ),
        join_notes=list(receipt.merge_notes or []),
    )


def evaluate_receipt(db: Session, settings: Settings, receipt: Receipt) -> Evaluation:
    return evaluate(view_of(receipt), thresholds_for(db, settings, receipt.merchant))


def apply_evaluation(receipt: Receipt, evaluation: Evaluation) -> None:
    receipt.review_reasons = [r.model_dump(mode="json") for r in evaluation.reasons]
    receipt.overall_confidence = evaluation.overall_confidence
    for i, line in enumerate(receipt.lines):
        if i in evaluation.line_flags:
            line.review_status = LineReviewStatus.NEEDS_REVIEW
        else:
            line.review_status = LineReviewStatus.EDITED if line.manually_edited else LineReviewStatus.OK


# ---------------------------------------------------------------- upload


@dataclass
class UploadInit:
    receipt: Receipt
    upload: PresignedUpload


def upload_init(
    db: Session,
    settings: Settings,
    storage: ObjectStorage,
    user: User,
    *,
    filename: str,
    content_type: str,
    size_bytes: int,
    sha256: str,
    merchant_hint: Merchant | None,
    allow_duplicate: bool,
) -> UploadInit:
    errors: list[tuple[str, str]] = []
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if content_type not in ALLOWED_MIMES:
        errors.append(("content_type", "Tipo de ficheiro não suportado. Use PDF, JPEG, PNG ou HEIC."))
    elif EXTENSIONS.get(ext) is None or source_type_for(EXTENSIONS[ext]) != source_type_for(content_type):
        errors.append(("filename", "A extensão do ficheiro não corresponde ao tipo indicado."))
    if size_bytes > settings.upload_max_bytes:
        errors.append(("size_bytes", f"O ficheiro excede {settings.upload_max_bytes // (1024 * 1024)} MB."))
    if errors:
        raise ServiceError(422, "invalid_file", "Ficheiro inválido.", field_errors=errors)

    sha = sha256.lower()
    if not allow_duplicate:
        dup = db.scalar(
            select(Receipt)
            .where(
                Receipt.user_id == user.id,
                (Receipt.file_hash == sha) | (Receipt.declared_hash == sha),
                Receipt.processing_status.notin_([s.value for s in ERROR_STATUSES]),
            )
            .order_by(Receipt.created_at.desc())
            .limit(1)
        )
        if dup is not None:
            raise ServiceError(
                409,
                "duplicate_suspected",
                "Este documento parece já ter sido importado.",
                details={"duplicate_of": duplicate_info(dup)},
            )

    receipt_id = uuid.uuid4()
    receipt = Receipt(
        id=receipt_id,
        user_id=user.id,
        merchant_hint=merchant_hint if merchant_hint and merchant_hint != Merchant.UNKNOWN else None,
        source_type=source_type_for(content_type),
        content_type=content_type,
        size_bytes=size_bytes,
        object_key=original_key(user.id, receipt_id),
        declared_hash=sha,
        processing_status=ReceiptStatus.RECEIVED,
        # The user already chose "guardar nova versão" for a known duplicate.
        duplicate_acknowledged=allow_duplicate,
    )
    db.add(receipt)
    db.flush()
    upload = storage.presign_upload(
        receipt.object_key, content_type, settings.upload_max_bytes, settings.upload_url_ttl_seconds
    )
    audit.record(
        db, "UPLOAD_INITIATED", user_id=user.id, receipt_id=receipt.id, source_type=receipt.source_type
    )
    return UploadInit(receipt=receipt, upload=upload)


def duplicate_info(receipt: Receipt) -> dict[str, Any]:
    return {
        "receipt_id": str(receipt.id),
        "merchant": receipt.merchant,
        "purchase_at": receipt.purchase_at.isoformat() if receipt.purchase_at else None,
        "total_cents": receipt.total_cents,
        "created_at": receipt.created_at.isoformat() if receipt.created_at else None,
    }


# ---------------------------------------------------------------- processing


def request_processing(
    db: Session,
    settings: Settings,
    storage: ObjectStorage,
    queue: JobQueue,
    user: User,
    receipt_id: uuid.UUID,
    idempotency_key: str,
    external: bool = False,
) -> tuple[ProcessingJob, bool]:
    """Start or restart processing. The same Idempotency-Key always returns the same job."""
    receipt = get_owned(db, user, receipt_id, for_update=True)
    scoped_key = f"{receipt.id}:{idempotency_key}"
    existing = db.scalar(select(ProcessingJob).where(ProcessingJob.idempotency_key == scoped_key))
    if existing is not None:
        return existing, False
    if receipt.processing_status not in PROCESSABLE_STATUSES:
        raise ServiceError(409, "invalid_state", "Esta compra não pode ser processada no estado atual.")
    active = db.scalar(
        select(ProcessingJob).where(
            ProcessingJob.receipt_id == receipt.id,
            ProcessingJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRY]),
        )
    )
    if active is not None:
        return active, False
    if receipt.original_deleted_at is not None:
        raise ServiceError(409, "original_deleted", "O ficheiro original já foi eliminado.")
    if not all(storage.exists(key) for key in [receipt.object_key, *(p.object_key for p in receipt.photos)]):
        # F-24: the upload did not complete (e.g. network loss). Recoverable: upload again.
        raise ServiceError(409, "upload_missing", "O envio do ficheiro não foi concluído. Envie novamente.")
    job = ProcessingJob(
        receipt_id=receipt.id,
        idempotency_key=scoped_key,
        status=JobStatus.QUEUED,
        external_requested=external,
        source_type=receipt.source_type,
    )
    db.add(job)
    receipt.processing_status = ReceiptStatus.RECEIVED
    receipt.error_code = None
    receipt.version += 1
    db.flush()
    audit.record(
        db, "PROCESSING_REQUESTED", user_id=user.id, receipt_id=receipt.id, job_id=job.id, external=external
    )
    # Enqueued after commit by the caller (see routers) so the worker always finds the job.
    return job, True


def photo_file(receipt: Receipt, position: int) -> tuple[str, str]:
    """Key and content type of a photo of the receipt (0 is the first, the rest are `receipt.photos`)."""
    if position == 0:
        return receipt.object_key, receipt.content_type
    photo = next((p for p in receipt.photos if p.position == position), None)
    if photo is None:
        raise ServiceError(404, "not_found", "Foto não encontrada.")
    return photo.object_key, photo.content_type


def new_upload_url(
    db: Session,
    settings: Settings,
    storage: ObjectStorage,
    user: User,
    receipt_id: uuid.UUID,
    position: int = 0,
) -> PresignedUpload:
    """F-24: a fresh upload URL for a photo (0 is the first) of a receipt whose upload did not complete."""
    receipt = get_owned(db, user, receipt_id)
    if receipt.processing_status != ReceiptStatus.RECEIVED or latest_job(db, receipt.id) is not None:
        raise ServiceError(409, "invalid_state", "O ficheiro desta compra já foi recebido.")
    key, content_type = photo_file(receipt, position)
    return storage.presign_upload(
        key, content_type, settings.upload_max_bytes, settings.upload_url_ttl_seconds
    )


def add_photo(
    db: Session,
    settings: Settings,
    storage: ObjectStorage,
    user: User,
    receipt_id: uuid.UUID,
    *,
    filename: str,
    content_type: str,
    size_bytes: int,
    sha256: str,
) -> PresignedUpload:
    """Another photo of a receipt too long for one (joined with the first when processed)."""
    receipt = get_owned(db, user, receipt_id, for_update=True)
    if receipt.processing_status != ReceiptStatus.RECEIVED or latest_job(db, receipt.id) is not None:
        raise ServiceError(409, "invalid_state", "Só pode acrescentar fotos antes de processar a compra.")
    errors: list[tuple[str, str]] = []
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if content_type not in IMAGE_MIMES or EXTENSIONS.get(ext) != content_type:
        errors.append(("content_type", "Só é possível juntar fotografias (JPEG, PNG ou HEIC)."))
    if size_bytes > settings.upload_max_bytes:
        errors.append(("size_bytes", f"O ficheiro excede {settings.upload_max_bytes // (1024 * 1024)} MB."))
    if receipt.source_type != SourceType.IMAGE:
        errors.append(("content_type", "Só é possível juntar fotografias a uma fotografia."))
    if len(receipt.photos) + 1 >= settings.upload_max_photos:
        errors.append(("photos", f"No máximo {settings.upload_max_photos} fotos por nota."))
    if errors:
        raise ServiceError(422, "invalid_file", "Ficheiro inválido.", field_errors=errors)
    position = max((p.position for p in receipt.photos), default=0) + 1
    photo = ReceiptPhoto(
        receipt_id=receipt.id,
        position=position,
        object_key=photo_key(user.id, receipt.id, position),
        content_type=content_type,
        size_bytes=size_bytes,
        declared_hash=sha256.lower(),
    )
    receipt.photos.append(photo)
    db.flush()
    audit.record(db, "PHOTO_ADDED", user_id=user.id, receipt_id=receipt.id, position=position)
    return storage.presign_upload(
        photo.object_key, content_type, settings.upload_max_bytes, settings.upload_url_ttl_seconds
    )


# ---------------------------------------------------------------- review / edit

LINE_FIELDS = (
    "raw_description",
    "quantity",
    "unit",
    "unit_price_cents",
    "line_total_cents",
    "discount_cents",
)


def _check_editable(receipt: Receipt, version: int) -> None:
    if receipt.version != version:
        raise ServiceError(
            409,
            "version_conflict",
            "A compra foi alterada noutro lado. Recarregue.",
            details={"current_version": receipt.version},
        )
    if receipt.processing_status not in EDITABLE_STATUSES:
        raise ServiceError(409, "invalid_state", "Esta compra não pode ser editada no estado atual.")


def edit_receipt(
    db: Session, settings: Settings, user: User, receipt_id: uuid.UUID, patch: dict[str, Any]
) -> Receipt:
    receipt = get_owned(db, user, receipt_id, for_update=True)
    _check_editable(receipt, patch["version"])
    edited = set(receipt.manually_edited_fields or [])

    if "merchant" in patch and patch["merchant"] != receipt.merchant:
        receipt.merchant = patch["merchant"]
        edited.add("merchant")
    if "merchant_name" in patch and patch["merchant_name"] != receipt.merchant_name:
        receipt.merchant_name = patch["merchant_name"]
        edited.add("merchant_name")
    if "purchase_at" in patch and patch["purchase_at"] != receipt.purchase_at:
        receipt.purchase_at = patch["purchase_at"]
        edited.add("purchase_at")
    if "total_cents" in patch and patch["total_cents"] != receipt.total_cents:
        receipt.total_cents = patch["total_cents"]
        edited.add("total")

    if "lines" in patch and patch["lines"] is not None:
        _apply_lines(db, user, receipt, patch["lines"])
        edited.add("lines")

    receipt.manually_edited_fields = sorted(edited)
    receipt.version += 1
    if receipt.processing_status == ReceiptStatus.COMPLETED:
        receipt.processing_status = ReceiptStatus.REVIEW_REQUIRED
    evaluation = evaluate_receipt(db, settings, receipt)
    apply_evaluation(receipt, evaluation)
    receipt.processing_status = (
        ReceiptStatus.REVIEW_REQUIRED if evaluation.reasons else ReceiptStatus.COMPLETED
    )
    db.flush()
    audit.record(db, "RECEIPT_EDITED", user_id=user.id, receipt_id=receipt.id, fields=sorted(edited))
    return receipt


def _apply_lines(db: Session, user: User, receipt: Receipt, incoming: list[dict[str, Any]]) -> None:
    existing = {line.id: line for line in receipt.lines}
    kept: list[ReceiptLine] = []
    for position, data in enumerate(incoming, start=1):
        line_id = data.get("id")
        line = existing.pop(line_id, None) if line_id else None
        if line_id and line is None:
            raise ServiceError(
                422,
                "unknown_line",
                "Linha inexistente.",
                field_errors=[(f"lines.{position - 1}.id", "Linha inexistente.")],
            )
        is_new = line is None
        if line is None:
            line = ReceiptLine(
                receipt_id=receipt.id,
                line_number=position,
                raw_description="",
                discount_cents=0,
                field_confidence={},
                evidence={},
            )
            db.add(line)
        changed = _update_line(line, data, is_new)
        if changed:
            line.manually_edited = True
            line.edited_fields = sorted(set(line.edited_fields or []) | changed)
            ev = dict(line.evidence or {})
            ev["unresolved"] = False  # the user has looked at it
            line.evidence = ev
        if data.get("product_id"):
            product = products.get_visible(db, user.id, data["product_id"])
            if product is None:
                raise ServiceError(422, "unknown_product", "Produto inexistente.")
            line.normalized_product_id = product.id
            products.remember_alias(db, user.id, line.raw_description, product.id)
            line.manually_edited = True
        elif data.get("product_name"):
            line.normalized_product_id = products.assign_by_name(
                db, user.id, line.raw_description, data["product_name"], line.unit
            )
            line.manually_edited = True
        elif (line.normalized_product_id is None or "description" in changed) and line.raw_description:
            line.normalized_product_id = products.resolve_product(
                db, user.id, line.raw_description, line.unit
            )
        line.line_number = position
        kept.append(line)
    for removed in existing.values():
        db.delete(removed)
    receipt.lines = kept
    db.flush()
    for line in kept:  # product ids may have changed: reload the relationship
        db.expire(line, ["product"])


def _update_line(line: ReceiptLine, data: dict[str, Any], is_new: bool) -> set[str]:
    changed: set[str] = set()
    mapping = {
        "raw_description": ("raw_description", "description"),
        "quantity": ("quantity_decimal", "quantity"),
        "unit": ("unit", "unit"),
        "unit_price_cents": ("unit_price_cents", "unit_price"),
        "line_total_cents": ("line_total_cents", "line_total"),
        "discount_cents": ("discount_cents", "discount"),
    }
    for key, (attr, label) in mapping.items():
        if key not in data:
            continue
        value = data[key]
        if key == "discount_cents" and value is None:
            value = 0
        if is_new or getattr(line, attr) != value:
            setattr(line, attr, value)
            changed.add(label)
    # RF-07: recalculate derived values.
    qty, unit_price, total = line.quantity_decimal, line.unit_price_cents, line.line_total_cents
    if (
        ("quantity" in changed or "unit_price" in changed)
        and "line_total" not in changed
        and qty is not None
        and unit_price is not None
    ):
        line.line_total_cents = multiply_cents(qty, unit_price)
        changed.add("line_total")
    elif "line_total" in changed and "unit_price" not in changed and qty and total is not None:
        line.unit_price_cents = int((Decimal(total) / qty).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        changed.add("unit_price")
    if is_new and line.unit is None and qty is not None:
        line.unit = "un"
    return changed


def confirm(db: Session, settings: Settings, user: User, receipt_id: uuid.UUID, version: int) -> Receipt:
    receipt = get_owned(db, user, receipt_id, for_update=True)
    if receipt.processing_status == ReceiptStatus.CONFIRMED:
        return receipt  # idempotent: confirming twice never double counts
    _check_editable(receipt, version)
    evaluation = evaluate_receipt(db, settings, receipt)
    if evaluation.blocking:
        raise ServiceError(
            409,
            "review_incomplete",
            "Ainda há valores em falta para confirmar a compra.",
            details={"reasons": [r.model_dump(mode="json") for r in evaluation.blocking]},
        )
    apply_evaluation(receipt, evaluation)
    mark_confirmed(receipt, settings, ConfirmedBy.USER)
    db.flush()
    audit.record(db, "RECEIPT_CONFIRMED", user_id=user.id, receipt_id=receipt.id, by=ConfirmedBy.USER)
    return receipt


def mark_confirmed(receipt: Receipt, settings: Settings, by: ConfirmedBy) -> None:
    now = datetime.now(UTC)
    receipt.processing_status = ReceiptStatus.CONFIRMED
    receipt.confirmed_at = now
    receipt.confirmed_by = by
    if receipt.original_deleted_at is None:
        receipt.original_expires_at = now + timedelta(days=settings.retention_days)
    receipt.version += 1


def duplicate_decision(
    db: Session, settings: Settings, storage: ObjectStorage, user: User, receipt_id: uuid.UUID, keep: bool
) -> Receipt | None:
    receipt = get_owned(db, user, receipt_id, for_update=True)
    if not keep:
        delete_receipt(db, storage, user, receipt_id)
        return None
    receipt.duplicate_acknowledged = True
    receipt.version += 1
    if receipt.processing_status in EDITABLE_STATUSES:
        evaluation = evaluate_receipt(db, settings, receipt)
        apply_evaluation(receipt, evaluation)
        receipt.processing_status = (
            ReceiptStatus.REVIEW_REQUIRED if evaluation.reasons else ReceiptStatus.COMPLETED
        )
    audit.record(db, "DUPLICATE_KEPT", user_id=user.id, receipt_id=receipt.id)
    return receipt


def mark_unsupported(db: Session, user: User, receipt_id: uuid.UUID) -> Receipt:
    receipt = get_owned(db, user, receipt_id, for_update=True)
    if receipt.processing_status == ReceiptStatus.CONFIRMED:
        raise ServiceError(409, "invalid_state", "Compra já confirmada.")
    receipt.lines = []
    receipt.processing_status = ReceiptStatus.UNSUPPORTED_MERCHANT
    receipt.error_code = "user_marked_unsupported"
    receipt.version += 1
    audit.record(db, "MARKED_UNSUPPORTED", user_id=user.id, receipt_id=receipt.id)
    return receipt


# ---------------------------------------------------------------- deletion


def delete_receipt(db: Session, storage: ObjectStorage, user: User, receipt_id: uuid.UUID) -> None:
    receipt = get_owned(db, user, receipt_id, for_update=True)
    storage.delete_prefix(receipt_prefix(user.id, receipt.id))  # original and any derivative
    db.delete(receipt)  # lines cascade; aggregates are queries, so they update immediately
    audit.record(db, "RECEIPT_DELETED", user_id=user.id, receipt_id=receipt_id)


def delete_original(db: Session, storage: ObjectStorage, user: User, receipt_id: uuid.UUID) -> Receipt:
    receipt = get_owned(db, user, receipt_id, for_update=True)
    if receipt.processing_status not in (ReceiptStatus.CONFIRMED, *ERROR_STATUSES, *EDITABLE_STATUSES):
        raise ServiceError(409, "invalid_state", "Aguarde o fim do processamento.")
    storage.delete_prefix(receipt_prefix(user.id, receipt.id))
    receipt.original_deleted_at = datetime.now(UTC)
    receipt.original_expires_at = None
    receipt.version += 1
    audit.record(db, "ORIGINAL_DELETED", user_id=user.id, receipt_id=receipt.id, reason="user_request")
    return receipt
