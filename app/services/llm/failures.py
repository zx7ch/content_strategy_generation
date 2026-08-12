"""Stable, safe failures emitted by OpenAI-compatible adapters."""

from __future__ import annotations

from app.services.llm.types import LLMServiceError


STATUS_FAILURES = {
    401: ("llm_auth_invalid", "API Key 无效", True),
    402: ("llm_account_unavailable", "模型账户余额或套餐不可用", True),
    403: ("llm_auth_invalid", "API Key 无权调用该服务", True),
    404: ("llm_model_unavailable", "配置的模型不存在或不可用", True),
    429: ("llm_rate_limited", "模型服务请求过于频繁", True),
}


class LLMProviderFailure(LLMServiceError):
    def __init__(
        self,
        code: str,
        public_message: str,
        recoverable: bool,
        status_code: int | None,
        provider: str | None = None,
        model: str | None = None,
        configuration_source: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.public_message = public_message
        self.recoverable = recoverable
        self.status_code = status_code
        self.provider = provider
        self.model = model
        self.configuration_source = configuration_source

    def with_target(
        self, *, provider: str, model: str, configuration_source: str
    ) -> "LLMProviderFailure":
        enriched = LLMProviderFailure(
            self.code,
            self.public_message,
            self.recoverable,
            self.status_code,
            provider=provider,
            model=model,
            configuration_source=configuration_source,
        )
        if self.__cause__ is not None:
            enriched.__cause__ = self.__cause__
        return enriched


def classify_provider_exception(exc: Exception) -> LLMProviderFailure:
    if isinstance(exc, LLMProviderFailure):
        return exc
    status_code = getattr(exc, "status_code", None)
    if not isinstance(status_code, int):
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int) and status_code in STATUS_FAILURES:
        code, public_message, recoverable = STATUS_FAILURES[status_code]
    elif isinstance(status_code, int) and status_code >= 500:
        code, public_message, recoverable = "llm_service_unavailable", "模型服务暂时不可用", True
    elif isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        code, public_message, recoverable = "llm_service_unavailable", "模型服务暂时不可用", True
    else:
        code, public_message, recoverable = "llm_service_unavailable", "模型服务暂时不可用", True
    failure = LLMProviderFailure(code, public_message, recoverable, status_code if isinstance(status_code, int) else None)
    failure.__cause__ = exc
    return failure


def unsupported_optional_parameters(exc: Exception) -> set[str]:
    """Identify the two compatibility-only optional fields without exposing text."""
    if getattr(exc, "status_code", None) != 400:
        return set()
    message = str(exc).lower()
    return {name for name in ("temperature", "max_tokens") if name in message}
