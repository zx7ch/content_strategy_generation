from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.services.llm.types import LLMResponse, TokenUsage
from app.services.llm.failures import LLMProviderFailure


class FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.waiting = False

    async def start_presearch_run(self, *, thread_id: str, user_id: str, seed_text: str) -> str:
        self.calls.append({"thread_id": thread_id, "user_id": user_id, "seed_text": seed_text})
        return "run_1"

    async def mark_presearch_ready(self, workflow_run_id: str) -> None:
        self.calls.append({"workflow_run_id": workflow_run_id, "event": "presearch_ready"})

    async def wait_for_presearch_recovery(self, workflow_run_id: str, reason: dict) -> dict:
        self.waiting = True
        self.calls.append({"workflow_run_id": workflow_run_id, "event": "wait_for_presearch_recovery", "reason": reason})
        return {"workflow_run_id": workflow_run_id, "status": "waiting_user"}

    async def restart_presearch_step(self, workflow_run_id: str) -> dict:
        self.waiting = False
        self.calls.append({"workflow_run_id": workflow_run_id, "event": "restart_presearch_step"})
        return {"workflow_run_id": workflow_run_id, "status": "running"}

    async def get_runtime_snapshot(self, workflow_run_id: str) -> dict:
        return {
            "run": {"run_id": workflow_run_id, "status": "waiting_user" if self.waiting else "running"},
            "steps": [{"step_name": "presearch", "status": "retrying", "attempt_count": 1, "max_attempts": 3}],
            "child_tasks": [],
        }

    async def list_events(self, workflow_run_id: str) -> list[dict]:
        return []


class FakeLLM:
    def __init__(self, content: str | None = None, *, delay: float = 0.0, fail: bool = False) -> None:
        self.content = content or json.dumps(
            {
                "subject_confirmation": "徒步短裤更可能是户外服饰品类，请确认。",
                "competitor_tags": ["迪卡侬", "凯乐石"],
                "research_directions": ["产品卖点表达", "用户评论痛点"],
                "custom_research_question": "关注夏季轻量户外",
                "custom_competitor_input": "",
            },
            ensure_ascii=False,
        )
        self.delay = delay
        self.fail = fail
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("llm unavailable")
        return LLMResponse(
            content=self.content,
            provider="fake",
            model="fake-model",
            usage=TokenUsage(total_tokens=10),
            latency_ms=1,
        )


@pytest.fixture()
def store(tmp_path):
    return SQLiteContentResearchStore(str(tmp_path / "content_research.db"))


def _service(store, llm=None, *, first_timeout=0.05, hard_cutoff=0.1, runtime=None):
    return ContentResearchService(
        store=store,
        presearch=PresearchService(
            llm,
            first_feedback_timeout_seconds=first_timeout,
            hard_cutoff_seconds=hard_cutoff,
        ),
        workflow_runtime=runtime or FakeRuntime(),
    )


@pytest.mark.asyncio
async def test_presearch_success_creates_workflow_brief_trace_and_observation(store):
    service = _service(store, FakeLLM())

    response = await service.submit_presearch(
        seed_text="徒步短裤",
        user_note="关注夏季",
        thread_id="thread_1",
        user_id="user_1",
    )

    assert response.workflow_run_id == "run_1"
    assert response.attempt_id.startswith("att_")
    assert response.brief_id.startswith("rb_")
    assert response.status == "completed"
    assert response.fallback_used is False
    assert response.timeout_status == "none"
    assert response.competitor_tags == ["迪卡侬", "凯乐石"]
    assert service._presearch._llm.requests[0].response_format is None
    assert service._presearch._llm.requests[0].temperature == 1.0
    assert service._presearch._llm.requests[0].model_policy == "balanced"
    system_prompt = service._presearch._llm.requests[0].messages[0].content
    assert "只输出一个合法 JSON 对象" in system_prompt
    assert "不要输出 Markdown" in system_prompt
    assert "competitor_tags" in system_prompt
    assert store.get_brief(response.brief_id).payload["attempt_id"] == response.attempt_id
    traces = store.list_traces_for_workflow(response.workflow_run_id)
    assert len(traces) == 1
    events = store.list_observation_events(traces[0].id)
    assert [event.event_name for event in events] == ["presearch_started", "presearch_completed"]

    with sqlite3.connect(store._db_path) as conn:
        evidence_rows = conn.execute("SELECT COUNT(*) FROM content_research_evidence_records").fetchone()[0]
    assert evidence_rows == 0


