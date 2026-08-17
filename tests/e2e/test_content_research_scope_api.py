from __future__ import annotations

import json
from dataclasses import replace

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

    final_queries = [
        "白衬衫通勤穿搭",
        *(group["final_query"] for group in scope["query_groups"][1:]),
    ]
    response = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "confirm_scope",
            "payload": {
                "scope_draft_id": scope["id"],
                "structure_hash": scope["structure_hash"],
                "query_groups": [
                    {"final_query": final_query} for final_query in final_queries
                ],
            },
        },
    )

    assert response.status_code == 200
    contract = response.json()["result"]["scope_contract"]
    store = app.state.content_research_service._store
    persisted_draft = store.get_scope_draft(scope["id"])
    assert persisted_draft is not None
    persisted_contract = store.get_scope_contract(
        workflow_run_id, version=contract["version"]
    )
    assert persisted_contract is not None
    assert contract["constraints"] == scope["constraints"]
    assert contract["query_groups"][0]["suggested_query"] == scope["query_groups"][0][
        "suggested_query"
    ]
    assert contract["query_groups"][0]["final_query"] == "白衬衫通勤穿搭"
    assert contract["query_groups"][0]["origin"] == "user_edited"
    assert contract["query_groups"][0]["execution_role"] == "exploratory"
    assert persisted_contract.constraints == persisted_draft.constraints
    assert (
        persisted_contract.query_groups[0].suggested_query
        == persisted_draft.query_groups[0].suggested_query
    )
    audit_event = response.json()["result"]["audit_event"]
    assert audit_event["event_name"] == "scope_confirmed"
    assert audit_event["payload"]["scope_draft_id"] == scope["id"]
    assert audit_event["payload"]["structure_hash"] == scope["structure_hash"]
    assert audit_event["payload"]["queries"][0] == {
        "query_group_id": contract["query_groups"][0]["id"],
        "suggested_query": scope["query_groups"][0]["suggested_query"],
        "final_query": "白衬衫通勤穿搭",
        "changed": True,
    }
    persisted_events = store.list_scope_audit_events(
        workflow_run_id, version=contract["version"]
    )
    assert len(persisted_events) == 1
    assert persisted_events[0].id == audit_event["id"]
    assert persisted_events[0].payload == audit_event["payload"]


@pytest.mark.asyncio
async def test_confirm_scope_rejects_stale_structure_hash(scope_client):
    workflow = await _scope_ready_workflow(scope_client)
    workflow_run_id = workflow["presearch"]["workflow_run_id"]
    prepared = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={"action": "prepare_scope", "payload": {"direction_id": "product_marketing"}},
    )
    scope = prepared.json()["result"]["scope"]

    response = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "confirm_scope",
            "payload": {
                "scope_draft_id": scope["id"],
                "structure_hash": "stale-structure-hash",
                "query_groups": [
                    {"final_query": group["final_query"]}
                    for group in scope["query_groups"]
                ],
            },
        },
    )

    assert response.status_code == 422
    assert "structure hash" in response.json()["error_message"]
    assert app.state.content_research_service._store.list_scope_contracts(workflow_run_id) == []


@pytest.mark.asyncio
async def test_confirm_scope_rejects_client_owned_constraint_and_query_fields(scope_client):
    workflow = await _scope_ready_workflow(scope_client)
    workflow_run_id = workflow["presearch"]["workflow_run_id"]
    prepared = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={"action": "prepare_scope", "payload": {"direction_id": "product_marketing"}},
    )
    scope = prepared.json()["result"]["scope"]

    response = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "confirm_scope",
            "payload": {
                "scope_draft_id": scope["id"],
                "structure_hash": scope["structure_hash"],
                "constraints": [
                    {
                        "id": "core_object",
                        "label": "核心对象",
                        "value": "客户端替换的核心对象",
                        "mode": "required",
                    }
                ],
                "query_groups": [
                    {
                        "suggested_query": "客户端替换的建议词",
                        "targeted_required_terms": ["客户端替换的核心对象"],
                        "final_query": group["final_query"],
                    }
                    for group in scope["query_groups"]
                ],
            },
        },
    )

    assert response.status_code == 422
    assert app.state.content_research_service._store.list_scope_contracts(workflow_run_id) == []


@pytest.mark.asyncio
async def test_confirm_scope_rejects_missing_final_query_edits(scope_client):
    workflow = await _scope_ready_workflow(scope_client)
    workflow_run_id = workflow["presearch"]["workflow_run_id"]
    prepared = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={"action": "prepare_scope", "payload": {"direction_id": "product_marketing"}},
    )
    scope = prepared.json()["result"]["scope"]

    response = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "confirm_scope",
            "payload": {
                "scope_draft_id": scope["id"],
                "structure_hash": scope["structure_hash"],
                "query_groups": [
                    {"final_query": scope["query_groups"][0]["final_query"]}
                ],
            },
        },
    )

    assert response.status_code == 422
    assert "final query count" in response.json()["error_message"]
    assert app.state.content_research_service._store.list_scope_contracts(workflow_run_id) == []


