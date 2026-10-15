"""Real Agno + Ollama ambiguity resolution (docker compose --profile llm up; model pulled).

Whatever the model answers, the deterministic validation must hold: accepted values appear in
the receipt line, and nothing without evidence is filled in.
"""

from __future__ import annotations

import os

import httpx
import pytest

from basketiq.domain.money import parse_money_cents
from basketiq.pipeline.llm import apply_proposals
from basketiq.pipeline.llm.agno_ollama import AgnoOllamaResolver
from basketiq.pipeline.schema import ParsedLine

pytestmark = [pytest.mark.llm, pytest.mark.req("F-29")]

HOST = os.environ.get("BASKETIQ_TEST_OLLAMA_HOST", "http://localhost:11434")
MODEL = os.environ.get("BASKETIQ_OLLAMA_MODEL", "qwen2.5:3b-instruct")


@pytest.fixture(scope="module")
def resolver() -> AgnoOllamaResolver:
    try:
        tags = httpx.get(f"{HOST}/api/tags", timeout=3).json()
    except httpx.HTTPError:
        pytest.skip(f"Ollama not reachable at {HOST}")
    if not any(m["name"].startswith(MODEL) for m in tags.get("models", [])):
        pytest.skip(f"model {MODEL} not pulled")
    return AgnoOllamaResolver(HOST, MODEL, timeout=120)


def unresolved(text: str) -> ParsedLine:
    return ParsedLine(
        raw_description=text, evidence=[text], unresolved=True, field_confidence={"line_total": 0.3}
    )


def test_ambiguous_lines_are_resolved_only_with_evidence(resolver: AgnoOllamaResolver) -> None:
    lines = [
        unresolved("?? QUEIJO FLAMENGO FATIAS 2 X 1,49 2,98"),
        unresolved("TOTAL PAGO 12,40"),
    ]
    proposals = resolver.propose("PINGO_DOCE", [line.evidence[0] for line in lines])
    apply_proposals(lines, [0, 1], proposals)

    product, payment = lines
    print(f"\nLLM proposals: {proposals.model_dump()}\nproduct line after validation: {product.model_dump()}")
    if product.resolved_by == "llm":
        evidence = product.evidence[0]
        assert product.line_total_cents is not None
        assert f"{product.line_total_cents // 100},{product.line_total_cents % 100:02d}" in evidence
        if product.quantity is not None:
            assert product.unit_price_cents == parse_money_cents("1,49")
    # A payment line is never turned into a product with invented values.
    assert payment.resolved_by == "parser" and payment.unresolved
