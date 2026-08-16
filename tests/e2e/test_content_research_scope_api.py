from __future__ import annotations

import json

import httpx
import pytest

from app.api.routes.router import app
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.services.llm.types import LLMResponse, TokenUsage
from tests.e2e.test_content_research_brief_confirm_api import WORKSPACE_HEADERS, FakeRuntime


class SummerCommuteFakeLLM:
    async def generate(self, _request):
        return LLMResponse(
            content=json.dumps(
                {
                    "subject_confirmation": "夏季通勤长袖的产品营销调研，请确认。",
                    "competitor_tags": [],
                    "research_directions": ["产品营销"],
                    "custom_research_question": "",
                    "custom_competitor_input": "",
                    "subject_structure": {
                        "schema_version": "content_research_subject_structure_v1",
                        "canonical_subject": "夏季通勤长袖",
                        "subject_type": "category",
                        "core_entities": [
                            {
                                "canonical_name": "长袖衬衫",
                                "raw_mentions": ["长袖"],
                            }
                        ],
                        "research_intents": ["通勤"],
                        "context_modifiers": ["夏季"],
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
async def scope_client(tmp_path):
    original = getattr(app.state, "content_research_service", None)
    db_path = str(tmp_path / "content_research.db")
    app.state.content_research_service = ContentResearchService(
        store=SQLiteContentResearchStore(db_path),
        presearch=PresearchService(
            SummerCommuteFakeLLM(), first_feedback_timeout_seconds=0.05, hard_cutoff_seconds=0.1
        ),
        workflow_runtime=FakeRuntime(db_path),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers=WORKSPACE_HEADERS,
    ) as client:
        yield client
    if original is None:
        delattr(app.state, "content_research_service")
    else:
        app.state.content_research_service = original


async def _scope_ready_workflow(client: httpx.AsyncClient) -> dict:
    presearch = await client.post(
        "/content-research/presearch",
        json={"seed_text": "夏季通勤长袖", "thread_id": "thread-scope"},
    )
    assert presearch.status_code == 201
    brief = presearch.json()
    confirmed = await client.post(
        f"/content-research/briefs/{brief['brief_id']}/confirm",
        json={
            "confirmed_subject": "夏季通勤长袖",
            "subject_structure_hash": brief["subject_structure_hash"],
            "subject_type": "category",
            "selected_directions": ["product_marketing"],
            "primary_marketing_goal": "content_seeding",
            "subject_structure_confirmation": {
                "core_object": "长袖衬衫",
                "research_intent": "通勤",
                "context_modifiers": ["夏季"],
            },
        },
    )
    assert confirmed.status_code == 200
    return {"presearch": brief, "summary": confirmed.json()}


@pytest.mark.asyncio
async def test_prepare_scope_preserves_summer_commute_constraints_and_audits_draft(scope_client):
    workflow = await _scope_ready_workflow(scope_client)
    workflow_run_id = workflow["presearch"]["workflow_run_id"]

    response = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={"action": "prepare_scope", "payload": {"direction_id": "product_marketing"}},
    )

    assert response.status_code == 200
    scope = response.json()["result"]["scope"]
    assert {
        (constraint["id"], constraint["value"], constraint["mode"])
        for constraint in scope["constraints"]
    } == {
        ("core_object", "长袖衬衫", "required"),
        ("season", "夏季", "required"),
        ("scenario", "通勤", "required"),
    }
    assert any(
        all(term in group["suggested_query"] for term in ("夏季", "长袖衬衫", "通勤"))
        for group in scope["query_groups"]
    )
    assert scope["audit_event"]["event_name"] == "scope_suggested"
    assert scope["audit_event"]["scope_draft_id"] == scope["id"]


@pytest.mark.asyncio
async def test_confirm_scope_keeps_arbitrary_user_query_and_persists_matching_audit(scope_client):
    workflow = await _scope_ready_workflow(scope_client)
    workflow_run_id = workflow["presearch"]["workflow_run_id"]
    prepared = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={"action": "prepare_scope", "payload": {"direction_id": "product_marketing"}},
    )
    assert prepared.status_code == 200
    scope = prepared.json()["result"]["scope"]

    scope["query_groups"][0]["final_query"] = "白衬衫通勤穿搭"
    response = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "confirm_scope",
            "payload": {
                "scope_draft_id": scope["id"],
                "research_plan_id": scope["research_plan_id"],
                "constraints": scope["constraints"],
                "query_groups": scope["query_groups"],
            },
        },
    )

    assert response.status_code == 200
    contract = response.json()["result"]["scope_contract"]
    assert contract["query_groups"][0]["final_query"] == "白衬衫通勤穿搭"
    assert contract["query_groups"][0]["origin"] == "user_edited"
    assert contract["query_groups"][0]["execution_role"] == "exploratory"
    assert response.json()["result"]["audit_event"]["event_name"] == "scope_confirmed"
