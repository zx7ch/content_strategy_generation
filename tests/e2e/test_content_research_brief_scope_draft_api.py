from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

from app.api.routes.router import app
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from app.services.llm.types import LLMResponse, TokenUsage


class ScopeDraftLLM:
    async def generate(self, _request):
        return LLMResponse(
            content=json.dumps(
                {
                    "subject_confirmation": "夏季凉感T恤",
                    "competitor_tags": ["优衣库", "蕉下"],
                    "research_directions": ["product_marketing"],
                    "custom_competitor_input": "",
                    "subject_structure": {
                        "schema_version": "content_research_subject_structure_v1",
                        "canonical_subject": "夏季凉感T恤",
                        "subject_type": "category",
                        "source_terms": ["夏季", "凉感", "T恤"],
                        "term_roles": {
                            "core_object": ["T恤"],
                            "product_experience": ["凉感"],
                            "context_audience": ["夏季"],
                        },
                        "core_entities": [
                            {
                                "canonical_name": "T恤",
                                "raw_mentions": ["T恤"],
                            }
                        ],
                        "research_intents": ["凉感"],
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


class InvalidScopeDraftLLM(ScopeDraftLLM):
    async def generate(self, request):
        response = await super().generate(request)
        payload = json.loads(response.content)
        payload["subject_structure"]["term_roles"]["product_experience"] = [
            "透气性"
        ]
        return LLMResponse(
            content=json.dumps(payload, ensure_ascii=False),
            provider=response.provider,
            model=response.model,
            usage=response.usage,
            latency_ms=response.latency_ms,
        )


class LegacyScopeDraftLLM(ScopeDraftLLM):
    async def generate(self, request):
        response = await super().generate(request)
        payload = json.loads(response.content)
        structure = payload["subject_structure"]
        structure.pop("source_terms")
        structure.pop("term_roles")
        structure["core_entities"] = [
            {"canonical_name": "服装", "raw_mentions": ["服装"]}
        ]
        structure["research_intents"] = ["透气性"]
        return LLMResponse(
            content=json.dumps(payload, ensure_ascii=False),
            provider=response.provider,
            model=response.model,
            usage=response.usage,
            latency_ms=response.latency_ms,
        )


class ScopeDraftRuntime:
    async def get_runtime_snapshot(self, workflow_run_id: str) -> dict:
        return {"run": {"run_id": workflow_run_id}, "steps": [], "child_tasks": []}

    async def list_events(self, _workflow_run_id: str) -> list[dict]:
        return []


@pytest.fixture()
async def scope_client(tmp_path):
    db_path = str(tmp_path / "scope-draft.db")
    previous = getattr(app.state, "content_research_service", None)
    service = ContentResearchService(
        store=SQLiteContentResearchStore(db_path),
        presearch=PresearchService(
            ScopeDraftLLM(),
            first_feedback_timeout_seconds=0.05,
            hard_cutoff_seconds=0.1,
        ),
        workflow_runtime=ScopeDraftRuntime(),
    )
    app.state.content_research_service = service
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(title="夏季凉感T恤", workspace_id="ws-scope")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, db_path, thread["id"]
    if previous is None:
        delattr(app.state, "content_research_service")
    else:
        app.state.content_research_service = previous


async def _presearch(client: httpx.AsyncClient, thread_id: str) -> dict:
    response = await client.post(
        "/content-research/presearch",
        headers={"X-Workspace-Id": "ws-scope", "X-User-Id": "user-scope"},
        json={
            "command_id": "scope-submit",
            "seed_text": "夏季凉感T恤",
            "user_note": None,
            "thread_id": thread_id,
        },
    )
    assert response.status_code == 201
    return response.json()


def _rows(db_path: str, table: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


@pytest.mark.asyncio
async def test_failed_system_mapping_cannot_reach_an_executable_scope_query(
    scope_client,
):
    client, _db_path, thread_id = scope_client
    app.state.content_research_service._presearch = PresearchService(
        InvalidScopeDraftLLM(),
        first_feedback_timeout_seconds=0.05,
        hard_cutoff_seconds=0.1,
    )
    presearch = await _presearch(client, thread_id)

    assert presearch["subject_structure_analysis_state"] == "needs_confirmation"
    assert presearch["subject_structure"]["research_intents"] == []
    assert "透气性" not in str(presearch["subject_structure"])
    run = presearch["run"]
    confirmed = await client.post(
        f"/content-research/workflows/{run['run_id']}/actions",
        json={
            "command_id": "confirm-invalid-system-mapping",
            "expected_state": run["state"],
            "expected_revision": run["state_revision"],
            "action": "confirm_brief",
            "payload": {
                "brief_id": presearch["brief_id"],
                "selected_competitors": [],
                "custom_competitor_input": "",
                "selected_directions": ["product_marketing"],
            },
        },
    )

    assert confirmed.status_code == 200, confirmed.text
    queries = [
        group["final_query"]
        for group in confirmed.json()["result"]["scope"]["draft"]["query_groups"]
    ]
    assert queries == ["T恤", "T恤 夏季"]
    assert all("透气性" not in query for query in queries)


@pytest.mark.asyncio
async def test_legacy_system_structure_cannot_fall_back_into_a_scope_draft(
    scope_client,
):
    client, db_path, thread_id = scope_client
    app.state.content_research_service._presearch = PresearchService(
        LegacyScopeDraftLLM(),
        first_feedback_timeout_seconds=0.05,
        hard_cutoff_seconds=0.1,
    )
    presearch = await _presearch(client, thread_id)

    assert presearch["subject_structure"]["core_entities"] == []
    assert presearch["subject_structure"]["research_intents"] == []
    run = presearch["run"]
    confirmed = await client.post(
        f"/content-research/workflows/{run['run_id']}/actions",
        json={
            "command_id": "reject-legacy-system-structure",
            "expected_state": run["state"],
            "expected_revision": run["state_revision"],
            "action": "confirm_brief",
            "payload": {
                "brief_id": presearch["brief_id"],
                "selected_competitors": [],
                "custom_competitor_input": "",
                "selected_directions": ["product_marketing"],
            },
        },
    )

    assert confirmed.status_code == 422
    assert _rows(db_path, "content_research_plans") == 0
    assert _rows(db_path, "content_research_scope_drafts") == 0


@pytest.mark.asyncio
async def test_grounded_term_mapping_reaches_one_editable_server_compiled_scope_without_collection(
    scope_client,
):
    client, db_path, thread_id = scope_client
    presearch = await _presearch(client, thread_id)
    run = presearch["run"]
    command = {
        "command_id": "confirm-brief-once",
        "expected_state": run["state"],
        "expected_revision": run["state_revision"],
        "action": "confirm_brief",
        "payload": {
            "brief_id": presearch["brief_id"],
            "selected_competitors": ["优衣库"],
            "custom_competitor_input": "",
            "selected_directions": ["product_marketing"],
        },
    }

    confirmed = await client.post(
        f"/content-research/workflows/{run['run_id']}/actions", json=command
    )
    replay = await client.post(
        f"/content-research/workflows/{run['run_id']}/actions", json=command
    )

    assert confirmed.status_code == replay.status_code == 200
    assert replay.json() == confirmed.json()
    projection = confirmed.json()["result"]["run"]
    assert projection["state"] == "scope_confirmation_required"
    assert projection["state_revision"] == run["state_revision"] + 1
    workflow = await client.get(f"/content-research/workflows/{run['run_id']}")
    trace = await client.get(f"/content-research/workflows/{run['run_id']}/trace")
    assert workflow.status_code == 200, workflow.text
    assert trace.status_code == 200, trace.text

    scope = (await client.get(
        f"/content-research/workflows/{run['run_id']}/scope"
    )).json()
    assert scope["state"] == "scope_confirmation_required"
    assert scope["state_revision"] == projection["state_revision"]
    assert scope["run"] == projection
    assert scope["scope_contract"] is None
    assert [action["action"] for action in scope["allowed_actions"]] == [
        "replace_scope_draft"
    ]
    assert scope["subject_structure_analysis_state"] == "confirmed"
    assert scope["subject_structure_analysis_reason_codes"] == []
    assert scope["draft"]["core_object"] == "T恤"
    assert scope["draft"]["product_experience_aspect"] == "凉感"
    assert scope["draft"]["context_audience_aspect"] == "夏季"
    assert [group["final_query"] for group in scope["draft"]["query_groups"]] == [
        "T恤",
        "T恤 凉感",
        "T恤 夏季",
    ]
    assert _rows(db_path, "content_research_plans") == 1
    assert _rows(db_path, "content_research_scope_drafts") == 1
    assert _rows(db_path, "content_research_scope_contracts") == 0
    assert _rows(db_path, "content_research_dispatch_jobs") == 0
    assert _rows(db_path, "content_research_subagent_tasks") == 0

    cancelled = await client.post(
        f"/content-research/workflows/{run['run_id']}/actions",
        json={
            "command_id": "cancel-scope-draft",
            "expected_state": projection["state"],
            "expected_revision": projection["state_revision"],
            "action": "cancel",
            "payload": {},
        },
    )
    assert cancelled.status_code == 200
    cancelled_scope = (await client.get(
        f"/content-research/workflows/{run['run_id']}/scope"
    )).json()
    assert cancelled_scope["state"] == "cancelled_or_failed"
    assert cancelled_scope["allowed_actions"] == []


@pytest.mark.asyncio
async def test_missing_bc_can_replace_only_latest_scope_draft(scope_client):
    client, db_path, thread_id = scope_client
    presearch = await _presearch(client, thread_id)
    run = presearch["run"]
    confirmed = await client.post(
        f"/content-research/workflows/{run['run_id']}/actions",
        json={
            "command_id": "confirm-for-replace",
            "expected_state": run["state"],
            "expected_revision": run["state_revision"],
            "action": "confirm_brief",
            "payload": {
                "brief_id": presearch["brief_id"],
                "selected_competitors": [],
                "custom_competitor_input": "",
                "selected_directions": ["product_marketing"],
            },
        },
    )
    confirmed_run = confirmed.json()["result"]["run"]
    original = (await client.get(
        f"/content-research/workflows/{run['run_id']}/scope"
    )).json()["draft"]
    replacement_command = {
        "command_id": "replace-scope-bc",
        "expected_state": confirmed_run["state"],
        "expected_revision": confirmed_run["state_revision"],
        "action": "replace_scope_draft",
        "payload": {
            "scope_draft_id": original["id"],
            "core_object": "T恤",
            "product_experience_aspect": "凉感",
            "context_audience_aspect": "夏季",
        },
    }

    replaced = await client.post(
        f"/content-research/workflows/{run['run_id']}/actions",
        json=replacement_command,
    )
    replay = await client.post(
        f"/content-research/workflows/{run['run_id']}/actions",
        json=replacement_command,
    )
    stale = await client.post(
        f"/content-research/workflows/{run['run_id']}/actions",
        json={**replacement_command, "command_id": "stale-replace"},
    )

    assert replaced.status_code == replay.status_code == 200
    assert replay.json() == replaced.json()
    assert stale.status_code == 409
    latest = (await client.get(
        f"/content-research/workflows/{run['run_id']}/scope"
    )).json()["draft"]
    assert latest["id"] != original["id"]
    assert [group["final_query"] for group in latest["query_groups"]] == [
        "T恤",
        "T恤 凉感",
        "T恤 夏季",
    ]
    assert latest["constraints"] == [
        {
            "id": "core_object",
            "label": "核心对象",
            "value": "T恤",
            "mode": "required",
            "allowed_aliases": [],
        }
    ]
    assert all(
        group["targeted_required_terms"] == ["T恤"]
        for group in latest["query_groups"]
    )
    assert all(group["origin"] == "user_edited" for group in latest["query_groups"])
    assert _rows(db_path, "content_research_scope_drafts") == 2
    assert _rows(db_path, "content_research_scope_contracts") == 0
    assert _rows(db_path, "content_research_dispatch_jobs") == 0
