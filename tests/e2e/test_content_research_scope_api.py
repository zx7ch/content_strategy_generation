from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from app.api.routes.router import app
from app.content_research.api_schemas import ContentResearchSourceCollectionRequest
from app.content_research.async_dispatch import AsyncScopeExecutionContinuationRepository
from app.content_research.persistence_models import (
    DirectionalEvidencePacketRecord,
    ReportPublicationRecord,
)
from app.content_research.presearch.service import PresearchService
from app.content_research.scope_contract import (
    CoverageSnapshot,
    ScopeAuditEvent,
)
from app.content_research.service import ContentResearchService, ContentResearchValidationError
from app.content_research.sources import SourceAdapterRegistry
from app.content_research.sources.base import SourceOperationResult
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


class ScopeFakeRuntime(FakeRuntime):
    async def resume_content_research_run(self, *, workflow_run_id: str) -> dict:
        raise AssertionError(
            "resolve_coverage must queue its persisted continuation, not resume inline"
        )


class ScopeDiagnosticSourceAdapter:
    async def discover_candidates(self, request):
        return SourceOperationResult(
            provider="xiaohongshu",
            operation="discover_candidates",
            source_kind="search_result",
            status="completed",
            items=[],
            metadata={"request_query": request.query},
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
        workflow_runtime=ScopeFakeRuntime(db_path),
        source_registry=SourceAdapterRegistry({"xiaohongshu": ScopeDiagnosticSourceAdapter()}),
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


async def _confirmed_scope_with_unmet_season(client: httpx.AsyncClient) -> tuple[str, dict]:
    workflow = await _scope_ready_workflow(client)
    workflow_run_id = workflow["presearch"]["workflow_run_id"]
    prepared = await client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={"action": "prepare_scope", "payload": {"direction_id": "product_marketing"}},
    )
    scope = prepared.json()["result"]["scope"]
    confirmed = await client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "confirm_scope",
            "payload": {
                "scope_draft_id": scope["id"],
                "structure_hash": scope["structure_hash"],
                "query_groups": [
                    {"final_query": group["final_query"]} for group in scope["query_groups"]
                ],
            },
        },
    )
    contract = confirmed.json()["result"]["scope_contract"]
    store = app.state.content_research_service._store
    snapshot = CoverageSnapshot(
        id="scv_api_unmet_season",
        workflow_run_id=workflow_run_id,
        scope_contract_id=contract["id"],
        scope_contract_version=contract["version"],
        state="awaiting_scope_decision",
        constraint_counts={
            "season": {
                "matched_candidate_count": 1,
                "independent_author_count": 1,
                "required": True,
            },
            "_summary": {
                "minimum_samples": 2,
                "minimum_independent_authors": 2,
                "reason_codes": ["required_constraint_coverage_unmet:season"],
            },
        },
        unmet_constraint_ids=("season",),
    )
    store.save_coverage_snapshot_with_audit_event(
        snapshot,
        ScopeAuditEvent(
            id="sae_api_coverage_evaluated",
            workflow_run_id=workflow_run_id,
            scope_contract_id=contract["id"],
            scope_contract_version=contract["version"],
            event_name="coverage_evaluated",
            payload={
                "schema_version": "content_research_scope_audit_event_v1",
                "coverage_snapshot_id": snapshot.id,
                "state": snapshot.state,
                "constraint_counts": snapshot.constraint_counts,
                "unmet_constraint_ids": ["season"],
                "reason_codes": ["required_constraint_coverage_unmet:season"],
            },
        ),
    )
    return workflow_run_id, contract


async def _confirm_initial_scope(client: httpx.AsyncClient, workflow_run_id: str) -> dict:
    prepared = await client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={"action": "prepare_scope", "payload": {"direction_id": "product_marketing"}},
    )
    assert prepared.status_code == 200
    scope = prepared.json()["result"]["scope"]
    confirmed = await client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "confirm_scope",
            "payload": {
                "scope_draft_id": scope["id"],
                "structure_hash": scope["structure_hash"],
                "query_groups": [
                    {"final_query": group["final_query"]} for group in scope["query_groups"]
                ],
            },
        },
    )
    assert confirmed.status_code == 200
    return confirmed.json()["result"]["scope_contract"]


@pytest.mark.asyncio
async def test_initial_confirmed_scope_can_dispatch_its_first_collection(scope_client):
    workflow = await _scope_ready_workflow(scope_client)
    workflow_run_id = workflow["presearch"]["workflow_run_id"]
    await _confirm_initial_scope(scope_client, workflow_run_id)

    response = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "start_formal_research",
            "payload": {
                "provider": "xiaohongshu",
                "source_kind": "search_result",
                "limit": 20,
            },
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["status"] == "queued"


