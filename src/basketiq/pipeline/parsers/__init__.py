from __future__ import annotations

from basketiq.domain.status import Merchant
from basketiq.pipeline.parsers.base import Parser
from basketiq.pipeline.parsers.continente import ContinenteParser
from basketiq.pipeline.parsers.generic_pt import GenericPtParser
from basketiq.pipeline.parsers.lidl import LidlParser
from basketiq.pipeline.parsers.pingo_doce import PingoDoceParser


def parser_for(merchant: Merchant) -> Parser:
    if merchant == Merchant.PINGO_DOCE:
        return PingoDoceParser()
    if merchant == Merchant.LIDL:
        return LidlParser()
    if merchant == Merchant.CONTINENTE:
        return ContinenteParser()
    # Auchan has no calibrated parser yet (no samples); see generic_pt docstring.
    return GenericPtParser(merchant)


__all__ = ["ContinenteParser", "GenericPtParser", "LidlParser", "Parser", "PingoDoceParser", "parser_for"]
