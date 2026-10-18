"""Auchan (My Auchan / Auchan Retail Portugal) receipt parser.

Layout (calibrated on one real photographed "Fatura", 2026):

    IVA ARTIGO/QT/LI   PRECO   VALOR        <- header of the items area
    Perfumaria                              <- section name, ignored
     I2 COLGATE MAX WHITE ON                <- VAT class, description (no price when quantity follows)
        2 X 2,68              5,36          <- quantity, unit price, line total
     I2 ELIXIR AUCHAN 500 ML  1,40          <- single unit: description and line total
    3 volumes                 6,76
    TOTAL PAGO                6,76
    RESUMO DO IVA
     23,00% (I2)   5,50   1,26              <- rate, base, VAT

Only these shapes are calibrated. Any other priced line (discounts, weighed items, ...) is kept as an
unresolved line for review instead of being guessed. A quantity digit lost by the OCR is rebuilt
from total / unit price when that is a whole number, at a lower confidence.
"""

from __future__ import annotations

import re
from decimal import Decimal

from basketiq.domain.money import parse_money_cents
from basketiq.domain.status import Merchant
from basketiq.pipeline.parsers.base import (
    MONEY,
    RULE_EXACT,
    RULE_IMPLICIT_QTY,
    build_item,
    choose_purchase_datetime,
    find_amount,
    index_of,
    parse_date_candidates,
    unresolved_line,
)
from basketiq.pipeline.schema import DocumentText, ParsedLine, ParsedReceipt, TextLine

_HEADER = re.compile(r"^IVA\s+ARTIGO", re.I)
# VAT class ("I2"; the OCR often reads it as "12" or "l2") followed by the description.
_ITEM = re.compile(rf"^(?P<tax>[A-Za-z1]\d)\s+(?P<desc>.*?[A-Za-zÀ-ÿ]{{2}}.*?)(?:\s+(?P<total>{MONEY}))?$")
_QTY = re.compile(rf"^(?:(?P<qty>\d+(?:[,.]\d{{1,3}})?)\s*)?[xX]\s*(?P<unit>{MONEY})\s+(?P<total>{MONEY})$")
_VOLUMES_PREFIX = r"\d+\s+vol\w*"  # "3 volumes": OCR variants such as "volunes" are tolerated
_END_OF_ITEMS = re.compile(rf"^(?:{_VOLUMES_PREFIX}|TOTAL\s+PAGO)\b", re.I)
_VOLUMES = re.compile(rf"^{_VOLUMES_PREFIX}\s+(?P<amount>{MONEY})$", re.I)
_HAS_AMOUNT = re.compile(MONEY)
_TOTAL_PAID = re.compile(rf"^TOTAL\s+PAGO\b.*?(?P<amount>{MONEY})", re.I)
# Not anchored: the OCR sometimes glues the "IVA Liquidado" heading in front of the row.
_VAT_ROW = re.compile(rf"\d{{1,2}}[,.]\d{{2}}\s*%\s*\(\w+\)\s+(?P<base>{MONEY})\s+(?P<tax>{MONEY})$")
_REF = re.compile(r"\bN[o0]\.?\s*(?P<ref>\d{10,}/\d+/\d+)")


class AuchanParser:
    merchant = Merchant.AUCHAN
    version = "auchan-1.0"

    def parse(self, doc: DocumentText) -> ParsedReceipt:
        lines = doc.lines
        stop = index_of(lines, lambda t: bool(_END_OF_ITEMS.match(t)))
        end = len(lines) if stop is None else stop
        header = index_of(lines[:end], lambda t: bool(_HEADER.match(t)))
        if header is not None:
            start = header + 1
        else:  # header not read: start at the first line shaped like an item
            first = index_of(lines[:end], lambda t: bool(_ITEM.match(t) or _QTY.match(t)))
            start = first or 0
        parsed = _parse_items(lines[start:end])
        summary = lines[end:]

        total, total_conf = _total(summary)
        subtotal, tax = _vat_totals(summary)
        purchase_at, date_conf = choose_purchase_datetime(parse_date_candidates(lines))
        ref = next((m.group("ref") for line in lines if (m := _REF.search(line.text))), None)

        return ParsedReceipt(
            merchant=self.merchant,
            purchase_at=purchase_at,
            receipt_ref=ref,
            subtotal_cents=subtotal,
            tax_cents=tax,
            total_cents=total,
            lines=parsed,
            field_confidence={
                "purchase_at": date_conf,
                "total": round(RULE_EXACT * total_conf, 3) if total is not None else 0.0,
                "subtotal": 0.95 if subtotal is not None else 0.0,
                "tax": 0.95 if tax is not None else 0.0,
            },
            parser_version=self.version,
        )


