"""Generate the synthetic golden set (tests/golden/synthetic).

Layouts reproduce the real Pingo Doce and Lidl receipts used for calibration, with invented
products, fake customer NIF and no card data. Run: `python scripts/make_synthetic_fixtures.py`.
Output is deterministic, so regenerating produces identical expectations.
"""

from __future__ import annotations

import io
import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import mm
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parents[1] / "tests" / "golden" / "synthetic"
NEG = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

PINGO_HEADER = [
    "ESTRADA DO EXEMPLO",
    "Tel.: 210000000",
    "Pingo Doce - Distribuição Alimentar, S.A.",
    "Sede: R Actor António Silva,N7,1649-033 Lisboa",
    "Registo C.R.C. Lisboa-Matrícula/NIPC: 500829993",
    "Artigos",
]


def pingo_lines(
    items: list[str], summary: list[str], tax_rows: list[str], date_line: str, issue: str
) -> list[str]:
    return [
        *PINGO_HEADER,
        *items,
        "Resumo",
        *summary,
        "Pagamentos",
        "TOTAL PAGO " + summary[-1].split()[-1],
        "Resumo IVA",
        "Taxa Valor s/IVA Valor IVA Valor c/IVA",
        *tax_rows,
        "------------------------------------------------",
        date_line,
        "Fatura Simplificada FS 00000000000000000/000001",
        f"1ª Via Original Data de emissão:{issue}",
        "N. Contribuinte: 999999990",
        "Fatura necessária para troca em 15 dias",
        "pingodoce.pt/apoio-ao-cliente/",
    ]


def write_text_pdf(path: Path, lines: list[str], width_mm: float = 80) -> None:
    buf = io.BytesIO()
    height = (len(lines) * 5 + 30) * mm
    c = canvas.Canvas(buf, pagesize=(width_mm * mm, height))
    c.setFont("Courier", 8)
    y = height - 15 * mm
    for line in lines:
        c.drawString(5 * mm, y, line)
        y -= 5 * mm
    c.showPage()
    c.save()
    path.write_bytes(buf.getvalue())


def render_image(
    lines: list[str], *, width: int = 900, line_h: int = 34, quality: str = "good"
) -> Image.Image:
    font = ImageFont.load_default(size=24)
    img = Image.new("RGB", (width, line_h * len(lines) + 120), "white")
    draw = ImageDraw.Draw(img)
    y = 60
    for line in lines:
        draw.text((40, y), line, fill="black", font=font)
        y += line_h
    if quality == "medium":
        img = img.rotate(1.5, expand=True, fillcolor="white").filter(ImageFilter.GaussianBlur(0.8))
        rnd = random.Random(42)
        px = img.load()
        for _ in range(4000):
            x, yy = rnd.randrange(img.width), rnd.randrange(img.height)
            px[x, yy] = (200, 200, 200)
    return img


def write_scanned_pdf(path: Path, lines: list[str]) -> None:
    img = render_image(lines)
    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=150)
    path.write_bytes(buf.getvalue())