@pytest.mark.asyncio
async def test_awaiting_scope_decision_rejects_legacy_execution_entrypoints(scope_client):
    workflow_run_id, _contract = await _confirmed_scope_with_unmet_season(scope_client)
    formal_payload = {
        "provider": "xiaohongshu",
        "source_kind": "search_result",
        "limit": 20,
    }

    for action, payload in (
        ("start_formal_research", formal_payload),
        ("retry_formal_research", formal_payload),
        ("resume_formal_research", {}),
    ):
        response = await scope_client.post(
            f"/content-research/workflows/{workflow_run_id}/actions",
            json={"action": action, "payload": payload},
        )
        assert response.status_code == 422, response.text
        assert "scope_execution_authorization_required" in response.text

    service = app.state.content_research_service
    with pytest.raises(ContentResearchValidationError, match="scope_execution_authorization_required"):
        await service.start_formal_research(
            workflow_run_id=workflow_run_id,
            request=ContentResearchSourceCollectionRequest(**formal_payload),
        )

    report = await scope_client.get(
        f"/content-research/workflows/{workflow_run_id}/lite-report"
    )
    assert report.status_code == 404, report.text


@pytest.mark.asyncio
async def test_relax_successor_requires_its_persisted_continuation_authority(scope_client, monkeypatch):
    workflow_run_id, _contract = await _confirmed_scope_with_unmet_season(scope_client)
    resolution = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "resolve_coverage",
            "payload": {
                "scope_contract_version": 1,
                "coverage_snapshot_id": "scv_api_unmet_season",
                "resolution": "relax_constraint",
                "constraint_id": "season",
            },
        },
    )
    assert resolution.status_code == 200
    assert resolution.json()["result"]["scope_contract"]["version"] == 2

    legacy = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "start_formal_research",
            "payload": {"provider": "xiaohongshu", "source_kind": "search_result", "limit": 20},
        },
    )
    assert legacy.status_code == 422, legacy.text
    assert "scope_execution_authorization_required" in legacy.text

    service = app.state.content_research_service
    captured = {}

    async def capture_execution(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(service, "_execute_formal_research", capture_execution)
    continuation = service._store.list_scope_execution_continuations(workflow_run_id)[0]
    await service.execute_scope_continuation(continuation)
    assert captured["execution_authorization"].id == continuation.authorization_id


@pytest.mark.asyncio
async def test_later_scope_confirmation_cannot_reset_unresolved_workflow_coverage(scope_client):
    """Ordinary confirmation cannot strand an unresolved Coverage decision."""
    workflow_run_id, _contract = await _confirmed_scope_with_unmet_season(scope_client)
    service = app.state.content_research_service
    original_draft = service._store.get_latest_scope_draft(workflow_run_id)
    assert original_draft is not None
    confirmed = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "confirm_scope",
            "payload": {
                "scope_draft_id": original_draft.id,
                "structure_hash": original_draft.structure_hash,
                "query_groups": [
                    {"final_query": group.final_query} for group in original_draft.query_groups
                ],
            },
        },
    )
    assert confirmed.status_code == 422, confirmed.text
    assert "coverage_decision_required" in confirmed.text
    assert len(service._store.list_scope_contracts(workflow_run_id)) == 1

    prepared = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "prepare_scope",
            "payload": {"direction_id": "product_marketing"},
        },
    )
    assert prepared.status_code == 422, prepared.text
    assert "coverage_decision_required" in prepared.text

    resolved = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "resolve_coverage",
            "payload": {
                "scope_contract_version": 1,
                "coverage_snapshot_id": "scv_api_unmet_season",
                "resolution": "relax_constraint",
                "constraint_id": "season",
            },
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["result"]["scope_contract"]["version"] == 2


@pytest.mark.asyncio
async def test_scope_continuation_rejects_forged_command_and_completed_replay(scope_client):
    workflow_run_id, _contract = await _confirmed_scope_with_unmet_season(scope_client)
    resolution = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "resolve_coverage",
            "payload": {
                "scope_contract_version": 1,
                "coverage_snapshot_id": "scv_api_unmet_season",
                "resolution": "expand_required_constraint",
                "constraint_id": "season",
                "supplementary_queries": ["夏季 防晒 长袖衬衫"],
            },
        },
    )
    assert resolution.status_code == 200
    service = app.state.content_research_service
    persisted = service._store.list_scope_execution_continuations(workflow_run_id)[0]
    forged = replace(persisted, supplementary_queries=("伪造检索词",))

    with pytest.raises(ContentResearchValidationError, match="persisted command"):
        await service.execute_scope_continuation(forged)

    repository = AsyncScopeExecutionContinuationRepository(service._store._db_path)
    claimed = await repository.claim_next(owner="scope-test")
    assert claimed is not None
    assert await repository.complete(
        authorization_id=claimed.authorization_id,
        owner="scope-test",
        token=str(claimed.lease_token),
    )
    completed = service._store.list_scope_execution_continuations(workflow_run_id)[0]
    with pytest.raises(ContentResearchValidationError, match="not claimable"):
        await service.execute_scope_continuation(completed)


