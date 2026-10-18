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
