from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from app.services.llm import (
    CredentialResolutionError,
    CredentialResolver,
    LLMConfigurationReader,
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
    UserLLMConfiguration,
)
from app.services.llm.failures import LLMProviderFailure


NOW = datetime(2026, 8, 3, tzinfo=timezone.utc)


class FakeConfigurationReader:
    def __init__(self, configuration: UserLLMConfiguration | None) -> None:
        self.configuration = configuration

    def get(self, workspace_id: str, user_id: str) -> UserLLMConfiguration | None:
        if self.configuration is None:
            return None
        if (workspace_id, user_id) != (
            self.configuration.workspace_id,
            self.configuration.user_id,
        ):
            return None
        return self.configuration


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
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[LLMRequest, str, str, str | None]] = []

    async def generate(self, request: LLMRequest, api_key: str, model: str, base_url=None) -> LLMResponse:
        self.calls.append((request, api_key, model, base_url))
        if self.error is not None:
            raise self.error
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
    assert provider.calls == [(request, "openai-key", "gpt-test", None)]


@pytest.mark.asyncio
async def test_user_configuration_overrides_policy_as_one_atomic_target():
    reader = FakeConfigurationReader(UserLLMConfiguration(
        workspace_id="ws_1", user_id="user_1", base_url="https://custom.example/v1",
        model="model-x", api_key="user-key", validation_status="validated", validated_at=NOW,
    ))
    provider = FakeProvider()
    service = LLMService(
        router=ModelRouter({"balanced": ResolvedModel("openai", "env-model")}),
        credential_resolver=CredentialResolver(FakeSettings()),
        providers={"openai_compatible": provider, "openai": FakeProvider()},
        configuration_reader=reader,
    )
    request = LLMRequest(
        messages=[Message(role="user", content="hi")], task_type="chat", model_policy="balanced",
        context=LLMCallContext(tenant_id="ws_1", user_id="user_1"),
    )

    response = await service.generate(request)

    assert provider.calls[0][1:] == ("user-key", "model-x", "https://custom.example/v1")
    assert response.configuration_source == "user"


@pytest.mark.asyncio
async def test_user_configuration_failure_never_calls_env_provider():
    custom = FakeProvider(error=LLMProviderFailure("llm_auth_invalid", "API Key 无效", True, 401))
    env_provider = FakeProvider()
    reader = FakeConfigurationReader(UserLLMConfiguration(
        workspace_id="ws_1", user_id="user_1", base_url="https://custom.example/v1",
        model="model-x", api_key="user-key", validation_status="validated", validated_at=NOW,
    ))
    service = LLMService(
        router=ModelRouter({"balanced": ResolvedModel("openai", "env-model")}),
        credential_resolver=CredentialResolver(FakeSettings()),
        providers={"openai_compatible": custom, "openai": env_provider}, configuration_reader=reader,
    )
    request = LLMRequest(
        messages=[Message(role="user", content="hi")], task_type="chat", model_policy="balanced",
        context=LLMCallContext(tenant_id="ws_1", user_id="user_1"),
    )

    with pytest.raises(LLMProviderFailure) as error:
        await service.generate(request)

    assert error.value.code == "llm_auth_invalid"
    assert env_provider.calls == []


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