@pytest.mark.asyncio
async def test_direct_source_collection_is_diagnostic_only(scope_client):
    workflow_run_id, contract = await _confirmed_scope_with_unmet_season(scope_client)
    store = app.state.content_research_service._store
    coverage_before = store.get_coverage_snapshot(workflow_run_id, version=contract["version"])
    packet_ids_before = {
        item.id
        for item in store.list_typed_records(DirectionalEvidencePacketRecord)
        if item.workflow_run_id == workflow_run_id
    }
    publication_ids_before = {
        item.id
        for item in store.list_typed_records(ReportPublicationRecord)
        if item.workflow_run_id == workflow_run_id
    }

    response = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/source-collections",
        json={"query": "夏季 长袖衬衫", "limit": 5},
    )

    assert response.status_code == 200, response.text
    assert response.json()["execution_authority"] == "diagnostic_only"
    assert store.get_coverage_snapshot(workflow_run_id, version=contract["version"]) == coverage_before
    assert {
        item.id
        for item in store.list_typed_records(DirectionalEvidencePacketRecord)
        if item.workflow_run_id == workflow_run_id
    } == packet_ids_before
    assert {
        item.id
        for item in store.list_typed_records(ReportPublicationRecord)
        if item.workflow_run_id == workflow_run_id
    } == publication_ids_before


async def _authorized_continuation_snapshot(
    client: httpx.AsyncClient,
    *,
    workflow_run_id: str,
    contract: dict,
    snapshot_id: str,
    constraint_counts: dict,
    unmet_constraint_ids: tuple[str, ...],
) -> dict:
    """Persist the next coverage evaluation owned by a resolved continuation."""
    resolution = await client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "resolve_coverage",
            "payload": {
                "scope_contract_version": contract["version"],
                "coverage_snapshot_id": "scv_api_unmet_season",
                "resolution": "expand_required_constraint",
                "constraint_id": "season",
                "supplementary_queries": ["夏季 防晒 长袖衬衫"],
            },
        },
    )
    assert resolution.status_code == 200
    authorization = resolution.json()["result"]["execution_authorization"]
    store = app.state.content_research_service._store
    snapshot = CoverageSnapshot(
        id=snapshot_id,
        workflow_run_id=workflow_run_id,
        scope_contract_id=contract["id"],
        scope_contract_version=contract["version"],
        state="awaiting_scope_decision",
        constraint_counts=constraint_counts,
        unmet_constraint_ids=unmet_constraint_ids,
        execution_revision=authorization["execution_revision"],
        execution_authorization_id=authorization["id"],
        source_coverage_snapshot_id=authorization["coverage_snapshot_id"],
    )
    store.save_coverage_snapshot(snapshot)
    return {"authorization": authorization, "snapshot": snapshot}


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
async def test_generate_limited_report_resolution_preserves_v1_and_exact_season_decision(
    scope_client,
):
    """Removing explicit limited-report authorization must make this test fail."""
    workflow_run_id, contract_v1 = await _confirmed_scope_with_unmet_season(scope_client)

    response = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "resolve_coverage",
            "payload": {
                "scope_contract_version": 1,
                "coverage_snapshot_id": "scv_api_unmet_season",
                "resolution": "generate_limited_report",
            },
        },
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["report_mode"] == "limited"
    assert result["scope_contract"] == contract_v1
    assert result["unmet_constraint_ids"] == ["season"]
    assert result["audit_event"]["event_name"] == "coverage_resolved"
    assert result["audit_event"]["payload"]["resolution"] == "generate_limited_report"
    store = app.state.content_research_service._store
    authorization = result["execution_authorization"]
    assert authorization["resolution"] == "generate_limited_report"
    assert authorization["state"] == "authorized_limited_report"
    assert store.list_scope_execution_authorizations(workflow_run_id)[0].state == (
        "authorized_limited_report"
    )
    continuation = store.list_scope_execution_continuations(workflow_run_id)[0]
    assert continuation.authorization_id == authorization["id"]
    assert continuation.execution_revision == authorization["execution_revision"] == 2
    assert continuation.operation == "limited_report"
    assert continuation.supplementary_queries == ()
    assert continuation.state == "pending"
    assert [contract.version for contract in store.list_scope_contracts(workflow_run_id)] == [1]