@pytest.mark.asyncio
async def test_presearch_llm_failure_waits_for_model_configuration(store):
    service = _service(store, FakeLLM(fail=True))

    response = await service.submit_presearch(
        seed_text="Satisfy Running",
        user_note=None,
        thread_id="thread_1",
        user_id="user_1",
    )

    assert response.status == "waiting_model_config"
    assert response.fallback_used is False
    assert response.error_code == "llm_service_unavailable"


@pytest.mark.asyncio
async def test_presearch_malformed_llm_response_waits_for_configuration(store):
    service = _service(store, FakeLLM(content="not json"))

    response = await service.submit_presearch(
        seed_text="露营灯",
        user_note=None,
        thread_id="thread_1",
        user_id="user_1",
    )

    assert response.status == "waiting_model_config"
    assert response.fallback_used is False
    assert response.error_code == "llm_structured_output_invalid"


class FailingLLM:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def generate(self, _request):
        raise self.error


class FailOnceThenSucceedLLM(FakeLLM):
    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    async def generate(self, request):
        self.call_count += 1
        if self.call_count == 1:
            raise LLMProviderFailure("llm_auth_invalid", "API Key 无效", True, 401,
                provider="openai_compatible", model="model-x", configuration_source="user")
        return await super().generate(request)


@pytest.mark.asyncio
async def test_provider_failure_waits_for_configuration_without_completing_presearch(store):
    runtime = FakeRuntime()
    service = _service(store, FailingLLM(
        LLMProviderFailure("llm_account_unavailable", "模型账户不可用", True, 402)
    ), runtime=runtime)

    response = await service.submit_presearch(
        seed_text="夏季通勤短裤", user_note=None, thread_id="thread_1", workspace_id="ws_1", user_id="user_1",
    )

    assert response.status == "waiting_model_config"
    assert response.error_code == "llm_account_unavailable"
    assert runtime.calls[-1]["event"] == "wait_for_presearch_recovery"
    assert all(call.get("event") != "presearch_ready" for call in runtime.calls)


@pytest.mark.asyncio
async def test_submit_presearch_propagates_real_workspace_scope_to_llm(store):
    llm = FakeLLM()
    service = _service(store, llm)

    await service.submit_presearch(
        seed_text="夏季通勤短裤",
        user_note=None,
        thread_id="thread_1",
        workspace_id="workspace_from_router",
        user_id="user_1",
    )

    assert llm.requests[0].context is not None
    assert llm.requests[0].context.tenant_id == "workspace_from_router"
    assert llm.requests[0].context.user_id == "user_1"


@pytest.mark.asyncio
async def test_retry_presearch_reuses_attempt_brief_and_run(store):
    runtime = FakeRuntime()
    service = _service(store, FailOnceThenSucceedLLM(), runtime=runtime)
    first = await service.submit_presearch(
        seed_text="夏季通勤短裤", user_note=None, thread_id="thread_1", workspace_id="ws_1", user_id="user_1",
    )
    retried = await service.retry_presearch(first.workflow_run_id)

    assert retried.attempt_id == first.attempt_id
    assert retried.brief_id == first.brief_id
    assert retried.workflow_run_id == first.workflow_run_id
    assert retried.status == "completed"


@pytest.mark.asyncio
async def test_presearch_first_timeout_returns_fallback_then_hard_cutoff_updates_attempt(store):
    service = _service(store, FakeLLM(delay=0.2), first_timeout=0.01, hard_cutoff=0.03)

    response = await service.submit_presearch(
        seed_text="越野跑背心",
        user_note=None,
        thread_id="thread_1",
        user_id="user_1",
    )

    assert response.status == "fallback"
    assert response.timeout_status == "first_timeout"
    assert response.fallback_used is True

    await asyncio.sleep(0.06)
    final = service.get_presearch(response.attempt_id)
    assert final.status == "final_timeout"
    assert final.timeout_status == "final_timeout"

    trace = store.list_traces_for_workflow(response.workflow_run_id)[0]
    event_names = [event.event_name for event in store.list_observation_events(trace.id)]
    assert event_names == ["presearch_started", "presearch_first_timeout", "presearch_final_timeout"]
