"""Credential resolution for LLM providers."""

from __future__ import annotations

from app.config import settings
from app.services.llm.types import CredentialResolutionError


PROVIDER_KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "QWEN_API_KEY",
    "kimi": "KIMI_API_KEY",
    "moonshot": "KIMI_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


class CredentialResolver:
    def __init__(self, settings_obj: object = settings) -> None:
        self._settings = settings_obj

    def resolve(
        self,
        provider: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> str:
        del tenant_id, user_id
        normalized_provider = provider.lower()
        attr_name = PROVIDER_KEY_ENV.get(normalized_provider)
        if attr_name is None:
            raise CredentialResolutionError(f"No credential mapping configured for provider: {provider}")

        value = getattr(self._settings, attr_name, "")
        if not isinstance(value, str) or not value.strip():
            raise CredentialResolutionError(f"Missing API key for provider: {provider}")
        return value.strip()
