"""The fiscal QR code (spec 009, A1): reading, interpreting, classifying, checking and the no-QR guarantee.

Messages follow the AT specification (Portaria 195/2020, point 4) with made-up values; the real receipts are
checked by `tests/golden/test_golden_qr.py` (marker golden_real)."""

from __future__ import annotations

import sys
import time
from dataclasses import fields
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from basketiq.domain.confidence import Thresholds, evaluate
from basketiq.domain.status import Merchant, ProcessingRoute, SourceType
from basketiq.eval.golden import discover
from basketiq.pipeline import qr as qr_module
from basketiq.pipeline.classify import CHAIN_NIFS, MIN_CONFIDENCE, classify
from basketiq.pipeline.ocr import NoOcr
from basketiq.pipeline.orchestrator import (
    PipelineDeps,
    PipelineOutcome,
    PipelineRequest,
    run_pipeline,
    view_from_parsed,
)
from basketiq.pipeline.parsers.base import LISBON
from basketiq.pipeline.qr import AtQr, apply_qr, atcud_matches_print, parse_at_message, read_at_qr
from basketiq.pipeline.schema import ParsedReceipt
from tests.helpers import (
    BUYER,
    ISSUER,
    PINGO_LINES,
    SYNTHETIC,
    FakeOcr,
    at_message,
    doc_from_lines,
    make_pdf,
    make_pdf_with_qr,
    make_photo_with_qr,
)

# The example of the specification, point 5.2 (fatura simplificada).
SPEC_EXAMPLE = (
    "A:123456789*B:999999990*C:PT*D:FS*E:N*F:20190812*G:FS CDVF/12345*H:CDF7T5HD-12345*I1:PT*I7:0.65*I8:0.15*"
    "N:0.15*O:0.80*Q:YhGV*R:9999*S:NU;0.80"
)


def qr(**kw: Any) -> AtQr:
    parsed = parse_at_message(at_message(**kw))
    assert parsed is not None
    return parsed


# ----------------------------------------------------------------------------------------- F-39: reading


@pytest.mark.req("F-39")
def test_reads_the_qr_of_a_photo_and_of_a_pdf_page_after_the_first() -> None:
    message = at_message(total="8.81")
    photo = read_at_qr(make_photo_with_qr(message), SourceType.IMAGE)
    assert photo is not None and (photo.total_cents, photo.issuer_nif) == (881, ISSUER)
    later_page = read_at_qr(make_pdf_with_qr(["x 1,00"], message, qr_page=2, pages=2), SourceType.PDF)
    assert later_page is not None and later_page.doc_date == date(2026, 9, 15)


@pytest.mark.req("F-39")
def test_a_small_qr_in_a_big_photo_and_a_huge_photo_are_both_read() -> None:
    message = at_message()
    assert read_at_qr(make_photo_with_qr(message, (900, 1600), qr_px=105), SourceType.IMAGE) is not None
    assert read_at_qr(make_photo_with_qr(message, (3000, 4500), qr_px=900), SourceType.IMAGE) is not None


@pytest.mark.req("F-39")
def test_without_a_readable_fiscal_qr_there_is_nothing() -> None:
    assert read_at_qr(make_pdf(PINGO_LINES), SourceType.PDF) is None
    assert read_at_qr(make_photo_with_qr("https://example.invalid/not-a-receipt"), SourceType.IMAGE) is None
    assert read_at_qr(make_photo_with_qr("A:1*F:2*O:3"), SourceType.IMAGE) is None  # looks fiscal, is not


@pytest.mark.req("F-39")
def test_the_time_budget_is_respected() -> None:
    photo = make_photo_with_qr(at_message())
    assert read_at_qr(photo, SourceType.IMAGE, budget_s=0.0) is None
    started = time.perf_counter()
    assert read_at_qr(photo, SourceType.IMAGE) is not None
    assert time.perf_counter() - started < qr_module.QR_BUDGET_S


@pytest.mark.req("F-39")
def test_reading_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    assert read_at_qr(b"not an image", SourceType.IMAGE) is None
    assert read_at_qr(b"%PDF- broken", SourceType.PDF) is None
    photo = make_photo_with_qr(at_message())
    monkeypatch.setattr(
        qr_module, "_decode", lambda _array: (_ for _ in ()).throw(RuntimeError("decoder crash"))
    )
    assert read_at_qr(photo, SourceType.IMAGE) is None
    monkeypatch.undo()
    monkeypatch.setitem(sys.modules, "zxingcpp", None)  # the library is not installed
    assert read_at_qr(photo, SourceType.IMAGE) is None


def _deps(ocr: Any = None, **kw: Any) -> PipelineDeps:
    return PipelineDeps(
        ocr=ocr or NoOcr(),
        resolver=None,
        external=None,
        external_budget_remaining_cents=0,
        thresholds_for=lambda _m: Thresholds(),
        merchant_enabled=lambda _m: True,
        max_bytes=20 * 1024 * 1024,
        max_pages=5,
        **kw,
    )


