from __future__ import annotations

from basketiq.config import Settings
from basketiq.pipeline.llm.base import AmbiguityResolver, LlmProposals, LlmUnavailableError, apply_proposals


def build_resolver(settings: Settings) -> AmbiguityResolver | None:
    if not settings.llm_enabled:
        return None
    from basketiq.pipeline.llm.agno_ollama import AgnoOllamaResolver

    return AgnoOllamaResolver(settings.ollama_host, settings.ollama_model, settings.llm_timeout_seconds)


__all__ = ["AmbiguityResolver", "LlmProposals", "LlmUnavailableError", "apply_proposals", "build_resolver"]
