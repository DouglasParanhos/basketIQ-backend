"""Direct text extraction from searchable PDFs (route PDF_TEXT, no OCR)."""

from __future__ import annotations

import io
import re

import pdfplumber

from basketiq.domain.status import ProcessingRoute
from basketiq.pipeline.layout import group_into_lines
from basketiq.pipeline.schema import DocumentText, TextToken

MIN_ALNUM_CHARS = 30
_AMOUNT = re.compile(r"\d+,\d{2}")


def extract_pdf_text(data: bytes) -> DocumentText:
    tokens: list[TextToken] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        page_count = len(pdf.pages)
        for page_index, page in enumerate(pdf.pages):
            # Bold text drawn twice at the same spot would read "EEUURR/kg" (Lidl e-invoices).
            for word in page.dedupe_chars().extract_words(keep_blank_chars=False, use_text_flow=False):
                tokens.append(
                    TextToken(
                        text=word["text"],
                        x0=word["x0"],
                        y0=word["top"],
                        x1=word["x1"],
                        y1=word["bottom"],
                        confidence=1.0,
                        page=page_index,
                    )
                )
    return DocumentText(
        lines=group_into_lines(tokens),
        route=ProcessingRoute.PDF_TEXT,
        engine="pdfplumber",
        page_count=page_count,
    )


def has_usable_text(doc: DocumentText) -> bool:
    """A scanned PDF has no (or almost no) text layer; then OCR is needed."""
    text = doc.text
    alnum = sum(c.isalnum() for c in text)
    return alnum >= MIN_ALNUM_CHARS and len(_AMOUNT.findall(text)) >= 2
