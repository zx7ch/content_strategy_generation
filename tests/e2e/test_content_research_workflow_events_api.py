from __future__ import annotations

import json

import httpx
import pytest

from app.api.routes.router import app
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
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
                    "subject_structure": {
                        "schema_version": "content_research_subject_structure_v1",
                        "canonical_subject": "Satisfy Running",
                        "subject_type": "brand",
                        "source_terms": ["Satisfy", "Running"],
                        "term_roles": {
                            "core_object": ["Satisfy", "Running"],
                            "product_experience": [],
                            "context_audience": [],
                        },
                        "core_entities": [{"canonical_name": "Satisfy Running", "raw_mentions": ["Satisfy Running"]}],
                        "research_intents": [],
                        "context_modifiers": [],
                        "synonym_groups": {},
                        "ambiguities": [],
                        "resolution_state": "resolved",
                    },
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
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(
            title="Satisfy Running",
            workspace_id="workspace-events",
        )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c, thread["id"]
    if original is None:
        delattr(app.state, "content_research_service")
    else:
        app.state.content_research_service = original


@pytest.mark.asyncio
async def test_workflow_summary_and_events_expose_runtime_state(client):
    client, thread_id = client
    presearch_response = await client.post(
        "/content-research/presearch",
        headers={
            "X-Workspace-Id": "workspace-events",
            "X-User-Id": "user-events-1",
        },
        json={
            "command_id": "workflow-events-presearch",
            "seed_text": "Satisfy Running",
            "user_note": "关注跑步社群",
            "thread_id": thread_id,
        },
    )
    assert presearch_response.status_code == 201
    presearch = presearch_response.json()

    run = presearch["run"]
    confirm_response = await client.post(
        f"/content-research/workflows/{run['run_id']}/actions",
        json={
            "command_id": "workflow-events-confirm-brief",
            "expected_state": run["state"],
            "expected_revision": run["state_revision"],
            "action": "confirm_brief",
            "payload": {
                "brief_id": presearch["brief_id"],
                "selected_competitors": ["District Vision", "Salomon"],
                "custom_competitor_input": "",
                "selected_directions": ["product_marketing", "content_performance"],
            },
        },
    )
    assert confirm_response.status_code == 200

    fetched_summary = await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}")
    assert fetched_summary.status_code == 200
    summary = fetched_summary.json()
    assert summary["run"]["state"] == "scope_confirmation_required"
    assert summary["plan"] is not None
    assert summary["subagent_tasks"] == []
    assert summary["runtime_child_tasks"] == []

    events_response = await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}/events")
    assert events_response.status_code == 200
    event_types = [event["event_type"] for event in events_response.json()["events"]]
    assert event_types == ["run_started"]
