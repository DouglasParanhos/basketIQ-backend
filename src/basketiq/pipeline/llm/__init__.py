from __future__ import annotations

from basketiq.app_config import AppConfig
from basketiq.config import Settings
from basketiq.pipeline.llm.base import AmbiguityResolver, LlmProposals, LlmUnavailableError, apply_proposals


def build_resolver(app_config: AppConfig, settings: Settings) -> AmbiguityResolver | None:
    """The LLM agent exists only when LLM_ENABLED is true in config/app-config.yaml."""
    if not app_config.llm_enabled:
        return None
    from basketiq.pipeline.llm.agno_ollama import AgnoOllamaResolver

    return AgnoOllamaResolver(settings.ollama_host, settings.ollama_model, settings.llm_timeout_seconds)


__all__ = ["AmbiguityResolver", "LlmProposals", "LlmUnavailableError", "apply_proposals", "build_resolver"]
