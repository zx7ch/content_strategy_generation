from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.llm import (
    CredentialResolutionError,
    CredentialResolver,
    LLMCallContext,
    LLMRequest,
    LLMResponse,
    LLMService,
    Message,
    ModelRouter,
    ModelRoutingError,
    ProviderNotRegisteredError,
    ResolvedModel,
    TokenUsage,
)


@dataclass
class FakeSettings:
    OPENAI_API_KEY: str = "openai-key"
    DEEPSEEK_API_KEY: str = "deepseek-key"
    QWEN_API_KEY: str = "qwen-key"
    KIMI_API_KEY: str = "kimi-key"
    MINIMAX_API_KEY: str = "minimax-key"
    OPENAI_MODEL: str = "gpt-test"
    DEEPSEEK_MODEL: str = "deepseek-test"
    KIMI_MODEL: str = "kimi-test"


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[LLMRequest, str, str]] = []

    async def generate(self, request: LLMRequest, api_key: str, model: str) -> LLMResponse:
        self.calls.append((request, api_key, model))
        return LLMResponse(
            content="normalized output",
            provider=request.provider or "openai",
            model=model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            latency_ms=123,
            raw_response_id="resp_1",
        )


def test_router_resolves_policy() -> None:
    router = ModelRouter(
        {
            "balanced": ResolvedModel(provider="OpenAI", model="gpt-test", model_policy="balanced"),
        }
    )
    request = LLMRequest(messages=[Message(role="user", content="hi")], task_type="topic", model_policy="balanced")

    resolved = router.resolve(request)

    assert resolved == ResolvedModel(provider="openai", model="gpt-test", model_policy="balanced")


def test_router_explicit_provider_and_model_override_policy() -> None:
    router = ModelRouter(
        {
            "balanced": ResolvedModel(provider="openai", model="gpt-test", model_policy="balanced"),
        }
    )
    request = LLMRequest(
        messages=[Message(role="user", content="hi")],
        task_type="topic",
        model_policy="balanced",
        provider="DeepSeek",
        model_id="deepseek-chat",
    )

    resolved = router.resolve(request)

    assert resolved == ResolvedModel(provider="deepseek", model="deepseek-chat", model_policy="balanced")


def test_router_unknown_policy_raises() -> None:
    router = ModelRouter({"balanced": ResolvedModel(provider="openai", model="gpt-test")})
    request = LLMRequest(messages=[Message(role="user", content="hi")], task_type="topic", model_policy="missing")

    with pytest.raises(ModelRoutingError, match="missing"):
        router.resolve(request)


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("openai", "openai-key"),
        ("deepseek", "deepseek-key"),
        ("qwen", "qwen-key"),
        ("kimi", "kimi-key"),
        ("moonshot", "kimi-key"),
        ("minimax", "minimax-key"),
    ],
)
def test_credential_resolver_reads_provider_keys(provider: str, expected: str) -> None:
    resolver = CredentialResolver(FakeSettings())

    assert resolver.resolve(provider) == expected


def test_credential_resolver_missing_key_raises() -> None:
    resolver = CredentialResolver(FakeSettings(OPENAI_API_KEY=""))

    with pytest.raises(CredentialResolutionError, match="openai"):
        resolver.resolve("openai")


@pytest.mark.asyncio
async def test_service_generate_routes_to_fake_provider() -> None:
    provider = FakeProvider()
    service = LLMService(
        router=ModelRouter({"balanced": ResolvedModel(provider="openai", model="gpt-test")}),
        credential_resolver=CredentialResolver(FakeSettings()),
        providers={"openai": provider},
    )
    request = LLMRequest(
        messages=[Message(role="user", content="生成选题")],
        task_type="topic_generation",
        model_policy="balanced",
        context=LLMCallContext(tenant_id="tenant-1", user_id="user-1"),
    )

    response = await service.generate(request)

    assert response.content == "normalized output"
    assert response.usage.total_tokens == 15
    assert provider.calls == [(request, "openai-key", "gpt-test")]


@pytest.mark.asyncio
async def test_service_missing_provider_raises() -> None:
    service = LLMService(
        router=ModelRouter({"balanced": ResolvedModel(provider="openai", model="gpt-test")}),
        credential_resolver=CredentialResolver(FakeSettings()),
        providers={},
    )
    request = LLMRequest(messages=[Message(role="user", content="hi")], task_type="topic", model_policy="balanced")

    with pytest.raises(ProviderNotRegisteredError, match="openai"):
        await service.generate(request)
