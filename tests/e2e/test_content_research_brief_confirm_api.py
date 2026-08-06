from __future__ import annotations

import json
from dataclasses import replace

import aiosqlite
import httpx
import pytest

from app.api.routes.router import app
from app.config import settings
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.services.llm.types import LLMResponse, TokenUsage

WORKSPACE_HEADERS = {"X-Workspace-Id": "ws-1", "X-User-Id": "user-1"}


class FakeRuntime:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def start_presearch_run(self, *, thread_id: str, user_id: str, seed_text: str) -> str:
        return "run_confirm_1"

    async def mark_presearch_ready(self, workflow_run_id: str) -> None:
        return None

    async def complete_brief_and_plan_atomically(
        self, *, workflow_run_id: str, task_specs: list[dict], confirmation_writer
    ) -> list[str]:
        child_ids = [f"child_{index}" for index, _spec in enumerate(task_specs)]
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await confirmation_writer(conn, child_ids)
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
        return child_ids

    async def get_runtime_snapshot(self, workflow_run_id: str) -> dict:
        return {
            "run": {"run_id": workflow_run_id, "current_step": "source_collect_minimal"},
            "steps": [],
            "child_tasks": [],
        }

    async def list_events(self, workflow_run_id: str) -> list[dict]:
        return []


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
                    "subject_structure": {
                        "schema_version": "content_research_subject_structure_v1",
                        "canonical_subject": "徒步短裤",
                        "subject_type": "category",
                        "core_entities": [
                            {
                                "canonical_name": "短裤",
                                "raw_mentions": ["短裤"],
                            }
                        ],
                        "research_intents": ["产品营销"],
                        "context_modifiers": [],
                        "synonym_groups": {"短裤": ["户外短裤"]},
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


class ProductMarketingFakeLLM:
    async def generate(self, _request):
        return LLMResponse(
            content=json.dumps(
                {
                    "subject_confirmation": "夏季凉感 T 恤的产品营销调研，请确认。",
                    "competitor_tags": [],
                    "research_directions": ["产品营销"],
                    "custom_research_question": "",
                    "custom_competitor_input": "",
                    "subject_structure": {
                        "schema_version": "content_research_subject_structure_v1",
                        "canonical_subject": "夏季凉感 T 恤",
                        "subject_type": "category",
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


@pytest.fixture()
async def client(tmp_path):
    original = getattr(app.state, "content_research_service", None)
    app.state.content_research_service = ContentResearchService(
        store=SQLiteContentResearchStore(str(tmp_path / "content_research.db")),
        presearch=PresearchService(
            FakeLLM(), first_feedback_timeout_seconds=0.05, hard_cutoff_seconds=0.1
        ),
        workflow_runtime=FakeRuntime(str(tmp_path / "content_research.db")),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers=WORKSPACE_HEADERS,
    ) as c:
        yield c
    if original is None:
        delattr(app.state, "content_research_service")
    else:
        app.state.content_research_service = original


@pytest.fixture()
async def product_marketing_client(tmp_path):
    original = getattr(app.state, "content_research_service", None)
    app.state.content_research_service = ContentResearchService(
        store=SQLiteContentResearchStore(str(tmp_path / "content_research.db")),
        presearch=PresearchService(
            ProductMarketingFakeLLM(), first_feedback_timeout_seconds=0.05, hard_cutoff_seconds=0.1
        ),
        workflow_runtime=FakeRuntime(str(tmp_path / "content_research.db")),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers=WORKSPACE_HEADERS,
    ) as c:
        yield c
    if original is None:
        delattr(app.state, "content_research_service")
    else:
        app.state.content_research_service = original


async def _create_presearch(client, *, seed_text: str = "徒步短裤"):
    response = await client.post(
        "/content-research/presearch",
        json={"seed_text": seed_text, "thread_id": "thread-1"},
    )
    assert response.status_code == 201
    return response.json()


def _shorts_structure_confirmation() -> dict:
    return {
        "core_object": "短裤",
        "research_intent": "产品营销",
        "context_modifiers": [],
    }


async def _confirm_product_marketing_brief(client, *, custom_research_question: str = ""):
    presearch = await _create_presearch(client, seed_text="夏季凉感 T恤")
    response = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "夏季凉感 T 恤",
            "subject_structure_hash": presearch["subject_structure_hash"],
            "subject_type": "category",
            "selected_directions": ["product_marketing"],
            "custom_research_question": custom_research_question,
            "primary_marketing_goal": "content_seeding",
            "subject_structure_confirmation": {
                "core_object": "T恤",
                "research_intent": "凉感",
                "context_modifiers": ["夏季"],
            },
        },
    )
    assert response.status_code == 200
    snapshot = await client.get(
        f"/content-research/workflows/{presearch['workflow_run_id']}/policy-snapshot"
    )
    assert snapshot.status_code == 200
    return snapshot.json()["effective_policy"]["locked_query_plan"]


@pytest.mark.asyncio
async def test_product_marketing_confirmation_requires_explicit_structure_fields(
    product_marketing_client,
):
    presearch = await _create_presearch(product_marketing_client, seed_text="夏季凉感 T恤")

    response = await product_marketing_client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "夏季凉感 T 恤",
            "subject_structure_hash": presearch["subject_structure_hash"],
            "subject_type": "category",
            "selected_directions": ["product_marketing"],
            "primary_marketing_goal": "content_seeding",
        },
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_CONTENT_RESEARCH_PAYLOAD"


@pytest.mark.asyncio
async def test_product_marketing_confirmation_freezes_explicit_structure_fields_and_query_plan(
    product_marketing_client,
):
    presearch = await _create_presearch(product_marketing_client, seed_text="夏季凉感 T恤")

    response = await product_marketing_client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "夏季透气 T 恤",
            "subject_structure_hash": presearch["subject_structure_hash"],
            "subject_type": "category",
            "selected_directions": ["product_marketing"],
            "primary_marketing_goal": "content_seeding",
            "subject_structure_confirmation": {
                "core_object": "T恤",
                "research_intent": "透气",
                "context_modifiers": ["通勤", "夏季"],
            },
        },
    )

    assert response.status_code == 200
    frozen_structure = response.json()["brief"]["payload"]["subject_structure"]
    assert frozen_structure["core_entities"][0]["canonical_name"] == "T恤"
    assert frozen_structure["research_intents"] == ["透气"]
    assert frozen_structure["context_modifiers"] == ["通勤", "夏季"]
    assert response.json()["brief"]["payload"]["subject_structure_user_confirmed_fields"] == [
        "core_entities[0]",
        "research_intents[0]",
        "context_modifiers",
    ]

    snapshot = await product_marketing_client.get(
        f"/content-research/workflows/{presearch['workflow_run_id']}/policy-snapshot"
    )
    groups = snapshot.json()["effective_policy"]["locked_query_plan"]["directions"][
        "product_marketing"
    ]["query_groups"]
    assert [group["normalized_query"] for group in groups if group["activation"] == "primary"] == [
        "T恤 透气",
        "T恤 透气 上身感受",
    ]


