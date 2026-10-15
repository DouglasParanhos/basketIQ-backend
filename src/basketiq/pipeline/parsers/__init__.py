from __future__ import annotations

from basketiq.domain.status import Merchant
from basketiq.pipeline.parsers.base import Parser
from basketiq.pipeline.parsers.generic_pt import GenericPtParser
from basketiq.pipeline.parsers.lidl import LidlParser
from basketiq.pipeline.parsers.pingo_doce import PingoDoceParser


def parser_for(merchant: Merchant) -> Parser:
    if merchant == Merchant.PINGO_DOCE:
        return PingoDoceParser()
    if merchant == Merchant.LIDL:
        return LidlParser()
    # Continente / Auchan have no calibrated parser yet (no samples); see generic_pt docstring.
    return GenericPtParser(merchant)


__all__ = ["GenericPtParser", "LidlParser", "Parser", "PingoDoceParser", "parser_for"]