def write_case(name: str, document: str, expected: dict, writer) -> None:  # type: ignore[no-untyped-def]
    folder = OUT / name
    folder.mkdir(parents=True, exist_ok=True)
    writer(folder / document)
    (folder / "expected.json").write_text(
        json.dumps(expected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> None:
    # --- Pingo Doce, searchable PDF: weight, multi-unit, discount, two VAT rates -------------
    pd_items = [
        "MERCEARIA",
        " C ARROZ CAROLINO PD 1KG 1,49",
        " C AZEITE VIRGEM EXTRA 750ML 6,99",
        "      Poupança Imediata (1,40)",
        "TALHO",
        " C PEITO FRANGO 0,742 X 5,99 4,44",
        "BEBIDAS",
        " E AGUA COM GAS 6X33CL 2 X 2,29 4,58",
        "FRUTAS E VEGETAIS",
        " C BANANA DA MADEIRA 1,038 X 1,99 2,07",
    ]
    pd_summary = ["TOTAL 19,57", "TOTAL POUPANÇA (1,40)", "TOTAL A PAGAR 18,17"]
    pd_tax = [" C 6% 12,82 0,77 13,59", " E 23% 3,72 0,86 4,58"]
    lines = pingo_lines(pd_items, pd_summary, pd_tax, "000001 2026-09-03 18:42 0001 0001 0001", "03/09/2026")
    pingo_expected = {
        "merchant": "PINGO_DOCE",
        "route": "PDF_TEXT",
        "purchase_at": "2026-09-03T18:42:00+01:00",
        "total_cents": 1817,
        "subtotal_cents": 1654,
        "tax_cents": 163,
        "status": "COMPLETED",
        "lines": [
            {
                "raw_description": "ARROZ CAROLINO PD 1KG",
                "quantity": "1",
                "unit": "un",
                "unit_price_cents": 149,
                "line_total_cents": 149,
                "discount_cents": 0,
            },
            {
                "raw_description": "AZEITE VIRGEM EXTRA 750ML",
                "quantity": "1",
                "unit": "un",
                "unit_price_cents": 699,
                "line_total_cents": 699,
                "discount_cents": 140,
            },
            {
                "raw_description": "PEITO FRANGO",
                "quantity": "0.742",
                "unit": "kg",
                "unit_price_cents": 599,
                "line_total_cents": 444,
                "discount_cents": 0,
            },
            {
                "raw_description": "AGUA COM GAS 6X33CL",
                "quantity": "2",
                "unit": "un",
                "unit_price_cents": 229,
                "line_total_cents": 458,
                "discount_cents": 0,
            },
            {
                "raw_description": "BANANA DA MADEIRA",
                "quantity": "1.038",
                "unit": "kg",
                "unit_price_cents": 199,
                "line_total_cents": 207,
                "discount_cents": 0,
            },
        ],
    }
    write_case("pingo_doce_text", "document.pdf", pingo_expected, lambda p: write_text_pdf(p, lines))

    # --- Pingo Doce, totals that do not reconcile -> review ---------------------------------
    bad_lines = [ln.replace("TOTAL A PAGAR 18,17", "TOTAL A PAGAR 18,97") for ln in lines]
    bad_lines = [ln.replace("TOTAL PAGO 18,17", "TOTAL PAGO 18,97") for ln in bad_lines]
    mismatch = {
        **pingo_expected,
        "total_cents": 1897,
        "status": "REVIEW_REQUIRED",
        "review_codes": ["totals_mismatch"],
    }
    write_case("pingo_doce_mismatch", "document.pdf", mismatch, lambda p: write_text_pdf(p, bad_lines))

    # --- Lidl, searchable PDF with quantity and discount lines ------------------------------
    lidl_lines = [
        "LIDL & Cia - EXEMPLO - AV.EXEMPLO",
        "NIF:503340855 C.S. 498.880 EUR",
        "FATURA SIMPLIFICADA",
        "Original Data de Venda: 2026-09-12",
        "No : FS 000000001/000001",
        "NIF...: 999999990",
        "EUR",
        "IOGURTE NATURAL 4X125G 1,98 B",
        "2 x 0,99",
        "TOMATE CHERRY 2,49 B",
        "Desconto -0,50",
        "QUEIJO FRESCO 1,79 B",
        "DETERGENTE LOICA 3,29 A",
        "Total 9,05",
        "MULTIBANCO 9,05",
        "Taxa Base Imp. Val.Total Val.IVA",
        "A 23% 2,67 3,29 0,62",
        "B 6% 5,43 5,76 0,33",
        "0001 000001/01 12.09.26 10:15",
        "Obrigado pela sua visita lidl.pt",
    ]
    lidl_expected = {
        "merchant": "LIDL",
        "route": "PDF_TEXT",
        "purchase_at": "2026-09-12T10:15:00+01:00",
        "total_cents": 905,
        "subtotal_cents": 810,
        "tax_cents": 95,
        "status": "COMPLETED",
        "lines": [
            {
                "raw_description": "IOGURTE NATURAL 4X125G",
                "quantity": "2",
                "unit": "un",
                "unit_price_cents": 99,
                "line_total_cents": 198,
                "discount_cents": 0,
            },
            {
                "raw_description": "TOMATE CHERRY",
                "quantity": "1",
                "unit": "un",
                "unit_price_cents": 249,
                "line_total_cents": 249,
                "discount_cents": 50,
            },
            {
                "raw_description": "QUEIJO FRESCO",
                "quantity": "1",
                "unit": "un",
                "unit_price_cents": 179,
                "line_total_cents": 179,
                "discount_cents": 0,
            },
            {
                "raw_description": "DETERGENTE LOICA",
                "quantity": "1",
                "unit": "un",
                "unit_price_cents": 329,
                "line_total_cents": 329,
                "discount_cents": 0,
            },
        ],
    }
    write_case("lidl_text", "document.pdf", lidl_expected, lambda p: write_text_pdf(p, lidl_lines))

    # --- Continente (layout of the real "Fatura Simplificada"; items are fictional) ---------
    cont_lines = [
        "Modelo Continente Hipermercados, SA",
        "NIF: PT502011475",
        "Fatura Simplificada Original",
        "Nro:FS CNP203/100001 20/09/2026 11:05",
        "NIF: Consumidor Final",
        "IVA DESCRICAO VALOR",
        "Mercearia:",
        "(A) LEITE MEIO GORDO 0,89",
        "(A) PAO DE FORMA 1,59",
        "(A) IOGURTE NATURAL 4X125G",
        "3 X 0,99 2,97",
        "POUPANCA 0,60",
        "Frutas:",
        "(A) MACAS GALA",
        "1,20 kg X 2,49 EUR/kg 2,99",
        "SUBTOTAL 8,44",
        "Desconto Cartao Utilizado 2,00",
        "TOTAL A PAGAR 6,44",
        "Total de descontos e poupancas 0,60",
        "%IVA Total Liq. IVA Total",
        "(A) 6,00% 4,06 0,24 4,30",
    ]
    cont_expected = {
        "merchant": "CONTINENTE",
        "route": "PDF_TEXT",
        "purchase_at": "2026-09-20T11:05:00+01:00",
        "total_cents": 844,
        "status": "COMPLETED",
        "lines": [
            {"raw_description": "LEITE MEIO GORDO", "line_total_cents": 89},
            {"raw_description": "PAO DE FORMA", "line_total_cents": 159},
            {
                "raw_description": "IOGURTE NATURAL 4X125G",
                "quantity": "3",
                "unit": "un",
                "unit_price_cents": 119,
                "line_total_cents": 357,
                "discount_cents": 60,
            },
            {
                "raw_description": "MACAS GALA",
                "quantity": "1.20",
                "unit": "kg",
                "unit_price_cents": 249,
                "line_total_cents": 299,
            },
        ],
    }
    write_case("continente_text", "document.pdf", cont_expected, lambda p: write_text_pdf(p, cont_lines))

    # --- Auchan (layout of the real "Fatura"; items are fictional) --------------------------
    auchan_lines = [
        "AUCHAN RETAIL PORTUGAL, S.A.",
        "Contribuinte Nr 503003808",
        "FATURA",
        "No.4111032026100001/001/10",
        "IVA ARTIGO/QT/LI PRECO VALOR",
        "Mercearia",
        "I1 ARROZ AGULHA 1KG 1,29",
        "I1 LEITE MEIO GORDO",
        "3 X 0,85 2,55",
        "Perfumaria",
        "I2 SABONETE LIQUIDO 300ML 2,49",
        "4 volumes 6,33",
        "TOTAL PAGO 6,33",
        "Multibanco 6,33",
        "TROCO 0,00",
        "RESUMO DO IVA",
        "TAXA BASE VALOR",
        "IVA Liquidado",
        "6,00% (I1) 3,62 0,22",
        "23,00% (I2) 2,02 0,47",
        "TOTAL 5,64 0,69",
        "Data Emissao : 2026-09-14 16:20",
    ]
    auchan_expected = {
        "merchant": "AUCHAN",
        "route": "PDF_TEXT",
        "purchase_at": "2026-09-14T16:20:00+01:00",
        "total_cents": 633,
        "subtotal_cents": 564,
        "tax_cents": 69,
        "status": "COMPLETED",
        "lines": [
            {
                "raw_description": "ARROZ AGULHA 1KG",
                "quantity": "1",
                "unit": "un",
                "unit_price_cents": 129,
                "line_total_cents": 129,
                "discount_cents": 0,
            },
            {
                "raw_description": "LEITE MEIO GORDO",
                "quantity": "3",
                "unit": "un",
                "unit_price_cents": 85,
                "line_total_cents": 255,
                "discount_cents": 0,
            },
            {
                "raw_description": "SABONETE LIQUIDO 300ML",
                "quantity": "1",
                "unit": "un",
                "unit_price_cents": 249,
                "line_total_cents": 249,
                "discount_cents": 0,
            },
        ],
    }
    write_case("auchan_text", "document.pdf", auchan_expected, lambda p: write_text_pdf(p, auchan_lines))

    # --- Unknown merchant -> user must choose ------------------------------------------------
    unknown_lines = ["MERCEARIA DO BAIRRO", "Data: 21/09/2026 09:00", "PAO 0,50", "LEITE 0,89", "TOTAL 1,39"]
    unknown_expected = {
        "merchant": "UNKNOWN",
        "route": "PDF_TEXT",
        "status": "REVIEW_REQUIRED",
        "review_codes": ["merchant_unknown"],
    }
    write_case(
        "unknown_merchant", "document.pdf", unknown_expected, lambda p: write_text_pdf(p, unknown_lines)
    )

    # --- Image-only documents (need OCR) ----------------------------------------------------
    write_case(
        "pingo_doce_scanned",
        "document.pdf",
        {**pingo_expected, "route": "LOCAL_OCR", "requires_ocr": True},
        lambda p: write_scanned_pdf(p, lines),
    )
    write_case(
        "lidl_photo",
        "document.png",
        {**lidl_expected, "route": "LOCAL_OCR", "requires_ocr": True, "status": None},
        lambda p: render_image(lidl_lines, quality="medium").save(p),
    )

    write_mutations()

    # --- Negative fixtures for validation tests ---------------------------------------------
    NEG.mkdir(parents=True, exist_ok=True)
    good = (OUT / "pingo_doce_text" / "document.pdf").read_bytes()
    (NEG / "corrupted.pdf").write_bytes(good[: len(good) // 3])
    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(good)).pages:
        writer.add_page(page)
    writer.encrypt(user_password="secret", owner_password="owner")  # noqa: S106
    buf = io.BytesIO()
    writer.write(buf)
    (NEG / "encrypted.pdf").write_bytes(buf.getvalue())
    (NEG / "fake.pdf").write_bytes(b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff" + b"\x00" * 200)
    (NEG / "tiny.png").write_bytes(_png(Image.new("RGB", (40, 40), "white")))
    (NEG / "blank.png").write_bytes(_png(Image.new("RGB", (600, 900), "white")))
    (NEG / "dark.jpg").write_bytes(_jpeg(Image.new("RGB", (600, 900), (10, 10, 10))))
    print(f"fixtures written to {OUT} and {NEG}")


# --- Mutations of the real searchable-PDF layouts, with invented shops and products ---------------
# They are what the generic parser is judged on besides the real receipts (spec 009, A2.1): the same
# ideas as the real layouts (column table with a two-line header, description above its prices, savings
# before or after the line, card balance, voucher) moved to places the parser was not written for.


def _line(
    desc: str, qty: str, unit_cents: int, total_cents: int, discount: int = 0, unit: str = "un"
) -> dict:
    return {
        "raw_description": desc,
        "quantity": qty,
        "unit": unit,
        "unit_price_cents": unit_cents,
        "line_total_cents": total_cents,
        "discount_cents": discount,
    }


def _mutation(purchase_at: str, total: int, lines: list[dict]) -> dict:
    return {
        "merchant": "UNKNOWN",
        "route": "PDF_TEXT",
        "purchase_at": purchase_at,
        "total_cents": total,
        "lines": lines,
    }


def _euros(cents: int) -> str:
    return f"{cents // 100},{cents % 100:02d}"


def write_grid_pdf(path: Path, rows: list[list[tuple[float, str, str]]], width_mm: float = 170) -> None:
    """Each row is a list of (x in mm, text, "l" or "r" for left or right aligned at x)."""
    buf = io.BytesIO()
    height = (len(rows) * 5 + 30) * mm
    c = canvas.Canvas(buf, pagesize=(width_mm * mm, height))
    c.setFont("Helvetica", 8)
    y = height - 15 * mm
    for row in rows:
        for x, text, align in row:
            (c.drawRightString if align == "r" else c.drawString)(x * mm, y, text)
        y -= 5 * mm
    c.showPage()
    c.save()
    path.write_bytes(buf.getvalue())


def _grid_case() -> tuple[list[list[tuple[float, str, str]]], dict]:
    """Column order moved (quantity before price, total before the VAT class) and a header on two lines."""
    items = [
        ("Pão de forma integral", 2, 129, "A"),
        ("Leite meio gordo 1L", 6, 79, "A"),
        ("Detergente roupa 2L", 1, 699, "C"),
        ("Azeite virgem extra", 1, 849, "A"),
    ]
    rows: list[list[tuple[float, str, str]]] = [
        [(10, "Mercearia Aurora, Lda", "l")],
        [(10, "Data: 2026-10-02 11:20", "l")],
        [],
        [
            (10, "Artigo", "l"),
            (95, "Qtd.", "r"),
            (112, "Preço", "r"),
            (135, "Total", "r"),
            (158, "Taxa", "r"),
        ],
        [(112, "unitário", "r"), (158, "IVA", "r")],
    ]
    lines = []
    total = 0
    for name, qty, unit, tax in items:
        line_total = qty * unit
        total += line_total
        rows.append(
            [
                (10, name, "l"),
                (95, str(qty), "r"),
                (112, _euros(unit), "r"),
                (113, "€", "l"),
                (135, _euros(line_total), "r"),
                (136, "€", "l"),
                (158, tax, "r"),
            ]
        )
        lines.append(_line(name, str(qty), unit, line_total))
    rows += [[], [(120, "TOTAL", "l"), (150, _euros(total), "r"), (151, "€", "l")]]
    rows += [[(10, "Multibanco", "l"), (150, _euros(total), "r"), (151, "€", "l")]]
    return rows, _mutation("2026-10-02T11:20:00+01:00", total, lines)


def write_mutations() -> None:
    rows, expected = _grid_case()
    write_case("mutations/colunas_ordem_trocada", "document.pdf", expected, lambda p: write_grid_pdf(p, rows))

    # Description above its "qty X price total" line, no VAT tag on some, section titles, savings after the
    # line, prices already net of the saving, and a card balance used as payment.
    text = [
        "Mini-Mercado Aurora",
        "Fatura Simplificada Original",
        "Nro:FS AB1/000123 04/10/2026 17:05",
        "NIF: Consumidor Final",
        "DESCRICAO VALOR",
        "Higiene:",
        "GEL DUCHE FRESCO 250 ML",
        "2 X 2,49 4,98",
        "POUPANCA 1,00",
        "Padaria:",
        "(A) BROA DE MILHO 400G 1,89",
        "Frutas:",
        "(A) MACA GALA",
        "3 X 0,95 2,85",
        "SUBTOTAL 9,72",
        "Desconto Cartao Utilizado 5,00",
        "TOTAL A PAGAR 4,72",
        "Multibanco 4,72",
    ]
    expected = _mutation(
        "2026-10-04T17:05:00+01:00",
        972,
        [
            _line("GEL DUCHE FRESCO 250 ML", "2", 299, 598, 100),
            _line("BROA DE MILHO 400G", "1", 189, 189),
            _line("MACA GALA", "3", 95, 285),
        ],
    )
    write_case(
        "mutations/duas_linhas_poupanca_depois", "document.pdf", expected, lambda p: write_text_pdf(p, text)
    )

    # Each saving is printed before its line, prices as on the shelf: gross 10,62, net 8,92.
    text = [
        "Loja do Bairro, Lda",
        "Artigos",
        "LATICINIOS",
        "      Poupanca Imediata (0,50)",
        " C IOGURTE GREGO 4X125G 2,19",
        "TALHO",
        "      Poupanca Imediata (1,20)",
        " C FRANGO INTEIRO 0,950 X 4,99 4,74",
        "MERCEARIA",
        " C ARROZ AGULHA 1KG 1,39",
        " E SUMO LARANJA 1L 2 X 1,15 2,30",
        "Resumo",
        "TOTAL 10,62",
        "TOTAL POUPANCA (1,70)",
        "TOTAL A PAGAR 8,92",
        "Pagamentos",
        "Multibanco 8,92",
        "000001 2026-10-05 10:15 0001 0001 0001",
    ]
    expected = _mutation(
        "2026-10-05T10:15:00+01:00",
        892,
        [
            _line("IOGURTE GREGO 4X125G", "1", 219, 219, 50),
            _line("FRANGO INTEIRO", "0.950", 499, 474, 120, "kg"),
            _line("ARROZ AGULHA 1KG", "1", 139, 139),
            _line("SUMO LARANJA 1L", "2", 115, 230),
        ],
    )
    write_case("mutations/poupanca_antes", "document.pdf", expected, lambda p: write_text_pdf(p, text))

    # Gross and net both fit the lines (25,40 and 20,00): only what was paid (card + voucher) decides.
    text = [
        "Supermercado Estrela",
        "Artigos",
        " C QUEIJO FLAMENGO FATIAS 4,50",
        "      Poupanca Imediata (1,50)",
        " C CAFE MOIDO 250G 2 X 5,95 11,90",
        "      Poupanca Imediata (3,90)",
        " C BOLACHAS MARIA 3 X 1,00 3,00",
        " C PRESUNTO FATIADO 6,00",
        "Resumo",
        "TOTAL 25,40",
        "TOTAL POUPANCA (5,40)",
        "TOTAL A PAGAR 20,00",
        "Pagamentos",
        "Multibanco 15,00",
        "Vale desconto 5,00",
        "000002 2026-10-06 18:30 0001 0001 0001",
    ]
    expected = _mutation(
        "2026-10-06T18:30:00+01:00",
        2000,
        [
            _line("QUEIJO FLAMENGO FATIAS", "1", 450, 450, 150),
            _line("CAFE MOIDO 250G", "2", 595, 1190, 390),
            _line("BOLACHAS MARIA", "3", 100, 300),
            _line("PRESUNTO FATIADO", "1", 600, 600),
        ],
    )
    write_case("mutations/empate_bruto_liquido", "document.pdf", expected, lambda p: write_text_pdf(p, text))

    # No header, no VAT tag, one label ("TOTAL"), a price on a row of its own and cash with change.
    text = [
        "Frutaria Sol",
        "Fatura Simplificada",
        "04/10/2026 09:41",
        "BANANAS 1,200 X 1,49 1,79",
        "MACAS GALA 0,850 X 2,20 1,87",
        "LIMOES 3 X 0,35 1,05",
        "ALFACE",
        "1,20",
        "TOTAL 5,91",
        "Numerario 10,00",
        "Troco 4,09",
    ]
    expected = _mutation(
        "2026-10-04T09:41:00+01:00",
        591,
        [
            _line("BANANAS", "1.200", 149, 179, unit="kg"),
            _line("MACAS GALA", "0.850", 220, 187, unit="kg"),
            _line("LIMOES", "3", 35, 105),
            _line("ALFACE", "1", 120, 120),
        ],
    )
    write_case(
        "mutations/sem_cabecalho_sem_etiqueta", "document.pdf", expected, lambda p: write_text_pdf(p, text)
    )


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


if __name__ == "__main__":
    main()
