"""OpenAI-compatible chat completions provider adapter."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from openai import AsyncOpenAI

from app.services.llm.failures import (
    LLMProviderFailure,
    classify_provider_exception,
    unsupported_optional_parameters,
)
from app.services.llm.types import LLMRequest, LLMResponse, TokenUsage

DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "kimi": "https://api.kimi.com/coding/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}


def _read_value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _parse_usage(raw_usage: Any) -> TokenUsage:
    if raw_usage is None:
        return TokenUsage()
    return TokenUsage(
        prompt_tokens=int(_read_value(raw_usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(_read_value(raw_usage, "completion_tokens", 0) or 0),
        total_tokens=int(_read_value(raw_usage, "total_tokens", 0) or 0),
    )


class OpenAICompatibleAdapter:
    def __init__(
        self,
        *,
        provider: str,
        base_url: str | None = None,
        client_factory: Callable[..., Any] = AsyncOpenAI,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.provider = provider.lower()
        self.base_url = base_url or DEFAULT_BASE_URLS.get(self.provider)
        self._client_factory = client_factory
        self._clock = clock
    async def generate(
        self, request: LLMRequest, api_key: str, model: str, base_url: str | None = None
    ) -> LLMResponse:
        if request.stream:
            raise NotImplementedError("Streaming is not supported by OpenAICompatibleAdapter yet")

        effective_base_url = base_url or self.base_url
        if not effective_base_url:
            raise LLMProviderFailure(
                "llm_protocol_incompatible", "模型服务 Base URL 未配置", True, None
            )

        payload: dict[str, Any] = {
            "model": model,
            "temperature": request.temperature,
            "messages": [{"role": message.role, "content": message.content} for message in request.messages],
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.response_format is not None:
            payload["response_format"] = request.response_format

        client = self._client_factory(api_key=api_key, base_url=effective_base_url)
        started_at = self._clock()
        try:
            response = await client.chat.completions.create(**payload)
        except Exception as exc:
            fields = unsupported_optional_parameters(exc)
            if fields:
                retry_payload = {key: value for key, value in payload.items() if key not in fields}
                try:
                    response = await client.chat.completions.create(**retry_payload)
                except Exception as retry_error:
                    raise classify_provider_exception(retry_error) from retry_error
            else:
                raise classify_provider_exception(exc) from exc
        latency_ms = int((self._clock() - started_at) * 1000)

        choices = _read_value(response, "choices", []) or []
        first_choice = choices[0] if choices else None
        message = _read_value(first_choice, "message", None) if first_choice is not None else None
        content = _read_value(message, "content", "") or ""

        if not choices or first_choice is None or message is None or not isinstance(content, str):
            raise LLMProviderFailure(
                "llm_protocol_incompatible", "模型服务响应格式不兼容", True, None
            )

        return LLMResponse(
            content=str(content).strip(),
            provider=self.provider,
            model=model,
            usage=_parse_usage(_read_value(response, "usage", None)),
            latency_ms=latency_ms,
            raw_response_id=_read_value(response, "id", None),
            finish_reason=_read_value(first_choice, "finish_reason", None),
        )
