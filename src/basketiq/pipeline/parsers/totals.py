"""The total of a receipt: what the purchase was worth after the real discounts, whatever the way of paying
(spec 009, FR-007). Card balance, vouchers and bottle-deposit credit are ways of paying, not discounts.

A receipt prints several amounts that can pass for the total, and the printed labels do not say which is
right. The lines and the payments do:

  1. A printed amount that equals the sum of the lines is the total. The sum is read two ways, because
     receipts disagree on what a saving does to the printed price: Pingo Doce prints the shelf price and
     takes the saving off afterwards, Continente prints the price already net.
  2. When both ways give a printed amount (Pingo Doce: 47,46 gross, 38,30 net), what was paid decides:
     the card, card balance, vouchers and deposit credit add up to the total.
  3. When no printed amount agrees with the lines, a printed amount equal to what was paid is the total,
     and a single printed amount is taken as it stands (the lines may be the ones misread).
  4. Otherwise there is no total: the receipt goes to review. A computed sum is never returned.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterable
from decimal import Decimal

from basketiq.domain.money import one_digit_apart, parse_money_cents
from basketiq.pipeline.parsers.base import COUNT_INFERRED_CONFIDENCE, MONEY
from basketiq.pipeline.schema import ParsedLine, TextLine

_EURO = r"(?:\s*(?:€|EUR))?"
_SUBTOTAL = re.compile(rf"^SUBTOTAL\s*:?\s+(?P<amount>{MONEY}){_EURO}$", re.I)
# Ways of paying with money, and ways of paying with something else: card balance, vouchers, deposit credit.
_METHODS = (
    r"Multibanco|MB(?:\s*WAY)?|Cart[aã]o(?:\s+(?:de\s+)?(?:cr[eé]dito|d[eé]bito|banc[aá]rio))?|Visa|Mastercard|"
    r"Numer[aá]rio|Dinheiro|Cheque|Pagamento\s+com\s+cart\w*"
)
_BALANCES = (
    r"Desconto\s+Cart[aã]o\s+Utilizado|Vale|Valor\s+dep[oó]sito|Cr[eé]dito\s+dep[oó]sito|"
    r"Saldo\s+(?:utilizado|cart[aã]o)"
)


def _paid_with(words: str) -> re.Pattern[str]:
    return re.compile(rf"^(?:{words})\b.*?\s(?P<amount>{MONEY}){_EURO}$", re.I)


_PAYMENT = _paid_with(f"{_METHODS}|{_BALANCES}")
_BALANCE = _paid_with(_BALANCES)
_SUMMARY_START = re.compile(r"^(?:SUBTOTAL|TOTAL|A\s+PAGAR)\b", re.I)
_VAT_TABLE = re.compile(r"^(?:Resumo\s+IVA|%IVA|IVA\b|TAXA\b)", re.I)


def printed_totals(summary: Iterable[TextLine], total_label: re.Pattern[str]) -> list[int]:
    """The distinct amounts printed next to a total label, in reading order."""
    found: list[int] = []
    for line in summary:
        text = line.text.strip()
        m = _SUBTOTAL.match(text) or total_label.match(text)
        cents = parse_money_cents(m.group("amount")) if m else None
        if cents is not None and cents not in found:
            found.append(cents)
    return found


def paid(summary: Iterable[TextLine]) -> int | None:
    """Everything paid, in any way, as printed before the VAT table; None without a payment amount."""
    amounts: list[int] = []
    for line in summary:
        text = line.text.strip()
        if _VAT_TABLE.match(text):
            break
        if (m := _PAYMENT.match(text)) and (cents := parse_money_cents(m.group("amount"))) is not None:
            amounts.append(cents)
    return sum(amounts) if amounts else None


def balance_used(lines: list[TextLine]) -> int:
    """Card balance, vouchers and deposit credit printed in the summary of the receipt (0 when none): what
    was paid with something other than money, and so is not a discount."""
    texts = [line.text.strip() for line in lines]
    start = next((i for i, text in enumerate(texts) if _SUMMARY_START.match(text)), len(texts))
    total = 0
    for text in texts[start:]:
        if _VAT_TABLE.match(text):
            break
        if (m := _BALANCE.match(text)) and (cents := parse_money_cents(m.group("amount"))) is not None:
            total += cents
    return total


def line_sums(items: Iterable[ParsedLine]) -> set[int]:
    """The sum of the resolved lines read both ways: prices as printed (already net), and prices as
    printed minus the line discounts (printed gross)."""
    resolved = [i for i in items if not i.unresolved and i.line_total_cents is not None]
    if not resolved:
        return set()
    printed = sum(i.line_total_cents or 0 for i in resolved)
    return {printed, printed - sum(i.discount_cents for i in resolved)}


def choose_total(
    summary: list[TextLine], items: list[ParsedLine], total_label: re.Pattern[str]
) -> int | None:
    candidates = printed_totals(summary, total_label)
    if not candidates:
        return None
    sums, payments = line_sums(items), paid(summary)
    agreeing = [c for c in candidates if c in sums]
    if len(agreeing) == 1:
        return agreeing[0]
    if len(agreeing) > 1:  # the lines fit two readings: what was paid decides, else nothing does
        return payments if payments in agreeing else None
    if payments in candidates:
        return payments
    return candidates[0] if len(candidates) == 1 else None


def settle_savings(items: list[ParsedLine], total: int | None) -> None:
    """A receipt that prints its prices already net of the savings (Continente: SUBTOTAL is the sum of the
    printed prices) is brought to the common model, gross price and line discount, by adding each
    saving back. Done only when the net reading is the one that reaches the total."""
    resolved = [i for i in items if not i.unresolved and i.line_total_cents is not None]
    if total is None or not any(i.discount_cents for i in resolved):
        return
    printed = sum(i.line_total_cents or 0 for i in resolved)
    if printed != total or printed == printed - sum(i.discount_cents for i in resolved):
        return
    for item in resolved:
        if item.discount_cents and item.quantity:
            item.line_total_cents = (item.line_total_cents or 0) + item.discount_cents
            item.unit_price_cents = int((Decimal(item.line_total_cents) / item.quantity).to_integral_value())


MAX_UNCLASSED = 8  # items without a VAT class tried against the class totals (2^8 ways at most)


def settle_by_class(items: list[ParsedLine], lines: list[TextLine], class_total: re.Pattern[str]) -> None:
    """Settle the items against the total the receipt prints for each VAT class (`class_total` matches such a
    row, with groups `cls` and `total`). When the items of a class add up to it, or do with one digit of one
    item changed (4,26 read for 4,25), the misread digit is corrected and the line reviewed, and items whose
    class letter the OCR lost get the class whose total they complete. Only a single way to complete a total
    is taken; anything else is left as read."""
    pool = [i for i in items if i.tax_code is None and i.line_total_cents is not None][:MAX_UNCLASSED]
    for line in lines:
        if (
            not (m := class_total.match(line.text.strip()))
            or (total := parse_money_cents(m.group("total"))) is None
        ):
            continue
        cls = m.group("cls")
        tagged = [i for i in items if i.tax_code == cls and i.line_total_cents is not None]
        ways = []
        for n in range(len(pool) + 1):
            for picked in itertools.combinations(pool, n):
                members = tagged + list(picked)
                delta = (
                    sum(i.line_total_cents - i.discount_cents for i in members if i.line_total_cents) - total
                )
                fixable = (
                    [
                        i
                        for i in members
                        if i.line_total_cents
                        and one_digit_apart(i.line_total_cents, i.line_total_cents - delta)
                    ]
                    if delta
                    else []
                )
                if delta == 0 or len(fixable) == 1:
                    ways.append((members, fixable, delta))
        if len(ways) != 1:
            continue
        members, fixable, delta = ways[0]
        for item in fixable:
            item.line_total_cents = (item.line_total_cents or 0) - delta
            if item.quantity == 1:
                item.unit_price_cents = item.line_total_cents
            for name in ("line_total", "unit_price"):
                item.field_confidence[name] = min(
                    item.field_confidence.get(name, 1.0), COUNT_INFERRED_CONFIDENCE
                )
            item.evidence.append(line.text.strip())
        for item in members:
            item.tax_code = cls
        pool = [i for i in pool if i not in members]