@pytest.mark.asyncio
async def test_limited_continuation_reaches_report_execution_without_source_tasks(
    scope_client, monkeypatch
):
    workflow_run_id, _contract_v1 = await _confirmed_scope_with_unmet_season(scope_client)
    response = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "resolve_coverage",
            "payload": {
                "scope_contract_version": 1,
                "coverage_snapshot_id": "scv_api_unmet_season",
                "resolution": "generate_limited_report",
            },
        },
    )
    assert response.status_code == 200
    service = app.state.content_research_service
    continuation = service._store.list_scope_execution_continuations(workflow_run_id)[0]
    captured = {}

    async def capture_execution(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(service, "_execute_formal_research", capture_execution)
    await service.execute_scope_continuation(continuation)

    assert captured["executable_task_ids"] == set()
    assert captured["execution_authorization"].id == continuation.authorization_id


@pytest.mark.asyncio
async def test_expand_required_constraint_retains_v1_and_authorizes_collection(
    scope_client,
):
    """Creating a semantic Scope revision for supplementary collection must fail this test."""
    workflow_run_id, contract_v1 = await _confirmed_scope_with_unmet_season(scope_client)
    supplementary_queries = ["夏季 防晒 长袖衬衫", "夏季 透气 衬衫"]

    response = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "resolve_coverage",
            "payload": {
                "scope_contract_version": 1,
                "coverage_snapshot_id": "scv_api_unmet_season",
                "resolution": "expand_required_constraint",
                "constraint_id": "season",
                "supplementary_queries": supplementary_queries,
            },
        },
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["scope_contract"] == contract_v1
    assert result["audit_event"]["payload"]["resolution"] == "expand_required_constraint"
    assert result["audit_event"]["payload"]["source_scope_contract_version"] == 1
    assert result["audit_event"]["payload"]["resulting_scope_contract_version"] == 1
    assert result["execution_authorization"]["state"] == "authorized_collection"
    store = app.state.content_research_service._store
    persisted_v1 = store.get_scope_contract(workflow_run_id, version=1)
    assert persisted_v1 is not None
    assert [group.final_query for group in persisted_v1.query_groups] == [
        group["final_query"] for group in contract_v1["query_groups"]
    ]
    authorizations = store.list_scope_execution_authorizations(workflow_run_id)
    assert len(authorizations) == 1
    assert authorizations[0].scope_contract_version == 1
    assert authorizations[0].execution_revision == 2
    continuation = store.list_scope_execution_continuations(workflow_run_id)[0]
    assert continuation.authorization_id == authorizations[0].id
    assert continuation.operation == "supplementary_collection"
    assert continuation.supplementary_queries == tuple(supplementary_queries)
    assert continuation.state == "pending"


@pytest.mark.asyncio
async def test_supplementary_continuation_executes_only_its_authorized_task(
    scope_client, monkeypatch
):
    workflow_run_id, _contract_v1 = await _confirmed_scope_with_unmet_season(scope_client)
    response = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "resolve_coverage",
            "payload": {
                "scope_contract_version": 1,
                "coverage_snapshot_id": "scv_api_unmet_season",
                "resolution": "expand_required_constraint",
                "constraint_id": "season",
                "supplementary_queries": ["夏季 防晒 长袖衬衫"],
            },
        },
    )
    assert response.status_code == 200
    service = app.state.content_research_service
    continuation = service._store.list_scope_execution_continuations(workflow_run_id)[0]
    captured = {}

    async def capture_execution(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(service, "_execute_formal_research", capture_execution)
    await service.execute_scope_continuation(continuation)

    executable_ids = captured["executable_task_ids"]
    assert len(executable_ids) == 1
    continuation_task = service._store.get_subagent_task(next(iter(executable_ids)))
    assert continuation_task is not None
    assert continuation_task.payload.get("workflow_child_task_id") is None
    assert continuation_task.payload["input_payload"]["scope_execution"] == {
        "authorization_id": continuation.authorization_id,
        "execution_revision": 2,
        "supplementary_queries": ["夏季 防晒 长袖衬衫"],
    }
    initial_task_ids = {
        task.id
        for task in service._store.list_subagent_tasks_for_workflow(workflow_run_id)
        if task.payload.get("workflow_child_task_id")
    }
    assert executable_ids.isdisjoint(initial_task_ids)


@pytest.mark.asyncio
async def test_relax_constraint_creates_v2_and_keeps_v1_required(scope_client):
    """Mutating the frozen constraint instead of versioning it must make this test fail."""
    workflow_run_id, _contract_v1 = await _confirmed_scope_with_unmet_season(scope_client)

    response = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "resolve_coverage",
            "payload": {
                "scope_contract_version": 1,
                "coverage_snapshot_id": "scv_api_unmet_season",
                "resolution": "relax_constraint",
                "constraint_id": "season",
            },
        },
    )

    assert response.status_code == 200
    result = response.json()["result"]
    contract_v2 = result["scope_contract"]
    assert contract_v2["version"] == 2
    assert (
        next(item for item in contract_v2["constraints"] if item["id"] == "season")["mode"]
        == "preferred"
    )
    assert result["audit_event"]["payload"]["resolution"] == "relax_constraint"
    store = app.state.content_research_service._store
    persisted_v1 = store.get_scope_contract(workflow_run_id, version=1)
    assert persisted_v1 is not None
    assert next(item for item in persisted_v1.constraints if item.id == "season").mode == "required"
    authorization = store.list_scope_execution_authorizations(workflow_run_id)
    assert len(authorization) == 1
    assert authorization[0].scope_contract_version == 2
    assert authorization[0].state == "authorized_collection"


