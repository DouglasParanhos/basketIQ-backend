"""Invented receipts in three layouts, photographed four ways (spec 009, A2.2).

Shops and products are made up. The layouts copy shapes seen on real photos, never their text:
  - "deposit": a quantity line printed before its item, a weighed item, a discount line and bottle deposits
    (a "DEPOSITO" line is an item, the "TOTAL DEPOSITO" line is a summary);
  - "vatcode": a VAT code in front of every item, a count the OCR dropped ("X 2,68 5,36"), section titles;
  - "plain": "count x price" and the line total on the row of the item.
`build` returns the rows to draw and the reading to expect; `photograph` draws them and damages the picture.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

WIDTH, PITCH, MARGIN = 1000, 44, 50
LAYOUTS = ("deposit", "vatcode", "plain")
DAMAGES = ("noise", "tilt", "glued", "broken")


@dataclass(frozen=True)
class Item:
    description: str
    unit_price_cents: int
    quantity: Decimal = Decimal(1)
    unit: str = "un"
    discount_cents: int = 0
    tax: str = "1"

    @property
    def total_cents(self) -> int:
        return int((self.quantity * self.unit_price_cents).quantize(Decimal(1), ROUND_HALF_UP))


Row = tuple[str, str]  # (left text, right-aligned text); the right one may be empty

ITEMS: dict[str, list[Item]] = {
    "deposit": [
        Item("PAO DE FORMA 500G", 189),
        Item("IOGURTE NATURAL 125G", 59, Decimal(6)),
        Item("TOMATE RAMA", 249, Decimal("0.412"), "kg"),
        Item("CAFE MOIDO 250G", 299, discount_cents=50, tax="3"),
        Item("MANTEIGA SEM SAL 250G", 245),
        Item("AGUA MINERAL 1,5L", 45, Decimal(4)),
        Item("DEPOSITO", 10, tax="0"),
        Item("DEPOSITO", 10, Decimal(2), tax="0"),
        Item("BATATA BRANCA 2KG", 229, tax="1"),
    ],
    "vatcode": [
        Item("DETERGENTE LOUCA 500ML", 268, Decimal(2)),
        Item("ESCOVA DENTES SOFT", 140),
        Item("ESPONJA COZINHA 3UN", 175),
        Item("CHAMPO NEUTRO 400ML", 395, Decimal(2)),
        Item("LENCOS PAPEL 10UN", 99),
        Item("SABONETE LIQUIDO 300ML", 189),
    ],
    "plain": [
        Item("FARINHA TRIGO 1KG", 95, Decimal(2)),
        Item("ARROZ AGULHA 1KG", 129),
        Item("AZEITE VIRGEM 750ML", 749),
        Item("LEITE MEIO GORDO 1L", 79, Decimal(6)),
        Item("OVOS CLASSE M 12UN", 219),
        Item("ATUM EM AZEITE 120G", 189, Decimal(3)),
        Item("BOLACHA MARIA 200G", 85),
    ],
}


def _eur(cents: int, sign: str = "") -> str:
    return f"{cents // 100},{cents % 100:02d}{sign}"


def _qty(item: Item) -> str:
    return f"{item.quantity:.3f}".replace(".", ",") + " kg" if item.unit == "kg" else f"{item.quantity} x"


def _rows_deposit(items: list[Item]) -> tuple[list[Row], int]:
    rows: list[Row] = [("MERCEARIA EXEMPLO LDA", ""), ("DENOMINACAO", "PRECO IVA")]
    for it in items:
        if it.unit == "kg":
            rows.append((f"{_qty(it)} x {_eur(it.unit_price_cents)} EUR/kg", ""))
        elif it.quantity > 1:
            rows.append((f"{it.quantity} x {_eur(it.unit_price_cents)} EUR", ""))
        rows.append((it.description, f"{_eur(it.total_cents)} EUR {it.tax}"))
        if it.discount_cents:
            rows.append(("DESCONTO", f"{_eur(it.discount_cents, '-')} EUR"))
    deposits = sum(i.total_cents for i in items if i.description == "DEPOSITO")
    total = sum(i.total_cents - i.discount_cents for i in items)
    rows += [("TOTAL DEPOSITO VOLTA*", f"{_eur(deposits)} EUR"), ("A PAGAR", f"{_eur(total)} EUR")]
    rows += [("PAGAMENTO COM CARTAO", f"{_eur(total)} EUR")]
    return rows, total


def _rows_vatcode(items: list[Item]) -> tuple[list[Row], int]:
    rows: list[Row] = [("HIPERMERCADO EXEMPLO SA", ""), ("IVA ARTIGO/QT/LI", "PRECO VALOR"), ("Higiene", "")]
    for n, it in enumerate(items):
        code = "12" if n % 2 == 0 else "I2"
        if it.quantity > 1:
            rows.append((f"{code} {it.description}", ""))
            rows.append((f"X {_eur(it.unit_price_cents)}", _eur(it.total_cents)))  # the count was lost
        else:
            rows.append((f"{code} {it.description}", _eur(it.total_cents)))
    total = sum(i.total_cents for i in items)
    rows += [
        ("TOTAL PAGO", _eur(total)),
        ("Visa", f"{_eur(total)} 0,00"),
        ("TROCO", ""),
        ("RESUMO DO IVA", ""),
    ]
    return rows, total


def _rows_plain(items: list[Item]) -> tuple[list[Row], int]:
    rows: list[Row] = [("MERCADO EXEMPLO", ""), ("DESCRICAO", "VALOR")]
    for it in items:
        if it.quantity > 1:
            rows.append(
                (it.description, f"{it.quantity} x {_eur(it.unit_price_cents)} {_eur(it.total_cents)} A")
            )
        else:
            rows.append((it.description, f"{_eur(it.total_cents)} A"))
    total = sum(i.total_cents for i in items)
    rows += [("TOTAL", _eur(total)), ("MULTIBANCO", _eur(total))]
    return rows, total


_BUILDERS = {"deposit": _rows_deposit, "vatcode": _rows_vatcode, "plain": _rows_plain}


def build(layout: str) -> tuple[list[Row], dict]:
    items = ITEMS[layout]
    rows, total = _BUILDERS[layout](items)
    expected = {
        "merchant": "UNKNOWN",
        "route": "LOCAL_OCR",
        "requires_ocr": True,
        "total_cents": total,
        "lines": [
            {
                "raw_description": it.description,
                "quantity": str(it.quantity),
                "unit": it.unit,
                "unit_price_cents": it.unit_price_cents,
                "line_total_cents": it.total_cents,
                "discount_cents": it.discount_cents,
            }
            for it in items
        ],
    }
    return rows, expected


# ---- drawing and damage ---------------------------------------------------------------------------------


def _draw(rows: list[Row], glued: bool = False, broken: bool = False) -> Image.Image:
    font = ImageFont.load_default(size=26)
    img = Image.new("RGB", (WIDTH, int(PITCH * (len(rows) * 1.4)) + 2 * MARGIN), "white")
    draw = ImageDraw.Draw(img)
    y = float(MARGIN)
    for n, (left, right) in enumerate(rows):
        if glued and right:
            draw.text((MARGIN, y), f"{left}  {right}", fill="black", font=font)  # one run: the OCR joins them
        elif broken and right and len(left.split()) > 2 and n % 3 == 0:
            head, _, tail = left.rpartition(" ")
            draw.text((MARGIN, y), head, fill="black", font=font)  # the name breaks over two rows,
            y += PITCH
            draw.text((MARGIN, y), tail, fill="black", font=font)  # the price stays on the second one
            draw.text((WIDTH - MARGIN, y), right, fill="black", font=font, anchor="ra")
        else:
            draw.text((MARGIN, y), left, fill="black", font=font)
            if right:
                draw.text((WIDTH - MARGIN, y), right, fill="black", font=font, anchor="ra")
        y += PITCH
    return img.crop((0, 0, WIDTH, int(y) + MARGIN))


def _noisy(img: Image.Image) -> Image.Image:
    blurred = img.filter(ImageFilter.GaussianBlur(1.0))
    noise = np.random.default_rng(7).normal(0, 16, (blurred.height, blurred.width, 1))
    return Image.fromarray(np.clip(np.asarray(blurred) + noise, 0, 255).astype(np.uint8))


def _photo_like(img: Image.Image) -> Image.Image:
    blurred = img.filter(ImageFilter.GaussianBlur(0.8))
    noise = np.random.default_rng(42).normal(0, 6, (blurred.height, blurred.width, 1))
    return Image.fromarray(np.clip(np.asarray(blurred) + noise, 0, 255).astype(np.uint8))


def _tilted(img: Image.Image) -> Image.Image:
    return img.rotate(3.5, expand=True, fillcolor="white", resample=Image.Resampling.BICUBIC)


_DAMAGE: dict[str, tuple[dict[str, bool], Callable[[Image.Image], Image.Image]]] = {
    "noise": ({}, _noisy),
    "tilt": ({}, lambda i: _photo_like(_tilted(i))),
    "glued": ({"glued": True}, _photo_like),
    "broken": ({"broken": True}, _photo_like),
}


def photograph(layout: str, damage: str) -> Image.Image:
    rows, _ = build(layout)
    draw_options, after = _DAMAGE[damage]
    return after(_draw(rows, **draw_options))


def write_all(folder: Path) -> list[str]:
    """The twelve photos, one folder each with `document.png` and `expected.json`."""
    names = []
    for layout in LAYOUTS:
        for damage in DAMAGES:
            name = f"{layout}_{damage}"
            target = folder / name
            target.mkdir(parents=True)
            photograph(layout, damage).save(target / "document.png")
            (target / "expected.json").write_text(json.dumps(build(layout)[1]), encoding="utf-8")
            names.append(name)
    return names
