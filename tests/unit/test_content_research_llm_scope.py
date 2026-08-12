from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.content_research.analysis import DirectionalAnalysisService
from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.reporting.faithfulness import LLMReportSemanticAuditor
from app.services.llm import (
    CredentialResolver,
    LLMRequest,
    LLMResponse,
    LLMService,
    ModelRouter,
    ResolvedModel,
    SQLiteLLMConfigurationStore,
    TokenUsage,
    UserLLMConfiguration,
)
from app.services.llm.failures import LLMProviderFailure
from tests.unit.test_content_research_report_composer import _snapshot


class _Settings:
    OPENAI_API_KEY = "environment-key"


class _Provider:
    def __init__(self, failure: LLMProviderFailure | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[LLMRequest, str, str, str | None]] = []

    async def generate(
        self,
        request: LLMRequest,
        api_key: str,
        model: str,
        base_url: str | None = None,
    ) -> LLMResponse:
        self.calls.append((request, api_key, model, base_url))
        if self.failure is not None:
            raise self.failure
        content = (
            '{"summary":"bounded","observations":[],"evidence_refs":["ev_1"],"missing_evidence":[]}'
            if request.task_type == "content_research.directional_analysis"
            else '{"state":"passed","reason_codes":[],"affected_section_ids":[]}'
        )
        return LLMResponse(
            content=content,
            provider="openai",
            model=model,
            usage=TokenUsage(1, 1, 2),
            latency_ms=1,
        )


def _service(tmp_path, provider: _Provider, environment_provider: _Provider | None = None):
    store = SQLiteLLMConfigurationStore(str(tmp_path / "scope.db"))
    store.upsert(
        UserLLMConfiguration(
            workspace_id="workspace-saved",
            user_id="user-saved",
            base_url="https://saved.example/v1",
            model="gpt-4o-mini",
            api_key="saved-key",
            validation_status="validated",
            validated_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
        )
    )
    return LLMService(
        router=ModelRouter(
            {"quality": ResolvedModel("openai", "environment-model")}
        ),
        credential_resolver=CredentialResolver(_Settings()),
        providers={
            "openai_compatible": provider,
            "openai": environment_provider or _Provider(),
        },
        configuration_reader=store,
    )


def _task() -> SimpleNamespace:
    return SimpleNamespace(
        thread_id="thread-1",
        workflow_run_id="run_1",
        plan_id=None,
        direction_id="product_marketing",
        payload={
            "agent_name": "directional_research",
            "llm_scope": {
                "workspace_id": "workspace-saved",
                "user_id": "user-saved",
            },
        },
    )


def _scoped_snapshot():
    snapshot = _snapshot()
    return snapshot.__class__(
        **{
            **snapshot.__dict__,
            "metadata": {
                **snapshot.metadata,
                "llm_scope": {
                    "workspace_id": "workspace-saved",
                    "user_id": "user-saved",
                },
            },
        }
    )


@pytest.mark.asyncio
async def test_downstream_content_research_llms_use_saved_scope_target(tmp_path):
    provider = _Provider()
    environment_provider = _Provider()
    llm = _service(tmp_path, provider, environment_provider)
    task = _task()

    analysis = await DirectionalAnalysisService(
        llm=llm, db_path=str(tmp_path / "scope.db")
    ).analyze(
        task=task,
        direction={"id": "product_marketing"},
        query="commute shorts",
        facts=[{"evidence_id": "ev_1", "claim": "bounded"}],
    )
    snapshot = _scoped_snapshot()
    semantic = await LLMReportSemanticAuditor(llm).audit(
        snapshot, ResearchReportComposer().compose(snapshot)
    )

    assert analysis is not None
    assert semantic.state == "passed"
    assert len(provider.calls) == 2
    for request, api_key, model, base_url in provider.calls:
        assert (
            request.context.tenant_id,
            request.context.user_id,
            request.context.job_id,
        ) == ("workspace-saved", "user-saved", snapshot.workflow_run_id)
        assert (api_key, model, base_url) == (
            "saved-key",
            "gpt-4o-mini",
            "https://saved.example/v1",
        )
    assert environment_provider.calls == []


@pytest.mark.asyncio
async def test_downstream_configuration_failure_is_not_silently_downgraded(tmp_path):
    provider = _Provider(
        LLMProviderFailure("llm_auth_invalid", "API Key 无效", True, 401)
    )
    environment_provider = _Provider()
    llm = _service(tmp_path, provider, environment_provider)

    with pytest.raises(LLMProviderFailure) as analysis_failure:
        await DirectionalAnalysisService(
            llm=llm, db_path=str(tmp_path / "scope.db")
        ).analyze(
            task=_task(),
            direction={"id": "product_marketing"},
            query="commute shorts",
            facts=[{"evidence_id": "ev_1", "claim": "bounded"}],
        )

    snapshot = _scoped_snapshot()
    semantic = await LLMReportSemanticAuditor(llm).audit(
        snapshot, ResearchReportComposer().compose(snapshot)
    )

    assert analysis_failure.value.code == "llm_auth_invalid"
    assert semantic.state == "unavailable"
    assert semantic.reason_codes == ("llm_auth_invalid",)
    assert environment_provider.calls == []


@pytest.mark.asyncio
async def test_downstream_llm_missing_durable_scope_fails_explicitly(tmp_path):
    provider = _Provider()
    llm = _service(tmp_path, provider)
    task = _task()
    task.payload.clear()

    with pytest.raises(LLMProviderFailure) as analysis_failure:
        await DirectionalAnalysisService(
            llm=llm, db_path=str(tmp_path / "scope.db")
        ).analyze(
            task=task,
            direction={"id": "product_marketing"},
            query="commute shorts",
            facts=[{"evidence_id": "ev_1", "claim": "bounded"}],
        )

    snapshot = _snapshot()
    semantic = await LLMReportSemanticAuditor(llm).audit(
        snapshot, ResearchReportComposer().compose(snapshot)
    )

    assert analysis_failure.value.code == "llm_configuration_scope_missing"
    assert semantic.reason_codes == ("llm_configuration_scope_missing",)
    assert provider.calls == []
