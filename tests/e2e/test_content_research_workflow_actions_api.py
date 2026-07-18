from __future__ import annotations

import json

import httpx
import pytest

from app.api.routes.router import app
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.sources import SourceAdapterRegistry
from app.content_research.sources.base import SourceCollectionResult
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from app.services.llm.types import LLMResponse, TokenUsage


class FakeLLM:
    async def generate(self, _request):
        return LLMResponse(
            content=json.dumps(
                {
                    "subject_confirmation": "徒步短裤更可能是户外服饰品类，请确认。",
                    "competitor_tags": ["迪卡侬", "凯乐石"],
                    "research_directions": ["产品营销", "用户评论痛点"],
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


class FakeSourceAdapter:
    def __init__(self) -> None:
        self.requests = []

    async def collect(self, request):
        self.requests.append(request)
        return SourceCollectionResult(
            provider="xiaohongshu",
            source_kind=request.source_kind,
            status="completed",
            items=[
                {
                    "schema_version": "content_research_source_payload_v1",
                    "canonical_id": "note-1",
                    "source_kind": request.source_kind,
                    "cookie_status": "valid",
                }
            ],
            cookie_status="valid",
            metadata={"item_count": 1},
        )


@pytest.fixture()
async def client(tmp_path):
    original = getattr(app.state, "content_research_service", None)
    db_path = str(tmp_path / "content_research.db")
    source_adapter = FakeSourceAdapter()
    app.state.content_research_service = ContentResearchService(
        store=SQLiteContentResearchStore(db_path),
        presearch=PresearchService(FakeLLM(), first_feedback_timeout_seconds=0.05, hard_cutoff_seconds=0.1),
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
        source_registry=SourceAdapterRegistry({"xiaohongshu": source_adapter}),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    if original is None:
        delattr(app.state, "content_research_service")
    else:
        app.state.content_research_service = original


async def _create_presearch(client):
    service = app.state.content_research_service
    async with ThreadStore(service._store._db_path) as thread_store:
        thread = await thread_store.create_thread(title="Content Research Actions")
    response = await client.post(
        "/content-research/presearch",
        headers={"X-User-Id": "user-actions-1"},
        json={"seed_text": "徒步短裤", "thread_id": thread["id"]},
    )
    assert response.status_code == 201
    payload = response.json()
    payload["_thread_id"] = thread["id"]
    return payload


@pytest.mark.asyncio
async def test_confirm_brief_workflow_action_returns_summary_envelope(client):
    presearch = await _create_presearch(client)

    response = await client.post(
        f"/content-research/workflows/{presearch['workflow_run_id']}/actions",
        json={
            "action": "confirm_brief",
            "payload": {
                "confirmed_subject": "徒步短裤",
                "subject_type": "category",
                "selected_competitors": ["迪卡侬"],
                "custom_competitors": ["凯乐石"],
                "selected_directions": ["product_marketing"],
                "custom_research_question": "轻量速干",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "content_research_workflow_action_response_v1"
    assert payload["workflow_run_id"] == presearch["workflow_run_id"]
    assert payload["action"] == "confirm_brief"
    assert payload["status"] == "completed"
    assert payload["execution_mode"] == "local"
    assert payload["sync_status"] == "local_only"
    assert payload["result"]["schema_version"] == "content_research_api_v1"
    assert payload["result"]["brief"]["payload"]["confirmed_subject"] == "徒步短裤"
    assert payload["result"]["plan"]["payload"]["selected_directions"] == ["product_marketing"]


@pytest.mark.asyncio
async def test_formal_research_actions_dispatch_specialists_without_parent_collection(client):
    presearch = await _create_presearch(client)
    confirm = await client.post(
        f"/content-research/workflows/{presearch['workflow_run_id']}/actions",
        json={
            "action": "confirm_brief",
            "payload": {
                "confirmed_subject": "徒步短裤",
                "subject_type": "category",
                "selected_directions": ["product_marketing", "comment_insight"],
            },
        },
    )
    assert confirm.status_code == 200

    for action in ("start_formal_research",):
        response = await client.post(
            f"/content-research/workflows/{presearch['workflow_run_id']}/actions",
            json={"action": action, "payload": {"query": "徒步短裤", "limit": 3}},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["action"] == action
        assert payload["status"] == "completed"
        assert payload["result"]["schema_version"] == "content_research_api_v1"
        assert payload["result"]["task_count"] == 2
        assert payload["result"]["completed_task_count"] == 2

    trace = await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}/trace")
    assert trace.status_code == 200
    runtime = trace.json()
    assert runtime["run_status"] == "succeeded"
    assert {task["status"] for task in runtime["runtime_child_tasks"]} <= {"succeeded", "failed"}
    completed_agents = {
        event["payload"].get("agent_name")
        for event in runtime["observation_events"]
        if event["event_name"] == "subagent_task_started"
    }
    assert completed_agents == {"ProductMarketingResearchAgent", "CommentInsightAgent"}


@pytest.mark.asyncio
async def test_end_content_research_action_clears_thread_active_run(client):
    presearch = await _create_presearch(client)

    response = await client.post(
        f"/content-research/workflows/{presearch['workflow_run_id']}/actions",
        json={"action": "end_content_research", "payload": {}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "end_content_research"
    assert payload["status"] == "completed"
    assert payload["result"]["ended"] is True
    assert payload["result"]["active_run_cleared"] is True
    assert payload["result"]["resources_destroyed"] is True

    service = app.state.content_research_service
    async with ThreadStore(service._store._db_path) as thread_store:
        thread = await thread_store.get_thread(presearch["_thread_id"])
    assert thread is None


@pytest.mark.asyncio
async def test_invalid_workflow_action_returns_contract_error(client):
    presearch = await _create_presearch(client)

    response = await client.post(
        f"/content-research/workflows/{presearch['workflow_run_id']}/actions",
        json={"action": "delete_everything", "payload": {}},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_CONTENT_RESEARCH_ACTION"


@pytest.mark.asyncio
async def test_legacy_confirm_endpoint_remains_compatible(client):
    presearch = await _create_presearch(client)

    response = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "徒步短裤",
            "subject_type": "category",
            "selected_directions": ["product_marketing"],
        },
    )

    assert response.status_code == 200
    assert response.json()["workflow_run_id"] == presearch["workflow_run_id"]