@pytest.mark.req("F-39")
def test_all_the_files_of_a_receipt_share_one_budget() -> None:
    budgets: list[float] = []

    def slow_reader(_data: bytes, _kind: SourceType, budget: float) -> AtQr | None:
        budgets.append(budget)
        time.sleep(0.3)
        return None

    request = PipelineRequest(
        data=make_photo_with_qr("x", (300, 300), 100),
        declared_mime="image/png",
        extra=[(make_photo_with_qr("y", (300, 300), 100), "image/png")] * 2,
    )
    run_pipeline(request, _deps(ocr=FakeOcr(["PAO 1,00"]), qr_reader=slow_reader))
    assert len(budgets) == 3 and budgets[0] == qr_module.QR_BUDGET_S
    assert budgets[0] > budgets[1] > budgets[2]


# ------------------------------------------------------------------------- F-40: interpreting the message


@pytest.mark.req("F-40")
def test_reads_the_fields_in_use_from_the_specification_example() -> None:
    parsed = parse_at_message(SPEC_EXAMPLE)
    assert parsed == AtQr(
        issuer_nif="123456789",
        doc_date=date(2019, 8, 12),
        total_cents=80,
        doc_type="FS",
        doc_number="FS CDVF/12345",
        atcud="CDF7T5HD-12345",
        tax_cents=15,
    )


@pytest.mark.req("F-40")
def test_the_buyers_tax_number_has_nowhere_to_live() -> None:
    parsed = qr()
    assert BUYER not in repr(parsed) and BUYER not in str(sorted(vars(parsed).values(), key=str))
    assert not any("buyer" in f.name or "adquirente" in f.name for f in fields(AtQr))
    consumer = parse_at_message(SPEC_EXAMPLE)
    assert consumer is not None and "999999990" not in repr(consumer)


@pytest.mark.req("F-40")
def test_unknown_new_and_out_of_order_fields_do_not_break_the_reading() -> None:
    message = at_message(extra="Z9:whatever*I9:3.00*S:TB;PT00000000000000000000000;8.81*X:")
    assert qr().total_cents == parse_at_message(message).total_cents == 881  # type: ignore[union-attr]
    shuffled = "*".join(reversed(at_message().split("*")))
    parsed = parse_at_message(shuffled)
    assert parsed is not None and (parsed.issuer_nif, parsed.total_cents) == (ISSUER, 881)


@pytest.mark.req("F-40")
@pytest.mark.parametrize(
    "message",
    [
        "",
        "https://example.invalid/receipt",
        at_message().replace("*O:8.81", ""),  # no total
        at_message(total="8,81"),  # a comma is not the decimal separator of the specification
        at_message(total="8.8"),  # money has two decimals
        at_message(day="20261340"),  # not a date
        at_message(day="2026-09-15"),
        at_message(issuer="12345"),  # not a tax number
        at_message().replace(f"A:{ISSUER}*", ""),  # no issuer
    ],
)
def test_a_message_without_issuer_date_or_total_is_not_a_receipt(message: str) -> None:
    assert parse_at_message(message) is None


@pytest.mark.req("F-40")
def test_optional_fields_that_are_malformed_are_left_out_not_fatal() -> None:
    parsed = parse_at_message(at_message(atcud="short").replace("D:FS", "D:fs1").replace("N:0.50", "N:half"))
    assert parsed is not None
    assert (parsed.atcud, parsed.doc_type, parsed.tax_cents) == (None, None, None)
    assert len(qr(number="X" * 100).doc_number or "") == 64


# --------------------------------------------------------------------------- F-41: classifying by tax number


def _unnamed_receipt() -> Any:
    return doc_from_lines(["Fatura simplificada", "PAO 1,00", "TOTAL 1,00"])


@pytest.mark.req("F-41")
@pytest.mark.parametrize(("merchant", "nif"), sorted(CHAIN_NIFS.items(), key=lambda kv: kv[0].value))
def test_the_issuer_in_the_qr_identifies_the_chain(merchant: Merchant, nif: str) -> None:
    assert classify(_unnamed_receipt()).merchant == Merchant.UNKNOWN
    result = classify(_unnamed_receipt(), issuer_nif=nif)
    assert result.merchant == merchant and "qr_nif" in result.signals and result.confidence >= 0.9


@pytest.mark.req("F-41")
@pytest.mark.parametrize(("merchant", "nif"), sorted(CHAIN_NIFS.items(), key=lambda kv: kv[0].value))
def test_the_same_table_reads_the_number_printed_in_the_text(merchant: Merchant, nif: str) -> None:
    groups = (nif[:3], nif[3:6], nif[6:])
    for printed in (" ".join(groups), ".".join(groups), "".join(groups), " ".join(groups).replace("0", "O")):
        result = classify(doc_from_lines(["Fatura", f"Contribuinte {printed}", "TOTAL 1,00"]))
        assert result.merchant == merchant and result.confidence >= MIN_CONFIDENCE, printed


