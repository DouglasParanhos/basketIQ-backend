"""The fiscal QR code of Portuguese receipts (Portaria n.º 195/2020; AT, "Especificações Técnicas: Código de
Barras Bidimensional, Código QR", v1.0 of 2020-08-13: fields `A:...*B:...*F:...*O:...` joined by `*`).

Local, silent and tolerant:

- Local: zxing-cpp reads it on this machine; nothing is sent anywhere.
- Silent: a receipt without a readable QR goes on exactly as before. Nothing here raises, blocks or waits more
  than a couple of seconds.
- Tolerant: unknown or new fields are ignored and never break the reading; only the fields in use are kept.

Only what the checks use is kept. The buyer's tax number (field B) is never read into anything, and the raw
text of the QR leaves this module only as an `AtQr`.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as clock
from decimal import Decimal
from typing import Any, TypedDict

import numpy as np
import pypdfium2 as pdfium
from PIL import Image

from basketiq.domain.money import format_cents
from basketiq.domain.status import ProcessingRoute, SourceType
from basketiq.pipeline.parsers.base import LISBON
from basketiq.pipeline.parsers.totals import balance_used
from basketiq.pipeline.preprocess import load_image
from basketiq.pipeline.schema import DocumentText, ParsedReceipt, TextLine

QR_BUDGET_S = 2.0  # for the whole receipt, every photo included
PDF_DPI = 150
SCALES = (1.0, 2.0, 3.0, 0.5)  # a small QR in a big photo needs enlarging, a huge photo needs shrinking
MAX_LONG_SIDE = 6000  # no enlarging beyond this: the decode would eat the budget
MAX_PDF_PAGES = 5
QR_CONFIDENCE = 0.95  # a machine-readable value, but the receipt is still reviewed when it overrules the text
QR_DATE_ONLY_CONFIDENCE = min(QR_CONFIDENCE, 0.85)  # the QR knows the day, not the time

_NIF = re.compile(r"\d{9}")
_DAY = re.compile(r"\d{8}")
_MONEY = re.compile(r"\d+\.\d{2}")
_ATCUD = re.compile(r"[A-Za-z0-9]{8,}-\d+")
_DOC_TYPE = re.compile(r"[A-Z]{1,4}")
_PRINTED_ATCUD = re.compile(r"ATCUD:\s*(\S+)")


@dataclass(frozen=True)
class AtQr:
    """What is kept of the message. There is no field for the buyer's tax number."""

    issuer_nif: str  # A
    doc_date: date  # F (the QR carries the date only)
    total_cents: int  # O, the document total with taxes
    doc_type: str | None = None  # D
    doc_number: str | None = None  # G
    atcud: str | None = None  # H
    tax_cents: int | None = None  # N


class QrNote(TypedDict):
    """A reason to review, kept with the receipt like the notes of a photo join."""

    code: str
    field: str
    message: str
    details: dict[str, int | str]


QrReader = Callable[[bytes, SourceType, float], AtQr | None]


def _cents(text: str | None) -> int | None:
    return int(Decimal(text) * 100) if text and _MONEY.fullmatch(text) else None


def parse_at_message(text: str) -> AtQr | None:
    """The fields in use, or None when the text is not a fiscal QR (or lacks issuer, date or total)."""
    fields: dict[str, str] = {}
    for part in text.split("*"):
        key, sep, value = part.partition(":")
        if sep and key != "B" and key not in fields:  # B is the buyer's tax number: never read
            fields[key] = value
    issuer, day, total = fields.get("A", ""), fields.get("F", ""), _cents(fields.get("O"))
    if not (_NIF.fullmatch(issuer) and _DAY.fullmatch(day) and total is not None):
        return None
    try:
        doc_date = date(int(day[:4]), int(day[4:6]), int(day[6:]))
    except ValueError:
        return None
    atcud, doc_type = fields.get("H", ""), fields.get("D", "")
    return AtQr(
        issuer_nif=issuer,
        doc_date=doc_date,
        total_cents=total,
        doc_type=doc_type if _DOC_TYPE.fullmatch(doc_type) else None,
        doc_number=fields.get("G", "")[:64] or None,
        atcud=atcud if _ATCUD.fullmatch(atcud) else None,
        tax_cents=_cents(fields.get("N")),
    )


# ------------------------------------------------------------------------------------------- reading


def _gray(image: Image.Image) -> np.ndarray[Any, Any]:
    return np.asarray(image.convert("L"), dtype=np.uint8)


def _tiers(data: bytes, source_type: SourceType, deadline: float) -> Iterator[np.ndarray[Any, Any]]:
    """The images to try, in order, while there is time left."""
    if source_type == SourceType.PDF:
        pdf = pdfium.PdfDocument(data)
        try:
            for index in range(min(len(pdf), MAX_PDF_PAGES)):
                if time.perf_counter() >= deadline:
                    return
                yield _gray(pdf[index].render(scale=PDF_DPI / 72).to_pil())
        finally:
            pdf.close()
        return
    image = load_image(data)
    for factor in SCALES:
        if time.perf_counter() >= deadline:
            return
        width, height = image.size
        if factor == 1.0:
            yield _gray(image)
        elif factor < 1.0 or max(width, height) * factor <= MAX_LONG_SIDE:
            yield _gray(
                image.resize(
                    (max(1, round(width * factor)), max(1, round(height * factor))), Image.Resampling.LANCZOS
                )
            )


