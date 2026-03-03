from __future__ import annotations

import os
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    def complete(self, prompt: str) -> str:
        ...


class NullProvider:
    """No-op provider used when LLM is disabled."""

    def complete(self, prompt: str) -> str:
        return ""


def get_llm_provider(
    provider_name: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> LLMProvider:
    """Factory that returns an LLMProvider based on configuration.

    Config via env vars (overridden by explicit args):
      FPML_CDM_LLM_PROVIDER  — provider name (default: none)
      FPML_CDM_LLM_MODEL     — model name (default: provider-specific)
      GEMINI_API_KEY         — for gemini provider
      FPML_CDM_LLM_BASE_URL  — for openai_compat provider
      FPML_CDM_LLM_API_KEY   — for openai_compat provider (optional)
    """
    name = provider_name or os.environ.get("FPML_CDM_LLM_PROVIDER", "none")

    if name in ("none", "", None):
        return NullProvider()

    if name == "gemini":
        from .gemini_provider import GeminiProvider

        resolved_model = model or os.environ.get("FPML_CDM_LLM_MODEL", "gemini-2.5-flash")
        return GeminiProvider(model=resolved_model)

    if name == "openai_compat":
        from .openai_compatible import OpenAICompatProvider

        resolved_base_url = base_url or os.environ.get("FPML_CDM_LLM_BASE_URL", "http://localhost:11434/v1")
        resolved_model = model or os.environ.get("FPML_CDM_LLM_MODEL", "llama3.2")
        resolved_api_key = api_key or os.environ.get("FPML_CDM_LLM_API_KEY", "")
        return OpenAICompatProvider(
            base_url=resolved_base_url,
            model=resolved_model,
            api_key=resolved_api_key,
        )

    raise ValueError(
        f"Unknown LLM provider: {name!r}. Supported: 'none', 'gemini', 'openai_compat'."
    )
