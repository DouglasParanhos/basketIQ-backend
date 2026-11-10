"""Package size read from a line description (step C, D9). Pure and deterministic.

The size is what one package holds, normalised to kg, l, un or a unit of use (rolo, dose, cápsula,
saqueta). It never depends on the product group (the group only chooses which base to compare in),
it is never guessed: a description with two different sizes, a number without a unit (a cut
description like «ARROZ CAROLINO 1») or an implausible size gives nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from basketiq.domain.money import parse_decimal
from basketiq.domain.normalization import fold

KG, L, UN = "kg", "l", "un"

# Printed unit → (base, factor to the base).
_WEIGHT_VOLUME = {
    "KG": (KG, Decimal(1)),
    "G": (KG, Decimal("0.001")),
    "GR": (KG, Decimal("0.001")),
    "L": (L, Decimal(1)),
    "LT": (L, Decimal(1)),
    "CL": (L, Decimal("0.01")),
    "ML": (L, Decimal("0.001")),
}
# Counted things: the noun (or its abbreviation) after the number names the base.
_COUNTED = {
    "UN": UN,
    "UND": UN,
    "UNID": UN,
    "UNIDADES": UN,
    "OVOS": UN,
    "ROLO": "rolo",
    "ROLOS": "rolo",
    "DOSES": "dose",
    "CAPSULAS": "capsula",
    "CAPS": "capsula",
    "CAP": "capsula",
    "SAQUETAS": "saqueta",
}
_DOZENS = {"UMA DUZIA": 12, "1 DUZIA": 12, "MEIA DUZ": 6, "1/2 DUZ": 6, "DUAS DUZ": 24, "2 DUZ": 24}

_NUM = r"(\d+(?:[.,]\d+)?)"
_WV = "|".join(sorted(_WEIGHT_VOLUME, key=len, reverse=True))
_MULTI = re.compile(rf"(?<![\d.,])(\d+)\s*X\s*{_NUM}\s*({_WV})\b")
_SIMPLE = re.compile(rf"(?<![\d.,]){_NUM}\s*({_WV})\b")
_COUNT = re.compile(rf"(?<![\d.,])(\d+)\s*({'|'.join(_COUNTED)})\b")
_TIMES_COUNT = re.compile(r"(?<![A-Z\d])X\s*(\d+)\b")  # «ATUM AZEITE X4»: four tins
_DOZEN = re.compile(r"\b(UMA DUZIA|1 DUZIA|MEIA DUZ|1/2 DUZ|DUAS DUZ|2 DUZ)")


@dataclass(frozen=True)
class Size:
    value: Decimal  # in `unit`
    unit: str  # kg, l, un or a unit of use


def plain(value: Decimal) -> Decimal:
    """1.000 → 1 and 40 → 40 (normalize() alone would write 4E+1)."""
    return value.quantize(Decimal(1)) if value == value.to_integral_value() else value.normalize()


def _found(text: str) -> list[Size]:
    found: list[Size] = []
    for m in _MULTI.finditer(text):
        count, amount, unit = int(m.group(1)), parse_decimal(m.group(2)), m.group(3)
        base, factor = _WEIGHT_VOLUME[unit]
        if amount is not None:
            found.append(Size(count * amount * factor, base))
    text = _MULTI.sub(" ", text)
    for m in _SIMPLE.finditer(text):
        amount, unit = parse_decimal(m.group(1)), m.group(2)
        base, factor = _WEIGHT_VOLUME[unit]
        if amount is not None:
            found.append(Size(amount * factor, base))
    for m in _COUNT.finditer(text):
        found.append(Size(Decimal(m.group(1)), _COUNTED[m.group(2)]))
    found.extend(Size(Decimal(m.group(1)), UN) for m in _TIMES_COUNT.finditer(text))
    found.extend(Size(Decimal(_DOZENS[m.group(1)]), UN) for m in _DOZEN.finditer(text))
    return found


def extract_size(description: str, plausible: dict[str, tuple[Decimal, Decimal]]) -> Size | None:
    """The size printed in `description`; None when there is none, two different ones or an implausible one.

    `plausible` maps each base to its (min, max); a base missing from it is never plausible.
    """
    sizes = {Size(plain(s.value), s.unit) for s in _found(fold(description))}
    if len(sizes) != 1:
        return None
    size = sizes.pop()
    return size if is_plausible(size, plausible) else None


def is_plausible(size: Size, plausible: dict[str, tuple[Decimal, Decimal]]) -> bool:
    """Within the catalogue's (min, max) for its base (D9); a base without a range is never plausible."""
    low_high = plausible.get(size.unit)
    return low_high is not None and low_high[0] <= size.value <= low_high[1]
