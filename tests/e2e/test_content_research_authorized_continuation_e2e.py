"""End-to-end regression coverage for persisted Scope continuation commands."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.api.routes.router import app
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.sources import SourceAdapterRegistry
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.worker import ContentResearchDispatchWorker
from app.memory.thread_store import ThreadStore
from tests.e2e.test_content_research_formal_workflow_e2e import (
    CapableFakeAdapter,
    FakeLLM,
)
from tests.e2e.test_content_research_scope_api import SummerCommuteFakeLLM


class RecordingCapableFakeAdapter(CapableFakeAdapter):
    """The real router sees this adapter; tests retain only submitted queries."""

    def __init__(self) -> None:
        super().__init__()
        self.discover_queries: list[str] = []

    async def discover_candidates(self, request):
        self.discover_queries.append(request.query)
        return await super().discover_candidates(request)


@pytest.fixture()
async def continuation_harness(tmp_path):
    original = getattr(app.state, "content_research_service", None)
    db_path = str(tmp_path / "continuation-e2e.db")
    adapter = RecordingCapableFakeAdapter()
    analysis_llm = FakeLLM()
    adapter.analysis_llm = analysis_llm
    service = ContentResearchService(
        store=SQLiteContentResearchStore(db_path),
        presearch=PresearchService(
            SummerCommuteFakeLLM(),
            first_feedback_timeout_seconds=0.05,
            hard_cutoff_seconds=0.1,
        ),
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
        source_registry=SourceAdapterRegistry({"xiaohongshu": adapter}),
        analysis_llm=analysis_llm,
    )
    app.state.content_research_service = service
    worker = ContentResearchDispatchWorker(
        store=service._store,
        service_factory=lambda: service,
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-Workspace-Id": "ws-1", "X-User-Id": "user-1"},
    )
    try:
        yield client, service, worker, adapter, db_path
    finally:
        await client.aclose()
        if original is None:
            delattr(app.state, "content_research_service")
        else:
            app.state.content_research_service = original


async def _wait_for_workflow_status(
    client: httpx.AsyncClient, workflow_run_id: str, status: str
) -> dict:
    for _ in range(100):
        summary = (
            await client.get(f"/content-research/workflows/{workflow_run_id}")
        ).json()
        if summary["runtime_run"]["status"] == status:
            return summary
        await asyncio.sleep(0.01)
    pytest.fail(f"workflow {workflow_run_id} did not reach {status}")


async def _confirmed_scope_awaiting_coverage(
    client: httpx.AsyncClient,
    worker: ContentResearchDispatchWorker,
    db_path: str,
) -> tuple[str, dict]:
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    thread = await thread_store.create_thread(title="authorized continuation")
    created = await client.post(
        "/content-research/presearch",
        json={"seed_text": "夏季通勤长袖", "thread_id": thread["id"]},
    )
    assert created.status_code == 201, created.text
    workflow = created.json()
    confirmed = await client.post(
        f"/content-research/briefs/{workflow['brief_id']}/confirm",
        json={
            "confirmed_subject": "夏季通勤长袖",
            "subject_structure_hash": workflow["subject_structure_hash"],
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
    assert confirmed.status_code == 200, confirmed.text
    workflow_run_id = workflow["workflow_run_id"]
    prepared = await client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={"action": "prepare_scope", "payload": {"direction_id": "product_marketing"}},
    )
    assert prepared.status_code == 200, prepared.text
    draft = prepared.json()["result"]["scope"]
    scope_response = await client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "confirm_scope",
            "payload": {
                "scope_draft_id": draft["id"],
                "structure_hash": draft["structure_hash"],
                "query_groups": [
                    {"final_query": group["final_query"]}
                    for group in draft["query_groups"]
                ],
            },
        },
    )
    assert scope_response.status_code == 200, scope_response.text
    contract = scope_response.json()["result"]["scope_contract"]
    started = await client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "start_formal_research",
            "payload": {"provider": "xiaohongshu", "source_kind": "search_result", "limit": 20},
        },
    )
    assert started.status_code == 200, started.text
    assert await worker.run_once()
    await _wait_for_workflow_status(client, workflow_run_id, "waiting_user")
    snapshot = SQLiteContentResearchStore(db_path).get_coverage_snapshot(
        workflow_run_id, version=contract["version"]
    )
    assert snapshot is not None
    assert snapshot.state == "awaiting_scope_decision"
    assert "core_object" in snapshot.unmet_constraint_ids
    await thread_store.close()
    return workflow_run_id, contract


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolution", "payload", "expected_scope_version"),
    [
        (
            "expand_required_constraint",
            {
                "constraint_id": "core_object",
                "supplementary_queries": ["长袖衬衫 防晒"],
            },
            1,
        ),
        (
            "relax_constraint",
            {"constraint_id": "core_object"},
            2,
        ),
    ],
)
async def test_authorized_collection_continuations_run_through_worker_and_pipeline(
    continuation_harness, resolution, payload, expected_scope_version
):
    client, service, worker, adapter, db_path = continuation_harness
    workflow_run_id, original_scope = await _confirmed_scope_awaiting_coverage(
        client, worker, db_path
    )
    resolved = await client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "resolve_coverage",
            "payload": {
                "scope_contract_version": 1,
                "resolution": resolution,
                **payload,
            },
        },
    )
    assert resolved.status_code == 200, resolved.text
    result = resolved.json()["result"]
    authorization = result["execution_authorization"]
    if resolution == "expand_required_constraint":
        assert result["scope_contract"] == original_scope
    else:
        assert result["scope_contract"]["version"] == 2
        assert len(service._store.list_scope_contracts(workflow_run_id)) == 2

    assert await worker.run_once()
    if resolution == "expand_required_constraint":
        assert payload["supplementary_queries"][0] in adapter.discover_queries
    continuation_snapshot = service._store.get_coverage_snapshot(
        workflow_run_id,
        version=expected_scope_version,
        execution_revision=authorization["execution_revision"],
    )
    assert continuation_snapshot is not None
    assert continuation_snapshot.execution_authorization_id == authorization["id"]
    assert continuation_snapshot.scope_contract_version == expected_scope_version
    assert continuation_snapshot.source_coverage_snapshot_id
    assert service._store.list_scope_execution_continuations(workflow_run_id)[0].state == "completed"


@pytest.mark.asyncio
async def test_limited_continuation_replays_then_publishes_through_real_worker(
    continuation_harness,
):
    client, service, worker, _adapter, db_path = continuation_harness
    workflow_run_id, _scope = await _confirmed_scope_awaiting_coverage(
        client, worker, db_path
    )
    endpoint = f"/content-research/workflows/{workflow_run_id}/actions"
    request = {
        "action": "resolve_coverage",
        "payload": {"scope_contract_version": 1, "resolution": "generate_limited_report"},
    }
    first = await client.post(endpoint, json=request)
    replay = await client.post(endpoint, json=request)
    assert first.status_code == replay.status_code == 200
    assert replay.json()["result"] == first.json()["result"]
    authorization = first.json()["result"]["execution_authorization"]
    assert len(service._store.list_scope_execution_authorizations(workflow_run_id)) == 1
    assert await worker.run_once()
    await _wait_for_workflow_status(client, workflow_run_id, "succeeded")
    assert service._store.list_scope_execution_continuations(workflow_run_id)[0].state == "completed"
    assert authorization["state"] == "authorized_limited_report"
    report = await client.get(f"/content-research/workflows/{workflow_run_id}/lite-report")
    assert report.status_code == 200, report.text
    assert report.json()["publication"]["state"] == "evidence_only_report"


@pytest.mark.asyncio
async def test_failed_expand_replay_uses_a_new_worker_attempt_and_reaches_coverage(
    continuation_harness,
):
    client, service, worker, adapter, db_path = continuation_harness
    workflow_run_id, original_scope = await _confirmed_scope_awaiting_coverage(
        client, worker, db_path
    )
    adapter.calls.clear()
    adapter.discover_queries.clear()
    adapter.discover_failure_reason = "timeout"
    endpoint = f"/content-research/workflows/{workflow_run_id}/actions"
    request = {
        "action": "resolve_coverage",
        "payload": {
            "scope_contract_version": 1,
            "resolution": "expand_required_constraint",
            "constraint_id": "core_object",
            "supplementary_queries": ["长袖衬衫 防晒"],
        },
    }

    first = await client.post(endpoint, json=request)
    assert first.status_code == 200, first.text
    authorization = first.json()["result"]["execution_authorization"]
    assert first.json()["result"]["scope_contract"] == original_scope
    assert await worker.run_once()
    assert len(adapter.discover_queries) == 1
    assert service._store.list_scope_execution_continuations(workflow_run_id)[0].state == "failed"

    adapter.discover_failure_reason = None
    replay = await client.post(endpoint, json=request)
    assert replay.status_code == 200, replay.text
    assert replay.json()["result"]["execution_authorization"] == authorization
    assert await worker.run_once()
    assert len(adapter.discover_queries) == 2
    continuation = service._store.list_scope_execution_continuations(workflow_run_id)[0]
    assert continuation.state == "completed"
    continuation_tasks = [
        task
        for task in service._store.list_subagent_tasks_for_workflow(workflow_run_id)
        if task.metadata.get("scope_execution_authorization_id") == authorization["id"]
    ]
    assert [task.metadata["scope_execution_attempt"] for task in continuation_tasks] == [1, 2]
    assert {task.status for task in continuation_tasks} == {"failed", "completed"}
    snapshot = service._store.get_coverage_snapshot(
        workflow_run_id,
        version=1,
        execution_revision=authorization["execution_revision"],
    )
    assert snapshot is not None
    assert snapshot.execution_authorization_id == authorization["id"]
    assert [scope.version for scope in service._store.list_scope_contracts(workflow_run_id)] == [1]