@pytest.mark.asyncio
async def test_product_marketing_confirmation_freezes_user_corrected_core_absent_from_seed(
    product_marketing_client,
):
    presearch = await _create_presearch(product_marketing_client, seed_text="夏季凉感 T恤")

    response = await product_marketing_client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "夏季透气背心",
            "subject_structure_hash": presearch["subject_structure_hash"],
            "subject_type": "category",
            "selected_directions": ["product_marketing"],
            "primary_marketing_goal": "content_seeding",
            "subject_structure_confirmation": {
                "core_object": "背心",
                "research_intent": "透气",
                "context_modifiers": ["夏季"],
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["brief"]["payload"]["subject_structure"]["core_entities"] == [
        {"canonical_name": "背心", "raw_mentions": ["背心"]}
    ]
    snapshot = await product_marketing_client.get(
        f"/content-research/workflows/{presearch['workflow_run_id']}/policy-snapshot"
    )
    groups = snapshot.json()["effective_policy"]["locked_query_plan"]["directions"][
        "product_marketing"
    ]["query_groups"]
    assert [group["normalized_query"] for group in groups if group["activation"] == "primary"] == [
        "背心 透气",
        "背心 透气 上身感受",
    ]


@pytest.mark.asyncio
async def test_product_marketing_confirmation_reuses_task_one_confirmed_fields_without_second_payload(
    product_marketing_client,
):
    presearch = await _create_presearch(product_marketing_client, seed_text="夏季凉感 T恤")
    service = app.state.content_research_service
    brief = service._store.get_brief(presearch["brief_id"])
    assert brief is not None
    service._store.save_brief(
        replace(
            brief,
            payload={
                **brief.payload,
                "subject_structure_user_confirmed_fields": [
                    "core_entities[0]",
                    "research_intents[0]",
                    "context_modifiers",
                ],
            },
        )
    )

    response = await product_marketing_client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "夏季凉感 T 恤",
            "subject_structure_hash": presearch["subject_structure_hash"],
            "subject_type": "category",
            "selected_directions": ["product_marketing"],
            "primary_marketing_goal": "content_seeding",
        },
    )

    assert response.status_code == 200
    assert response.json()["brief"]["payload"]["subject_structure_user_confirmed_fields"] == [
        "core_entities[0]",
        "research_intents[0]",
        "context_modifiers",
    ]


