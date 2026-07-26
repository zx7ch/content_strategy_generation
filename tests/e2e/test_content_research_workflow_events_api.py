from __future__ import annotations

import json

import httpx
import pytest

from app.api.routes.router import app
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.services.llm.types import LLMResponse, TokenUsage


class FakeLLM:
    async def generate(self, _request):
        return LLMResponse(
            content=json.dumps(
                {
                    "subject_confirmation": "Satisfy Running 可能是跑步服饰品牌，请确认。",
                    "competitor_tags": ["District Vision", "Salomon"],
                    "research_directions": ["产品营销", "品牌活动"],
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
    db_path = str(tmp_path / "content_research.db")
    app.state.content_research_service = ContentResearchService(
        store=SQLiteContentResearchStore(db_path),
        presearch=PresearchService(FakeLLM(), first_feedback_timeout_seconds=0.05, hard_cutoff_seconds=0.1),
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    if original is None:
        delattr(app.state, "content_research_service")
    else:
        app.state.content_research_service = original


@pytest.mark.asyncio
async def test_workflow_summary_and_events_expose_runtime_state(client):
    presearch_response = await client.post(
        "/content-research/presearch",
        headers={"X-User-Id": "user-events-1"},
        json={
            "seed_text": "Satisfy Running",
            "user_note": "关注跑步社群",
            "thread_id": "thread-events-1",
        },
    )
    assert presearch_response.status_code == 201
    presearch = presearch_response.json()

    confirm_response = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "Satisfy Running",
            "subject_type": "brand",
            "selected_competitors": ["District Vision"],
            "custom_competitors": ["Salomon"],
            "selected_directions": ["product_marketing", "brand_activity"],
            "custom_research_question": "关注跑步社群活动",
        },
    )
    assert confirm_response.status_code == 200
    summary = confirm_response.json()

    assert summary["runtime_run"]["current_step"] == "formal_research"
    assert [step["step_name"] for step in summary["runtime_steps"]] == [
        "presearch",
        "brief_confirm",
        "plan_build",
        "formal_research",
    ]
    assert [step["status"] for step in summary["runtime_steps"]] == [
        "succeeded",
        "succeeded",
        "succeeded",
        "running",
    ]
    assert len(summary["runtime_child_tasks"]) == 2
    assert {
        task["payload"]["workflow_child_task_id"] for task in summary["subagent_tasks"]
    } == {task["child_task_id"] for task in summary["runtime_child_tasks"]}

    fetched_summary = await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}")
    assert fetched_summary.status_code == 200
    assert fetched_summary.json() == summary

    events_response = await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}/events")
    assert events_response.status_code == 200
    event_types = [event["event_type"] for event in events_response.json()["events"]]
    assert event_types.count("step_completed") == 3
    assert "run_started" in event_types
    assert "steps_initialized" in event_types
    assert "step_started" in event_types
    assert "child_tasks_created" in event_types
