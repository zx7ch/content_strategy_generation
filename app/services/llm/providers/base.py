"""Provider adapter contract for the LLM abstraction layer."""

from __future__ import annotations

from typing import Protocol

from app.services.llm.types import LLMRequest, LLMResponse


class BaseLLMProvider(Protocol):
    async def generate(
        self,
        request: LLMRequest,
        api_key: str,
        model: str,
        base_url: str | None = None,
    ) -> LLMResponse:
        """Generate a normalized LLM response with the given provider credential."""
        ...
