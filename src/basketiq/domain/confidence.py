"""Review decision: combines per-field confidence, consistency rules and reconciliation (RF-06).

Used both right after extraction and after every user edit, so the same rules decide whether a
receipt can be confirmed automatically and what the review screen must highlight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from basketiq.domain.reconciliation import Reconciliation, reconcile
from basketiq.domain.status import Merchant
from basketiq.pipeline.schema import ReviewReason

# Reasons that also prevent a *user* confirmation: values are missing, not merely uncertain.
# An unknown store does not block: a purchase at a corner shop is a purchase like any other.
BLOCKING_CODES = frozenset({"date_missing", "quantity_pending", "line_total_missing", "no_lines"})


@dataclass
class Thresholds:
    default: float = 0.80
    per_field: dict[str, float] = field(default_factory=dict)
    tolerance_cents: int = 2

    def for_field(self, name: str) -> float:
        return self.per_field.get(name, self.default)


@dataclass
class LineView:
    raw_description: str
    quantity: Decimal | None
    line_total_cents: int | None
    discount_cents: int
    field_confidence: dict[str, float]
    unresolved: bool = False
    edited_fields: frozenset[str] = frozenset()


@dataclass
class ReceiptView:
    merchant: str | None
    purchase_at: datetime | None
    total_cents: int | None
    subtotal_cents: int | None
    tax_cents: int | None
    field_confidence: dict[str, float]
    lines: list[LineView]
    edited_fields: frozenset[str] = frozenset()
    duplicate_suspected: bool = False
    merchant_hint_conflict: bool = False
    # What was done to join the photos of one receipt: {"photo", "overlap", "removed"} per pair of photos.
    join_notes: list[Any] = field(default_factory=list)
    # What the fiscal QR code said against what was read: {"code", "field", "message", "details"} each.
    qr_notes: list[Any] = field(default_factory=list)


@dataclass
class Evaluation:
    reasons: list[ReviewReason]
    line_flags: dict[int, list[str]]
    reconciliation: Reconciliation
    overall_confidence: float

    @property
    def auto_confirmable(self) -> bool:
        return not self.reasons

    @property
    def blocking(self) -> list[ReviewReason]:
        return [r for r in self.reasons if r.code in BLOCKING_CODES]


def _effective(conf: dict[str, float], name: str, edited: frozenset[str]) -> float:
    # A value the user typed or confirmed is certain by definition.
    return 1.0 if name in edited else conf.get(name, 0.0)


def _join_reason(note: dict[str, Any]) -> ReviewReason:
    first, second = note["photo"], note["photo"] + 1
    if note["overlap"] == 0:
        return ReviewReason(
            code="photos_no_overlap",
            message=(
                f"Não encontrámos linhas em comum entre a foto {first} e a foto {second}: "
                "pode faltar uma parte da nota."
            ),
            details=dict(note),
        )
    removed = "; ".join(note["removed"])
    return ReviewReason(
        code="photos_joined",
        message=(
            f"Juntámos a foto {first} e a foto {second} e removemos {len(note['removed'])} linhas "
            f"repetidas ou cortadas: {removed}. Se alguma era uma compra repetida, acrescente-a."
        ),
        details=dict(note),
    )


def evaluate(view: ReceiptView, thresholds: Thresholds) -> Evaluation:
    reasons: list[ReviewReason] = []
    line_flags: dict[int, list[str]] = {}
    confidences: list[float] = []

    if view.merchant in (None, Merchant.UNKNOWN):
        reasons.append(
            ReviewReason(
                code="merchant_unknown",
                field="merchant",
                message="Loja não reconhecida: confirme o nome e reveja as linhas.",
            )
        )
    elif view.merchant_hint_conflict and "merchant" not in view.edited_fields:
        reasons.append(
            ReviewReason(
                code="merchant_mismatch",
                field="merchant",
                message="O supermercado detetado difere do indicado. Confirme o supermercado.",
            )
        )

    if view.purchase_at is None:
        reasons.append(
            ReviewReason(code="date_missing", field="purchase_at", message="Indique a data da compra.")
        )

    present = {
        "merchant": view.merchant not in (None, Merchant.UNKNOWN),
        "purchase_at": view.purchase_at is not None,
        "total": view.total_cents is not None,
    }
    for name, value_present in present.items():
        if not value_present or (name not in view.field_confidence and name not in view.edited_fields):
            continue
        c = _effective(view.field_confidence, name, view.edited_fields)
        confidences.append(c)
        if c < thresholds.for_field(name):
            reasons.append(
                ReviewReason(
                    code="low_confidence",
                    field=name,
                    message="Valor lido com pouca confiança.",
                    details={"confidence": c},
                )
            )

    if not view.lines:
        reasons.append(
            ReviewReason(code="no_lines", message="Não foram encontrados produtos. Adicione as linhas.")
        )

    for i, line in enumerate(view.lines):
        flags: list[str] = []
        if line.unresolved and not line.edited_fields:
            flags.append("unresolved")
            reasons.append(
                ReviewReason(
                    code="line_unresolved", line_index=i, message="Linha não identificada: reveja ou elimine."
                )
            )
        if line.quantity is None:
            flags.append("quantity_pending")
            reasons.append(
                ReviewReason(
                    code="quantity_pending",
                    line_index=i,
                    field="quantity",
                    message="Quantidade por confirmar.",
                )
            )
        if line.line_total_cents is None:
            flags.append("line_total_missing")
            reasons.append(
                ReviewReason(
                    code="line_total_missing",
                    line_index=i,
                    field="line_total",
                    message="Preço da linha em falta.",
                )
            )
        for name in ("description", "quantity", "unit", "unit_price", "line_total", "discount"):
            if name not in line.field_confidence and name not in line.edited_fields:
                continue
            c = _effective(line.field_confidence, name, line.edited_fields)
            confidences.append(c)
            if c < thresholds.for_field(name):
                flags.append(f"low_confidence:{name}")
        if any(f.startswith("low_confidence") for f in flags) and "unresolved" not in flags:
            fields = [f.split(":")[1] for f in flags if f.startswith("low_confidence")]
            reasons.append(
                ReviewReason(
                    code="low_confidence",
                    line_index=i,
                    message=(
                        "Unidade por confirmar: são unidades ou kg?"
                        if fields == ["unit"]
                        else "Valores lidos com pouca confiança."
                    ),
                    details={"fields": fields},
                )
            )
        if flags:
            line_flags[i] = flags

    rec = reconcile(
        view.lines, view.total_cents, thresholds.tolerance_cents, view.subtotal_cents, view.tax_cents
    )
    if view.total_cents is None:
        reasons.append(
            ReviewReason(code="total_missing", field="total", message="Total da nota não encontrado.")
        )
    elif not rec.within_tolerance:
        reasons.append(
            ReviewReason(
                code="totals_mismatch",
                field="total",
                message="A soma das linhas não coincide com o total da nota.",
                details={
                    "printed_total_cents": rec.printed_total_cents,
                    "recalculated_total_cents": rec.recalculated_total_cents,
                    "difference_cents": rec.difference_cents,
                    "checks": rec.checks,
                },
            )
        )

    for note in view.join_notes:
        reasons.append(_join_reason(note))

    for qr_note in view.qr_notes:
        if qr_note["field"] not in view.edited_fields:  # a value the person confirmed is settled
            reasons.append(
                ReviewReason(
                    code=qr_note["code"],
                    field=qr_note["field"],
                    message=qr_note["message"],
                    details=dict(qr_note["details"]),
                )
            )

    if view.duplicate_suspected:
        reasons.append(
            ReviewReason(code="possible_duplicate", message="Esta compra parece já ter sido importada.")
        )

    overall = round(min(confidences), 3) if confidences else 0.0
    return Evaluation(reasons=reasons, line_flags=line_flags, reconciliation=rec, overall_confidence=overall)
