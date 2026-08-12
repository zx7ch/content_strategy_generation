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
                    "subject_structure": {
                        "schema_version": "content_research_subject_structure_v1",
                        "canonical_subject": "Satisfy Running",
                        "subject_type": "brand",
                        "core_entities": [{"canonical_name": "Satisfy Running", "raw_mentions": ["Satisfy Running"]}],
                        "research_intents": ["品牌内容"],
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
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    if original is None:
        delattr(app.state, "content_research_service")
    else:
        app.state.content_research_service = original


async def _create_workflow(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/content-research/presearch",
        headers={"X-User-Id": "user-decision-1"},
        json={
            "seed_text": "Satisfy Running",
            "user_note": "关注跑步社群",
            "thread_id": "thread-decision-1",
        },
    )
    assert response.status_code == 201
    return response.json()["workflow_run_id"]


@pytest.mark.asyncio
async def test_brand_and_content_decisions_are_replayable_after_refresh(client):
    workflow_run_id = await _create_workflow(client)

    brand_response = await client.post(
        f"/content-research/workflows/{workflow_run_id}/brand-decisions",
        headers={"X-User-Id": "user-decision-1"},
        json={
            "target_id": "brand_satisfy",
            "decision_request_id": "brand_req_1",
            "decision_status": "selected",
            "rationale": "品牌内容值得深入",
        },
    )
    assert brand_response.status_code == 200
    brand_decision = brand_response.json()
    assert brand_decision["target_type"] == "brand_candidate"
    assert brand_decision["decision_status"] == "selected"
    assert brand_decision["is_current"] is True
    assert brand_decision["advancement"]["resource_policy"] == "full_deep_research"

    content_response = await client.post(
        f"/content-research/workflows/{workflow_run_id}/content-decisions",
        headers={"X-User-Id": "user-decision-1"},
        json={
            "target_id": "content_commute",
            "decision_request_id": "content_req_1",
            "decision_status": "watchlist",
        },
    )
    assert content_response.status_code == 200
    content_decision = content_response.json()
    assert content_decision["target_type"] == "recommended_content"
    assert content_decision["advancement"]["resource_policy"] == "deferred"

    decisions_response = await client.get(f"/content-research/workflows/{workflow_run_id}/decisions")
    assert decisions_response.status_code == 200
    replay = decisions_response.json()
    assert [item["decision_id"] for item in replay["decisions"]] == [
        brand_decision["decision_id"],
        content_decision["decision_id"],
    ]
    assert {item["decision_id"] for item in replay["current_decisions"]} == {
        brand_decision["decision_id"],
        content_decision["decision_id"],
    }

    events_response = await client.get(f"/content-research/workflows/{workflow_run_id}/events")
    assert events_response.status_code == 200
    decision_events = [
        event for event in events_response.json()["events"] if event["event_type"] == "human_decision_submitted"
    ]
    assert [event["payload_json"]["decision_id"] for event in decision_events] == [
        brand_decision["decision_id"],
        content_decision["decision_id"],
    ]


@pytest.mark.asyncio
async def test_decision_idempotency_and_change_of_mind(client):
    workflow_run_id = await _create_workflow(client)
    payload = {
        "target_id": "brand_satisfy",
        "decision_request_id": "brand_req_idempotent",
        "decision_status": "watchlist",
    }

    first = await client.post(f"/content-research/workflows/{workflow_run_id}/brand-decisions", json=payload)
    second = await client.post(f"/content-research/workflows/{workflow_run_id}/brand-decisions", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["decision_id"] == first.json()["decision_id"]
    assert second.json()["idempotent_replay"] is True

    changed = await client.post(
        f"/content-research/workflows/{workflow_run_id}/brand-decisions",
        json={
            "target_id": "brand_satisfy",
            "decision_request_id": "brand_req_changed",
            "decision_status": "rejected",
        },
    )
    assert changed.status_code == 200
    assert changed.json()["decision_id"] != first.json()["decision_id"]

    replay = (await client.get(f"/content-research/workflows/{workflow_run_id}/decisions")).json()
    assert len(replay["decisions"]) == 2
    assert [item["decision_id"] for item in replay["current_decisions"]] == [changed.json()["decision_id"]]

    events = (await client.get(f"/content-research/workflows/{workflow_run_id}/events")).json()["events"]
    decision_events = [event for event in events if event["event_type"] == "human_decision_submitted"]
    assert len(decision_events) == 2


@pytest.mark.asyncio
async def test_invalid_decision_payload_returns_422_without_events(client):
    workflow_run_id = await _create_workflow(client)

    response = await client.post(
        f"/content-research/workflows/{workflow_run_id}/brand-decisions",
        json={
            "target_id": "brand_satisfy",
            "decision_request_id": "bad_req",
            "decision_status": "maybe",
        },
    )

    assert response.status_code == 422
    decisions = (await client.get(f"/content-research/workflows/{workflow_run_id}/decisions")).json()
    assert decisions["decisions"] == []