@pytest.mark.req("F-41")
def test_text_and_qr_agreeing_raise_the_confidence_and_an_unknown_number_changes_nothing() -> None:
    nif = CHAIN_NIFS[Merchant.CONTINENTE]
    doc = doc_from_lines(["Fatura", f"NIF: {nif}", "TOTAL 1,00"])
    assert classify(doc, issuer_nif=nif).confidence > classify(doc).confidence
    assert classify(_unnamed_receipt(), issuer_nif="123456789").merchant == Merchant.UNKNOWN


@pytest.mark.req("F-41")
def test_the_pipeline_classifies_with_the_qr_and_keeps_an_unknown_issuer() -> None:
    lines = ["Mercearia sem nome", " C PAO 1,00", "TOTAL 1,00", "000001 2026-09-15 12:30 0001"]
    data = make_pdf_with_qr(lines, at_message(issuer=CHAIN_NIFS[Merchant.LIDL], total="1.00"))
    known = run_pipeline(PipelineRequest(data=data, declared_mime="application/pdf"), _deps())
    assert known.parsed is not None and known.parsed.merchant == Merchant.LIDL
    other = make_pdf_with_qr(lines, at_message(issuer="123456789", total="1.00"))
    unknown = run_pipeline(PipelineRequest(data=other, declared_mime="application/pdf"), _deps())
    assert unknown.parsed is not None and unknown.parsed.merchant == Merchant.UNKNOWN
    assert unknown.qr is not None and unknown.qr.issuer_nif == "123456789"  # the key of the establishment


# ------------------------------------------------------------------------ F-42: total and date against the QR


def parsed_receipt(total: int | None, day: tuple[int, int, int] | None = (2026, 10, 9)) -> ParsedReceipt:
    return ParsedReceipt(
        merchant=Merchant.CONTINENTE,
        purchase_at=datetime(*day, 15, 16, tzinfo=LISBON) if day else None,
        total_cents=total,
        parser_version="test",
    )


CARD_BALANCE = [
    "SUBTOTAL 10,56",
    "Desconto Cartao Utilizado 7,39",
    "TOTAL A PAGAR 3,17",
    "Cartao Credito 3,17",
]


def check(read: ParsedReceipt, lines: list[str], **kw: Any) -> list[Any]:
    return apply_qr(read, qr(**{"day": "20261009", **kw}), doc_from_lines(lines).lines)


@pytest.mark.req("F-42")
def test_the_qr_total_is_the_read_total_minus_the_card_balance_and_nothing_is_flagged() -> None:
    receipt = parsed_receipt(1056)
    assert check(receipt, CARD_BALANCE, total="3.17") == []
    assert receipt.total_cents == 1056 and receipt.field_sources == {}


@pytest.mark.req("F-42")
def test_a_balance_that_was_not_read_never_lowers_the_total_in_silence() -> None:
    """A photo of the same receipt where the OCR lost the "Desconto Cartao Utilizado" line."""
    receipt = parsed_receipt(1056)
    (note,) = check(receipt, ["SUBTOTAL 10,56", "TOTAL A PAGAR 3,17"], total="3.17")
    assert receipt.total_cents == 1056 and "total" not in receipt.field_sources
    assert note["code"] == "total_qr_mismatch" and note["field"] == "total"
    assert note["details"] == {"read_total_cents": 1056, "qr_total_cents": 317}
    assert (
        "10,56 €" in note["message"]
        and "3,17 €" in note["message"]
        and "saldo de cartão ou vale" in note["message"]
    )


@pytest.mark.req("F-42")
def test_a_qr_total_above_the_read_one_can_only_be_a_misreading_so_the_qr_prevails() -> None:
    receipt = parsed_receipt(816)
    (note,) = check(receipt, ["TOTAL A PAGAR 8,16"], total="10.16")
    assert receipt.total_cents == 1016 and receipt.field_sources["total"] == "qr"
    assert note["code"] == "total_qr_mismatch" and note["details"]["read_total_cents"] == 816
    assert "8,16 €" in note["message"] and "10,16 €" in note["message"]


@pytest.mark.req("F-42")
def test_a_deposit_credit_that_was_identified_explains_the_difference() -> None:
    aldi = ["A PAGAR 61,88 €", "Valor depósito 5,20 €", "PAGAMENTO COM CART 56,68 €"]
    receipt = parsed_receipt(6188)
    assert check(receipt, aldi, total="56.68") == []
    assert receipt.total_cents == 6188
    assert check(parsed_receipt(6188), aldi, total="61.88") == []  # the document total did not take it off
    (note,) = check(parsed_receipt(6188), aldi, total="40.00")
    assert note["details"]["balance_cents"] == 520 and "5,20 €" in note["message"]


