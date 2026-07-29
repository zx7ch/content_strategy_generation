from __future__ import annotations

import json

import httpx
import pytest

from app.api.routes.router import app
from app.config import settings
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.services.llm.types import LLMResponse, TokenUsage


class FakeRuntime:
    async def start_presearch_run(self, *, thread_id: str, user_id: str, seed_text: str) -> str:
        return f"run_{thread_id}_{user_id}_{seed_text[:2]}"

    async def mark_presearch_ready(self, workflow_run_id: str) -> None:
        return None

    async def get_runtime_snapshot(self, workflow_run_id: str) -> dict:
        return {"run": {"run_id": workflow_run_id}, "steps": [], "child_tasks": []}

    async def list_events(self, workflow_run_id: str) -> list[dict]:
        return []


class FakeLLM:
    async def generate(self, _request):
        return LLMResponse(
            content=json.dumps(
                {
                    "subject_confirmation": "Satisfy Running 可能是跑步服饰品牌，请确认。",
                    "competitor_tags": ["District Vision", "Salomon"],
                    "research_directions": ["品牌活动", "UGC 社群互动"],
                    "custom_research_question": "",
                    "custom_competitor_input": "",
                },
                ensure_ascii=False,
            ),
            provider="fake",
            model="fake-model",
            usage=TokenUsage(total_tokens=10),
            latency_ms=1,
        )


@pytest.fixture()
async def client(tmp_path):
    original = getattr(app.state, "content_research_service", None)
    app.state.content_research_service = ContentResearchService(
        store=SQLiteContentResearchStore(str(tmp_path / "content_research.db")),
        presearch=PresearchService(FakeLLM(), first_feedback_timeout_seconds=0.05, hard_cutoff_seconds=0.1),
        workflow_runtime=FakeRuntime(),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    if original is None:
        delattr(app.state, "content_research_service")
    else:
        app.state.content_research_service = original


@pytest.mark.asyncio
async def test_content_research_presearch_post_and_get(client):
    created = await client.post(
        "/content-research/presearch",
        headers={"X-User-Id": "user-1"},
        json={
            "seed_text": "Satisfy Running",
            "user_note": "关注竞品",
            "thread_id": "thread-1",
        },
    )

    assert created.status_code == 201
    payload = created.json()
    assert payload["attempt_id"].startswith("att_")
    assert payload["brief_id"].startswith("rb_")
    assert payload["workflow_run_id"].startswith("run_thread-1_user-1")
    assert payload["status"] == "completed"
    assert payload["fallback_used"] is False
    assert payload["competitor_tags"] == ["District Vision", "Salomon"]

    fetched = await client.get(f"/content-research/presearch/{payload['attempt_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == payload


@pytest.mark.asyncio
async def test_content_research_presearch_get_missing_attempt_returns_404(client):
    response = await client.get("/content-research/presearch/att_missing")

    assert response.status_code == 404
    assert response.json()["error_code"] == "CONTENT_RESEARCH_PRESEARCH_NOT_FOUND"


@pytest.mark.asyncio
async def test_preview_off_rejects_presearch_before_persisting_a_run(
    client,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "F003_LITE_PREVIEW_ENABLED", False)

    response = await client.post(
        "/content-research/presearch",
        headers={"X-User-Id": "user-1"},
        json={
            "seed_text": "Satisfy Running",
            "user_note": "关注竞品",
            "thread_id": "thread-preview-off",
        },
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "F003_LITE_PREVIEW_DISABLED"
    service = app.state.content_research_service
    assert (
        service._store.get_brief_by_workflow(
            "run_thread-preview-off_user-1_Sa"
        )
        is None
    )
