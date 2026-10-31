"""El Corte Inglés (Portugal) receipt parser.

Two layouts, both calibrated on one receipt each (2026):

    Paper, photographed:                      Electronic invoice (PDF by e-mail):
    Descrição        Quantidade   Valor       Produtos Preçounitário Tipoimposto Quantidade TOTAL
    PACCHERI RIGA        1 A      2,16        MOLHO PICANTE 5,99 € C 1 5,99 €
    SACOS T-SHIRT 55MICR 1 C      0,10        DEPOSITO VOLTA 0,10 € D 1 0,10 €
    TOTAL                         9,10        TOTAL 11,37 €

The photo prints the VAT class (a letter) before the amount. The OCR often drops the quantity or the class
letter, or reads the letter twice ("1 AA 2,59"), so both are optional and the quantity is only used when it
is read and greater than one. The store is the line "EL CORTE INGLES - LOJA LISBOA" that follows the company
data.
"""

from __future__ import annotations

import re
from decimal import Decimal

from basketiq.domain.money import parse_money_cents
from basketiq.domain.status import Merchant
from basketiq.pipeline.parsers.base import (
    MONEY,
    build_item,
    count_from_total,
    unresolved_line,
)
from basketiq.pipeline.parsers.generic_pt import CALIBRATED_CAP, GenericPtParser
from basketiq.pipeline.parsers.totals import settle_by_class
from basketiq.pipeline.schema import DocumentText, ParsedLine, ParsedReceipt, TextLine

_EURO = r"\s*(?:€|EUR)"
_PDF_ITEM = re.compile(
    rf"^(?P<desc>.+?)\s+(?P<unit>{MONEY}){_EURO}\s+(?:(?P<tax>[A-Z])\s+)?(?:(?P<qty>\d+(?:[,.]\d+)?)\s+)?"
    rf"(?P<total>{MONEY}){_EURO}$"
)
_PHOTO_ITEM = re.compile(
    rf"^(?P<desc>.*?[A-Za-zÀ-ÿ]{{2}}.*?)\s+(?:(?P<qty>\d{{1,3}})\s+)?(?:(?P<tax>[A-Da-d])[A-Da-d]?\s+)?"
    rf"(?P<total>{MONEY})$"
)
_STORE = re.compile(r"^EL CORTE INGLES\s*-\s*(?P<store>LOJA\b.+)$", re.I)
# "(C) IMP 23,00% 3,54 0,81 4,35": the VAT class, its rate, base, VAT and total
_CLASS_TOTAL = re.compile(
    rf"^\((?P<cls>[A-Z])\)\s*IMP\s+\d{{1,2}}[,.]\d{{2}}\s*%\s+{MONEY}\s+{MONEY}\s+(?P<total>{MONEY})$"
)


class ElCorteInglesParser(GenericPtParser):
    version = "el_corte_ingles-1.0"
    cap = CALIBRATED_CAP
    geometry = False  # this parser reads its own PDF rows

    def __init__(self) -> None:
        super().__init__(Merchant.EL_CORTE_INGLES)

    def parse(self, doc: DocumentText) -> ParsedReceipt:
        receipt = super().parse(doc)
        settle_by_class(receipt.lines, doc.lines, _CLASS_TOTAL)
        return receipt

    def _start(self, head: list[TextLine]) -> int:
        return next((i for i, line in enumerate(head) if _match(line.text.strip())), len(head))

    def _store(self, lines: list[TextLine]) -> str | None:
        found = next((m.group("store") for line in lines if (m := _STORE.match(line.text.strip()))), None)
        return found or super()._store(lines)

    def _items(self, zone: list[TextLine]) -> list[ParsedLine]:
        parsed: list[ParsedLine] = []
        for line in zone:
            text = line.text.strip()
            if m := _match(text):
                pdf = m.re is _PDF_ITEM
                qty = m.group("qty") if pdf else None
                # A photo of the e-invoice: the OCR may lose the quantity (and the class letter).
                if pdf and qty is None:
                    count = count_from_total(
                        parse_money_cents(m.group("total")), parse_money_cents(m.group("unit"))
                    )
                    qty = str(count) if count else None
                item = build_item(
                    description=m.group("desc"),
                    total_text=m.group("total"),
                    qty_text=qty,
                    unit_price_text=m.group("unit") if pdf else None,
                    tax_code=m.group("tax").upper() if m.group("tax") else None,
                    line_conf=line.confidence,
                    evidence=[text],
                )
                if not pdf:
                    _printed_quantity(item, m.group("qty"))
                parsed.append(item)
            elif re.search(MONEY, text):
                parsed.append(unresolved_line(line))
        return parsed


def _match(text: str) -> re.Match[str] | None:
    return _PDF_ITEM.match(text) or _PHOTO_ITEM.match(text)


def _printed_quantity(item: ParsedLine, qty_text: str | None) -> None:
    """The photo prints a count and only the line total: the unit price is total / count."""
    if not qty_text or int(qty_text) <= 1 or item.line_total_cents is None:
        return
    count = int(qty_text)
    item.quantity = Decimal(count)
    item.unit_price_cents = round(item.line_total_cents / count)
    item.field_confidence["quantity"] = item.field_confidence["unit_price"] = 0.5  # always reviewed