@pytest.mark.asyncio
async def test_confirm_scope_rejects_draft_when_current_brief_structure_changed(scope_client):
    workflow = await _scope_ready_workflow(scope_client)
    workflow_run_id = workflow["presearch"]["workflow_run_id"]
    prepared = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={"action": "prepare_scope", "payload": {"direction_id": "product_marketing"}},
    )
    scope = prepared.json()["result"]["scope"]
    store = app.state.content_research_service._store
    brief = store.get_brief_by_workflow(workflow_run_id)
    assert brief is not None
    store.save_brief(
        replace(
            brief,
            payload={**brief.payload, "subject_structure_hash": "new-structure-hash"},
        )
    )

    response = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "confirm_scope",
            "payload": {
                "scope_draft_id": scope["id"],
                "structure_hash": scope["structure_hash"],
                "query_groups": [
                    {"final_query": group["final_query"]}
                    for group in scope["query_groups"]
                ],
            },
        },
    )

    assert response.status_code == 422
    assert "current brief" in response.json()["error_message"]
    assert store.list_scope_contracts(workflow_run_id) == []


@pytest.mark.asyncio
async def test_confirm_scope_rechecks_current_brief_inside_atomic_confirmation(scope_client, monkeypatch):
    workflow = await _scope_ready_workflow(scope_client)
    workflow_run_id = workflow["presearch"]["workflow_run_id"]
    prepared = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={"action": "prepare_scope", "payload": {"direction_id": "product_marketing"}},
    )
    scope = prepared.json()["result"]["scope"]
    store = app.state.content_research_service._store
    original_confirm = store.confirm_scope_atomically

    def update_brief_then_confirm(*args, **kwargs):
        brief = store.get_brief_by_workflow(workflow_run_id)
        assert brief is not None
        store.save_brief(
            replace(
                brief,
                payload={
                    **brief.payload,
                    "subject_structure_hash": "interleaved-structure-hash",
                },
            )
        )
        return original_confirm(*args, **kwargs)

    monkeypatch.setattr(store, "confirm_scope_atomically", update_brief_then_confirm)
    response = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "confirm_scope",
            "payload": {
                "scope_draft_id": scope["id"],
                "structure_hash": scope["structure_hash"],
                "query_groups": [
                    {"final_query": group["final_query"]}
                    for group in scope["query_groups"]
                ],
            },
        },
    )

    assert response.status_code == 422
    assert "current brief" in response.json()["error_message"]
    assert store.list_scope_contracts(workflow_run_id) == []


@pytest.mark.asyncio
async def test_scope_projection_recovers_persisted_draft_contract_and_audits(scope_client):
    """A scope read must be reconstructed from the immutable SQLite records."""
    workflow = await _scope_ready_workflow(scope_client)
    workflow_run_id = workflow["presearch"]["workflow_run_id"]
    prepared = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={"action": "prepare_scope", "payload": {"direction_id": "product_marketing"}},
    )
    assert prepared.status_code == 200
    prepared_draft_id = prepared.json()["result"]["scope"]["id"]
    confirmed = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "confirm_scope",
            "payload": {
                "scope_draft_id": prepared_draft_id,
                "structure_hash": prepared.json()["result"]["scope"]["structure_hash"],
                "query_groups": [
                    {"final_query": group["final_query"]}
                    for group in prepared.json()["result"]["scope"]["query_groups"]
                ],
            },
        },
    )
    assert confirmed.status_code == 200
    confirmed_contract_id = confirmed.json()["result"]["scope_contract"]["id"]

    response = await scope_client.get(f"/content-research/workflows/{workflow_run_id}/scope")
    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["id"] == prepared_draft_id
    assert body["scope_contract"]["id"] == confirmed_contract_id
    assert [event["event_name"] for event in body["audit_events"]] == [
        "scope_suggested",
        "scope_confirmed",
    ]
    assert isinstance(body["draft"]["created_at"], str)
    assert isinstance(body["scope_contract"]["created_at"], str)
    assert all(isinstance(event["created_at"], str) for event in body["audit_events"])

    versioned = await scope_client.get(
        f"/content-research/workflows/{workflow_run_id}/scope?version=1"
    )
    assert versioned.status_code == 200
    assert versioned.json()["scope_contract"]["id"] == confirmed_contract_id

    missing_version = await scope_client.get(
        f"/content-research/workflows/{workflow_run_id}/scope?version=2"
    )
    assert missing_version.status_code == 404
    assert missing_version.json()["error_code"] == "CONTENT_RESEARCH_PRESEARCH_NOT_FOUND"
