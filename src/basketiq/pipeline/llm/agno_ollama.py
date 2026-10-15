"""Agno agent backed by a local Ollama model. Only the minimum text (the ambiguous lines) is sent."""

from __future__ import annotations

from typing import Any

from basketiq.pipeline.llm.base import LlmProposals, LlmUnavailableError

PROMPT_VERSION = "ambiguity-1"

INSTRUCTIONS = [
    "You read lines printed on Portuguese supermarket receipts.",
    "For each numbered line decide whether it is a purchased product.",
    "Copy values exactly as printed. Amounts use a comma as decimal separator.",
    "If a value is not printed on the line, return null. Never guess or compute values.",
    "Totals, payments, VAT rows, section headers and noise are not products.",
]


class AgnoOllamaResolver:
    def __init__(self, host: str, model: str, timeout: float) -> None:
        self.name = "agno-ollama"
        self.model_version = f"{model}|{PROMPT_VERSION}"
        self._host = host
        self._model = model
        self._timeout = timeout
        self._agent: Any = None

    def _get_agent(self) -> Any:
        if self._agent is None:
            from agno.agent import Agent
            from agno.models.ollama.chat import Ollama

            self._agent = Agent(
                model=Ollama(
                    id=self._model, host=self._host, timeout=self._timeout, options={"temperature": 0}
                ),
                instructions=INSTRUCTIONS,
                output_schema=LlmProposals,
                telemetry=False,
                retries=0,
            )
        return self._agent

    def propose(self, merchant: str, lines: list[str]) -> LlmProposals:
        numbered = "\n".join(f"{i}: {text}" for i, text in enumerate(lines))
        try:
            output = self._get_agent().run(f"Supermarket: {merchant}\nLines:\n{numbered}")
        except Exception as exc:  # network, timeout, model missing
            raise LlmUnavailableError(str(exc)) from exc
        content = getattr(output, "content", None)
        if isinstance(content, LlmProposals):
            return content
        try:
            return LlmProposals.model_validate(content)
        except Exception as exc:  # output outside the schema
            raise LlmUnavailableError("LLM output did not match the schema") from exc
