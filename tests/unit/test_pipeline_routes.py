"""Processing routes and cost policy.

Plano de testes §8: PDF_TEXT, LOCAL_OCR, LOCAL_LLM and EXTERNAL_FALLBACK.
"""

from __future__ import annotations

import pytest

from basketiq.domain.confidence import Thresholds
from basketiq.domain.status import Merchant, ProcessingRoute, ReceiptStatus
from basketiq.pipeline.external import ExternalUnavailableError
from basketiq.pipeline.llm import LlmProposals, LlmUnavailableError
from basketiq.pipeline.llm.base import LlmLineProposal
from basketiq.pipeline.orchestrator import (
    PipelineDeps,
    PipelineRequest,
    TemporaryProcessingError,
    run_pipeline,
)
from basketiq.pipeline.schema import ParsedLine, ParsedReceipt
from tests.helpers import (
    PINGO_LINES,
    PINGO_TOTAL,
    FakeExternal,
    FakeOcr,
    FakeResolver,
    make_pdf,
    make_png,
    synthetic,
)


def deps(ocr=None, resolver=None, external=None, budget: int = 0, enabled: bool = True) -> PipelineDeps:  # type: ignore[no-untyped-def]
    return PipelineDeps(
        ocr=ocr or FakeOcr([]),
        resolver=resolver,
        external=external,
        external_budget_remaining_cents=budget,
        thresholds_for=lambda _m: Thresholds(),
        merchant_enabled=lambda _m: enabled,
        max_bytes=15 * 1024 * 1024,
        max_pages=5,
    )


@pytest.mark.req("F-26")
def test_searchable_pdf_uses_text_without_ocr() -> None:
    ocr = FakeOcr(["should not be used"])
    out = run_pipeline(PipelineRequest(make_pdf(PINGO_LINES), "application/pdf"), deps(ocr=ocr))
    assert out.route == ProcessingRoute.PDF_TEXT
    assert ocr.calls == 0
    assert out.parsed is not None and out.parsed.total_cents == PINGO_TOTAL


@pytest.mark.req("F-27")
def test_scanned_pdf_runs_local_ocr_before_any_external_call() -> None:
    ocr, external = FakeOcr(PINGO_LINES), FakeExternal()
    out = run_pipeline(
        PipelineRequest(synthetic("pingo_doce_scanned"), "application/pdf"),
        deps(ocr=ocr, external=external, budget=100),
    )
    assert out.route == ProcessingRoute.LOCAL_OCR
    assert ocr.calls == 1
    assert external.calls == 0


@pytest.mark.req("F-28")
@pytest.mark.req("F-15a")
def test_confident_local_ocr_resolves_without_llm_or_external() -> None:
    resolver, external = FakeResolver(), FakeExternal()
    out = run_pipeline(
        PipelineRequest(make_png(), "image/png"),
        deps(ocr=FakeOcr(PINGO_LINES, conf=0.97), resolver=resolver, external=external, budget=100),
    )
    assert out.status == ReceiptStatus.COMPLETED
    assert resolver.calls == [] and external.calls == 0
    assert out.route == ProcessingRoute.LOCAL_OCR


@pytest.mark.req("F-29")
def test_ambiguous_line_goes_to_local_llm_and_is_validated() -> None:
    lines = [*PINGO_LINES[:5], "?? LEITE CHOCOLATE 0,99", *PINGO_LINES[5:]]
    lines[lines.index("TOTAL A PAGAR 8,81")] = "TOTAL A PAGAR 9,80"
    lines[lines.index("TOTAL 9,11")] = "TOTAL 10,10"
    proposals = LlmProposals(
        lines=[
            LlmLineProposal(
                line_index=0, description="LEITE CHOCOLATE", line_total="0,99", quantity=None, is_product=True
            )
        ]
    )
    resolver = FakeResolver(proposals)
    out = run_pipeline(PipelineRequest(make_pdf(lines), "application/pdf"), deps(resolver=resolver))
    assert resolver.calls == [["?? LEITE CHOCOLATE 0,99"]]  # only the ambiguous line is sent
    assert out.route == ProcessingRoute.LOCAL_LLM and out.llm_used
    llm_line = next(line for line in out.parsed.lines if line.resolved_by == "llm")  # type: ignore[union-attr]
    assert llm_line.line_total_cents == 99
    assert llm_line.quantity is None  # no evidence for a quantity: stays pending, never invented
    assert out.status == ReceiptStatus.REVIEW_REQUIRED