@pytest.mark.asyncio
async def test_resolve_coverage_rejects_unknown_outcome_without_writing(scope_client):
    workflow_run_id, _contract_v1 = await _confirmed_scope_with_unmet_season(scope_client)

    response = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={
            "action": "resolve_coverage",
            "payload": {
                "scope_contract_version": 1,
                "resolution": "silently_broaden",
            },
        },
    )

    assert response.status_code == 422
    store = app.state.content_research_service._store
    assert [contract.version for contract in store.list_scope_contracts(workflow_run_id)] == [1]
    assert all(
        event.event_name != "coverage_resolved"
        for event in store.list_scope_audit_events(workflow_run_id, version=1)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {
            "scope_contract_version": 1,
            "resolution": "expand_required_constraint",
            "constraint_id": "season",
            "supplementary_queries": ["夏季 防晒 长袖衬衫"],
        },
        {
            "scope_contract_version": 1,
            "resolution": "relax_constraint",
            "constraint_id": "season",
        },
    ],
)
async def test_versioned_coverage_resolution_replays_after_lost_response(scope_client, payload):
    """Removing persisted-command replay must make this test fail."""
    workflow_run_id, _contract_v1 = await _confirmed_scope_with_unmet_season(scope_client)
    endpoint = f"/content-research/workflows/{workflow_run_id}/actions"
    payload = {**payload, "coverage_snapshot_id": "scv_api_unmet_season"}

    first = await scope_client.post(
        endpoint, json={"action": "resolve_coverage", "payload": payload}
    )
    assert first.status_code == 200
    first_result = first.json()["result"]
    original_unit_id = first_result["execution_unit"]["id"]
    store = app.state.content_research_service._store
    authorization = store.list_scope_execution_authorizations(workflow_run_id)[0]
    original_fact_sequence = [
        (fact.attempt_no, fact.sequence_no, fact.kind)
        for fact in store.execution_trace(original_unit_id)
    ]
    resulting_version = 2 if payload["resolution"] == "relax_constraint" else 1
    resulting_contract = store.get_scope_contract(
        workflow_run_id, version=resulting_version
    )
    assert resulting_contract is not None
    later_snapshot = CoverageSnapshot(
        id=f"scv_api_later_{payload['resolution']}",
        workflow_run_id=workflow_run_id,
        scope_contract_id=resulting_contract.id,
        scope_contract_version=resulting_version,
        state="satisfied",
        constraint_counts={},
        unmet_constraint_ids=(),
        execution_revision=authorization.execution_revision,
        execution_authorization_id=authorization.id,
        source_coverage_snapshot_id=payload["coverage_snapshot_id"],
    )
    store.save_coverage_snapshot(later_snapshot)
    assert (
        store.get_coverage_snapshot(workflow_run_id, version=resulting_version).id
        == later_snapshot.id
    )

    replay = await scope_client.post(
        endpoint, json={"action": "resolve_coverage", "payload": payload}
    )

    assert replay.status_code == 200
    assert replay.json()["result"] == first_result
    assert replay.json()["result"]["execution_unit"]["id"] == original_unit_id
    assert replay.json()["result"]["execution_unit"]["recovery_state"] == "replayable"
    assert [
        (fact.attempt_no, fact.sequence_no, fact.kind)
        for fact in store.execution_trace(original_unit_id)
    ] == original_fact_sequence
    assert original_fact_sequence == [(0, 1, "decision_accepted")]
    expected_versions = [1, 2] if payload["resolution"] == "relax_constraint" else [1]
    assert [
        contract.version for contract in store.list_scope_contracts(workflow_run_id)
    ] == expected_versions
    assert len(store.list_scope_execution_authorizations(workflow_run_id)) == 1
    assert len(store.list_scope_execution_continuations(workflow_run_id)) == 1
    assert (
        len(
            [
                event
                for event in store.list_scope_audit_events(
                    workflow_run_id, version=resulting_version
                )
                if event.event_name == "coverage_resolved"
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_resolve_coverage_rejects_a_different_decision_for_the_same_snapshot(scope_client):
    """Allowing a second decision for one coverage snapshot must fail this test."""
    workflow_run_id, _contract_v1 = await _confirmed_scope_with_unmet_season(scope_client)
    endpoint = f"/content-research/workflows/{workflow_run_id}/actions"

    first = await scope_client.post(
        endpoint,
        json={
            "action": "resolve_coverage",
            "payload": {
                "scope_contract_version": 1,
                "coverage_snapshot_id": "scv_api_unmet_season",
                "resolution": "generate_limited_report",
            },
        },
    )
    conflicting = await scope_client.post(
        endpoint,
        json={
            "action": "resolve_coverage",
            "payload": {
                "scope_contract_version": 1,
                "coverage_snapshot_id": "scv_api_unmet_season",
                "resolution": "relax_constraint",
                "constraint_id": "season",
            },
        },
    )

    assert first.status_code == 200
    assert conflicting.status_code == 422
    store = app.state.content_research_service._store
    assert len(store.list_scope_execution_authorizations(workflow_run_id)) == 1


@pytest.mark.asyncio
async def test_limited_resolution_reconciles_competing_atomic_calls(scope_client):
    """Bypassing the atomic resolution operation must make this test fail."""
    workflow_run_id, _contract_v1 = await _confirmed_scope_with_unmet_season(scope_client)
    store = app.state.content_research_service._store
    request = {
        "action": "resolve_coverage",
        "payload": {
            "scope_contract_version": 1,
            "coverage_snapshot_id": "scv_api_unmet_season",
            "resolution": "generate_limited_report",
        },
    }
    first, second = await asyncio.gather(
        scope_client.post(f"/content-research/workflows/{workflow_run_id}/actions", json=request),
        scope_client.post(f"/content-research/workflows/{workflow_run_id}/actions", json=request),
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["result"] == second.json()["result"]
    assert len(store.list_scope_execution_authorizations(workflow_run_id)) == 1
    assert len(store.list_scope_execution_continuations(workflow_run_id)) == 1
    assert (
        len(
            [
                event
                for event in store.list_scope_audit_events(workflow_run_id, version=1)
                if event.event_name == "coverage_resolved"
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_competing_different_atomic_decisions_have_one_winner(scope_client):
    workflow_run_id, _contract_v1 = await _confirmed_scope_with_unmet_season(scope_client)
    endpoint = f"/content-research/workflows/{workflow_run_id}/actions"
    limited, expand = await asyncio.gather(
        scope_client.post(
            endpoint,
            json={
                "action": "resolve_coverage",
                "payload": {
                    "scope_contract_version": 1,
                    "coverage_snapshot_id": "scv_api_unmet_season",
                    "resolution": "generate_limited_report",
                },
            },
        ),
        scope_client.post(
            endpoint,
            json={
                "action": "resolve_coverage",
                "payload": {
                    "scope_contract_version": 1,
                    "coverage_snapshot_id": "scv_api_unmet_season",
                    "resolution": "expand_required_constraint",
                    "constraint_id": "season",
                    "supplementary_queries": ["夏季 防晒 长袖衬衫"],
                },
            },
        ),
    )

    assert sorted([limited.status_code, expand.status_code]) == [200, 422]
    store = app.state.content_research_service._store
    assert len(store.list_scope_execution_authorizations(workflow_run_id)) == 1
    assert len(store.list_scope_execution_continuations(workflow_run_id)) == 1


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
                "query_groups": [{"final_query": final_query} for final_query in final_queries],
            },
        },
    )

    assert response.status_code == 200
    contract = response.json()["result"]["scope_contract"]
    store = app.state.content_research_service._store
    persisted_draft = store.get_scope_draft(scope["id"])
    assert persisted_draft is not None
    persisted_contract = store.get_scope_contract(workflow_run_id, version=contract["version"])
    assert persisted_contract is not None
    assert contract["constraints"] == scope["constraints"]
    assert (
        contract["query_groups"][0]["suggested_query"]
        == scope["query_groups"][0]["suggested_query"]
    )
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
    persisted_events = store.list_scope_audit_events(workflow_run_id, version=contract["version"])
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
                    {"final_query": group["final_query"]} for group in scope["query_groups"]
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
                "query_groups": [{"final_query": scope["query_groups"][0]["final_query"]}],
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
                    {"final_query": group["final_query"]} for group in scope["query_groups"]
                ],
            },
        },
    )

    assert response.status_code == 422
    assert "current brief" in response.json()["error_message"]
    assert store.list_scope_contracts(workflow_run_id) == []


