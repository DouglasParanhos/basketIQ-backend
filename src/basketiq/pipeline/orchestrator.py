"""Processing pipeline. Order (spec técnica §2.1 and §11): validate → PDF text | local OCR →
classify → deterministic parser → local LLM for ambiguities → external fallback only when the
rules and the budget allow → review decision.

Pure with respect to I/O: the caller provides the bytes and the components, and persists the
outcome. This keeps the whole pipeline runnable in deterministic mode for tests.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from PIL import Image

from basketiq.domain.confidence import Evaluation, LineView, ReceiptView, Thresholds, evaluate
from basketiq.domain.status import Merchant, ProcessingRoute, ReceiptStatus, SourceType
from basketiq.pipeline.classify import Classification, classify
from basketiq.pipeline.external import ExternalExtractor, ExternalUnavailableError, fallback_reason
from basketiq.pipeline.layout import group_into_lines
from basketiq.pipeline.llm import AmbiguityResolver, LlmUnavailableError, apply_proposals
from basketiq.pipeline.ocr import OcrEngine
from basketiq.pipeline.ocr.base import OcrUnavailableError
from basketiq.pipeline.parsers import parser_for
from basketiq.pipeline.parsers.base import Parser, apply_deposit_quantities
from basketiq.pipeline.pdf_text import extract_pdf_text, has_usable_text
from basketiq.pipeline.preprocess import load_image, ocr_tiles, rasterize_pdf
from basketiq.pipeline.qr import (
    QR_BUDGET_S,
    AtQr,
    QrNote,
    QrReader,
    apply_qr,
    atcud_matches_print,
    read_at_qr,
)
from basketiq.pipeline.quality import image_warnings, ocr_warnings
from basketiq.pipeline.schema import DocumentText, ParsedReceipt, TextToken
from basketiq.pipeline.stitch import JoinNote, join_documents
from basketiq.pipeline.validation import DocumentRejectedError, validate_document

log = logging.getLogger(__name__)

OCR_QUALITY_THRESHOLD = 0.75


class TemporaryProcessingError(Exception):
    """Retryable failure (OCR crash, storage hiccup). The job is retried idempotently."""


@dataclass
class PipelineDeps:
    ocr: OcrEngine
    resolver: AmbiguityResolver | None
    external: ExternalExtractor | None
    external_budget_remaining_cents: int
    thresholds_for: Callable[[Merchant], Thresholds]
    merchant_enabled: Callable[[Merchant], bool]
    max_bytes: int
    max_pages: int
    external_timeout: float = 30.0
    # Seams for the evaluation harness (unseen-layout mode); production keeps the defaults.
    classifier: Callable[[DocumentText, Merchant | None, str | None], Classification] = classify
    parser_factory: Callable[[Merchant], Parser] = parser_for
    qr_reader: QrReader = read_at_qr


@dataclass
class PipelineRequest:
    data: bytes
    declared_mime: str
    merchant_hint: Merchant | None = None
    external_requested: bool = False
    # More photos of the same (long) receipt, after `data`: (bytes, declared mime) in photographing order.
    extra: list[tuple[bytes, str]] = field(default_factory=list)


@dataclass
class PipelineOutcome:
    status: ReceiptStatus
    error_code: str | None = None
    error_message: str | None = None
    source_type: SourceType | None = None
    page_count: int = 0
    parsed: ParsedReceipt | None = None
    evaluation: Evaluation | None = None
    merchant_confidence: float = 0.0
    merchant_hint_conflict: bool = False
    route: ProcessingRoute = ProcessingRoute.NONE
    ocr_engine: str | None = None
    ocr_mean_confidence: float | None = None
    llm_used: bool = False
    model_version: str | None = None
    external_provider: str | None = None
    external_reason: str | None = None
    estimated_cost_cents: int = 0
    quality_warnings: list[str] = field(default_factory=list)
    join_notes: list[JoinNote] = field(default_factory=list)
    qr: AtQr | None = None
    qr_notes: list[QrNote] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stage_ms: dict[str, int] = field(default_factory=dict)


class _Timer:
    def __init__(self, sink: dict[str, int]) -> None:
        self.sink = sink

    def __call__(self, name: str) -> _Stage:
        return _Stage(self.sink, name)


class _Stage:
    def __init__(self, sink: dict[str, int], name: str) -> None:
        self.sink, self.name = sink, name

    def __enter__(self) -> None:
        self.start = time.perf_counter()

    def __exit__(self, *exc: object) -> None:
        self.sink[self.name] = int((time.perf_counter() - self.start) * 1000)


def run_pipeline(request: PipelineRequest, deps: PipelineDeps) -> PipelineOutcome:
    out = PipelineOutcome(status=ReceiptStatus.EXTRACTING)
    stage = _Timer(out.stage_ms)

    with stage("validate"):
        try:
            validated = validate_document(request.data, request.declared_mime, deps.max_bytes, deps.max_pages)
        except DocumentRejectedError as rejected:
            out.status, out.error_code, out.error_message = rejected.status, rejected.code, rejected.message
            return out
    out.source_type, out.page_count = validated.source_type, validated.page_count

    for data, mime in request.extra:
        with stage("validate"):
            try:
                extra = validate_document(data, mime, deps.max_bytes, deps.max_pages)
            except DocumentRejectedError as rejected:
                out.status, out.error_code, out.error_message = (
                    rejected.status,
                    rejected.code,
                    rejected.message,
                )
                return out
        if extra.source_type != SourceType.IMAGE or validated.source_type != SourceType.IMAGE:
            out.status, out.error_code = ReceiptStatus.INVALID_FORMAT, "photos_must_be_images"
            out.error_message = "Só é possível juntar fotografias. Para um PDF envie apenas esse ficheiro."
            return out
        out.page_count += 1

    with stage("text"):
        docs = [_extract_text(request.data, validated.source_type, deps.ocr, out)]
        docs += [_extract_text(data, SourceType.IMAGE, deps.ocr, out) for data, _ in request.extra]
        doc, out.join_notes = join_documents(docs)

    with stage("qr"):
        out.qr = _find_qr(request, validated.source_type, deps)

    with stage("classify"):
        classification = deps.classifier(doc, request.merchant_hint, out.qr.issuer_nif if out.qr else None)
    out.merchant_confidence = classification.confidence
    out.merchant_hint_conflict = classification.hint_conflict
    merchant = classification.merchant
    if merchant != Merchant.UNKNOWN and not deps.merchant_enabled(merchant):
        out.status, out.error_code = ReceiptStatus.UNSUPPORTED_MERCHANT, "merchant_disabled"
        out.error_message = "Este supermercado não está disponível de momento."
        return out

    with stage("parse"):
        parsed = deps.parser_factory(merchant).parse(doc)
        apply_deposit_quantities(parsed.lines)
        parsed.field_confidence["merchant"] = classification.confidence

    if deps.resolver is not None:
        with stage("llm"):
            _resolve_ambiguities(parsed, deps.resolver, out)

    with stage("external"):
        parsed = _maybe_external(request, deps, parsed, merchant, out)

    if out.qr is not None:
        out.qr_notes = apply_qr(parsed, out.qr, doc.lines)
        if atcud_matches_print(doc, out.qr) is False:
            out.notes.append("atcud_printed_mismatch")

    if not doc.lines and not parsed.lines:
        out.status, out.error_code = ReceiptStatus.UNREADABLE, "no_text"
        out.error_message = "Não foi possível ler texto no documento."
        return out

    with stage("decide"):
        thresholds = deps.thresholds_for(parsed.merchant)
        evaluation = evaluate(
            view_from_parsed(parsed, out.merchant_hint_conflict, out.join_notes, out.qr_notes), thresholds
        )
    out.parsed, out.evaluation = parsed, evaluation
    out.status = ReceiptStatus.REVIEW_REQUIRED if evaluation.reasons else ReceiptStatus.COMPLETED
    return out


def _extract_text(data: bytes, source_type: SourceType, ocr: OcrEngine, out: PipelineOutcome) -> DocumentText:
    if source_type == SourceType.PDF:
        doc = extract_pdf_text(data)
        if has_usable_text(doc):
            out.route, out.ocr_engine = ProcessingRoute.PDF_TEXT, doc.engine
            return doc
        images = rasterize_pdf(data)  # scanned PDF: no usable text layer
    else:
        images = [load_image(data)]
        out.quality_warnings.extend(w for w in image_warnings(images[0]) if w not in out.quality_warnings)

    tokens: list[TextToken] = []
    try:
        for page, image in enumerate(images):
            tokens.extend(_recognize_page(ocr, image, page))
    except OcrUnavailableError as exc:
        raise TemporaryProcessingError("OCR unavailable") from exc
    except Exception as exc:
        log.warning("ocr_failed", extra={"error_type": type(exc).__name__})
        raise TemporaryProcessingError("OCR failed") from exc
    doc = DocumentText(
        lines=group_into_lines(tokens),
        route=ProcessingRoute.LOCAL_OCR,
        engine=ocr.name,
        page_count=len(images),
    )
    out.route, out.ocr_engine = ProcessingRoute.LOCAL_OCR, ocr.name
    out.ocr_mean_confidence = round(doc.mean_confidence, 3)
    out.quality_warnings.extend(ocr_warnings(len(tokens), doc.mean_confidence))
    return doc


def _recognize_page(ocr: OcrEngine, image: Image.Image, page: int) -> list[TextToken]:
    """OCR one page slice by slice and put every token back at its place on the page."""
    tokens: list[TextToken] = []
    for tile in ocr_tiles(image):
        for token in ocr.recognize(tile.image, page=page):
            centre = tile.top + (token.y0 + token.y1) / 2
            if tile.own_top <= centre < tile.own_bottom:
                tokens.append(token.model_copy(update={"y0": token.y0 + tile.top, "y1": token.y1 + tile.top}))
    return tokens


def _resolve_ambiguities(parsed: ParsedReceipt, resolver: AmbiguityResolver, out: PipelineOutcome) -> None:
    indexes = [i for i, line in enumerate(parsed.lines) if line.unresolved]
    if not indexes:
        return  # the deterministic parser resolved everything: no LLM call
    try:
        proposals = resolver.propose(parsed.merchant, [" ".join(parsed.lines[i].evidence) for i in indexes])
    except LlmUnavailableError:
        out.notes.append("llm_unavailable")
        return
    out.llm_used = True
    out.model_version = resolver.model_version
    out.route = ProcessingRoute.LOCAL_LLM
    resolved = apply_proposals(parsed.lines, indexes, proposals)
    out.notes.append(f"llm_resolved:{resolved}/{len(indexes)}")


def _maybe_external(
    request: PipelineRequest,
    deps: PipelineDeps,
    parsed: ParsedReceipt,
    merchant: Merchant,
    out: PipelineOutcome,
) -> ParsedReceipt:
    reason = fallback_reason(
        ocr_mean_confidence=out.ocr_mean_confidence,
        ocr_threshold=OCR_QUALITY_THRESHOLD,
        merchant=merchant,
        parsed_lines=len(parsed.lines),
        user_requested=request.external_requested,
    )
    if reason is None:
        return parsed
    out.external_reason = reason
    if deps.external is None:
        out.notes.append("external_not_allowed")
        return parsed
    if deps.external_budget_remaining_cents < deps.external.cost_per_call_cents:
        out.notes.append("external_budget_exhausted")
        return parsed
    out.external_provider = deps.external.name
    out.estimated_cost_cents = deps.external.cost_per_call_cents
    try:
        candidate = deps.external.extract(request.data, request.declared_mime, deps.external_timeout)
    except (ExternalUnavailableError, TimeoutError):
        out.notes.append("external_unavailable")
        return parsed
    out.route = ProcessingRoute.EXTERNAL_FALLBACK
    if len(candidate.lines) > len(parsed.lines) or (
        merchant == Merchant.UNKNOWN and candidate.merchant != Merchant.UNKNOWN
    ):
        return candidate
    return parsed


def _find_qr(request: PipelineRequest, source_type: SourceType, deps: PipelineDeps) -> AtQr | None:
    """The fiscal QR of the receipt, from any of its files, within one budget for all of them."""
    documents = [(request.data, source_type), *((data, SourceType.IMAGE) for data, _ in request.extra)]
    remaining = QR_BUDGET_S
    for data, kind in documents:
        started = time.perf_counter()
        if qr := deps.qr_reader(data, kind, remaining):
            return qr
        remaining -= time.perf_counter() - started
        if remaining <= 0:
            break
    return None


def view_from_parsed(
    parsed: ParsedReceipt,
    hint_conflict: bool,
    join_notes: list[JoinNote] | None = None,
    qr_notes: list[QrNote] | None = None,
) -> ReceiptView:
    return ReceiptView(
        join_notes=list(join_notes or []),
        qr_notes=list(qr_notes or []),
        merchant=parsed.merchant,
        purchase_at=parsed.purchase_at,
        total_cents=parsed.total_cents,
        subtotal_cents=parsed.subtotal_cents,
        tax_cents=parsed.tax_cents,
        field_confidence=parsed.field_confidence,
        lines=[
            LineView(
                raw_description=line.raw_description,
                quantity=line.quantity,
                line_total_cents=line.line_total_cents,
                discount_cents=line.discount_cents,
                field_confidence=line.field_confidence,
                unresolved=line.unresolved,
            )
            for line in parsed.lines
        ],
        merchant_hint_conflict=hint_conflict,
    )
