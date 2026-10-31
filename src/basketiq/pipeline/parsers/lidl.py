"""Lidl Portugal receipt parser.

Layout (calibrated on a Lidl Plus app receipt, 2026):

                                       EUR
    SERRAMEL ROSMANINHO ALGARVE     3,49 B     <- description, line total, VAT class
    PAO DE QUEIJO                   3,29 A
    Total                          16,75
    Taxa  Base Imp.  Val.Total  Val.IVA
     A 23%   7,94      9,77      1,83          <- base, total, VAT

Multi-unit, weighed and discount lines follow the usual Lidl layout (a "2 x 1,65" or
"0,456 kg x 2,99 EUR/kg" line under the item, "Desconto -0,50" after it). Those rules are not
calibrated on real samples yet and therefore score lower.
"""

from __future__ import annotations

import re

from basketiq.domain.money import multiply_cents, parse_decimal, parse_money_cents
from basketiq.domain.status import Merchant, ProcessingRoute, Unit
from basketiq.pipeline.parsers.base import (
    MONEY,
    RULE_EXACT,
    build_item,
    choose_purchase_datetime,
    find_amount,
    is_weight_quantity,
    parse_date_candidates,
    parse_tax_table,
    unresolved_line,
)
from basketiq.pipeline.schema import DocumentText, ParsedLine, ParsedReceipt

UNCALIBRATED = 0.85

_NAME_AND_PRICE = rf"^(?P<desc>.*?[A-Za-zÀ-ÿ].*?)\s+(?P<total>-?{MONEY})"
_ITEM = re.compile(rf"{_NAME_AND_PRICE}\s+(?P<tax>[A-F])$")
# On a photo the VAT class after the price may be lost (glare), misread ("3,49 8" for B) or followed by a
# stray character ("3,49 B E"); only a letter A-F is kept. A searchable PDF prints it exactly, so there a
# missing class means the line is not a Lidl item.
_PHOTO_ITEM = re.compile(rf"{_NAME_AND_PRICE}(?:\s+(?P<tax>\S))?(?:\s+\S)?$")
_QTY = re.compile(
    rf"^(?P<qty>\d+(?:[,.]\d{{1,3}})?)\s*(?P<kg>kg)?\s*[xX]\s*(?P<unit>{MONEY})(?:\s*EUR/kg)?"
    rf"(?:\s+(?P<total>{MONEY})\s+(?P<tax>[A-E]))?$"
)
_DISCOUNT = re.compile(
    rf"^(?:Desconto|Poupan|Promo|Lidl\s*Plus)[^\d]*?-\s*(?P<amount>{MONEY})(?:\s+[A-E])?$",
    re.I,
)
_TOTAL = re.compile(rf"^Total\s+(?P<amount>{MONEY})$", re.I)
_STORE = re.compile(r"^LIDL\b.*?\s-\s(?P<store>.+)$", re.I)
_REF = re.compile(r"\bNo\s*:\s*(?P<ref>F[STR]\s*[\w/]+)", re.I)


class LidlParser:
    merchant = Merchant.LIDL
    version = "lidl-1.0"

    def parse(self, doc: DocumentText) -> ParsedReceipt:
        lines = doc.lines
        start = next((i for i, line in enumerate(lines) if line.text.strip().upper() == "EUR"), None)
        if start is None:
            start = next((i for i, line in enumerate(lines) if line.text.upper().startswith("NIF")), -1)
        end = next(
            (i for i in range(start + 1, len(lines)) if _TOTAL.match(lines[i].text.strip())), len(lines)
        )
        item_zone = lines[start + 1 : end]
        summary = lines[end:]

        item_shape = _PHOTO_ITEM if doc.route == ProcessingRoute.LOCAL_OCR else _ITEM
        parsed: list[ParsedLine] = []
        pending_description: str | None = None
        for line in item_zone:
            text = line.text.strip()
            if set(text) <= set("-=*_ "):
                continue
            if m := _QTY.match(text):
                if m.group("total"):  # "2 x 1,65  3,30 A" under a description-only line
                    item = build_item(
                        description=pending_description or "",
                        total_text=m.group("total"),
                        qty_text=m.group("qty"),
                        unit_price_text=m.group("unit"),
                        unit_printed=bool(m.group("kg")),
                        tax_code=_vat_class(m.group("tax")),
                        line_conf=line.confidence * UNCALIBRATED,
                        evidence=[pending_description or "", text],
                    )
                    if not pending_description:
                        item.unresolved = True
                    parsed.append(item)
                elif parsed and not parsed[-1].unresolved:  # quantity line under a priced item
                    _apply_quantity(
                        parsed[-1],
                        m.group("qty"),
                        m.group("unit"),
                        bool(m.group("kg")),
                        line.confidence,
                        text,
                    )
                else:
                    parsed.append(unresolved_line(line))
                pending_description = None
                continue
            if m := _DISCOUNT.match(text):
                if parsed and not parsed[-1].unresolved:
                    parsed[-1].discount_cents += abs(parse_money_cents(m.group("amount")) or 0)
                    parsed[-1].field_confidence["discount"] = round(
                        RULE_EXACT * UNCALIBRATED * line.confidence, 3
                    )
                    parsed[-1].evidence.append(text)
                else:
                    parsed.append(unresolved_line(line))
                pending_description = None
                continue
            if m := item_shape.match(text):
                parsed.append(
                    build_item(
                        description=m.group("desc"),
                        total_text=m.group("total"),
                        tax_code=_vat_class(m.group("tax")),
                        line_conf=line.confidence,
                        evidence=[text],
                    )
                )
                pending_description = None
                continue
            if re.search(MONEY, text):
                parsed.append(unresolved_line(line))
                pending_description = None
            else:
                pending_description = text

        total, total_conf, _ = find_amount(summary, _TOTAL)
        subtotal, tax = parse_tax_table(summary, base_col=0, tax_col=2)
        purchase_at, date_conf = choose_purchase_datetime(parse_date_candidates(lines))
        ref = next((m.group("ref") for line in lines if (m := _REF.search(line.text))), None)

        return ParsedReceipt(
            merchant=self.merchant,
            purchase_at=purchase_at,
            receipt_ref=ref,
            store_label=next(
                (m.group("store") for line in lines[:end] if (m := _STORE.match(line.text.strip()))), None
            ),
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


def _apply_quantity(
    item: ParsedLine, qty_text: str, unit_text: str, is_kg: bool, conf: float, evidence: str
) -> None:
    qty = parse_decimal(qty_text)
    unit_price = parse_money_cents(unit_text)
    if qty is None or unit_price is None:
        return
    item.quantity = qty
    item.unit = Unit.KG if is_weight_quantity(qty_text, is_kg) else Unit.UNIT
    item.unit_price_cents = unit_price
    consistent = (
        item.line_total_cents is not None
        and abs(multiply_cents(qty, unit_price) - item.line_total_cents) <= 1
    )
    rule = (RULE_EXACT if consistent else 0.5) * UNCALIBRATED
    item.field_confidence["quantity"] = round(rule * conf, 3)
    item.field_confidence["unit_price"] = round(rule * conf, 3)
    item.evidence.append(evidence)


def _vat_class(text: str | None) -> str | None:
    return text if text and text in "ABCDEF" else None