@pytest.mark.asyncio
async def test_confirm_scope_rechecks_current_brief_inside_atomic_confirmation(
    scope_client, monkeypatch
):
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
                    {"final_query": group["final_query"]} for group in scope["query_groups"]
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


@pytest.mark.asyncio
async def test_scope_projection_returns_pending_draft_with_confirm_command_without_writes(
    scope_client,
):
    """Rejecting a persisted pending Draft or writing during its read must fail this test."""
    workflow = await _scope_ready_workflow(scope_client)
    workflow_run_id = workflow["presearch"]["workflow_run_id"]
    prepared = await scope_client.post(
        f"/content-research/workflows/{workflow_run_id}/actions",
        json={"action": "prepare_scope", "payload": {"direction_id": "product_marketing"}},
    )
    assert prepared.status_code == 200
    draft = prepared.json()["result"]["scope"]
    store = app.state.content_research_service._store

    def persisted_scope_counts() -> dict[str, int]:
        with store._connect() as conn:
            return {
                "drafts": conn.execute(
                    "SELECT COUNT(*) FROM content_research_scope_drafts WHERE workflow_run_id = ?",
                    (workflow_run_id,),
                ).fetchone()[0],
                "contracts": conn.execute(
                    "SELECT COUNT(*) FROM content_research_scope_contracts WHERE workflow_run_id = ?",
                    (workflow_run_id,),
                ).fetchone()[0],
                "draft_audits": conn.execute(
                    "SELECT COUNT(*) FROM content_research_scope_draft_audit_events WHERE workflow_run_id = ?",
                    (workflow_run_id,),
                ).fetchone()[0],
                "scope_audits": conn.execute(
                    "SELECT COUNT(*) FROM content_research_scope_audit_events WHERE workflow_run_id = ?",
                    (workflow_run_id,),
                ).fetchone()[0],
            }

    before = persisted_scope_counts()

    response = await scope_client.get(f"/content-research/workflows/{workflow_run_id}/scope?version=99")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "awaiting_confirmation"
    assert body["draft"]["id"] == draft["id"]
    assert body["scope_contract"] is None
    assert [event["event_name"] for event in body["audit_events"]] == ["scope_suggested"]
    assert body["allowed_actions"] == [
        {
            "action": "confirm_scope",
            "available": True,
            "scope_draft_id": draft["id"],
            "structure_hash": draft["structure_hash"],
            "query_groups": draft["query_groups"],
        }
    ]
    assert body["coverage_snapshot"] is None
    assert body["allowed_resolutions"] == []
    after = persisted_scope_counts()
    assert after == before


