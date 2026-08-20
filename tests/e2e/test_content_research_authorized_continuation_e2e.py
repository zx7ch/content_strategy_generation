"""End-to-end regression coverage for persisted Scope continuation commands."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TypeAlias

import httpx
import pytest

from app.api.routes.router import app
from app.content_research.persistence_models import ReportPublicationRecord
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.sources import SourceAdapterRegistry
from app.content_research.sources.base import DiscoverCandidatesRequest, SourceOperationResult
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.worker import ContentResearchDispatchWorker
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
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
        self.discover_contexts: list[dict] = []
        self.discover_exception: Exception | None = None
        self.pause_next_discover = False
        self.discover_started = asyncio.Event()
        self.release_discover = asyncio.Event()

    async def discover_candidates(
        self, request: DiscoverCandidatesRequest
    ) -> SourceOperationResult:
        self.discover_queries.append(request.query)
        self.discover_contexts.append(dict(request.context))
        if self.pause_next_discover:
            self.pause_next_discover = False
            self.discover_started.set()
            await self.release_discover.wait()
        if self.discover_exception is not None:
            raise self.discover_exception
        return await super().discover_candidates(request)


ContinuationHarness: TypeAlias = tuple[
    httpx.AsyncClient,
    ContentResearchService,
    ContentResearchDispatchWorker,
    RecordingCapableFakeAdapter,
    str,
]


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
        summary = (await client.get(f"/content-research/workflows/{workflow_run_id}")).json()
        if summary["runtime_run"]["status"] == status:
            return summary
        await asyncio.sleep(0.01)
    pytest.fail(f"workflow {workflow_run_id} did not reach {status}")


async def _confirmed_scope_awaiting_coverage(
    client: httpx.AsyncClient,
    worker: ContentResearchDispatchWorker,
    db_path: str,
) -> tuple[str, dict, str]:
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
                    {"final_query": group["final_query"]} for group in draft["query_groups"]
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
    return workflow_run_id, contract, snapshot.id


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
    (
        workflow_run_id,
        original_scope,
        coverage_snapshot_id,
    ) = await _confirmed_scope_awaiting_coverage(client, worker, db_path)
    resolved = await client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "resolve_coverage",
            "payload": {
                "scope_contract_version": 1,
                "coverage_snapshot_id": coverage_snapshot_id,
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
    assert (
        service._store.list_scope_execution_continuations(workflow_run_id)[0].state == "completed"
    )


@pytest.mark.asyncio
async def test_limited_continuation_replays_then_publishes_through_real_worker(
    continuation_harness,
):
    client, service, worker, _adapter, db_path = continuation_harness
    workflow_run_id, _scope, coverage_snapshot_id = await _confirmed_scope_awaiting_coverage(
        client, worker, db_path
    )
    endpoint = f"/content-research/workflows/{workflow_run_id}/actions"
    request = {
        "action": "resolve_coverage",
        "payload": {
            "scope_contract_version": 1,
            "coverage_snapshot_id": coverage_snapshot_id,
            "resolution": "generate_limited_report",
        },
    }
    first = await client.post(endpoint, json=request)
    replay = await client.post(endpoint, json=request)
    assert first.status_code == replay.status_code == 200
    assert replay.json()["result"] == first.json()["result"]
    authorization = first.json()["result"]["execution_authorization"]
    assert len(service._store.list_scope_execution_authorizations(workflow_run_id)) == 1
    assert await worker.run_once()
    await _wait_for_workflow_status(client, workflow_run_id, "succeeded")
    assert (
        service._store.list_scope_execution_continuations(workflow_run_id)[0].state == "completed"
    )
    assert authorization["state"] == "authorized_limited_report"
    report = await client.get(f"/content-research/workflows/{workflow_run_id}/lite-report")
    assert report.status_code == 200, report.text
    assert report.json()["publication"]["state"] == "evidence_only_report"
    execution_unit_id = authorization["execution_unit_id"]
    publication_facts = [
        fact
        for fact in service._store.execution_trace(execution_unit_id)
        if fact.kind == "publication_persisted"
    ]
    assert len(publication_facts) == 1
    publication_id = publication_facts[0].payload["publication_id"]
    assert service._store.get_typed_record(ReportPublicationRecord, publication_id) is not None


@pytest.mark.asyncio
async def test_failed_expand_replay_uses_a_new_worker_attempt_and_reaches_coverage(
    continuation_harness,
):
    client, service, worker, adapter, db_path = continuation_harness
    (
        workflow_run_id,
        original_scope,
        coverage_snapshot_id,
    ) = await _confirmed_scope_awaiting_coverage(client, worker, db_path)
    adapter.calls.clear()
    adapter.discover_queries.clear()
    adapter.discover_failure_reason = "timeout"
    endpoint = f"/content-research/workflows/{workflow_run_id}/actions"
    request = {
        "action": "resolve_coverage",
        "payload": {
            "scope_contract_version": 1,
            "coverage_snapshot_id": coverage_snapshot_id,
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
    execution_unit_id = authorization["execution_unit_id"]
    with service._store._connect() as conn:
        attempts = conn.execute(
            """SELECT attempt_no, state, provider_state
               FROM content_research_scope_execution_attempts
               WHERE execution_unit_id=? ORDER BY attempt_no""",
            (execution_unit_id,),
        ).fetchall()
    assert [tuple(row) for row in attempts] == [
        (0, "failed", "retryable_failed"),
        (1, "completed", "succeeded"),
    ]
    provider_facts = [
        fact
        for fact in service._store.execution_trace(execution_unit_id)
        if fact.kind in {"provider_request_recorded", "provider_outcome_recorded"}
    ]
    assert {fact.attempt_no for fact in provider_facts} == {0, 1}
    continuation_contexts = [
        context
        for context in adapter.discover_contexts
        if context.get("execution_unit_id") == execution_unit_id
    ]
    assert [context["attempt_no"] for context in continuation_contexts] == [0, 1]


@pytest.mark.asyncio
async def test_unknown_provider_outcome_is_durable_and_exact_replay_does_not_call_again(
    continuation_harness: ContinuationHarness,
) -> None:
    """Replaying a request whose external outcome is unknown must never duplicate that call."""
    client, service, worker, adapter, db_path = continuation_harness
    workflow_run_id, _scope, coverage_snapshot_id = await _confirmed_scope_awaiting_coverage(
        client, worker, db_path
    )
    adapter.discover_queries.clear()
    adapter.discover_exception = ConnectionError("connection dropped after request send")
    endpoint = f"/content-research/workflows/{workflow_run_id}/actions"
    request = {
        "action": "resolve_coverage",
        "payload": {
            "scope_contract_version": 1,
            "coverage_snapshot_id": coverage_snapshot_id,
            "resolution": "expand_required_constraint",
            "constraint_id": "core_object",
            "supplementary_queries": ["长袖衬衫 防晒"],
        },
    }

    first = await client.post(endpoint, json=request)
    assert first.status_code == 200, first.text
    unit = first.json()["result"]["execution_unit"]
    assert await worker.run_once()
    assert len(adapter.discover_queries) == 1

    durable_unit = service._store.get_scope_execution_unit(unit["id"])
    assert durable_unit is not None
    assert durable_unit.state == "outcome_unknown"
    assert [fact.kind for fact in service._store.execution_trace(unit["id"])][-3:] == [
        "provider_request_recorded",
        "provider_outcome_recorded",
        "outcome_unknown",
    ]

    replay = await client.post(endpoint, json=request)
    assert replay.status_code == 200, replay.text
    assert replay.json()["result"]["execution_unit"] == {
        "id": unit["id"],
        "state": "outcome_unknown",
        "recovery_state": "outcome_unknown",
    }
    assert await worker.run_once() is False
    assert len(adapter.discover_queries) == 1
    trace = await client.get(f"/content-research/workflows/{workflow_run_id}/trace")
    assert trace.status_code == 200, trace.text
    projected = next(
        item for item in trace.json()["execution_units"] if item["id"] == unit["id"]
    )
    assert projected["recovery_state"] == "outcome_unknown"


@pytest.mark.asyncio
async def test_terminal_provider_failure_is_not_requeued_by_exact_replay(
    continuation_harness: ContinuationHarness,
) -> None:
    client, service, worker, adapter, db_path = continuation_harness
    workflow_run_id, _scope, coverage_snapshot_id = await _confirmed_scope_awaiting_coverage(
        client, worker, db_path
    )
    adapter.discover_queries.clear()
    adapter.discover_failure_reason = "provider_access_rejected"
    endpoint = f"/content-research/workflows/{workflow_run_id}/actions"
    request = {
        "action": "resolve_coverage",
        "payload": {
            "scope_contract_version": 1,
            "coverage_snapshot_id": coverage_snapshot_id,
            "resolution": "expand_required_constraint",
            "constraint_id": "core_object",
            "supplementary_queries": ["长袖衬衫 防晒"],
        },
    }

    first = await client.post(endpoint, json=request)
    assert first.status_code == 200, first.text
    unit_id = first.json()["result"]["execution_unit"]["id"]
    assert await worker.run_once()
    assert len(adapter.discover_queries) == 1
    attempt = service._store.get_scope_execution_attempt(unit_id, 0)
    assert attempt is not None
    assert (attempt.state, attempt.provider_state) == ("failed", "terminal_failed")

    replay = await client.post(endpoint, json=request)
    assert replay.status_code == 200, replay.text
    assert replay.json()["result"]["execution_unit"]["recovery_state"] == (
        "manual_recovery_required"
    )
    assert await worker.run_once() is False
    assert len(adapter.discover_queries) == 1
    trace = await client.get(f"/content-research/workflows/{workflow_run_id}/trace")
    assert trace.status_code == 200, trace.text
    projected = next(
        item for item in trace.json()["execution_units"] if item["id"] == unit_id
    )
    assert projected["recovery_state"] == "manual_recovery_required"


@pytest.mark.asyncio
async def test_limited_report_without_owned_publication_remains_recoverable(
    continuation_harness: ContinuationHarness,
) -> None:
    """A runtime status alone cannot terminalize an execution unit as completed."""
    client, service, worker, _adapter, db_path = continuation_harness
    workflow_run_id, _scope, coverage_snapshot_id = await _confirmed_scope_awaiting_coverage(
        client, worker, db_path
    )
    response = await client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "resolve_coverage",
            "payload": {
                "scope_contract_version": 1,
                "coverage_snapshot_id": coverage_snapshot_id,
                "resolution": "generate_limited_report",
            },
        },
    )
    assert response.status_code == 200, response.text
    unit_id = response.json()["result"]["execution_unit"]["id"]
    async with WorkflowStore(db_path) as workflow_store:
        assert workflow_store._conn is not None
        await workflow_store._conn.execute(
            "UPDATE workflow_runs SET status='succeeded' WHERE run_id=?",
            (workflow_run_id,),
        )
        await workflow_store._conn.commit()

    assert await worker.run_once()

    durable_unit = service._store.get_scope_execution_unit(unit_id)
    assert durable_unit is not None
    assert durable_unit.state == "failed"
    assert durable_unit.recovery_state == "replayable"
    assert not any(
        fact.kind == "publication_persisted"
        for fact in service._store.execution_trace(unit_id)
    )
    continuation = service._store.list_scope_execution_continuations(workflow_run_id)[0]
    assert continuation.state == "failed"


@pytest.mark.asyncio
async def test_stale_execution_claim_is_fenced_before_any_continuation_artifact(
    continuation_harness: ContinuationHarness,
) -> None:
    """A superseded worker claim must fail before task recovery or task creation writes."""
    client, service, worker, adapter, db_path = continuation_harness
    workflow_run_id, _scope, coverage_snapshot_id = await _confirmed_scope_awaiting_coverage(
        client, worker, db_path
    )
    adapter.discover_queries.clear()
    response = await client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "resolve_coverage",
            "payload": {
                "scope_contract_version": 1,
                "coverage_snapshot_id": coverage_snapshot_id,
                "resolution": "expand_required_constraint",
                "constraint_id": "core_object",
                "supplementary_queries": ["长袖衬衫 防晒"],
            },
        },
    )
    assert response.status_code == 200, response.text
    authorization = response.json()["result"]["execution_authorization"]
    unit_id = authorization["execution_unit_id"]
    continuation = service._store.list_scope_execution_continuations(workflow_run_id)[0]
    claim_a = service._store.claim_execution_unit(
        execution_unit_id=unit_id, owner="worker-a", lease_seconds=0
    )
    assert claim_a is not None
    claim_b = service._store.claim_execution_unit(
        execution_unit_id=unit_id, owner="worker-b", lease_seconds=120
    )
    assert claim_b is not None
    task_ids_before = {
        task.id for task in service._store.list_subagent_tasks_for_workflow(workflow_run_id)
    }

    with pytest.raises(Exception, match="lease"):
        await service.execute_execution_unit(claim_a, continuation)

    assert {
        task.id for task in service._store.list_subagent_tasks_for_workflow(workflow_run_id)
    } == task_ids_before
    assert adapter.discover_queries == []
    assert any(
        fact.attempt_no == claim_a.attempt_no and fact.kind == "lease_fenced"
        for fact in service._store.execution_trace(unit_id)
    )


@pytest.mark.asyncio
async def test_real_worker_late_provider_callback_cannot_mutate_after_takeover(
    continuation_harness: ContinuationHarness,
) -> None:
    """A worker that passed its entry check cannot write after a real takeover."""
    client, service, worker_a, adapter, db_path = continuation_harness
    workflow_run_id, _scope, coverage_snapshot_id = await _confirmed_scope_awaiting_coverage(
        client, worker_a, db_path
    )
    response = await client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "resolve_coverage",
            "payload": {
                "scope_contract_version": 1,
                "coverage_snapshot_id": coverage_snapshot_id,
                "resolution": "expand_required_constraint",
                "constraint_id": "core_object",
                "supplementary_queries": ["长袖衬衫 防晒"],
            },
        },
    )
    assert response.status_code == 200, response.text
    unit_id = response.json()["result"]["execution_unit"]["id"]
    adapter.discover_queries.clear()
    adapter.pause_next_discover = True
    running_a = asyncio.create_task(worker_a.run_once())
    await asyncio.wait_for(adapter.discover_started.wait(), timeout=5)

    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with service._store._connect() as conn:
        conn.execute(
            """UPDATE content_research_scope_execution_attempts
               SET lease_expires_at=? WHERE execution_unit_id=? AND state='running'""",
            (expired, unit_id),
        )
        conn.execute(
            """UPDATE content_research_scope_execution_continuations
               SET lease_expires_at=? WHERE execution_unit_id=? AND state='running'""",
            (expired, unit_id),
        )

    worker_b = ContentResearchDispatchWorker(
        store=service._store,
        service_factory=lambda: service,
    )
    assert await worker_b.run_once()

    guarded_tables = (
        "content_research_subagent_tasks",
        "content_research_observation_events",
        "content_research_stage_checkpoints",
        "content_research_scope_coverage_snapshots",
        "content_research_cross_direction_records",
        "content_research_aggregate_claims",
        "content_research_report_drafts",
        "content_research_report_faithfulness_decisions",
        "content_research_report_publications",
        "workflow_events",
        "workflow_artifacts",
    )

    def durable_domain_state() -> dict[str, tuple[tuple[object, ...], ...]]:
        with service._store._connect() as conn:
            return {
                table: tuple(
                    tuple(row)
                    for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                )
                for table in guarded_tables
            }

    after_takeover = durable_domain_state()
    adapter.release_discover.set()
    assert await running_a

    assert durable_domain_state() == after_takeover
    assert len(adapter.discover_queries) == 1
    execution_unit = service._store.get_scope_execution_unit(unit_id)
    assert execution_unit is not None
    assert execution_unit.state == "outcome_unknown"
    assert any(
        fact.kind == "lease_fenced" and fact.attempt_no == 0
        for fact in service._store.execution_trace(unit_id)
    )
