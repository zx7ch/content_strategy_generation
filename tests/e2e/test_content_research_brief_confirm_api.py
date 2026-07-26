from __future__ import annotations

import json

import aiosqlite
import httpx
import pytest

from app.api.routes.router import app
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.services.llm.types import LLMResponse, TokenUsage


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
        workflow_runtime=FakeRuntime(str(tmp_path / "content_research.db")),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    if original is None:
        delattr(app.state, "content_research_service")
    else:
        app.state.content_research_service = original


async def _create_presearch(client):
    response = await client.post(
        "/content-research/presearch",
        json={"seed_text": "徒步短裤", "thread_id": "thread-1"},
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_confirm_brief_creates_plan_directions_tasks_and_workflow_summary(client):
    presearch = await _create_presearch(client)

    response = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "徒步短裤",
            "subject_type": "category",
            "selected_competitors": ["迪卡侬"],
            "custom_competitors": ["凯乐石"],
            "selected_directions": ["product_marketing", "competitor_discovery"],
            "custom_research_question": "关注夏季轻量户外",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_run_id"] == presearch["workflow_run_id"]
    assert payload["brief"]["status"] == "ready"
    assert payload["brief"]["payload"]["confirmed_subject"] == "徒步短裤"
    assert payload["brief"]["payload"]["selected_competitors"] == ["迪卡侬"]
    assert payload["brief"]["payload"]["custom_competitors"] == ["凯乐石"]
    assert payload["plan"]["payload"]["priority_policy"]["version"] == "priority_v1"
    assert payload["plan"]["payload"]["evidence_boundary_policy"]["version"] == "evidence_boundary_v1"
    assert [item["payload"]["direction_id"] for item in payload["directions"]] == [
        "product_marketing",
        "competitor_discovery",
    ]
    assert [item["status"] for item in payload["subagent_tasks"]] == ["queued", "queued"]

    fetched = await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == payload

    snapshot = await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}/policy-snapshot")
    assert snapshot.status_code == 200
    snapshot_payload = snapshot.json()
    assert snapshot_payload["workflow_run_id"] == presearch["workflow_run_id"]
    assert snapshot_payload["effective_policy_hash"]
    assert snapshot_payload["effective_policy"]["direction_ids"] == [
        "product_marketing",
        "competitor_discovery",
    ]
    assert len(snapshot_payload["direction_contracts"]) == 2
    assert len(snapshot_payload["sample_policies"]) == 2
    assert snapshot_payload["validation_result"]["schema_version"] == "content_research_admission_capability_preflight_v1"
    assert snapshot_payload["validation_result"]["directions"]["product_marketing"]["status"] == "formal_directional_result"


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
async def test_confirm_brief_rejects_empty_requested_direction_selection(client):
    presearch = await _create_presearch(client)

    response = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "徒步短裤",
            "subject_type": "category",
            "selected_directions": [],
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
        },
    )

    assert response.status_code == 200
    assert response.json()["plan"]["payload"]["selected_directions"] == ["product_marketing"]
