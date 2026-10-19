"""Trusted startup-time selection of the configured ModelProvider implementation."""

import httpx

from agentic_intent_gate.config.configuration import Settings
from agentic_intent_gate.infrastructure.llm.cloud import (
    AnthropicProvider,
    OpenAICloudProvider,
    OpenAICompatibleProvider,
)
from agentic_intent_gate.infrastructure.llm.local_openai import LocalOpenAIProvider
from agentic_intent_gate.ports.model_provider import ModelProvider


def create_model_provider(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ModelProvider:
    """Build one provider from frozen operator settings; no request selects providers."""
    if settings.provider == "ollama":
        return LocalOpenAIProvider(settings.endpoint, settings.model, transport=transport)
    if settings.provider == "openai":
        if settings.api_key is None:
            raise ValueError("Validated OpenAI settings must include an API key.")
        return OpenAICloudProvider(settings.model, settings.api_key, transport=transport)
    if settings.provider == "anthropic":
        if settings.api_key is None:
            raise ValueError("Validated Anthropic settings must include an API key.")
        return AnthropicProvider(settings.model, settings.api_key, transport=transport)
    if settings.provider == "openai-compatible":
        if settings.api_key is None:
            raise ValueError("Validated compatible settings must include an API key.")
        return OpenAICompatibleProvider(
            settings.endpoint, settings.model, settings.api_key, transport=transport
        )
    # Settings rejects unknown provider ids; retain a defensive fail-closed branch for
    # custom/malformed Settings-like callers at this composition boundary.
    raise ValueError("Unsupported model provider configuration.")
