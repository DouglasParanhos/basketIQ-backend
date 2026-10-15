"""File validation on the real content (not the declared extension/MIME), spec técnica §5 passo 2."""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass

import filetype
from PIL import Image, UnidentifiedImageError
from pillow_heif import register_heif_opener
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from basketiq.domain.status import ReceiptStatus, SourceType

register_heif_opener()
Image.MAX_IMAGE_PIXELS = 60_000_000  # ~ 8000x7500; larger is not a phone photo

PDF_MIME = "application/pdf"
IMAGE_MIMES = frozenset({"image/jpeg", "image/png", "image/heic", "image/heif"})
ALLOWED_MIMES = IMAGE_MIMES | {PDF_MIME}
EXTENSIONS = {
    ".pdf": PDF_MIME,
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".heic": "image/heic",
    ".heif": "image/heif",
}
MIN_IMAGE_SIDE = 200


class DocumentRejectedError(Exception):
    def __init__(self, status: ReceiptStatus, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class ValidatedDocument:
    source_type: SourceType
    mime: str
    page_count: int


def source_type_for(mime: str) -> SourceType:
    return SourceType.PDF if mime == PDF_MIME else SourceType.IMAGE


def sniff_mime(data: bytes) -> str | None:
    kind = filetype.guess(data[:8192])
    if kind is None:
        return None
    return "image/heic" if kind.mime == "image/heif" else kind.mime


def validate_document(data: bytes, declared_mime: str, max_bytes: int, max_pages: int) -> ValidatedDocument:
    invalid = ReceiptStatus.INVALID_FORMAT
    if not data:
        raise DocumentRejectedError(invalid, "empty_file", "O ficheiro está vazio.")
    if len(data) > max_bytes:
        raise DocumentRejectedError(
            invalid, "file_too_large", "O ficheiro excede o tamanho máximo permitido."
        )
    real = sniff_mime(data)
    if real is None or real not in ALLOWED_MIMES:
        raise DocumentRejectedError(invalid, "unsupported_type", "Tipo de ficheiro não suportado.")
    declared = "image/heic" if declared_mime == "image/heif" else declared_mime
    if source_type_for(real) != source_type_for(declared):
        raise DocumentRejectedError(
            invalid, "type_mismatch", "O conteúdo do ficheiro não corresponde ao tipo indicado."
        )

    if real == PDF_MIME:
        pages = _validate_pdf(data, max_pages)
        return ValidatedDocument(SourceType.PDF, real, pages)
    _validate_image(data)
    return ValidatedDocument(SourceType.IMAGE, real, 1)


def _validate_pdf(data: bytes, max_pages: int) -> int:
    invalid = ReceiptStatus.INVALID_FORMAT
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            reader = PdfReader(io.BytesIO(data), strict=False)
            if reader.is_encrypted and not reader.decrypt(""):
                raise DocumentRejectedError(
                    invalid,
                    "pdf_encrypted",
                    "O PDF está protegido por palavra-passe. Exporte uma versão sem proteção.",
                )
            pages = len(reader.pages)
            if pages:
                reader.pages[0].extract_text()
    except DocumentRejectedError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
        raise DocumentRejectedError(
            invalid, "pdf_corrupted", "O PDF está corrompido. Escolha outro ficheiro."
        ) from exc
    if pages == 0:
        raise DocumentRejectedError(invalid, "pdf_empty", "O PDF não tem páginas.")
    if pages > max_pages:
        raise DocumentRejectedError(invalid, "too_many_pages", f"O PDF tem mais de {max_pages} páginas.")
    return pages


def _validate_image(data: bytes) -> None:
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            width, height = img.size
    except Image.DecompressionBombError as exc:
        raise DocumentRejectedError(
            ReceiptStatus.INVALID_FORMAT, "image_too_large", "A imagem é demasiado grande."
        ) from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise DocumentRejectedError(
            ReceiptStatus.INVALID_FORMAT,
            "image_corrupted",
            "A imagem está corrompida. Escolha outro ficheiro.",
        ) from exc
    if min(width, height) < MIN_IMAGE_SIDE:
        raise DocumentRejectedError(
            ReceiptStatus.UNREADABLE,
            "image_too_small",
            "A imagem tem resolução demasiado baixa para ser lida.",
        )