@pytest.mark.req("F-42")
def test_without_a_read_total_the_qr_fills_it_in_and_the_receipt_is_reviewed() -> None:
    receipt = parsed_receipt(None)
    (note,) = check(receipt, ["TOTAL", "Desconto Cartao Utilizado 7,39"], total="3.17")
    assert receipt.total_cents == 1056  # the document total plus the balance printed on the receipt
    assert receipt.field_sources["total"] == "qr" and note["code"] == "total_from_qr"


@pytest.mark.req("F-42")
def test_the_date_follows_the_qr_and_the_time_stays_from_the_receipt() -> None:
    receipt = parsed_receipt(1056, (2026, 10, 8))
    (note,) = check(receipt, CARD_BALANCE, total="3.17")
    assert receipt.purchase_at == datetime(2026, 10, 9, 15, 16, tzinfo=LISBON)
    assert note["code"] == "date_qr_mismatch" and note["field"] == "purchase_at"
    assert note["details"] == {"read_date": "2026-10-08", "qr_date": "2026-10-09"}
    missing = parsed_receipt(1056, None)
    assert check(missing, CARD_BALANCE, total="3.17") == []
    assert missing.purchase_at is not None and missing.purchase_at.date() == date(2026, 10, 9)
    assert missing.field_sources["purchase_at"] == "qr"


@pytest.mark.req("F-42")
def test_what_the_qr_said_reaches_the_review_and_a_confirmed_value_settles_it() -> None:
    receipt = parsed_receipt(1056)
    notes = check(receipt, ["SUBTOTAL 10,56"], total="3.17")
    reasons = evaluate(view_from_parsed(receipt, False, None, notes), Thresholds()).reasons
    assert "total_qr_mismatch" in {r.code for r in reasons}
    view = view_from_parsed(receipt, False, None, notes)
    view.edited_fields = frozenset({"total"})
    assert "total_qr_mismatch" not in {r.code for r in evaluate(view, Thresholds()).reasons}


@pytest.mark.req("F-42")
def test_the_sum_of_the_lines_is_still_checked_against_the_total_the_qr_left() -> None:
    lines = ["Mercearia", " C PAO 5,00", " C LEITE 3,00", "TOTAL 8,00", "000001 2026-09-15 12:30 0001"]
    data = make_pdf_with_qr(lines, at_message(issuer="123456789", total="10.00", day="20260915"))
    out = run_pipeline(PipelineRequest(data=data, declared_mime="application/pdf"), _deps())
    assert out.parsed is not None and out.parsed.total_cents == 1000
    codes = {r.code for r in (out.evaluation.reasons if out.evaluation else [])}
    assert {"total_qr_mismatch", "totals_mismatch"} <= codes


@pytest.mark.req("F-42")
def test_the_printed_atcud_is_compared_only_where_the_text_is_exact() -> None:
    pdf = doc_from_lines(["Fatura", "ATCUD:JJDBMXMX-052961"])
    assert atcud_matches_print(pdf, qr()) is True
    assert atcud_matches_print(pdf, qr(atcud="JJDBMXMX-052962")) is False
    assert atcud_matches_print(doc_from_lines(["Fatura"]), qr()) is None
    assert (
        atcud_matches_print(doc_from_lines(["ATCUD:JJDBMXMX-052961"], route=ProcessingRoute.LOCAL_OCR), qr())
        is None
    )


# ---------------------------------------------------------------------------------- F-45: without a QR


def _comparable(out: PipelineOutcome) -> dict[str, Any]:
    return {
        "status": out.status,
        "route": out.route,
        "merchant_confidence": out.merchant_confidence,
        "parsed": out.parsed.model_dump() if out.parsed else None,
        "reasons": [r.model_dump() for r in out.evaluation.reasons] if out.evaluation else None,
        "notes": out.notes,
    }


@pytest.mark.req("F-45")
def test_a_receipt_without_a_qr_goes_through_exactly_as_before() -> None:
    """Every synthetic receipt (none has a QR): the outcome with the reader equals the one without it."""
    cases = [c for c in discover(SYNTHETIC) if not c.expected.get("requires_ocr")]
    assert len(cases) >= 11
    for case in cases:
        mime = "application/pdf" if case.document.suffix == ".pdf" else "image/png"
        request = PipelineRequest(data=Path(case.document).read_bytes(), declared_mime=mime)
        with_reader = run_pipeline(request, _deps())
        without = run_pipeline(request, _deps(qr_reader=lambda *_: None))
        assert with_reader.qr is None and with_reader.qr_notes == [], case.name
        assert _comparable(with_reader) == _comparable(without), case.name