def _parse_items(item_zone: list[TextLine]) -> list[ParsedLine]:
    parsed: list[ParsedLine] = []
    pending: tuple[TextLine, re.Match[str]] | None = None  # description waiting for its quantity line

    def flush_pending() -> None:
        nonlocal pending
        if pending is not None:
            parsed.append(unresolved_line(pending[0]))
            pending = None

    for line in item_zone:
        text = line.text.strip()
        if m := _QTY.match(text):
            if pending is None:
                parsed.append(unresolved_line(line))
                continue
            parsed.append(_quantity_item(pending, m, line))
            pending = None
        elif m := _ITEM.match(text):
            flush_pending()
            if m.group("total"):
                parsed.append(
                    build_item(
                        description=m.group("desc"),
                        total_text=m.group("total"),
                        tax_code=m.group("tax"),
                        line_conf=line.confidence,
                        evidence=[text],
                    )
                )
            else:
                pending = (line, m)
        elif _HAS_AMOUNT.search(text):
            flush_pending()
            parsed.append(unresolved_line(line))
        # else: a section name ("Perfumaria") or noise without a price
    flush_pending()
    return parsed


def _quantity_item(
    pending: tuple[TextLine, re.Match[str]], qty_match: re.Match[str], qty_line: TextLine
) -> ParsedLine:
    desc_line, desc = pending
    conf = min(desc_line.confidence, qty_line.confidence)
    qty_text = qty_match.group("qty")
    rebuilt = qty_text is None
    if rebuilt:
        unit = parse_money_cents(qty_match.group("unit"))
        total = parse_money_cents(qty_match.group("total"))
        ratio = Decimal(total or 0) / Decimal(unit or 1)
        if not unit or ratio != ratio.to_integral_value() or ratio < 1:
            return unresolved_line(qty_line)
        qty_text = str(int(ratio))
    item = build_item(
        description=desc.group("desc"),
        total_text=qty_match.group("total"),
        qty_text=qty_text,
        unit_price_text=qty_match.group("unit"),
        tax_code=desc.group("tax"),
        line_conf=conf,
        evidence=[desc_line.text.strip(), qty_line.text.strip()],
    )
    if rebuilt:
        item.field_confidence["quantity"] = round(
            min(item.field_confidence["quantity"], RULE_IMPLICIT_QTY * conf), 3
        )
    return item


def _total(summary: list[TextLine]) -> tuple[int | None, float]:
    """`TOTAL PAGO`, or the amount on the "N volumes" line when that one was not read."""
    for pattern in (_TOTAL_PAID, _VOLUMES):
        cents, confidence, _ = find_amount(summary, pattern)
        if cents is not None:
            return cents, confidence
    return None, 0.0


def _vat_totals(summary: list[TextLine]) -> tuple[int | None, int | None]:
    """Sum of base and VAT over the rows of the VAT table; None when no row was read."""
    base = tax = rows = 0
    for line in summary:
        m = _VAT_ROW.search(line.text.strip())
        row = (parse_money_cents(m.group("base")), parse_money_cents(m.group("tax"))) if m else None
        if row and None not in row:
            base, tax, rows = base + (row[0] or 0), tax + (row[1] or 0), rows + 1
    return (base, tax) if rows else (None, None)
