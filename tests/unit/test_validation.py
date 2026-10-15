from __future__ import annotations

import pytest

from basketiq.domain.status import ReceiptStatus, SourceType
from basketiq.pipeline.validation import DocumentRejectedError, validate_document
from tests.helpers import FIXTURES, PINGO_LINES, make_pdf, make_png

MAX = 15 * 1024 * 1024


def rejected(data: bytes, mime: str, max_pages: int = 5) -> DocumentRejectedError:
    with pytest.raises(DocumentRejectedError) as exc:
        validate_document(data, mime, MAX, max_pages)
    return exc.value


def test_valid_pdf_and_image() -> None:
    assert validate_document(make_pdf(PINGO_LINES), "application/pdf", MAX, 5).source_type == SourceType.PDF
    assert validate_document(make_png(), "image/png", MAX, 5).source_type == SourceType.IMAGE


@pytest.mark.req("F-07")
def test_corrupted_pdf() -> None:
    err = rejected((FIXTURES / "corrupted.pdf").read_bytes(), "application/pdf")
    assert (err.status, err.code) == (ReceiptStatus.INVALID_FORMAT, "pdf_corrupted")


@pytest.mark.req("RF-03")
def test_password_protected_pdf() -> None:
    err = rejected((FIXTURES / "encrypted.pdf").read_bytes(), "application/pdf")
    assert err.code == "pdf_encrypted"


@pytest.mark.req("F-08")
def test_executable_with_pdf_extension_is_rejected_by_content() -> None:
    err = rejected((FIXTURES / "fake.pdf").read_bytes(), "application/pdf")
    assert err.code == "unsupported_type"


@pytest.mark.req("F-08")
def test_declared_type_must_match_content() -> None:
    assert rejected(make_png(), "application/pdf").code == "type_mismatch"


@pytest.mark.req("F-08")
def test_size_and_page_limits() -> None:
    with pytest.raises(DocumentRejectedError) as exc:
        validate_document(make_png(), "image/png", 10, 5)
    assert exc.value.code == "file_too_large"
    assert rejected(make_pdf(PINGO_LINES), "application/pdf", max_pages=0).code == "too_many_pages"
    assert rejected(b"", "image/png").code == "empty_file"


@pytest.mark.req("RF-05")
def test_tiny_image_is_unreadable() -> None:
    err = rejected((FIXTURES / "tiny.png").read_bytes(), "image/png")
    assert (err.status, err.code) == (ReceiptStatus.UNREADABLE, "image_too_small")


@pytest.mark.req("RF-04")
def test_heic_is_accepted() -> None:
    import io

    from PIL import Image
    from pillow_heif import register_heif_opener

    register_heif_opener()
    buf = io.BytesIO()
    Image.new("RGB", (400, 600), "white").save(buf, format="HEIF")
    assert validate_document(buf.getvalue(), "image/heic", MAX, 5).mime == "image/heic"