@pytest.mark.asyncio
async def test_confirmed_product_marketing_plan_uses_goal_facet_with_subject_intent(
    product_marketing_client,
):
    locked_plan = await _confirm_product_marketing_brief(product_marketing_client)

    groups = locked_plan["directions"]["product_marketing"]["query_groups"]
    primary_queries = [
        group["normalized_query"] for group in groups if group["activation"] == "primary"
    ]
    assert primary_queries == ["T恤 凉感", "T恤 凉感 上身感受"]


@pytest.mark.asyncio
async def test_confirmed_product_marketing_plan_prefers_custom_focus_over_goal_facet(
    product_marketing_client,
):
    locked_plan = await _confirm_product_marketing_brief(
        product_marketing_client,
        custom_research_question="通勤",
    )

    groups = locked_plan["directions"]["product_marketing"]["query_groups"]
    primary_queries = [
        group["normalized_query"] for group in groups if group["activation"] == "primary"
    ]
    assert primary_queries == ["T恤 凉感", "T恤 凉感 通勤"]


@pytest.mark.asyncio
async def test_presearch_exposes_fixed_lite_direction_catalog_separately_from_llm_suggestions(
    client,
):
    presearch = await _create_presearch(client)

    assert presearch["research_directions"] == ["产品营销", "用户评论痛点"]
    assert presearch["direction_catalog"] == [
        "product_marketing",
        "competitor_discovery",
        "content_performance",
    ]