@pytest.mark.req("F-29")
def test_llm_quantity_with_multiplication_sign_is_verified() -> None:
    # Observed with qwen2.5:3b on Ollama: quantity returned as "2 X".
    from basketiq.pipeline.llm import apply_proposals
    from basketiq.pipeline.schema import ParsedLine

    text = "?? QUEIJO FLAMENGO FATIAS 2 X 1,49 2,98"
    line = ParsedLine(raw_description=text, evidence=[text], unresolved=True)
    proposal = LlmLineProposal(
        line_index=0,
        description="QUEIJO FLAMENGO FATIAS",
        quantity="2 X",
        unit_price="1,49",
        line_total="2,98",
        is_product=True,
    )
    apply_proposals([line], [0], LlmProposals(lines=[proposal]))
    assert (line.quantity, line.unit, line.unit_price_cents, line.line_total_cents) == (2, "un", 149, 298)
    assert not line.unresolved and line.field_confidence["quantity"] == 0.85


@pytest.mark.req("F-29")
@pytest.mark.req("PT-6")
def test_llm_values_without_evidence_are_rejected() -> None:
    lines = ["Artigos", "C PAO 1,00", "XPTO ?? 2,50", "Resumo", "TOTAL A PAGAR 3,50", "2026-09-01 10:00"]
    invented = LlmProposals(
        lines=[
            LlmLineProposal(
                line_index=0,
                description="QUEIJO",
                line_total="9,99",
                quantity="3",
                unit_price="3,33",
                is_product=True,
            )
        ]
    )
    out = run_pipeline(
        PipelineRequest(make_pdf(["Pingo Doce", *lines]), "application/pdf"),
        deps(resolver=FakeResolver(invented)),
    )
    unresolved = [line for line in out.parsed.lines if line.unresolved]  # type: ignore[union-attr]
    assert unresolved and unresolved[0].line_total_cents == 250  # LLM proposal discarded


@pytest.mark.req("F-29")
def test_llm_unavailable_falls_back_to_review() -> None:
    lines = [
        "Pingo Doce",
        "Artigos",
        "C PAO 1,00",
        "XPTO ?? 2,50",
        "Resumo",
        "TOTAL A PAGAR 3,50",
        "2026-09-01 10:00",
    ]
    out = run_pipeline(
        PipelineRequest(make_pdf(lines), "application/pdf"),
        deps(resolver=FakeResolver(fail=LlmUnavailableError("down"))),
    )
    assert out.status == ReceiptStatus.REVIEW_REQUIRED
    assert "llm_unavailable" in out.notes


@pytest.mark.req("F-30")
def test_external_called_only_when_local_ocr_is_insufficient_and_allowed() -> None:
    external = FakeExternal(
        ParsedReceipt(
            merchant=Merchant.LIDL,
            total_cents=100,
            lines=[
                ParsedLine(raw_description="PAO", line_total_cents=100, field_confidence={"line_total": 0.9})
            ],
            parser_version="ext-1",
        )
    )
    out = run_pipeline(
        PipelineRequest(make_png(), "image/png"),
        deps(ocr=FakeOcr(["?? ##", "1,00 2,00"], conf=0.4), external=external, budget=100),
    )
    assert external.calls == 1
    assert out.route == ProcessingRoute.EXTERNAL_FALLBACK
    assert out.external_provider == "fake-external" and out.external_reason == "LOW_OCR_QUALITY"
    assert out.estimated_cost_cents == 5


@pytest.mark.req("F-31")
def test_budget_exhausted_means_no_paid_call() -> None:
    external = FakeExternal()
    out = run_pipeline(
        PipelineRequest(make_png(), "image/png"),
        deps(ocr=FakeOcr(["?? ##", "1,00 2,00"], conf=0.4), external=external, budget=4),
    )
    assert external.calls == 0
    assert "external_budget_exhausted" in out.notes
    assert out.status == ReceiptStatus.REVIEW_REQUIRED


