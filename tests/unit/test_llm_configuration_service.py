from __future__ import annotations

import pytest

from app.services.llm.configuration import LLMConfigurationCandidate
from app.services.llm.configuration_service import LiteLLMConfigurationService
from app.services.llm.configuration_store import SQLiteLLMConfigurationStore
from app.services.llm.failures import LLMProviderFailure
from app.services.llm.types import LLMRequest, LLMResponse, TokenUsage


class ProbeAdapter:
    def __init__(self, response: str) -> None:
        self.response = response
        self.failure: Exception | None = None
        self.calls: list[tuple[LLMRequest, str, str, str | None]] = []

    async def generate(self, request, api_key, model, base_url=None):
        self.calls.append((request, api_key, model, base_url))
        if self.failure is not None:
            raise self.failure
        return LLMResponse(
            content=self.response, provider="openai_compatible", model=model,
            usage=TokenUsage(), latency_ms=1, configuration_source="candidate",
        )


def make_configuration_service(tmp_path, response: str):
    store = SQLiteLLMConfigurationStore(str(tmp_path / "config.db"))
    adapter = ProbeAdapter(response)
    return LiteLLMConfigurationService(store=store, probe_adapter=adapter), adapter


def valid_candidate() -> LLMConfigurationCandidate:
    return LLMConfigurationCandidate(
        base_url="https://proxy.example/v1", model="model-x", api_key="secret-1234"
    )


@pytest.mark.asyncio
async def test_save_validates_then_returns_redacted_summary(tmp_path):
    service, adapter = make_configuration_service(tmp_path, response='{"ok":true}')

    summary = await service.save(
        workspace_id="ws_1", user_id="user_1",
        candidate=LLMConfigurationCandidate(
            base_url="https://proxy.example/v1/", model="model-x", api_key="secret-1234",
        ),
    )

    assert summary.base_url == "https://proxy.example/v1"
    assert summary.api_key_configured is True
    assert summary.api_key_suffix == "1234"
    assert "secret-1234" not in repr(summary)
    assert adapter.calls[0][2] == "model-x"


@pytest.mark.asyncio
async def test_failed_candidate_does_not_replace_valid_configuration(tmp_path):
    service, adapter = make_configuration_service(tmp_path, response='{"ok":true}')
    await service.save(workspace_id="ws_1", user_id="user_1", candidate=valid_candidate())
    adapter.failure = LLMProviderFailure("llm_auth_invalid", "API Key 无效", True, 401)

    validation = await service.validate(
        workspace_id="ws_1", user_id="user_1",
        candidate=LLMConfigurationCandidate(
            base_url="https://bad.example/v1", model="bad-model", api_key="bad-key"
        ),
    )

    assert validation.status == "invalid"
    assert service.get_summary("ws_1", "user_1").model == "model-x"


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", [
    "ftp://proxy.example/v1", "https:///v1", "https://user:pass@proxy.example/v1",
    "https://proxy.example/v1?q=1", "https://proxy.example/v1#fragment",
])
async def test_validate_rejects_unsafe_urls(tmp_path, base_url):
    service, adapter = make_configuration_service(tmp_path, response='{"ok":true}')

    summary = await service.validate(
        workspace_id="ws_1", user_id="user_1",
        candidate=LLMConfigurationCandidate(base_url=base_url, model="model-x", api_key="secret-1234"),
    )

    assert summary.status == "invalid"
    assert summary.error_code == "llm_protocol_incompatible"
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_validate_reuses_existing_key_only_when_present(tmp_path):
    service, _ = make_configuration_service(tmp_path, response='{"ok":true}')
    missing = await service.validate(
        workspace_id="ws_1", user_id="user_1",
        candidate=LLMConfigurationCandidate("https://proxy.example/v1", "model-x", None),
    )
    assert missing.status == "invalid"
    await service.save(workspace_id="ws_1", user_id="user_1", candidate=valid_candidate())
    reused = await service.validate(
        workspace_id="ws_1", user_id="user_1",
        candidate=LLMConfigurationCandidate("https://proxy.example/v1", "model-y", None),
    )
    assert reused.status == "validated"
    assert reused.api_key_suffix == "1234"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidate", "error_code"),
    [
        (
            LLMConfigurationCandidate("ftp://proxy.example/v1", "model-x", "secret-1234"),
            "llm_protocol_incompatible",
        ),
        (
            LLMConfigurationCandidate("https://proxy.example/v1", "model-x", None),
            "llm_auth_invalid",
        ),
    ],
)
async def test_save_normalization_failure_returns_redacted_invalid_summary(
    tmp_path, candidate, error_code
):
    service, adapter = make_configuration_service(tmp_path, response='{"ok":true}')

    summary = await service.save(
        workspace_id="ws_1", user_id="user_1", candidate=candidate
    )

    assert summary.status == "invalid"
    assert summary.error_code == error_code
    assert summary.api_key_suffix == ("1234" if candidate.api_key else None)
    assert "secret-1234" not in repr(summary)
    assert adapter.calls == []
