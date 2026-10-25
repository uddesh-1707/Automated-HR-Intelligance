"""Provider-independent LLM access.

HR business logic only talks to :class:`LLMClient`. The provider, model, key and endpoint come from
configuration (``LLM_PROVIDER``, ``LLM_MODEL``, ``LLM_API_KEY``, ``LLM_BASE_URL``, ``LLM_TEMPERATURE``).
Adding a provider means adding an adapter and one entry in ``PROVIDERS``; no agent code changes.
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.core.config import settings
from app.services.llm.base import (
    LLMClient,
    LLMConfigError,
    LLMNotConfigured,
    LLMProviderError,
    LLMResponse,
    ToolCall,
)

__all__ = [
    "LLMClient", "LLMConfigError", "LLMNotConfigured", "LLMProviderError", "LLMResponse", "ToolCall",
    "PROVIDERS", "get_llm_client", "resolve_llm_config", "validate_llm_config",
]


def _openai_factory(config: "ResolvedConfig") -> LLMClient:
    from app.services.llm.openai_provider import OpenAICompatibleClient

    return OpenAICompatibleClient(
        api_key=config.api_key, model=config.model, base_url=config.base_url,
        temperature=config.temperature, max_tokens=config.max_tokens, timeout=config.timeout,
    )


def _anthropic_factory(config: "ResolvedConfig") -> LLMClient:
    from app.services.llm.anthropic_provider import AnthropicClient

    return AnthropicClient(
        api_key=config.api_key or "", model=config.model, base_url=config.base_url,
        temperature=config.temperature, max_tokens=config.max_tokens, timeout=config.timeout,
    )


# provider name -> (factory, api key required unless a custom base URL is set)
PROVIDERS: dict[str, tuple[Callable[["ResolvedConfig"], LLMClient], bool]] = {
    "openai": (_openai_factory, True),
    "openai_compatible": (_openai_factory, False),
    "anthropic": (_anthropic_factory, True),
}


@dataclass
class ResolvedConfig:
    provider: str
    model: str
    api_key: str | None
    base_url: str | None
    temperature: float
    max_tokens: int
    timeout: float


def resolve_llm_config() -> ResolvedConfig | None:
    """Merge LLM_* settings with the legacy AGENT_* names. Returns None when nothing is configured."""
    api_key = settings.llm_api_key or settings.agent_api_key
    base_url = settings.llm_base_url or settings.agent_api_base_url
    explicitly_configured = bool(settings.llm_provider or settings.llm_model or api_key or base_url)
    if not explicitly_configured:
        return None
    return ResolvedConfig(
        provider=(settings.llm_provider or ("openai_compatible" if base_url else "openai")).strip().lower(),
        model=(settings.llm_model or settings.agent_model or "").strip(),
        api_key=api_key,
        base_url=base_url,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout_seconds,
    )


def validate_llm_config() -> list[str]:
    """Human-readable configuration problems (empty when valid or when no LLM is configured at all)."""
    config = resolve_llm_config()
    if config is None:
        return []
    problems = []
    if config.provider not in PROVIDERS:
        problems.append(f"LLM_PROVIDER '{config.provider}' is not supported. Use one of: {', '.join(sorted(PROVIDERS))}.")
        return problems
    if not config.model:
        problems.append("LLM_MODEL is required when an LLM provider is configured.")
    needs_key = PROVIDERS[config.provider][1] and not config.base_url
    if needs_key and not config.api_key:
        problems.append(f"LLM_API_KEY is required for provider '{config.provider}'.")
    return problems


def get_llm_client() -> LLMClient:
    config = resolve_llm_config()
    if config is None:
        raise LLMNotConfigured("The AI assistant is disabled. Configure LLM_PROVIDER, LLM_MODEL and LLM_API_KEY.")
    problems = validate_llm_config()
    if problems:
        raise LLMConfigError(" ".join(problems))
    return PROVIDERS[config.provider][0](config)
