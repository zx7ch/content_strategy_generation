"""
Unified LLM Client
==================
Wraps multiple LLM providers behind a single interface.

Anthropic uses its own SDK.
DeepSeek, MiniMax, and Kimi are all OpenAI-compatible REST APIs,
so they share one AsyncOpenAI client pointed at the right base_url.

Usage:
    from app.llm.client import LLMClient
    from app.config import settings

    llm = LLMClient(provider=settings.LLM_PROVIDER)
    response = await llm.chat(
        system="You are a helpful assistant.",
        user="What is the market gap for matcha content?",
        max_tokens=1024,
    )
    print(response)  # plain string
"""

from __future__ import annotations

import logging
from enum import Enum

import anthropic
from openai import AsyncOpenAI

from app.config import settings
from app.logging_config import get_logger, log_event

logger = logging.getLogger(__name__)
structured_logger = get_logger(__name__, component="llm")


# ---------------------------------------------------------------------------
# Provider enum — single source of truth for valid provider names
# ---------------------------------------------------------------------------

class LLMProvider(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI    = "openai"
    DEEPSEEK  = "deepseek"
    MINIMAX   = "minimax"
    KIMI      = "kimi"


# ---------------------------------------------------------------------------
# Provider configs — base URLs and default models
# ---------------------------------------------------------------------------

PROVIDER_CONFIGS: dict[LLMProvider, dict] = {
    LLMProvider.ANTHROPIC: {
        "base_url": None,                             # uses native SDK, no base_url needed
        "default_model": "claude-opus-4-6",
    },
    LLMProvider.OPENAI: {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
    LLMProvider.DEEPSEEK: {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    LLMProvider.MINIMAX: {
        "base_url": "https://api.minimax.chat/v1",
        "default_model": "abab6.5s-chat",
    },
    LLMProvider.KIMI: {
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
    },
}


# ---------------------------------------------------------------------------
# Unified client
# ---------------------------------------------------------------------------

class LLMClient:
    """
    Single interface for all supported LLM providers.

    All providers expose a chat() method that accepts a system prompt and
    user message and returns a plain string response.

    Args:
        provider: one of LLMProvider enum values (or plain string)
        model:    override the default model for the chosen provider
    """

    def __init__(
        self,
        provider: str = settings.LLM_PROVIDER,
        model: str | None = None,
    ) -> None:
        normalized_provider = provider.lower() if isinstance(provider, str) else provider
        self.provider = LLMProvider(normalized_provider)
        config = PROVIDER_CONFIGS[self.provider]
        self.model = model or getattr(settings, f"{self.provider.upper()}_MODEL", config["default_model"])

        if self.provider == LLMProvider.ANTHROPIC:
            self._anthropic = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            self._openai = None
        else:
            # All other providers are OpenAI-compatible
            api_key = getattr(settings, f"{self.provider.upper()}_API_KEY", "")
            base_url = getattr(settings, f"{self.provider.upper()}_BASE_URL", "") or config["base_url"]
            self._openai = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
            )
            self._anthropic = None

        logger.info(f"LLMClient initialised: provider={self.provider}, model={self.model}")

    async def chat(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> str:
        """
        Send a system + user message and return the response as a plain string.

        Args:
            system:     system prompt (instruction / persona)
            user:       user message (runtime data injected here)
            max_tokens: max tokens in the response
            temperature: sampling temperature

        Returns:
            str — the assistant's reply text

        Raises:
            ValueError: if provider is unknown
            Exception:  propagates API errors to the caller for handling
        """
        try:
            if self.provider == LLMProvider.ANTHROPIC:
                response = await self._chat_anthropic(system, user, max_tokens, temperature)
            else:
                response = await self._chat_openai_compatible(system, user, max_tokens, temperature)
        except Exception as exc:
            log_event(
                structured_logger,
                event_name="llm_call_failed",
                level="error",
                component="llm",
                stage="llm",
                provider=self.provider.value,
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                prompt_chars=len(system or "") + len(user or ""),
                error_type=type(exc).__name__,
            )
            raise

        log_event(
            structured_logger,
            event_name="llm_call_completed",
            level="info",
            component="llm",
            stage="llm",
            provider=self.provider.value,
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            prompt_chars=len(system or "") + len(user or ""),
            response_chars=len(response or ""),
        )
        return response

    # ------------------------------------------------------------------
    # Anthropic
    # ------------------------------------------------------------------

    async def _chat_anthropic(
        self, system: str, user: str, max_tokens: int, temperature: float
    ) -> str:
        response = self._anthropic.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text.strip()

    # ------------------------------------------------------------------
    # OpenAI-compatible (DeepSeek / MiniMax / Kimi)
    # ------------------------------------------------------------------

    async def _chat_openai_compatible(
        self, system: str, user: str, max_tokens: int, temperature: float
    ) -> str:
        response = await self._openai.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
        )
        return response.choices[0].message.content.strip()
