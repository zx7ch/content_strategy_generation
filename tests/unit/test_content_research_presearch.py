from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.services.llm.types import LLMResponse, TokenUsage


class FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def start_presearch_run(self, *, thread_id: str, user_id: str, seed_text: str) -> str:
        self.calls.append({"thread_id": thread_id, "user_id": user_id, "seed_text": seed_text})
        return "run_1"

    async def mark_presearch_ready(self, workflow_run_id: str) -> None:
        self.calls.append({"workflow_run_id": workflow_run_id, "event": "presearch_ready"})

    async def get_runtime_snapshot(self, workflow_run_id: str) -> dict:
        return {"run": {"run_id": workflow_run_id}, "steps": [], "child_tasks": []}

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


def _service(store, llm=None, *, first_timeout=0.05, hard_cutoff=0.1):
    return ContentResearchService(
        store=store,
        presearch=PresearchService(
            llm,
            first_feedback_timeout_seconds=first_timeout,
            hard_cutoff_seconds=hard_cutoff,
        ),
        workflow_runtime=FakeRuntime(),
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
async def test_presearch_llm_failure_uses_fallback(store):
    service = _service(store, FakeLLM(fail=True))

    response = await service.submit_presearch(
        seed_text="Satisfy Running",
        user_note=None,
        thread_id="thread_1",
        user_id="user_1",
    )

    assert response.status == "fallback"
    assert response.fallback_used is True
    assert "Satisfy Running" in response.subject_confirmation


@pytest.mark.asyncio
async def test_presearch_malformed_llm_response_uses_fallback(store):
    service = _service(store, FakeLLM(content="not json"))

    response = await service.submit_presearch(
        seed_text="露营灯",
        user_note=None,
        thread_id="thread_1",
        user_id="user_1",
    )

    assert response.status == "fallback"
    assert response.fallback_used is True


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
