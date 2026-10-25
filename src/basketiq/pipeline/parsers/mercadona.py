"""Mercadona Portugal receipt parser.

Layout (calibrated on one photographed receipt, 2026):

    MERCADONA
    R BENTO GONÇALVES, 1                      <- store: the lines between the name and TELEFONE
    CORROIOS
    TELEFONE: 260000001
    Descrição            P. Unit   Valor (€)
    1 ROLO GIGANTE                    2,90 D  <- count, description, total, VAT class
    3 TOALHITA BEBE AGUA     1,25     3,75 B  <- count, unit price, total
    1 COUVE-FLOR                              <- weighed: description, then
      1,004 kg  2,50 €/kg             2,51 B     quantity, unit price and total
    TOTAL                           106,55

Read by the shared rules of `GenericPtParser`. A leading number is a quantity only when it explains the
total ("3 x 1,25 = 3,75"); otherwise it belongs to the name ("24 OVOS FRESCOS").
"""

from __future__ import annotations

import re

from basketiq.domain.status import Merchant
from basketiq.pipeline.parsers.base import index_of
from basketiq.pipeline.parsers.generic_pt import CALIBRATED_CAP, GenericPtParser
from basketiq.pipeline.schema import TextLine

_NAME = re.compile(r"^MERCADONA$", re.I)
_PHONE = re.compile(r"^TELEFONE\b", re.I)


class MercadonaParser(GenericPtParser):
    version = "mercadona-1.0"
    cap = CALIBRATED_CAP

    def __init__(self) -> None:
        super().__init__(Merchant.MERCADONA)

    def _store(self, lines: list[TextLine]) -> str | None:
        name = index_of(lines, lambda t: bool(_NAME.match(t)))
        phone = index_of(lines, lambda t: bool(_PHONE.match(t)), (name or 0) + 1)
        if name is None or phone is None or phone - name > 3:
            return super()._store(lines)
        return " - ".join(line.text.strip() for line in lines[name + 1 : phone]) or None
