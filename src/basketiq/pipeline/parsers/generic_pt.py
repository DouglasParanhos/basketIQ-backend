"""Generic Portuguese receipt parser.

Used for Auchan (no real samples yet) and for unknown merchants. It recognises the
common shape "description … amount [VAT]" with optional "qty x unit price" and stops at the
total. Every field is capped below the review threshold, so its output is never auto-confirmed.
"""

from __future__ import annotations

import re

from basketiq.domain.money import parse_money_cents
from basketiq.domain.status import Merchant
from basketiq.pipeline.parsers.base import (
    MONEY,
    build_item,
    choose_purchase_datetime,
    find_amount,
    parse_date_candidates,
    unresolved_line,
)
from basketiq.pipeline.schema import DocumentText, ParsedLine, ParsedReceipt

CONFIDENCE_CAP = 0.6

_ITEM = re.compile(
    rf"^(?:\(?[A-Z]\)?\s+)?(?P<desc>.*?[A-Za-zÀ-ÿ]{{2}}.*?)\s+"
    rf"(?:(?P<qty>\d+(?:[,.]\d{{1,3}})?)\s*(?:kg|un)?\s*[xX]\s*(?P<unit>{MONEY})\s+)?"
    rf"(?P<total>-?{MONEY})(?:\s*€)?(?:\s+[A-Z]|\s+\d{{1,2}}%)?$"
)
_DISCOUNT = re.compile(
    rf"^(?:Desconto|Poupan[çc]a|Promo[çc][ãa]o|Cup[ãa]o|Vale).*?\(?-(?P<amount>{MONEY})\)?$", re.I
)
_TOTAL = re.compile(rf"^TOTAL(?:\s+A\s+PAGAR)?\s*:?\s+(?P<amount>{MONEY})(?:\s*€)?$", re.I)
_STOP = re.compile(r"^(SUBTOTAL|TOTAL|IVA|TAXA|MULTIBANCO|NUMER[AÁ]RIO|TROCO|PAGAMENTO)", re.I)


class GenericPtParser:
    version = "generic_pt-1.0"

    def __init__(self, merchant: Merchant) -> None:
        self.merchant = merchant

    def parse(self, doc: DocumentText) -> ParsedReceipt:
        lines = doc.lines
        end = next((i for i, line in enumerate(lines) if _STOP.match(line.text.strip())), len(lines))
        first_item = next((i for i, line in enumerate(lines[:end]) if _ITEM.match(line.text.strip())), end)

        parsed: list[ParsedLine] = []
        for line in lines[first_item:end]:
            text = line.text.strip()
            if m := _DISCOUNT.match(text):
                if parsed:
                    parsed[-1].discount_cents += abs(parse_money_cents(m.group("amount")) or 0)
                    parsed[-1].field_confidence["discount"] = CONFIDENCE_CAP * line.confidence
                    parsed[-1].evidence.append(text)
                continue
            if m := _ITEM.match(text):
                parsed.append(
                    build_item(
                        description=m.group("desc"),
                        total_text=m.group("total"),
                        qty_text=m.group("qty"),
                        unit_price_text=m.group("unit"),
                        line_conf=line.confidence,
                        evidence=[text],
                    )
                )
            elif re.search(MONEY, text):
                parsed.append(unresolved_line(line))

        for item in parsed:
            item.field_confidence = {
                k: round(min(v, CONFIDENCE_CAP), 3) for k, v in item.field_confidence.items()
            }

        total, _, _ = find_amount(lines[end:], _TOTAL)
        purchase_at, date_conf = choose_purchase_datetime(parse_date_candidates(lines))
        return ParsedReceipt(
            merchant=self.merchant,
            purchase_at=purchase_at,
            total_cents=total,
            lines=parsed,
            field_confidence={
                "purchase_at": min(date_conf, CONFIDENCE_CAP),
                "total": CONFIDENCE_CAP if total is not None else 0.0,
            },
            parser_version=self.version,
            notes=["generic_parser_uncalibrated"],
        )