@pytest.mark.asyncio
async def test_confirm_brief_creates_plan_directions_tasks_and_workflow_summary(client):
    presearch = await _create_presearch(client)

    response = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
                "confirmed_subject": "徒步短裤",
                "subject_structure_hash": presearch["subject_structure_hash"],
            "subject_type": "category",
            "selected_competitors": ["迪卡侬"],
            "custom_competitors": ["凯乐石"],
            "selected_directions": ["product_marketing", "competitor_discovery"],
            "custom_research_question": "关注夏季轻量户外",
            "primary_marketing_goal": "content_seeding",
            "subject_structure_confirmation": _shorts_structure_confirmation(),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_run_id"] == presearch["workflow_run_id"]
    assert payload["brief"]["status"] == "ready"
    assert payload["brief"]["payload"]["confirmed_subject"] == "徒步短裤"
    assert payload["brief"]["payload"]["subject_structure_hash"] != presearch[
        "subject_structure_hash"
    ]
    assert payload["brief"]["payload"]["subject_structure_user_confirmed_fields"] == [
        "core_entities[0]",
        "research_intents[0]",
        "context_modifiers",
    ]
    frozen_structure_hash = payload["brief"]["payload"]["subject_structure_hash"]
    assert payload["brief"]["payload"]["selected_competitors"] == ["迪卡侬"]
    assert payload["brief"]["payload"]["custom_competitors"] == ["凯乐石"]
    assert payload["brief"]["payload"]["primary_marketing_goal"] == "content_seeding"
    assert payload["brief"]["payload"]["direction_catalog"] == [
        "product_marketing",
        "competitor_discovery",
        "content_performance",
    ]
    assert payload["brief"]["payload"]["requested_direction_ids"] == [
        "product_marketing",
        "competitor_discovery",
    ]
    assert [item["payload"]["direction_id"] for item in payload["directions"]] == [
        "product_marketing",
        "competitor_discovery",
    ]
    assert [item["status"] for item in payload["subagent_tasks"]] == ["queued", "queued"]
    assert {
        (
            item["payload"]["llm_scope"]["workspace_id"],
            item["payload"]["llm_scope"]["user_id"],
            item["payload"]["workflow_run_id"],
        )
        for item in payload["subagent_tasks"]
    } == {("ws-1", "user-1", presearch["workflow_run_id"])}
    assert all(
        item["payload"]["input_payload"]["subject_structure_hash"]
        == frozen_structure_hash
        for item in payload["subagent_tasks"]
    )
    assert all(
        item["payload"]["input_payload"]["primary_marketing_goal"] == "content_seeding"
        for item in payload["subagent_tasks"]
    )
    assert payload["plan"]["payload"]["subject_structure_hash"] == frozen_structure_hash
    assert payload["plan"]["payload"]["primary_marketing_goal"] == "content_seeding"

    fetched = await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == payload

    snapshot = await client.get(
        f"/content-research/workflows/{presearch['workflow_run_id']}/policy-snapshot"
    )
    assert snapshot.status_code == 200
    snapshot_payload = snapshot.json()
    assert snapshot_payload["workflow_run_id"] == presearch["workflow_run_id"]
    assert snapshot_payload["effective_policy_hash"]
    assert snapshot_payload["effective_policy"]["direction_ids"] == [
        "product_marketing",
        "competitor_discovery",
    ]
    assert snapshot_payload["effective_policy"]["report_compose_mode"] == "template_only"
    assert snapshot_payload["effective_policy"]["marketing_conclusion_policy"] == {
        "primary_marketing_goal": "content_seeding",
        "tracks": ["need", "value", "message"],
        "minimum_notes_per_conclusion": 3,
        "minimum_independent_authors_per_conclusion": 2,
        "require_core_and_first_intent_support": True,
        "maximum_primary_conclusions_per_track": 1,
    }
    assert (
        snapshot_payload["effective_policy"]["subject_structure_hash"]
        == frozen_structure_hash
    )
    assert len(snapshot_payload["direction_contracts"]) == 2
    assert len(snapshot_payload["sample_policies"]) == 2
    assert (
        snapshot_payload["validation_result"]["schema_version"]
        == "content_research_admission_capability_preflight_v1"
    )
    assert (
        snapshot_payload["validation_result"]["directions"]["product_marketing"]["status"]
        == "formal_directional_result"
    )
    locked = snapshot_payload["effective_policy"]["locked_query_plan"]
    assert locked["schema_version"] == "content_research_locked_query_plan_v2"
    assert locked["query_compiler_version"] == "content_research_query_compiler_v2"
    assert locked["primary_query_group_cap"] == 2
    assert locked["coverage_fallback_query_group_cap"] == 1
    assert locked["candidate_cap_per_group"] == 20
    assert locked["custom_research_question"] == "关注夏季轻量户外"
    assert set(locked["directions"]) == {
        "product_marketing",
        "competitor_discovery",
    }
    for direction_id, direction_plan in locked["directions"].items():
        assert direction_plan["query_plan_hash"]
        assert direction_plan["query_groups"]
        assert (
            sum(group["activation"] == "primary" for group in direction_plan["query_groups"]) <= 2
        )
        assert (
            sum(
                group["activation"] == "coverage_fallback"
                for group in direction_plan["query_groups"]
            )
            <= 1
        )
        for group in direction_plan["query_groups"]:
            assert group["direction_id"] == direction_id
            assert group["normalized_query"]
            assert group["sort"] == "likes"
            assert group["time_window"] == {"end_at": snapshot_payload["run_as_of_at"]}
            assert group["candidate_cap"] == 20
            assert group["roles"]
            assert group["normalized_identity"]
        task = next(
            item for item in payload["subagent_tasks"] if item["direction_id"] == direction_id
        )
        assert (
            task["payload"]["input_payload"]["query_plan_hash"] == direction_plan["query_plan_hash"]
        )
    assert any(
        "关注夏季轻量户外" in group["normalized_query"]
        for direction in locked["directions"].values()
        for group in direction["query_groups"]
    )
    assert all(
        sorted(contract["metadata"]["query_relevance"]["query_group_ids"])
        == sorted(
            group["id"] for group in locked["directions"][contract["direction_id"]]["query_groups"]
        )
        for contract in snapshot_payload["direction_contracts"]
    )
    checkpoints = [
        item
        for item in app.state.content_research_service._store.list_typed_records(
            StageCheckpointRecord
        )
        if item.workflow_run_id == presearch["workflow_run_id"] and item.stage_name == "query_plan"
    ]
    assert len(checkpoints) == 2
    assert all(item.payload["primary_group_count"] <= 2 for item in checkpoints)
    assert all(item.payload["fallback_group_count"] <= 1 for item in checkpoints)
    assert all("normalized_query" not in str(item.payload) for item in checkpoints)

    repeat_confirmation = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "徒步短裤",
            "subject_structure_hash": frozen_structure_hash,
            "subject_type": "category",
            "selected_competitors": [],
            "custom_competitors": [],
            "selected_directions": ["product_marketing"],
            "custom_research_question": "",
            "primary_marketing_goal": "content_seeding",
        },
    )
    assert repeat_confirmation.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requested",
    [
        ["product_marketing"],
        ["competitor_discovery"],
        ["content_performance"],
        ["product_marketing", "competitor_discovery"],
        ["product_marketing", "content_performance"],
        ["competitor_discovery", "content_performance"],
        ["product_marketing", "competitor_discovery", "content_performance"],
    ],
)
async def test_confirm_freezes_catalog_and_requested_subset(client, requested):
    presearch = await _create_presearch(client)

    response = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "徒步短裤",
            "subject_type": "category",
            "selected_directions": requested,
            "primary_marketing_goal": "content_seeding",
            **(
                {"subject_structure_confirmation": _shorts_structure_confirmation()}
                if "product_marketing" in requested
                else {}
            ),
        },
    )

    assert response.status_code == 200
    policy = await client.get(
        f"/content-research/workflows/{presearch['workflow_run_id']}/policy-snapshot"
    )
    assert policy.status_code == 200
    assert policy.json()["effective_policy"]["direction_catalog_version"] == "direction_catalog_v1"
    assert policy.json()["effective_policy"]["requested_direction_ids"] == requested


