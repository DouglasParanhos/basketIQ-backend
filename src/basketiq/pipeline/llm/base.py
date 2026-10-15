"""Ambiguity resolution with a local LLM (route LOCAL_LLM).

The LLM only proposes values for lines the deterministic parser could not interpret. Every
proposal is validated here, in code: amounts must appear verbatim in the source line, and
quantity × unit price must match the line total. Anything without evidence becomes null.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from basketiq.domain.money import multiply_cents, parse_decimal, parse_money_cents
from basketiq.domain.status import Unit
from basketiq.pipeline.schema import ParsedLine

LLM_VERIFIED = 0.85  # values present in the evidence and arithmetically consistent
LLM_UNVERIFIED = 0.6  # values present in the evidence, consistency not checkable


class LlmLineProposal(BaseModel):
    """Structured output requested from the model for one ambiguous line."""

    line_index: int
    description: str | None = Field(default=None, description="Product name exactly as printed")
    quantity: str | None = Field(default=None, description="Quantity as printed, e.g. '2' or '0,456'")
    unit: Literal["un", "kg", "g", "l", "ml"] | None = None
    unit_price: str | None = Field(default=None, description="Unit price as printed, e.g. '1,65'")
    line_total: str | None = Field(default=None, description="Line total as printed, e.g. '3,30'")
    is_product: bool = Field(description="False for totals, payments, headers or noise")


class LlmProposals(BaseModel):
    lines: list[LlmLineProposal]


class AmbiguityResolver(Protocol):
    name: str
    model_version: str

    def propose(self, merchant: str, lines: list[str]) -> LlmProposals: ...


class LlmUnavailableError(RuntimeError):
    pass


def apply_proposals(lines: list[ParsedLine], indexes: list[int], proposals: LlmProposals) -> int:
    """Validate proposals against the evidence and update unresolved lines in place.

    Returns how many lines were resolved."""
    resolved = 0
    by_index = {p.line_index: p for p in proposals.lines}
    for position, line_idx in enumerate(indexes):
        proposal = by_index.get(position)
        line = lines[line_idx]
        if proposal is None:
            continue
        evidence = " ".join(line.evidence)
        if not proposal.is_product:
            continue
        total = _verified_amount(proposal.line_total, evidence)
        unit_price = _verified_amount(proposal.unit_price, evidence)
        qty = _verified_quantity(proposal.quantity, evidence)
        description = (
            proposal.description
            if proposal.description and _in_evidence(proposal.description, evidence)
            else None
        )
        if total is None or description is None:
            continue  # nothing reliable: the line stays unresolved for the user
        consistent = (
            qty is not None and unit_price is not None and abs(multiply_cents(qty, unit_price) - total) <= 1
        )
        conf = LLM_VERIFIED if consistent else LLM_UNVERIFIED
        line.raw_description = description
        line.line_total_cents = total
        line.quantity = qty
        line.unit_price_cents = unit_price
        line.unit = proposal.unit or (Unit.UNIT if qty is not None else None)
        line.field_confidence = {
            "description": conf,
            "line_total": LLM_VERIFIED,
            "quantity": conf if qty is not None else 0.0,
            "unit_price": conf if unit_price is not None else 0.0,
        }
        line.unresolved = qty is None
        line.resolved_by = "llm"
        resolved += 1
    return resolved


def _verified_amount(value: str | None, evidence: str) -> int | None:
    if value is None:
        return None
    cents = parse_money_cents(value)
    if cents is None:
        return None
    printed = f"{abs(cents) // 100},{abs(cents) % 100:02d}"
    return cents if printed in evidence or printed.replace(",", ".") in evidence else None


def _verified_quantity(value: str | None, evidence: str) -> Decimal | None:
    if value is None:
        return None
    # Models often copy the multiplication sign with the number ("2 X"); the number is what counts.
    value = re.sub(r"\s*[xX×]\s*$", "", value.strip())
    qty = parse_decimal(value)
    if qty is None or qty <= 0:
        return None
    return qty if re.search(rf"(?<![\d,.]){re.escape(value)}(?![\d,])", evidence) else None


def _in_evidence(description: str, evidence: str) -> bool:
    words = [w for w in re.split(r"\s+", description.upper()) if len(w) > 2]
    return bool(words) and all(w in evidence.upper() for w in words)