def _decode(array: np.ndarray[Any, Any]) -> list[str]:
    import zxingcpp

    qr_only = zxingcpp.BarcodeFormat.QRCode
    return [r.text for r in zxingcpp.read_barcodes(array, formats=qr_only)]  # type: ignore[arg-type]


def read_at_qr(data: bytes, source_type: SourceType, budget_s: float = QR_BUDGET_S) -> AtQr | None:
    """The fiscal QR of a document, or None. Never raises."""
    deadline = time.perf_counter() + budget_s
    try:
        for array in _tiers(data, source_type, deadline):
            for text in _decode(array):
                if qr := parse_at_message(text):
                    return qr
    except Exception:  # unreadable file, missing library, decoder crash: the receipt goes on without it
        return None
    return None


# ----------------------------------------------------------------------------------- the checks


def apply_qr(parsed: ParsedReceipt, qr: AtQr, lines: list[TextLine]) -> list[QrNote]:
    """Checks the read total and date against the QR, overruling them only where the QR cannot be wrong.

    Total (FR-007: the value of the purchase, balance and vouchers are ways of paying, not discounts). The QR
    total is the document total, so it equals the purchase total minus the card balance, voucher or deposit
    credit printed on the receipt.
    - QR above the total read: a discount never raises a total, so the read one is a misreading. The QR (plus
      what was printed as balance) prevails.
    - QR below it: with the balance identified, the relation must hold; otherwise the read total stays,
      because the balance may simply not have been read, and the receipt is reviewed with both values.
    The date always follows the QR (it carries the date, not the time)."""
    balance = balance_used(lines)
    read, from_qr = parsed.total_cents, format_cents(qr.total_cents)
    details: dict[str, int | str] = {"read_total_cents": read or 0, "qr_total_cents": qr.total_cents}
    if balance:
        details["balance_cents"] = balance
    notes: list[QrNote] = []
    if read is None:
        parsed.total_cents = qr.total_cents + balance
        _from_qr(parsed, "total")
        text = (
            f"Total lido do código QR ({from_qr}): pode ser inferior ao valor das compras se houve saldo de "
        )
        notes.append(_note("total_from_qr", "total", text + "cartão ou vale. Confirme.", details))
    elif qr.total_cents > read:
        parsed.total_cents = qr.total_cents + balance
        _from_qr(parsed, "total")
        text = (
            f"O total lido ({format_cents(read)}) é inferior ao do código QR ({from_qr}): "
            "usámos o do QR. Confirme."
        )
        notes.append(_note("total_qr_mismatch", "total", text, details))
    elif qr.total_cents not in (read, read - balance):  # equal to the read total: nothing was taken off
        why = (
            f"mesmo descontando o saldo identificado ({format_cents(balance)})"
            if balance
            else ("pode ter havido saldo de cartão ou vale")
        )
        text = (
            f"O total lido ({format_cents(read)}) difere do código QR ({from_qr}): {why}. Confirme o total."
        )
        notes.append(_note("total_qr_mismatch", "total", text, details))
    if note := _check_date(parsed, qr):
        notes.append(note)
    return notes


def _note(code: str, field: str, message: str, details: dict[str, int | str]) -> QrNote:
    return {"code": code, "field": field, "message": message, "details": details}


def _from_qr(parsed: ParsedReceipt, field: str) -> None:
    parsed.field_sources[field] = "qr"
    parsed.field_confidence[field] = QR_CONFIDENCE


def _check_date(parsed: ParsedReceipt, qr: AtQr) -> QrNote | None:
    read = parsed.purchase_at
    if read is None:  # the QR knows the day, not the time
        parsed.purchase_at = datetime.combine(qr.doc_date, clock(0, 0), tzinfo=LISBON)
        parsed.field_sources["purchase_at"] = "qr"
        parsed.field_confidence["purchase_at"] = QR_DATE_ONLY_CONFIDENCE
        return None
    if read.date() == qr.doc_date:
        return None
    parsed.purchase_at = datetime.combine(qr.doc_date, read.timetz())
    _from_qr(parsed, "purchase_at")
    return _note(
        "date_qr_mismatch",
        "purchase_at",
        f"A data lida ({read.date().strftime('%d/%m/%Y')}) difere da do código QR "
        f"({qr.doc_date.strftime('%d/%m/%Y')}): usámos a do QR. Confirme.",
        {"read_date": read.date().isoformat(), "qr_date": qr.doc_date.isoformat()},
    )


def atcud_matches_print(doc: DocumentText, qr: AtQr) -> bool | None:
    """Does the ATCUD in the QR equal the printed one? Only a PDF with a text layer is exact enough to say."""
    if doc.route != ProcessingRoute.PDF_TEXT or qr.atcud is None:
        return None
    printed = _PRINTED_ATCUD.search(doc.text)
    return None if printed is None else printed.group(1) == qr.atcud
