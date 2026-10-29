"""Continente receipt parser.

Layout (calibrated on real 2026 "Fatura Simplificada" PDFs):

    Nro:FS CNP203/131123 09/10/2026 15:16
    IVA DESCRICAO VALOR
    Higiene:                                     <- section header (ends with ':')
    (C) DEO SP NIVEA MEN BLACK&WHI 150           <- VAT code + description, price on next line
    2 X 2,99 5,98                                <- qty × unit price, line total
    POUPANCA 4,00                                <- saving on the previous item
    (A) QJ CABRA FATIAS CONTINENTE 150G 2,19     <- single-unit item: description + total
    SUBTOTAL 10,56
    Desconto Cartao Utilizado 7,39               <- payment with card balance (ignored)
    TOTAL A PAGAR 3,17                           <- what was left to pay (ignored)
    Total de descontos e poupancas 4,60
    %IVA Total Liq. IVA Total
    (A) 6,00% 1,30 0,08 1,38                     <- VAT table, computed after the card discount

The printed line price is already net of "POUPANCA" (SUBTOTAL equals the sum of the printed
prices). To keep the common model (gross line total minus discount), the saving is added back to the
line total and unit price and recorded as the line discount.

The receipt total is SUBTOTAL: "TOTAL A PAGAR" depends on how much card balance the customer spent,
and the VAT table is skewed by it too, so both are ignored (the VAT table only when it ties out).
"""

from __future__ import annotations

import re
from decimal import Decimal

from basketiq.domain.money import multiply_cents, parse_money_cents
from basketiq.domain.status import Merchant
from basketiq.pipeline.parsers.base import (
    MONEY,
    RULE_EXACT,
    RULE_INCONSISTENT,
    build_item,
    choose_purchase_datetime,
    find_amount,
    index_of,
    line_before,
    parse_date_candidates,
    unresolved_line,
)
from basketiq.pipeline.schema import DocumentText, ParsedLine, ParsedReceipt, TextLine

_TAX = r"^\((?P<tax>[A-Z])\)\s+"
_NAME = re.compile(rf"{_TAX}(?P<desc>.+)$")
_ITEM = re.compile(rf"{_TAX}(?P<desc>.+?)\s+(?P<total>{MONEY})$")
_QTY = re.compile(
    rf"^(?P<qty>\d+(?:[,.]\d{{1,3}})?)\s*(?P<kg>kg)?\s*[Xx]\s*(?P<unit>{MONEY})(?:\s*(?:EUR|€)/kg)?"
    rf"\s+(?P<total>{MONEY})$",
    re.I,
)
_SAVINGS = re.compile(rf"^POUPAN[CÇ]A\s+(?P<amount>{MONEY})$", re.I)
_SECTION = re.compile(r"^[^\d()]+:$")
_ITEMS_HEADER = re.compile(r"^IVA\s+DESCRI", re.I)
_SUBTOTAL = re.compile(rf"^SUBTOTAL\s+(?P<amount>{MONEY})$", re.I)
_TOTAL_PAY = re.compile(rf"^TOTAL\s+A\s+PAGAR\s+(?P<amount>{MONEY})$", re.I)
_TOTAL_SAVINGS = re.compile(rf"^Total\s+de\s+descontos\s+e\s+poupan[cç]as\s+(?P<amount>{MONEY})$", re.I)
_VAT_ROW = re.compile(
    rf"^\([A-Z]\)\s+\d{{1,2}}[,.]\d{{2}}%\s+(?P<base>{MONEY})\s+(?P<tax>{MONEY})\s+(?P<total>{MONEY})$"
)
_REF = re.compile(r"\bNro:\s*(?P<ref>F[STR]\s*[\w/]+)", re.I)


