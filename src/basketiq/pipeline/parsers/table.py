"""Column table of a searchable PDF: the header words say what each column is, the x positions of the
words say which column a value belongs to.

    Produtos     Preço      Tipo               Quantidade     TOTAL         <- header, may span lines
                 unitário   imposto
    MOLHO PICANTE  5,99 €     C                      1         5,99 €

Only used for PDFs with a text layer, where the positions are exact. Without a recognisable header
(description, total and a price or quantity column) `table_items` answers None and the caller reads the
lines by their shape instead.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from basketiq.pipeline.parsers.base import MONEY, build_item, one_letter_apart, unresolved_line
from basketiq.pipeline.schema import ParsedLine, TextLine, TextToken

_ROLE_WORDS = {
    "desc": r"produtos?|descricao|designacao|artigos?|denominacao",
    "price": r"preco|unitario|unit|punit",
    "tax": r"imposto|iva|taxa|tipo",
    "qty": r"quantidade|qtd|qtde|qt|quant",
    "total": r"total|valor|importe",
}
_FILLER = {"de", "do", "da", "e"}
_NUMERIC_ROLES = ("price", "tax", "qty", "total")
_CURRENCY = {"€", "EUR"}
_QTY = re.compile(r"\d+(?:[,.]\d{1,3})?")
_TAX = re.compile(r"[A-Za-z0-9]{1,2}")
_MONEY = re.compile(MONEY)
_PRINTED_QTY_CONFIDENCE = 0.5  # a count with only the line total: the unit price is derived, so reviewed
_MAX_ABBREVIATION_LEN = 3  # letters: "QT", "LI" next to real titles in "ARTIGO/QT/LI"
# Column titles a photo may misread by one letter ("UALOR"); shorter words are too close to others to forgive.
_TITLES = (
    "produtos", "descricao", "designacao", "artigos", "denominacao", "preco", "unitario", "imposto",
    "quantidade", "total", "valor", "importe",
)  # fmt: skip
_MIN_TOLERATED_TITLE = 5


@dataclass(frozen=True)
class Table:
    columns: dict[str, float]  # role -> x centre of its header words
    left_edge: float  # a word that ends before this is part of the description
    first_row: int  # index of the first line after the header


def _fold(word: str) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c))
    return re.sub(r"[^a-z]", "", plain.lower())


def _role(word: str) -> str | None:
    folded = _fold(word)
    return next((r for r, pattern in _ROLE_WORDS.items() if re.fullmatch(pattern, folded)), None)


def _header_roles(line: TextLine) -> list[tuple[str, TextToken]] | None:
    """The (role, word) pairs of a line made only of header words, else None."""
    pairs: list[tuple[str, TextToken]] = []
    for token in line.tokens:
        role = _role(token.text)
        if role is not None:
            pairs.append((role, token))
        elif _fold(token.text) not in _FILLER:
            return None
    return pairs or None


def _misread_title(word: str) -> bool:
    folded = _fold(word)
    return len(folded) >= _MIN_TOLERATED_TITLE and any(one_letter_apart(folded, t) for t in _TITLES)


def _is_header_word(word: str) -> bool:
    parts = [p for p in word.split("/") if p]
    if len(parts) < 2:
        return _role(word) is not None or _fold(word) in _FILLER or _misread_title(word)
    # "ARTIGO/QT/LI": titles joined by slashes. At least two are column titles, the rest short abbreviations.
    roles = [_role(p) for p in parts]
    return sum(r is not None for r in roles) >= 2 and all(
        r is not None or len(_fold(p)) <= _MAX_ABBREVIATION_LEN for r, p in zip(roles, parts, strict=True)
    )


def is_header_row(text: str) -> bool:
    """ "IVA DESCRICAO VALOR": a column title, not the "IVA ..." summary that closes the items."""
    words = text.split()
    return bool(words) and all(_is_header_word(w) for w in words)


def first_column_role(text: str) -> str | None:
    """The role of the first column title of a header row ("tax" for "IVA ARTIGO/QT/LI PRECO VALOR")."""
    return _role(text.split()[0].split("/")[0]) if is_header_row(text) else None


def find_table(lines: list[TextLine]) -> Table | None:
    i = 0
    while i < len(lines):
        if _header_roles(lines[i]) is None:
            i += 1
            continue
        band: list[tuple[str, TextToken]] = []
        j = i
        while j < len(lines) and (pairs := _header_roles(lines[j])) is not None:
            band += pairs
            j += 1
        roles = {role for role, _ in band}
        if {"desc", "total"} <= roles and roles & {"price", "qty"}:
            centres = {
                role: sum((t.x0 + t.x1) / 2 for r, t in band if r == role)
                / sum(1 for r, _ in band if r == role)
                for role in _NUMERIC_ROLES
                if role in roles
            }
            left = min(t.x0 for r, t in band if r in _NUMERIC_ROLES) - 2
            return Table(centres, left, j)
        i = j
    return None


def table_items(lines: list[TextLine], end: int) -> list[ParsedLine] | None:
    """The items between the header and `end`, read column by column; None when there is no table or no
    row of it could be read."""
    table = find_table(lines[:end])
    if table is None:
        return None
    parsed: list[ParsedLine] = []
    for line in lines[table.first_row : end]:
        row = _row(line, table)
        if row is not None:
            parsed.append(row)
    return parsed if any(not p.unresolved for p in parsed) else None


def _row(line: TextLine, table: Table) -> ParsedLine | None:
    words = [t for t in line.tokens if t.text not in _CURRENCY]
    description = " ".join(t.text for t in words if t.x1 <= table.left_edge)
    cells: dict[str, list[str]] = {role: [] for role in table.columns}
    for t in words:
        if t.x1 > table.left_edge:
            nearest = min(table.columns, key=lambda r: abs((t.x0 + t.x1) / 2 - table.columns[r]))
            cells[nearest].append(t.text.removesuffix("€"))  # "1,29€" when the sign is not spaced off
    values = {role: " ".join(texts) for role, texts in cells.items() if texts}
    letters = len(re.findall(r"[A-Za-zÀ-ÿ]", description))
    if not values and letters < 2:
        return None
    total = values.get("total", "")
    if letters < 2 or not _MONEY.fullmatch(total):
        return unresolved_line(line) if values else None
    qty, price, tax = values.get("qty"), values.get("price"), values.get("tax")
    if qty is not None and not _QTY.fullmatch(qty):
        return unresolved_line(line)
    if price is not None and not _MONEY.fullmatch(price):
        return unresolved_line(line)
    item = build_item(
        description=description,
        total_text=total,
        qty_text=qty if price else None,
        unit_price_text=price if qty else None,
        tax_code=tax.upper() if tax and _TAX.fullmatch(tax) else None,
        line_conf=line.confidence,
        evidence=[line.text],
    )
    if qty and not price:
        _printed_quantity(item, qty)
    return item


def _printed_quantity(item: ParsedLine, qty_text: str) -> None:
    """A count and only the line total: the unit price is total / count."""
    count = Decimal(qty_text.replace(",", "."))
    total = item.line_total_cents
    if count <= 1 or total is None:
        return
    item.quantity = count
    item.unit_price_cents = int((Decimal(total) / count).to_integral_value())
    item.field_confidence["quantity"] = item.field_confidence["unit_price"] = _PRINTED_QTY_CONFIDENCE
