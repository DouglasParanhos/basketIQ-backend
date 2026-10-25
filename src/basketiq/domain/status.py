from __future__ import annotations

from enum import StrEnum


class Merchant(StrEnum):
    CONTINENTE = "CONTINENTE"
    PINGO_DOCE = "PINGO_DOCE"
    LIDL = "LIDL"
    AUCHAN = "AUCHAN"
    ALDI = "ALDI"
    MERCADONA = "MERCADONA"
    EL_CORTE_INGLES = "EL_CORTE_INGLES"
    UNKNOWN = "UNKNOWN"


SUPPORTED_MERCHANTS = (
    Merchant.CONTINENTE,
    Merchant.PINGO_DOCE,
    Merchant.LIDL,
    Merchant.AUCHAN,
    Merchant.ALDI,
    Merchant.MERCADONA,
    Merchant.EL_CORTE_INGLES,
)


class SourceType(StrEnum):
    PDF = "PDF"
    IMAGE = "IMAGE"


class ReceiptStatus(StrEnum):
    RECEIVED = "RECEIVED"
    VALIDATING = "VALIDATING"
    EXTRACTING = "EXTRACTING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    COMPLETED = "COMPLETED"
    CONFIRMED = "CONFIRMED"
    # Error states (spec funcional §9)
    INVALID_FORMAT = "INVALID_FORMAT"
    UNREADABLE = "UNREADABLE"
    UNSUPPORTED_MERCHANT = "UNSUPPORTED_MERCHANT"
    TEMPORARY_FAILURE = "TEMPORARY_FAILURE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"


ERROR_STATUSES = frozenset(
    {
        ReceiptStatus.INVALID_FORMAT,
        ReceiptStatus.UNREADABLE,
        ReceiptStatus.UNSUPPORTED_MERCHANT,
        ReceiptStatus.TEMPORARY_FAILURE,
        ReceiptStatus.PERMANENT_FAILURE,
    }
)
IN_PROGRESS_STATUSES = frozenset({ReceiptStatus.VALIDATING, ReceiptStatus.EXTRACTING})
EDITABLE_STATUSES = frozenset({ReceiptStatus.REVIEW_REQUIRED, ReceiptStatus.COMPLETED})
# Re-processing is allowed from anything that is neither confirmed nor currently running.
PROCESSABLE_STATUSES = frozenset(
    {
        ReceiptStatus.RECEIVED,
        ReceiptStatus.REVIEW_REQUIRED,
        ReceiptStatus.COMPLETED,
        ReceiptStatus.UNREADABLE,
        ReceiptStatus.TEMPORARY_FAILURE,
        ReceiptStatus.PERMANENT_FAILURE,
    }
)

# Recommended action shown to the user for each error state.
ERROR_ACTIONS: dict[ReceiptStatus, str] = {
    ReceiptStatus.INVALID_FORMAT: "Escolha um PDF, JPEG, PNG ou HEIC válido dentro dos limites indicados.",
    ReceiptStatus.UNREADABLE: "Tire uma nova fotografia com boa luz, a nota inteira visível e sem reflexos.",
    ReceiptStatus.UNSUPPORTED_MERCHANT: "As notas deste supermercado não estão disponíveis de momento.",
    ReceiptStatus.TEMPORARY_FAILURE: "Ocorreu uma falha temporária. Tente processar novamente.",
    ReceiptStatus.PERMANENT_FAILURE: (
        "Não foi possível processar este documento. Elimine-o e tente outro ficheiro."
    ),
}


class ConfirmedBy(StrEnum):
    AUTO = "AUTO"
    USER = "USER"


class LineReviewStatus(StrEnum):
    OK = "OK"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    EDITED = "EDITED"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY = "RETRY"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ProcessingRoute(StrEnum):
    PDF_TEXT = "PDF_TEXT"
    LOCAL_OCR = "LOCAL_OCR"
    LOCAL_LLM = "LOCAL_LLM"
    EXTERNAL_FALLBACK = "EXTERNAL_FALLBACK"
    NONE = "NONE"


class Unit(StrEnum):
    UNIT = "un"
    KG = "kg"
    G = "g"
    L = "l"
    ML = "ml"


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class ActorType(StrEnum):
    USER = "USER"
    SYSTEM = "SYSTEM"
    ADMIN = "ADMIN"