@pytest.mark.asyncio
async def test_confirmed_scope_projection_includes_current_action_metadata(scope_client):
    """An unbound newer snapshot must not override the authorized continuation."""
    workflow_run_id, contract = await _confirmed_scope_with_unmet_season(scope_client)
    continuation = await _authorized_continuation_snapshot(
        scope_client,
        workflow_run_id=workflow_run_id,
        contract=contract,
        snapshot_id="scv_api_authorized_continuation",
        constraint_counts={
            "scenario": {"required": True},
            "_summary": {"reason_codes": ["required_constraint_coverage_unmet:scenario"]},
        },
        unmet_constraint_ids=("scenario",),
    )
    store = app.state.content_research_service._store
    store.save_coverage_snapshot(
        CoverageSnapshot(
            id="scv_api_unbound_higher_revision",
            workflow_run_id=workflow_run_id,
            scope_contract_id=contract["id"],
            scope_contract_version=contract["version"],
            state="awaiting_scope_decision",
            constraint_counts={"_summary": {"reason_codes": ["minimum_samples_unmet"]}},
            unmet_constraint_ids=(),
            execution_revision=continuation["snapshot"].execution_revision + 1,
        )
    )

    response = await scope_client.get(f"/content-research/workflows/{workflow_run_id}/scope")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "confirmed"
    assert body["scope_contract"] == contract
    assert body["allowed_actions"] == [
        {
            "action": "prepare_scope",
            "available": False,
            "unavailable_reason": "coverage_decision_required",
            "recovery_action": "resolve_coverage",
        },
        {
            "action": "confirm_scope",
            "available": False,
            "unavailable_reason": "coverage_decision_required",
            "recovery_action": "resolve_coverage",
        },
        {
            "action": "resolve_coverage",
            "available": True,
            "scope_contract_version": contract["version"],
            "coverage_snapshot_id": "scv_api_authorized_continuation",
        }
    ]
    assert body["coverage_snapshot"]["id"] == "scv_api_authorized_continuation"
    execution_unit = body["execution_unit"]
    assert execution_unit["id"] == continuation["authorization"]["execution_unit_id"]
    assert execution_unit["state"] == "pending"
    assert execution_unit["attempt_no"] == 0
    assert execution_unit["allowed_actions"] == []
    assert execution_unit["trace_summary"] == {
        "fact_count": 1,
        "attempt_count": 1,
        "last_fact_kind": "decision_accepted",
    }
    assert "lease_token" not in execution_unit
    assert "lease_owner" not in execution_unit
    resolutions = {item["action"]: item for item in body["allowed_resolutions"]}
    assert resolutions["expand_required_constraint"]["valid_constraint_ids"] == ["scenario"]
    assert resolutions["relax_constraint"]["valid_constraint_ids"] == ["scenario"]


@pytest.mark.asyncio
async def test_scope_projection_marks_unknown_outcome_for_manual_recovery_without_replay(
    scope_client,
):
    workflow_run_id, contract = await _confirmed_scope_with_unmet_season(scope_client)
    continuation = await _authorized_continuation_snapshot(
        scope_client,
        workflow_run_id=workflow_run_id,
        contract=contract,
        snapshot_id="scv_api_unknown_outcome",
        constraint_counts={
            "season": {"required": True},
            "_summary": {"reason_codes": ["required_constraint_coverage_unmet:season"]},
        },
        unmet_constraint_ids=("season",),
    )
    store = app.state.content_research_service._store
    unit_id = continuation["authorization"]["execution_unit_id"]
    with store._connect() as conn:
        conn.execute(
            "UPDATE content_research_scope_execution_units SET state='outcome_unknown' WHERE id=?",
            (unit_id,),
        )
        conn.execute(
            "UPDATE content_research_scope_execution_attempts SET state='outcome_unknown', provider_state='outcome_unknown' WHERE execution_unit_id=? AND attempt_no=0",
            (unit_id,),
        )

    response = await scope_client.get(f"/content-research/workflows/{workflow_run_id}/scope")

    assert response.status_code == 200
    execution_unit = response.json()["execution_unit"]
    assert execution_unit["state"] == "outcome_unknown"
    assert execution_unit["recovery_state"] == "outcome_unknown"
    assert execution_unit["allowed_actions"] == []
    assert "lease_token" not in execution_unit


