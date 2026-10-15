from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from basketiq.domain.confidence import LineView, ReceiptView, Thresholds, evaluate


def view(**overrides) -> ReceiptView:  # type: ignore[no-untyped-def]
    base = dict(
        merchant="PINGO_DOCE",
        purchase_at=datetime(2026, 9, 1, tzinfo=UTC),
        total_cents=300,
        subtotal_cents=None,
        tax_cents=None,
        field_confidence={"merchant": 0.99, "purchase_at": 0.99, "total": 0.98},
        lines=[
            LineView(
                "A",
                Decimal(1),
                100,
                0,
                {"description": 0.95, "quantity": 0.92, "unit_price": 0.92, "line_total": 0.98},
            ),
            LineView(
                "B",
                Decimal(2),
                200,
                0,
                {"description": 0.95, "quantity": 0.98, "unit_price": 0.98, "line_total": 0.98},
            ),
        ],
    )
    base.update(overrides)
    return ReceiptView(**base)  # type: ignore[arg-type]


def codes(v: ReceiptView, t: Thresholds | None = None) -> set[str]:
    return {r.code for r in evaluate(v, t or Thresholds()).reasons}


@pytest.mark.req("F-15a")
def test_confident_and_reconciled_is_auto_confirmable() -> None:
    ev = evaluate(view(), Thresholds())
    assert ev.auto_confirmable and ev.overall_confidence == 0.92


@pytest.mark.req("F-12")
def test_field_below_threshold_requires_review() -> None:
    v = view()
    v.lines[0].field_confidence["quantity"] = 0.79
    ev = evaluate(v, Thresholds())
    assert "low_confidence" in {r.code for r in ev.reasons}
    assert ev.line_flags[0] == ["low_confidence:quantity"]


@pytest.mark.req("RF-06")
def test_threshold_can_vary_per_field() -> None:
    v = view()
    assert codes(v, Thresholds(per_field={"quantity": 0.95})) == {"low_confidence"}


@pytest.mark.req("F-13")
def test_manual_edit_counts_as_certain() -> None:
    v = view()
    v.lines[0].field_confidence["quantity"] = 0.3
    v.lines[0].edited_fields = frozenset({"quantity"})
    assert codes(v) == set()


@pytest.mark.req("RF-06")
def test_totals_outside_tolerance() -> None:
    assert "totals_mismatch" in codes(view(total_cents=310))
    assert codes(view(total_cents=302)) == set()  # within the 2 cent tolerance
    assert "totals_mismatch" in codes(view(total_cents=302), Thresholds(tolerance_cents=1))


@pytest.mark.req("RF-06")
def test_vat_summary_must_match_total() -> None:
    assert "totals_mismatch" in codes(view(subtotal_cents=250, tax_cents=10))


@pytest.mark.req("PT-7")
def test_pending_quantity_blocks_confirmation() -> None:
    v = view()
    v.lines[0].quantity = None
    ev = evaluate(v, Thresholds())
    assert "quantity_pending" in {r.code for r in ev.blocking}


@pytest.mark.req("F-10")
def test_unknown_merchant_and_missing_date_are_blocking() -> None:
    ev = evaluate(view(merchant="UNKNOWN", purchase_at=None), Thresholds())
    assert {"merchant_unknown", "date_missing"} <= {r.code for r in ev.blocking}


@pytest.mark.req("F-17")
def test_suspected_duplicate_prevents_auto_confirm() -> None:
    assert "possible_duplicate" in codes(view(duplicate_suspected=True))


@pytest.mark.req("PT-7")
def test_discounts_are_part_of_reconciliation() -> None:
    v = view(total_cents=250)
    v.lines[1].discount_cents = 50
    assert codes(v) == set()
