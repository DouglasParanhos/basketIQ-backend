"""Pingo Doce receipt parser.

Layout (calibrated on real 2026 receipts, PDF from the app/e-mail):

    Artigos
    MERCEARIA + PET FOOD                       <- section header (no amount)
     C PÃO 12 CER OROW550G           2,99      <- VAT code, description, line total
          Poupança Imediata         (0,60)     <- discount on the previous item
     C FRANGO LOMBINHOS ATP 0,513 X 6,19 3,18  <- weighed item: kg × €/kg
     E CROQ CARN PD 10X32G    2 X 3,19   6,38  <- several units ("10X32G" is part of the name)
    Resumo
    TOTAL 8,76 / TOTAL POUPANÇA (0,60) / TOTAL A PAGAR 8,16
    Resumo IVA: "C 6% 7,70 0,46 8,16" -> base, VAT, total
"""

from __future__ import annotations

import re
from collections.abc import Callable

from basketiq.domain.money import parse_money_cents
from basketiq.domain.status import Merchant
from basketiq.pipeline.parsers.base import (
    MONEY,
    RULE_EXACT,
    build_item,
    choose_purchase_datetime,
    find_amount,
    parse_date_candidates,
    parse_tax_table,
    unresolved_line,
)
from basketiq.pipeline.schema import DocumentText, ParsedLine, ParsedReceipt, TextLine

_ITEM = re.compile(
    rf"^(?P<tax>[A-Z])\s+(?P<desc>.+?)\s+"
    rf"(?:(?P<qty>\d+(?:[,.]\d{{1,3}})?)\s*[Xx]\s*(?P<unit>{MONEY})\s+)?"
    rf"(?P<total>{MONEY})$"
)
# Tolerant to OCR damage seen in practice: "PoupanaImediata(1,40)", "TOTAL POUPANXA (1,40)".
_DISCOUNT = re.compile(rf"^(?:Poupan|Desconto|Promo|Cup)[^\d()]*\(?\s*-?(?P<amount>{MONEY})\s*\)?$", re.I)
_TOTAL_GROSS = re.compile(rf"^TOTAL\s+(?P<amount>{MONEY})$")
_TOTAL_SAVINGS = re.compile(rf"^TOTAL\s+POUPAN\S*\s*\(?(?P<amount>{MONEY})\)?$", re.I)
_TOTAL_PAY = re.compile(rf"^TOTAL\s+A\s+PAGAR\s+(?P<amount>{MONEY})$")
_REF = re.compile(r"Fatura(?:\s+Simplificada)?\s+(?P<ref>F[STR]\s*[\w/]+)", re.I)


class PingoDoceParser:
    merchant = Merchant.PINGO_DOCE
    version = "pingo_doce-1.0"

    def parse(self, doc: DocumentText) -> ParsedReceipt:
        lines = doc.lines
        start = _index_of(lines, lambda t: t.strip().lower() == "artigos")
        end = _index_of(lines, lambda t: t.strip().lower() == "resumo", start + 1 if start is not None else 0)
        item_zone = lines[(start + 1 if start is not None else 0) : end if end is not None else len(lines)]
        summary = lines[end:] if end is not None else []

        parsed_lines: list[ParsedLine] = []
        for line in item_zone:
            text = line.text.strip()
            if m := _DISCOUNT.match(text):
                if parsed_lines and not parsed_lines[-1].unresolved:
                    target = parsed_lines[-1]
                    target.discount_cents += abs(parse_money_cents(m.group("amount")) or 0)
                    target.field_confidence["discount"] = round(RULE_EXACT * line.confidence, 3)
                    target.evidence.append(text)
                else:
                    parsed_lines.append(unresolved_line(line))
                continue
            if m := _ITEM.match(text):
                parsed_lines.append(
                    build_item(
                        description=m.group("desc"),
                        total_text=m.group("total"),
                        qty_text=m.group("qty"),
                        unit_price_text=m.group("unit"),
                        tax_code=m.group("tax"),
                        line_conf=line.confidence,
                        evidence=[text],
                    )
                )
                continue
            if re.search(rf"{MONEY}\)?$", text):
                parsed_lines.append(unresolved_line(line))
            # Anything else without an amount is a section header (e.g. "TALHO").

        gross, _, _ = find_amount(summary, _TOTAL_GROSS)
        savings, _, _ = find_amount(summary, _TOTAL_SAVINGS)
        total, total_conf, _ = find_amount(summary, _TOTAL_PAY)
        if total is None:  # receipts without savings may print only "TOTAL"
            total, total_conf = gross, (0.9 if gross is not None else 0.0)
        subtotal, tax = parse_tax_table(summary, base_col=0, tax_col=1)

        purchase_at, date_conf = choose_purchase_datetime(parse_date_candidates(lines))
        ref = next((m.group("ref") for line in lines if (m := _REF.search(line.text))), None)

        notes = []
        if gross is not None and savings is not None and total is not None and gross - savings != total:
            notes.append("gross_minus_savings_differs_from_total")

        return ParsedReceipt(
            merchant=self.merchant,
            purchase_at=purchase_at,
            receipt_ref=ref,
            subtotal_cents=subtotal,
            tax_cents=tax,
            total_cents=total,
            total_discount_cents=savings,
            lines=parsed_lines,
            field_confidence={
                "purchase_at": date_conf,
                "total": round(RULE_EXACT * total_conf, 3) if total is not None else 0.0,
                "subtotal": 0.95 if subtotal is not None else 0.0,
                "tax": 0.95 if tax is not None else 0.0,
            },
            parser_version=self.version,
            notes=notes,
        )


def _index_of(lines: list[TextLine], predicate: Callable[[str], bool], start: int = 0) -> int | None:
    for i in range(start, len(lines)):
        if predicate(lines[i].text):
            return i
    return None