@pytest.mark.req("F-32")
def test_external_unavailable_returns_to_manual_review() -> None:
    external = FakeExternal(fail=ExternalUnavailableError("timeout"))
    out = run_pipeline(
        PipelineRequest(make_png(), "image/png"),
        deps(ocr=FakeOcr(["?? ##", "1,00 2,00"], conf=0.4), external=external, budget=100),
    )
    assert external.calls == 1
    assert "external_unavailable" in out.notes
    assert out.status == ReceiptStatus.REVIEW_REQUIRED


@pytest.mark.req("F-30")
def test_external_disabled_by_default() -> None:
    out = run_pipeline(
        PipelineRequest(make_png(), "image/png"), deps(ocr=FakeOcr(["?? ##", "1,00 2,00"], conf=0.4))
    )
    assert "external_not_allowed" in out.notes and out.external_provider is None


@pytest.mark.req("F-15b")
def test_totals_mismatch_blocks_auto_confirmation() -> None:
    out = run_pipeline(PipelineRequest(synthetic("pingo_doce_mismatch"), "application/pdf"), deps())
    assert out.status == ReceiptStatus.REVIEW_REQUIRED
    assert "totals_mismatch" in {r.code for r in out.evaluation.reasons}  # type: ignore[union-attr]


@pytest.mark.req("F-10")
def test_unknown_merchant_requires_user_choice() -> None:
    out = run_pipeline(PipelineRequest(synthetic("unknown_merchant"), "application/pdf"), deps())
    assert out.status == ReceiptStatus.REVIEW_REQUIRED
    assert "merchant_unknown" in {r.code for r in out.evaluation.reasons}  # type: ignore[union-attr]


@pytest.mark.req("RF-admin")
def test_disabled_merchant_is_unsupported() -> None:
    out = run_pipeline(PipelineRequest(make_pdf(PINGO_LINES), "application/pdf"), deps(enabled=False))
    assert out.status == ReceiptStatus.UNSUPPORTED_MERCHANT


@pytest.mark.req("RF-05")
def test_blank_image_is_unreadable() -> None:
    out = run_pipeline(PipelineRequest(make_png(), "image/png"), deps(ocr=FakeOcr([])))
    assert out.status == ReceiptStatus.UNREADABLE


@pytest.mark.req("F-25")
def test_ocr_crash_is_temporary() -> None:
    with pytest.raises(TemporaryProcessingError):
        run_pipeline(
            PipelineRequest(make_png(), "image/png"), deps(ocr=FakeOcr([], fail=RuntimeError("segfault-ish")))
        )


@pytest.mark.req("RF-05")
def test_dark_image_gets_quality_warning_but_is_processed() -> None:
    out = run_pipeline(
        PipelineRequest(make_png(color=(10, 10, 10)), "image/png"), deps(ocr=FakeOcr(PINGO_LINES))
    )
    assert "too_dark" in out.quality_warnings
    assert out.status in (ReceiptStatus.COMPLETED, ReceiptStatus.REVIEW_REQUIRED)


def _receipt_on_a_mat(paper_width: int) -> bytes:
    """A white strip of paper, `paper_width` pixels wide, on a dark mat."""
    import io

    from PIL import Image, ImageDraw

    mat = Image.new("RGB", (paper_width * 3, paper_width * 5), (25, 25, 25))
    ImageDraw.Draw(mat).rectangle((paper_width, 20, paper_width * 2, paper_width * 5 - 20), fill="white")
    buf = io.BytesIO()
    mat.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.req("RF-05")
@pytest.mark.parametrize(("paper_width", "warned"), [(240, True), (900, False)])
def test_a_receipt_that_takes_few_pixels_of_the_photo_gets_a_resolution_warning(
    paper_width: int, warned: bool
) -> None:
    out = run_pipeline(
        PipelineRequest(_receipt_on_a_mat(paper_width), "image/png"), deps(ocr=FakeOcr(PINGO_LINES))
    )
    assert ("low_resolution" in out.quality_warnings) is warned
    assert out.status in (
        ReceiptStatus.COMPLETED,
        ReceiptStatus.REVIEW_REQUIRED,
    )  # a warning, never a rejection