@pytest.mark.asyncio
async def test_confirm_brief_freezes_one_primary_marketing_goal(client):
    presearch = await _create_presearch(client)

    response = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "徒步短裤",
            "subject_structure_hash": presearch["subject_structure_hash"],
            "subject_type": "category",
            "selected_competitors": [],
            "custom_competitors": [],
            "selected_directions": ["product_marketing"],
            "custom_research_question": "",
            "primary_marketing_goal": "content_seeding",
            "subject_structure_confirmation": _shorts_structure_confirmation(),
        },
    )

    assert response.status_code == 200
    policy_response = await client.get(
        f"/content-research/workflows/{presearch['workflow_run_id']}/policy-snapshot"
    )
    assert policy_response.status_code == 200
    policy = policy_response.json()["effective_policy"]
    assert policy["marketing_conclusion_policy"] == {
        "primary_marketing_goal": "content_seeding",
        "tracks": ["need", "value", "message"],
        "minimum_notes_per_conclusion": 3,
        "minimum_independent_authors_per_conclusion": 2,
        "require_core_and_first_intent_support": True,
        "maximum_primary_conclusions_per_track": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "primary_marketing_goal",
    [None, "unknown_goal", ["content_seeding"]],
)
async def test_confirm_brief_rejects_missing_unknown_or_list_valued_marketing_goal(
    client, primary_marketing_goal
):
    presearch = await _create_presearch(client)
    confirmation = {
        "confirmed_subject": "徒步短裤",
        "subject_type": "category",
        "selected_directions": ["product_marketing"],
    }
    if primary_marketing_goal is not None:
        confirmation["primary_marketing_goal"] = primary_marketing_goal

    response = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json=confirmation,
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_confirm_brief_rejects_empty_requested_direction_selection(client):
    presearch = await _create_presearch(client)

    response = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "徒步短裤",
            "subject_type": "category",
            "selected_directions": [],
            "primary_marketing_goal": "content_seeding",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_confirm_brief_rejects_direction_outside_lite_catalog(client):
    presearch = await _create_presearch(client)

    response = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "徒步短裤",
            "subject_type": "category",
            "selected_directions": ["comment_insight"],
            "primary_marketing_goal": "content_seeding",
        },
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_CONTENT_RESEARCH_PAYLOAD"