@pytest.mark.asyncio
async def test_scope_projection_declares_exact_replay_only_for_known_retryable_failure(
    scope_client,
):
    workflow_run_id, contract = await _confirmed_scope_with_unmet_season(scope_client)
    continuation = await _authorized_continuation_snapshot(
        scope_client,
        workflow_run_id=workflow_run_id,
        contract=contract,
        snapshot_id="scv_api_retryable_failure",
        constraint_counts={
            "season": {"required": True},
            "_summary": {"reason_codes": ["required_constraint_coverage_unmet:season"]},
        },
        unmet_constraint_ids=("season",),
    )
    store = app.state.content_research_service._store
    unit_id = continuation["authorization"]["execution_unit_id"]
    with store._connect() as conn:
        conn.execute(
            "UPDATE content_research_scope_execution_units SET state='failed' WHERE id=?",
            (unit_id,),
        )
        conn.execute(
            "UPDATE content_research_scope_execution_attempts SET state='failed', provider_state='retryable_failed' WHERE execution_unit_id=? AND attempt_no=0",
            (unit_id,),
        )

    response = await scope_client.get(f"/content-research/workflows/{workflow_run_id}/scope")

    assert response.status_code == 200
    assert response.json()["execution_unit"]["allowed_actions"] == [
        {
            "action": "replay_coverage_decision",
            "available": True,
            "request": {
                "scope_contract_version": 1,
                "coverage_snapshot_id": "scv_api_unmet_season",
                "resolution": "expand_required_constraint",
                "constraint_id": "season",
                "supplementary_queries": ["夏季 防晒 长袖衬衫"],
            },
        }
    ]

    with store._connect() as conn:
        conn.execute(
            "UPDATE content_research_scope_execution_attempts SET provider_state='succeeded' WHERE execution_unit_id=? AND attempt_no=0",
            (unit_id,),
        )
    downstream_failure = await scope_client.get(
        f"/content-research/workflows/{workflow_run_id}/scope"
    )
    assert downstream_failure.json()["execution_unit"]["allowed_actions"] == []


@pytest.mark.asyncio
async def test_scope_projection_exposes_initial_unresolved_coverage_without_authorization(
    scope_client,
):
    """Hiding a genuine initial coverage snapshot must fail this test."""
    workflow_run_id, contract = await _confirmed_scope_with_unmet_season(scope_client)

    response = await scope_client.get(f"/content-research/workflows/{workflow_run_id}/scope")

    assert response.status_code == 200
    body = response.json()
    assert body["coverage_snapshot"]["id"] == "scv_api_unmet_season"
    assert body["coverage_snapshot"]["execution_revision"] == 1
    assert body["coverage_snapshot"]["execution_authorization_id"] is None
    assert body["allowed_actions"] == [
        {
            "action": "prepare_scope",
            "available": False,
            "unavailable_reason": "coverage_decision_required",
            "recovery_action": "resolve_coverage",
        },
        {
            "action": "confirm_scope",
            "available": False,
            "unavailable_reason": "coverage_decision_required",
            "recovery_action": "resolve_coverage",
        },
        {
            "action": "resolve_coverage",
            "available": True,
            "scope_contract_version": contract["version"],
            "coverage_snapshot_id": "scv_api_unmet_season",
        }
    ]
    assert body["decision_recovery"] == {
        "state": "coverage_decision_required",
        "message": (
            "Current Coverage is awaiting a user decision; ordinary Scope preparation and "
            "confirmation are unavailable."
        ),
        "required_action": "resolve_coverage",
        "allowed_resolutions": [
            "expand_required_constraint",
            "generate_limited_report",
            "relax_constraint",
        ],
    }
    resolutions = {item["action"]: item for item in body["allowed_resolutions"]}
    assert resolutions["expand_required_constraint"]["valid_constraint_ids"] == ["season"]
    assert resolutions["relax_constraint"]["valid_constraint_ids"] == ["season"]


@pytest.mark.asyncio
async def test_scope_projection_only_offers_required_constraint_resolutions(scope_client):
    """Offering expansion for global-only shortfalls or the wrong constraint must fail."""
    workflow_run_id, contract = await _confirmed_scope_with_unmet_season(scope_client)
    continuation = await _authorized_continuation_snapshot(
        scope_client,
        workflow_run_id=workflow_run_id,
        contract=contract,
        snapshot_id="scv_api_global_shortfall",
        constraint_counts={
            "_summary": {
                "minimum_samples": 9,
                "minimum_independent_authors": 9,
                "reason_codes": ["minimum_samples_unmet", "minimum_independent_authors_unmet"],
            }
        },
        unmet_constraint_ids=(),
    )
    assert continuation["snapshot"].execution_authorization_id

    global_response = await scope_client.get(f"/content-research/workflows/{workflow_run_id}/scope")

    assert global_response.status_code == 200
    global_resolutions = {
        item["action"]: item for item in global_response.json()["allowed_resolutions"]
    }
    assert global_resolutions["generate_limited_report"] == {
        "action": "generate_limited_report",
        "available": True,
        "valid_constraint_ids": [],
        "supplementary_queries_required": False,
        "unavailable_reason": None,
    }
    for action in ("expand_required_constraint", "relax_constraint"):
        assert global_resolutions[action] == {
            "action": action,
            "available": False,
            "valid_constraint_ids": [],
            "supplementary_queries_required": action == "expand_required_constraint",
            "unavailable_reason": "no_unmet_required_constraints",
        }
