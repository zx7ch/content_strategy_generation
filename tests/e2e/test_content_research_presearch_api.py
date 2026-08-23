from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import httpx
import pytest

from app.api.routes.router import app
from app.config import settings
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from app.content_research.service import WorkflowRunManagerRuntime
from app.services.llm import (
    CredentialResolver,
    LLMService,
    ModelRouter,
    ResolvedModel,
    UserLLMConfiguration,
)
from app.services.llm.configuration_store import SQLiteLLMConfigurationStore
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
                    "subject_structure": {
                        "schema_version": "content_research_subject_structure_v1",
                        "canonical_subject": "Satisfy Running",
                        "subject_type": "brand",
                        "core_entities": [
                            {
                                "canonical_name": "Satisfy Running",
                                "raw_mentions": ["Satisfy Running"],
                            }
                        ],
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


class CapturingOpenAICompatibleAdapter(FakeLLM):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate(self, request, api_key, model, base_url=None):
        self.calls.append({
            "workspace_id": request.context.tenant_id if request.context else None,
            "user_id": request.context.user_id if request.context else None,
            "api_key": api_key,
            "model": model,
            "base_url": base_url,
        })
        return await super().generate(request)


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
    service = app.state.content_research_service
    async with ThreadStore(service._store._db_path) as threads:
        thread = await threads.create_thread(title="Satisfy Running", workspace_id="ws-1")
    created = await client.post(
        "/content-research/presearch",
        headers={"X-Workspace-Id": "ws-1", "X-User-Id": "user-1"},
        json={
            "command_id": "api-presearch-post-get",
            "seed_text": "Satisfy Running",
            "user_note": "关注竞品",
            "thread_id": thread["id"],
        },
    )

    assert created.status_code == 201
    payload = created.json()
    assert payload["attempt_id"].startswith("att_")
    assert payload["brief_id"].startswith("rb_")
    assert payload["workflow_run_id"].startswith("run_")
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
async def test_presearch_command_replay_is_idempotent_and_payload_reuse_conflicts(client):
    service = app.state.content_research_service
    async with ThreadStore(service._store._db_path) as threads:
        thread = await threads.create_thread(title="凉感T恤", workspace_id="ws-1")
    request = {
        "command_id": "stable-api-submit",
        "seed_text": "凉感T恤",
        "user_note": None,
        "thread_id": thread["id"],
    }

    first = await client.post(
        "/content-research/presearch",
        headers={"X-Workspace-Id": "ws-1", "X-User-Id": "user-1"},
        json=request,
    )
    duplicate = await client.post(
        "/content-research/presearch",
        headers={"X-Workspace-Id": "ws-1", "X-User-Id": "user-1"},
        json=request,
    )
    conflict = await client.post(
        "/content-research/presearch",
        headers={"X-Workspace-Id": "ws-1", "X-User-Id": "user-1"},
        json={**request, "seed_text": "另一件产品"},
    )

    assert first.status_code == duplicate.status_code == 201
    assert duplicate.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "STALE_CONTENT_RESEARCH_COMMAND"


@pytest.mark.asyncio
async def test_router_scope_selects_atomic_user_target_through_service_and_adapter(
    client, tmp_path
):
    db_path = str(tmp_path / "scoped-target.db")
    configuration_store = SQLiteLLMConfigurationStore(db_path)
    configuration_store.upsert(UserLLMConfiguration(
        workspace_id="workspace-real",
        user_id="user-real",
        base_url="https://proxy.example/v1",
        model="model-real",
        api_key="key-real",
        validation_status="validated",
        validated_at=datetime.now(timezone.utc),
    ))
    adapter = CapturingOpenAICompatibleAdapter()
    llm = LLMService(
        router=ModelRouter({"balanced": ResolvedModel("openai", "env-model")}),
        credential_resolver=CredentialResolver(),
        providers={"openai_compatible": adapter},
        configuration_reader=configuration_store,
    )
    app.state.content_research_service = ContentResearchService(
        store=SQLiteContentResearchStore(db_path),
        presearch=PresearchService(llm),
        workflow_runtime=FakeRuntime(),
    )
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(
            title="Satisfy Running",
            workspace_id="workspace-real",
        )

    response = await client.post(
        "/content-research/presearch",
        headers={"X-Workspace-Id": "workspace-real", "X-User-Id": "user-real"},
        json={
            "command_id": "api-scoped-target",
            "seed_text": "Satisfy Running",
            "thread_id": thread["id"],
        },
    )

    assert response.status_code == 201, response.text
    assert adapter.calls == [{
        "workspace_id": "workspace-real",
        "user_id": "user-real",
        "api_key": "key-real",
        "model": "model-real",
        "base_url": "https://proxy.example/v1",
    }]


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
            "command_id": "api-preview-off",
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


@pytest.mark.asyncio
async def test_presearch_owned_stack_creates_authoritative_brief_without_subject_confirmation(
    tmp_path,
) -> None:
    db_path = str(tmp_path / "owned-presearch.db")
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(
            title="夏季凉感T恤",
            workspace_id="ws-owned",
            brand_id="brand-owned",
        )
    original = getattr(app.state, "content_research_service", None)
    app.state.content_research_service = ContentResearchService(
        store=SQLiteContentResearchStore(db_path),
        presearch=PresearchService(
            FakeLLM(),
            first_feedback_timeout_seconds=0.05,
            hard_cutoff_seconds=0.1,
        ),
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as owned:
            response = await owned.post(
                "/content-research/presearch",
                headers={"X-Workspace-Id": "ws-owned", "X-User-Id": "user-owned"},
                json={
                    "command_id": "api-owned-presearch",
                    "seed_text": "Satisfy Running",
                    "thread_id": thread["id"],
                },
            )
            assert response.status_code == 201, response.text
            payload = response.json()
            run_id = payload["workflow_run_id"]
            assert payload["run"] == {
                "run_id": run_id,
                "thread_id": thread["id"],
                "state": "brief_confirmation_required",
                "state_revision": 2,
                "entered_at": payload["run"]["entered_at"],
                    "allowed_actions": ["confirm_brief", "revise_subject", "cancel"],
                "reason_code": None,
                "error": None,
                "brief_id": payload["brief_id"],
                "scope_contract_id": None,
                "execution_attempt_id": None,
                "coverage_snapshot_id": None,
                "publication_id": None,
            }
            assert payload["run"]["entered_at"]
            assert "subject_structure_state" not in payload
            assert "subject_structure_user_confirmed_fields" not in payload
            trace_response = await owned.get(
                f"/content-research/workflows/{run_id}/trace"
            )
            assert trace_response.status_code == 200, trace_response.text
            trace = trace_response.json()
            assert trace["state"] == "brief_confirmation_required"
            assert trace["state_revision"] == 2
            assert [item["event"] for item in trace["state_transitions"]] == [
                "submit_research_subject",
                "presearch_completed",
            ]
    finally:
        if original is None:
            delattr(app.state, "content_research_service")
        else:
            app.state.content_research_service = original

    with sqlite3.connect(db_path) as connection:
        active_run_id = connection.execute(
            "SELECT active_run_id FROM creator_threads WHERE id=?", (thread["id"],)
        ).fetchone()[0]
        scope_count = connection.execute(
            "SELECT COUNT(*) FROM content_research_scope_contracts WHERE workflow_run_id=?",
            (run_id,),
        ).fetchone()[0]
        dispatch_count = connection.execute(
            "SELECT COUNT(*) FROM content_research_dispatch_jobs WHERE workflow_run_id=?",
            (run_id,),
        ).fetchone()[0]
    assert active_run_id == run_id
    assert scope_count == 0
    assert dispatch_count == 0
