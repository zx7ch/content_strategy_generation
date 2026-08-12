from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.llm import LLMRequest, Message, OpenAICompatibleAdapter, TokenUsage
from app.services.llm.providers.openai_compatible import DEFAULT_BASE_URLS


@dataclass
class FakeUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class FakeCompletions:
    def __init__(self, response: Any = None, error: Exception | list[Exception] | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.error, list):
            if self.error:
                error = self.error.pop(0)
                if error is not None:
                    raise error
            return self.response
        if self.error is not None:
            raise self.error
        return self.response


class FakeClientFactory:
    def __init__(self, completions: FakeCompletions) -> None:
        self.completions = completions
        self.calls: list[dict[str, str]] = []

    def __call__(self, *, api_key: str, base_url: str) -> Any:
        self.calls.append({"api_key": api_key, "base_url": base_url})
        return SimpleNamespace(chat=SimpleNamespace(completions=self.completions))


class FakeClock:
    def __init__(self, values: list[float]) -> None:
        self.values = values

    def __call__(self) -> float:
        return self.values.pop(0)


def make_response(*, usage: Any = None, content: str | None = " hello ", response_id: str = "resp_1") -> Any:
    return SimpleNamespace(
        id=response_id,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=usage,
    )


@pytest.mark.asyncio
async def test_openai_compatible_adapter_returns_normalized_response() -> None:
    completions = FakeCompletions(make_response(usage=FakeUsage(12, 7, 19)))
    client_factory = FakeClientFactory(completions)
    adapter = OpenAICompatibleAdapter(
        provider="openai",
        client_factory=client_factory,
        clock=FakeClock([10.0, 10.125]),
    )

    response = await adapter.generate(
        LLMRequest(messages=[Message(role="user", content="hi")], task_type="chat"),
        api_key="key-1",
        model="gpt-test",
    )

    assert response.content == "hello"
    assert response.provider == "openai"
    assert response.model == "gpt-test"
    assert response.usage == TokenUsage(prompt_tokens=12, completion_tokens=7, total_tokens=19)
    assert response.latency_ms == 125
    assert response.raw_response_id == "resp_1"
    assert client_factory.calls == [{"api_key": "key-1", "base_url": DEFAULT_BASE_URLS["openai"]}]


@pytest.mark.asyncio
async def test_openai_compatible_adapter_parses_dict_usage() -> None:
    completions = FakeCompletions(
        make_response(usage={"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7})
    )
    adapter = OpenAICompatibleAdapter(
        provider="deepseek",
        client_factory=FakeClientFactory(completions),
        clock=FakeClock([1.0, 1.01]),
    )

    response = await adapter.generate(
        LLMRequest(messages=[Message(role="user", content="hi")], task_type="chat"),
        api_key="key",
        model="deepseek-chat",
    )

    assert response.usage == TokenUsage(prompt_tokens=3, completion_tokens=4, total_tokens=7)


@pytest.mark.asyncio
async def test_openai_compatible_adapter_missing_usage_defaults_to_zero() -> None:
    completions = FakeCompletions(make_response(usage=None))
    adapter = OpenAICompatibleAdapter(
        provider="kimi",
        client_factory=FakeClientFactory(completions),
        clock=FakeClock([1.0, 1.0]),
    )

    response = await adapter.generate(
        LLMRequest(messages=[Message(role="user", content="hi")], task_type="chat"),
        api_key="key",
        model="moonshot-v1-8k",
    )

    assert response.usage == TokenUsage()


@pytest.mark.asyncio
async def test_openai_compatible_adapter_forwards_request_options() -> None:
    completions = FakeCompletions(make_response())
    adapter = OpenAICompatibleAdapter(
        provider="qwen",
        client_factory=FakeClientFactory(completions),
        clock=FakeClock([1.0, 1.0]),
    )

    await adapter.generate(
        LLMRequest(
            messages=[Message(role="system", content="sys"), Message(role="user", content="hi")],
            task_type="json",
            temperature=0.2,
            max_tokens=128,
            response_format={"type": "json_object"},
        ),
        api_key="key",
        model="qwen-plus",
    )

    assert completions.calls == [
        {
            "model": "qwen-plus",
            "temperature": 0.2,
            "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
            "max_tokens": 128,
            "response_format": {"type": "json_object"},
        }
    ]


@pytest.mark.parametrize("provider", ["openai", "deepseek", "kimi", "moonshot", "qwen"])
def test_openai_compatible_adapter_supports_default_base_urls(provider: str) -> None:
    adapter = OpenAICompatibleAdapter(provider=provider, client_factory=FakeClientFactory(FakeCompletions()))

    assert adapter.base_url == DEFAULT_BASE_URLS[provider]


@pytest.mark.asyncio
async def test_openai_compatible_adapter_propagates_provider_exception() -> None:
    expected = RuntimeError("provider down")
    completions = FakeCompletions(error=expected)
    adapter = OpenAICompatibleAdapter(
        provider="openai",
        client_factory=FakeClientFactory(completions),
        clock=FakeClock([1.0]),
    )

    from app.services.llm.failures import LLMProviderFailure

    with pytest.raises(LLMProviderFailure) as exc_info:
        await adapter.generate(
            LLMRequest(messages=[Message(role="user", content="hi")], task_type="chat"),
            api_key="key",
            model="gpt-test",
        )

    assert exc_info.value.code == "llm_service_unavailable"
    assert exc_info.value.__cause__ is expected


@pytest.mark.asyncio
async def test_openai_compatible_adapter_uses_request_scoped_base_url():
    completions = FakeCompletions(make_response())
    client_factory = FakeClientFactory(completions)
    adapter = OpenAICompatibleAdapter(provider="openai", client_factory=client_factory, clock=FakeClock([1.0, 1.0]))

    await adapter.generate(
        LLMRequest(messages=[Message(role="user", content="hi")], task_type="chat"),
        api_key="key", model="local-model", base_url="http://127.0.0.1:11434/v1",
    )

    assert client_factory.calls == [{"api_key": "key", "base_url": "http://127.0.0.1:11434/v1"}]


@pytest.mark.asyncio
async def test_openai_compatible_adapter_retries_without_unsupported_optional_parameters():
    class HTTP400(Exception):
        status_code = 400

        def __str__(self) -> str:
            return "unsupported temperature and max_tokens"

    completions = FakeCompletions(make_response(), error=[HTTP400(), None])
    client_factory = FakeClientFactory(completions)
    adapter = OpenAICompatibleAdapter(provider="openai", client_factory=client_factory, clock=FakeClock([1.0, 1.0]))

    await adapter.generate(
        LLMRequest(messages=[Message(role="user", content="hi")], task_type="chat", max_tokens=8),
        api_key="key", model="local-model", base_url="http://127.0.0.1:11434/v1",
    )

    assert completions.calls[1] == {"model": "local-model", "messages": [{"role": "user", "content": "hi"}]}