@pytest.mark.asyncio
async def test_create_workflow_alias_confirms_brief(client):
    presearch = await _create_presearch(client)

    response = await client.post(
        "/content-research/workflows",
        params={"brief_id": presearch["brief_id"]},
        json={
            "confirmed_subject": "徒步短裤",
            "subject_type": "category",
            "selected_directions": ["product_marketing"],
            "primary_marketing_goal": "content_seeding",
            "subject_structure_confirmation": _shorts_structure_confirmation(),
        },
    )

    assert response.status_code == 200
    assert response.json()["plan"]["payload"]["selected_directions"] == ["product_marketing"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "confirmation_endpoint",
    ["brief", "workflow_alias", "workflow_action"],
)
async def test_preview_off_rejects_every_confirmation_entry_before_persisting_plan(
    client,
    monkeypatch: pytest.MonkeyPatch,
    confirmation_endpoint: str,
):
    presearch = await _create_presearch(client)
    service = app.state.content_research_service
    brief_before = service._store.get_brief(presearch["brief_id"])
    assert brief_before is not None
    assert service._store.list_plans_for_brief(presearch["brief_id"]) == []
    monkeypatch.setattr(settings, "F003_LITE_PREVIEW_ENABLED", False)
    confirmation = {
        "confirmed_subject": "徒步短裤",
        "subject_type": "category",
        "selected_directions": ["product_marketing"],
        "primary_marketing_goal": "content_seeding",
    }

    if confirmation_endpoint == "brief":
        response = await client.post(
            f"/content-research/briefs/{presearch['brief_id']}/confirm",
            json=confirmation,
        )
    elif confirmation_endpoint == "workflow_alias":
        response = await client.post(
            "/content-research/workflows",
            params={"brief_id": presearch["brief_id"]},
            json=confirmation,
        )
    else:
        response = await client.post(
            f"/content-research/workflows/{presearch['workflow_run_id']}/actions",
            json={"action": "confirm_brief", "payload": confirmation},
        )

    assert response.status_code == 403
    assert response.json()["error_code"] == "F003_LITE_PREVIEW_DISABLED"
    assert service._store.get_brief(presearch["brief_id"]) == brief_before
    assert service._store.list_plans_for_brief(presearch["brief_id"]) == []
