"""Aldi Portugal receipt parser.

Layout (calibrated on two photographed receipts, 2026):

    DENOMINAÇÃO                      PREÇO  IVA
    BRETZELS 868G                    2,99 €  1       <- description, price, VAT class (0, 1 or 3)
    6 x 0,99 €                                      <- quantity line, printed BEFORE its item
    LEITE UHT GORDO, 1L              5,94 €  1
    0,298 kg x 1,99 €/kg                            <- weighed item, same rule
    PEPINO                           0,59 €  1
    DESCONTO                         0,46-€          <- discount on the previous item
    DEPOSITO                         0,20 €  0       <- bottle deposit ("Volta"), 0,10 € each
    TOTAL DEPOSITO VOLTA*            0,30 €          <- summary of the deposits, not an item
    A PAGAR                         97,37 €
    Valor depósito                   5,20 €          <- refund of returned bottles, not an item

The shared rules of `GenericPtParser` read all of it: a quantity line belongs to the neighbouring line
whose total it explains, and the item zone ends at the first total. Only two samples exist, so the
confidence is capped below a fully calibrated parser.
"""

from __future__ import annotations

from basketiq.domain.status import Merchant
from basketiq.pipeline.parsers.generic_pt import CALIBRATED_CAP, GenericPtParser


class AldiParser(GenericPtParser):
    version = "aldi-1.0"
    cap = CALIBRATED_CAP

    def __init__(self) -> None:
        super().__init__(Merchant.ALDI)