class ContinenteParser:
    merchant = Merchant.CONTINENTE
    version = "continente-1.0"

    def parse(self, doc: DocumentText) -> ParsedReceipt:
        lines = doc.lines
        start = index_of(lines, lambda t: bool(_ITEMS_HEADER.match(t)))
        first = start + 1 if start is not None else 0
        end = index_of(lines, lambda t: bool(_SUBTOTAL.match(t) or _TOTAL_PAY.match(t)), first)
        item_zone = lines[first:end]
        summary = lines[end:] if end is not None else []

        parsed: list[ParsedLine] = []
        # "(C) DESCRIPTION": the price is on the next line
        pending: tuple[TextLine, re.Match[str]] | None = None

        def flush_pending() -> None:
            nonlocal pending
            if pending is not None:
                parsed.append(unresolved_line(pending[0]))
                pending = None

        for line in item_zone:
            text = line.text.strip()
            if m := _SAVINGS.match(text):
                flush_pending()
                if parsed and not parsed[-1].unresolved:
                    _apply_savings(
                        parsed[-1], parse_money_cents(m.group("amount")) or 0, line.confidence, text
                    )
                else:
                    parsed.append(unresolved_line(line))
            elif m := _QTY.match(text):
                if pending is None:
                    parsed.append(unresolved_line(line))
                    continue
                name_line, pending_name = pending
                parsed.append(
                    build_item(
                        description=pending_name.group("desc"),
                        total_text=m.group("total"),
                        qty_text=m.group("qty"),
                        unit_price_text=m.group("unit"),
                        unit_printed=bool(m.group("kg")),
                        tax_code=pending_name.group("tax"),
                        line_conf=min(line.confidence, name_line.confidence),
                        evidence=[name_line.text.strip(), text],
                    )
                )
                pending = None
            elif m := _ITEM.match(text):
                flush_pending()
                parsed.append(
                    build_item(
                        description=m.group("desc"),
                        total_text=m.group("total"),
                        tax_code=m.group("tax"),
                        line_conf=line.confidence,
                        evidence=[text],
                    )
                )
            elif name := _NAME.match(text):
                flush_pending()
                pending = (line, name)
            elif _SECTION.match(text):
                flush_pending()
            elif re.search(MONEY, text):
                flush_pending()
                parsed.append(unresolved_line(line))
        flush_pending()

        subtotal_total, subtotal_conf, _ = find_amount(summary, _SUBTOTAL)
        pay_total, pay_conf, _ = find_amount(summary, _TOTAL_PAY)
        total, total_conf = (
            (subtotal_total, subtotal_conf) if subtotal_total is not None else (pay_total, pay_conf)
        )
        savings, _, _ = find_amount(summary, _TOTAL_SAVINGS)
        subtotal, tax = _vat_table(summary, total)

        purchase_at, date_conf = choose_purchase_datetime(parse_date_candidates(lines))
        ref = next((m.group("ref") for line in lines if (m := _REF.search(line.text))), None)

        notes = []
        if subtotal_total is not None and pay_total is not None and pay_total != subtotal_total:
            notes.append("card_discount_ignored")

        return ParsedReceipt(
            merchant=self.merchant,
            purchase_at=purchase_at,
            receipt_ref=ref,
            store_label=line_before(lines, lambda t: t.lower().startswith("modelo continente")),
            subtotal_cents=subtotal,
            tax_cents=tax,
            total_cents=total,
            total_discount_cents=savings,
            lines=parsed,
            field_confidence={
                "purchase_at": date_conf,
                "total": round(RULE_EXACT * total_conf, 3) if total is not None else 0.0,
                "subtotal": 0.95 if subtotal is not None else 0.0,
                "tax": 0.95 if tax is not None else 0.0,
            },
            parser_version=self.version,
            notes=notes,
        )


def _apply_savings(item: ParsedLine, cents: int, conf: float, evidence: str) -> None:
    """Add the saving back to the printed (net) price and record it as the line discount."""
    if item.line_total_cents is None or not item.quantity:
        item.unresolved = True
        return
    item.line_total_cents += cents
    item.discount_cents += cents
    item.unit_price_cents = int((Decimal(item.line_total_cents) / item.quantity).to_integral_value())
    if abs(multiply_cents(item.quantity, item.unit_price_cents) - item.line_total_cents) > 1:
        item.field_confidence["unit_price"] = round(
            min(item.field_confidence.get("unit_price", 0.0), RULE_INCONSISTENT * conf), 3
        )
    item.field_confidence["discount"] = round(RULE_EXACT * conf, 3)
    item.evidence.append(evidence)


def _vat_table(summary: list[TextLine], total: int | None) -> tuple[int | None, int | None]:
    """Sum the VAT table, but only when it ties out with the receipt total: after a card discount
    the printed table covers just the amount left to pay."""
    rows = [m for line in summary if (m := _VAT_ROW.match(line.text.strip()))]

    def column(name: str) -> int:
        return sum(parse_money_cents(m.group(name)) or 0 for m in rows)

    if not rows or column("total") != total:
        return None, None
    return column("base"), column("tax")
