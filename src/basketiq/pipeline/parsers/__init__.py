from __future__ import annotations

from basketiq.domain.status import Merchant
from basketiq.pipeline.parsers.aldi import AldiParser
from basketiq.pipeline.parsers.auchan import AuchanParser
from basketiq.pipeline.parsers.base import Parser
from basketiq.pipeline.parsers.continente import ContinenteParser
from basketiq.pipeline.parsers.el_corte_ingles import ElCorteInglesParser
from basketiq.pipeline.parsers.generic_pt import GenericPtParser
from basketiq.pipeline.parsers.lidl import LidlParser
from basketiq.pipeline.parsers.mercadona import MercadonaParser
from basketiq.pipeline.parsers.pingo_doce import PingoDoceParser


def parser_for(merchant: Merchant) -> Parser:
    if merchant == Merchant.ALDI:
        return AldiParser()
    if merchant == Merchant.MERCADONA:
        return MercadonaParser()
    if merchant == Merchant.EL_CORTE_INGLES:
        return ElCorteInglesParser()
    if merchant == Merchant.PINGO_DOCE:
        return PingoDoceParser()
    if merchant == Merchant.LIDL:
        return LidlParser()
    if merchant == Merchant.CONTINENTE:
        return ContinenteParser()
    if merchant == Merchant.AUCHAN:
        return AuchanParser()
    return GenericPtParser(merchant)


__all__ = [
    "AldiParser",
    "AuchanParser",
    "ContinenteParser",
    "ElCorteInglesParser",
    "GenericPtParser",
    "LidlParser",
    "MercadonaParser",
    "Parser",
    "PingoDoceParser",
    "parser_for",
]
