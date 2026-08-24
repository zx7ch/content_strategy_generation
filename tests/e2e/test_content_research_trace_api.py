from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.api.routes.router import app
from app.content_research.models import ObservationEventRecord, TraceRecord, utcnow
from app.content_research.observation.trace_service import (
    _derive_current_stage,
    _provider_operations,
)
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from app.services.llm.pricing import UsageCost
from app.services.llm.types import LLMCallContext, LLMResponse, TokenUsage
from app.services.llm.usage_tracker import LLMUsageEventInput, LLMUsageTracker
from app.services.workflow_run_manager import WorkflowRunManager


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
                        "source_terms": ["Satisfy", "Running"],
                        "term_roles": {
                            "core_object": ["Satisfy", "Running"],
                            "product_experience": [],
                            "context_audience": [],
                        },
                        "core_entities": [{"canonical_name": "Satisfy Running", "raw_mentions": ["Satisfy Running"]}],
                        "research_intents": [],
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
async def client_with_db(tmp_path):
    original = getattr(app.state, "content_research_service", None)
    db_path = str(tmp_path / "content_research.db")
    app.state.content_research_service = ContentResearchService(
        store=SQLiteContentResearchStore(db_path),
        presearch=PresearchService(FakeLLM(), first_feedback_timeout_seconds=0.05, hard_cutoff_seconds=0.1),
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"X-Workspace-Id": "workspace-trace-api", "X-User-Id": "user-trace-api"},
    ) as client:
        yield client, db_path
    if original is None:
        delattr(app.state, "content_research_service")
    else:
        app.state.content_research_service = original


async def _record_usage(db_path: str, workflow_run_id: str) -> None:
    async with LLMUsageTracker(db_path) as tracker:
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(
                    session_id="thread-trace-api",
                    job_id=workflow_run_id,
                    step_id="presearch",
                    step_name="presearch",
                    agent_name="PresearchAgent",
                ),
                provider="fake",
                model="fake-model",
                model_policy="test",
                usage=TokenUsage(prompt_tokens=11, completion_tokens=22, total_tokens=33),
                cost=UsageCost(total_cost=0.04),
                latency_ms=99,
                status="success",
            )
        )


async def _create_creator_thread(db_path: str, title: str) -> str:
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(
            title=title,
            workspace_id="workspace-trace-api",
        )
    return str(thread["id"])


@pytest.mark.asyncio
async def test_trace_rejects_a_legacy_run_without_lifecycle_authority(
    client_with_db,
):
    client, db_path = client_with_db
    workflow_run_id = "run-interrupted-before-brief"
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(
            thread_id="thread-interrupted", user_id="user-interrupted", initial_request="夏季短裤"
        )
        assert run.run_id == workflow_run_id or run.run_id
        workflow_run_id = run.run_id
        await manager.initialize_steps(
            workflow_run_id,
            [{"step_name": "presearch", "phase": "intake", "max_attempts": 3}],
        )
        await manager.start_step(workflow_run_id, "presearch")

    store = SQLiteContentResearchStore(db_path)
    trace = store.save_trace(TraceRecord(
        id="trace-interrupted", workflow_run_id=workflow_run_id,
        thread_id="thread-interrupted", schema_version="content_research_trace_v1",
        status="running", started_at=utcnow(), payload={
            "schema_version": "content_research_trace_v1", "stage": "presearch"
        },
    ))
    store.append_observation_event(ObservationEventRecord(
        id="event-interrupted", trace_id=trace.id, workflow_run_id=workflow_run_id,
        thread_id="thread-interrupted", schema_version="content_research_observation_event_v1",
        status="recorded", sequence_no=1, event_type="task_started",
        event_name="presearch_started", timestamp=utcnow(), payload={
            "schema_version": "content_research_observation_event_v1"
        },
    ))

    response = await client.get(f"/content-research/workflows/{workflow_run_id}/trace")

    assert response.status_code == 422, response.text
    payload = response.json()
    assert payload["error_code"] == "INVALID_CONTENT_RESEARCH_PAYLOAD"
    assert "no current lifecycle authority" in payload["error_message"]


def test_provider_operations_do_not_merge_identical_calls_from_two_specialists(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "trace-specialists.db"))
    for task_id in ("specialist-a", "specialist-b"):
        store.save_stage_checkpoint(
            StageCheckpointRecord(
                id=f"checkpoint-{task_id}",
                schema_version="content_research_stage_checkpoint_v1",
                workflow_run_id="run-specialists",
                subagent_task_id=task_id,
                stage_name="operation",
                input_fingerprint="same-note-detail",
                status="completed",
                payload={
                    "operation": "detail",
                    "operation_fingerprint": "same-note-detail",
                    "completion": {
                        "provider": "xiaohongshu",
                        "provider_operation": "collect_note_detail",
                        "result_status": "completed",
                    },
                },
            )
        )

    operations = _provider_operations(store, "run-specialists")

    assert len(operations) == 2
    assert len({operation["operation_id"] for operation in operations}) == 2


@pytest.mark.asyncio
async def test_content_research_trace_api_restores_runtime_observation_and_usage(client_with_db):
    client, db_path = client_with_db
    thread_id = await _create_creator_thread(db_path, "Satisfy Running")
    presearch_response = await client.post(
        "/content-research/presearch",
        headers={"X-User-Id": "user-trace-api"},
        json={
            "command_id": "trace-runtime-presearch",
            "seed_text": "Satisfy Running",
            "user_note": "关注跑步社群",
            "thread_id": thread_id,
        },
    )
    assert presearch_response.status_code == 201
    presearch = presearch_response.json()

    run = presearch["run"]
    confirm_response = await client.post(
        f"/content-research/workflows/{run['run_id']}/actions",
        json={
            "command_id": "trace-confirm-brief",
            "expected_state": run["state"],
            "expected_revision": run["state_revision"],
            "action": "confirm_brief",
            "payload": {
                "brief_id": presearch["brief_id"],
                "selected_competitors": ["District Vision", "Salomon"],
                "custom_competitor_input": "",
                "selected_directions": ["product_marketing", "content_performance"],
            },
        },
    )
    assert confirm_response.status_code == 200
    await _record_usage(db_path, presearch["workflow_run_id"])

    trace_response = await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}/trace")

    assert trace_response.status_code == 200
    trace = trace_response.json()
    assert trace["workflow_run_id"] == presearch["workflow_run_id"]
    assert trace["thread_id"] == thread_id
    assert trace["current_stage"] == "scope_confirmation"
    assert trace["run_status"] == "waiting_user"
    assert trace["recoverable"] is False
    assert trace["duration_ms"] >= 0
    assert trace["observation_events"] == []
    assert "child_tasks_created" not in [
        event["event_type"] for event in trace["workflow_events"]
    ]
    assert trace["runtime_child_tasks"] == []
    # Lite Trace intentionally exposes execution state, not LLM usage payloads.
    assert trace["usage_summary"] == {}
    assert trace["usage_steps"] == []
    assert trace["usage_events"] == []


@pytest.mark.asyncio
async def test_marketing_conclusion_trace_api_exposes_only_safe_checkpoint_contract(
    client_with_db,
):
    client, db_path = client_with_db
    thread_id = await _create_creator_thread(db_path, "夏季凉感T恤")
    presearch_response = await client.post(
        "/content-research/presearch",
        json={
            "command_id": "trace-marketing-conclusion-presearch",
            "seed_text": "夏季凉感T恤",
            "user_note": "验证营销结论 Trace",
            "thread_id": thread_id,
        },
    )
    assert presearch_response.status_code == 201
    workflow_run_id = presearch_response.json()["workflow_run_id"]
    SQLiteContentResearchStore(db_path).save_stage_checkpoint(
        StageCheckpointRecord(
            id="checkpoint-marketing-conclusion-trace-api",
            schema_version="content_research_stage_checkpoint_v1",
            workflow_run_id=workflow_run_id,
            subagent_task_id="marketing-conclusion:plan-trace-api",
            stage_name="marketing_conclusion",
            input_fingerprint="private-input-fingerprint",
            status="waiting_user",
            payload={
                "reason_codes": ["marketing_analysis_unavailable", "secret-reason"],
                "failure_code": "llm_protocol_incompatible",
                "failure_detail": "invalid_json",
                "recovery_action": "repair_model_configuration_and_resume",
                "replayed_from_persisted_packets": True,
                "provider_operation_count_delta": 0,
                "packet_count_delta": 0,
                "query": "secret query",
                "prompt": "secret prompt",
                "quote": "secret quote",
                "candidate_count": 3,
                "note_id": "secret-note-id",
                "author_id": "secret-author-id",
                "provider_payload": {"response": "secret provider response"},
                "api_key": "secret-api-key",
            },
        )
    )

    response = await client.get(
        f"/content-research/workflows/{workflow_run_id}/trace"
    )

    assert response.status_code == 200
    checkpoint = next(
        item
        for item in response.json()["logical_checkpoints"]
        if item["stage"] == "marketing_conclusion"
    )
    assert checkpoint == {
        "stage": "marketing_conclusion",
        "status": "waiting_user",
        "reason_codes": ["marketing_analysis_unavailable"],
        "failure_code": "llm_protocol_incompatible",
        "failure_detail": "invalid_json",
        "recovery_action": "repair_model_configuration_and_resume",
        "replayed_from_persisted_packets": True,
        "provider_operation_count_delta": 0,
        "packet_count_delta": 0,
    }
    for forbidden in (
        "query",
        "prompt",
        "quote",
        "candidate_count",
        "note_id",
        "author_id",
        "private-input-fingerprint",
        "provider_payload",
        "secret provider response",
        "secret-api-key",
        "secret-reason",
    ):
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_terminal_trace_timing_is_stable_across_repeated_reads(client_with_db):
    client, db_path = client_with_db
    thread_id = await _create_creator_thread(db_path, "Satisfy Running")
    presearch_response = await client.post(
        "/content-research/presearch",
        headers={"X-User-Id": "user-trace-terminal"},
        json={
            "command_id": "trace-terminal-presearch",
            "seed_text": "Satisfy Running",
            "user_note": "验证终态冻结",
            "thread_id": thread_id,
        },
    )
    assert presearch_response.status_code == 201
    presearch = presearch_response.json()
    run = presearch["run"]
    cancelled = await client.post(
        f"/content-research/workflows/{run['run_id']}/actions",
        json={
            "command_id": "trace-terminal-cancel",
            "expected_state": run["state"],
            "expected_revision": run["state_revision"],
            "action": "cancel",
            "payload": {},
        },
    )
    assert cancelled.status_code == 200

    first = (
        await client.get(
            f"/content-research/workflows/{presearch['workflow_run_id']}/trace"
        )
    ).json()
    await asyncio.sleep(0.02)
    second = (
        await client.get(
            f"/content-research/workflows/{presearch['workflow_run_id']}/trace"
        )
    ).json()

    assert second["run_status"] == "failed"
    assert second["duration_ms"] == first["duration_ms"]
    assert [step["timing"] for step in second["runtime_steps"]] == [
        step["timing"] for step in first["runtime_steps"]
    ]
    assert [task["timing"] for task in second["runtime_child_tasks"]] == [
        task["timing"] for task in first["runtime_child_tasks"]
    ]


@pytest.mark.asyncio
async def test_content_research_trace_api_redacts_persisted_source_results(
    client_with_db,
):
    client, db_path = client_with_db
    thread_id = await _create_creator_thread(db_path, "Satisfy Running")
    presearch_response = await client.post(
        "/content-research/presearch",
        headers={"X-User-Id": "user-trace-safe-projection"},
        json={
            "command_id": "trace-safe-projection-presearch",
            "seed_text": "Satisfy Running",
            "user_note": "验证安全 Trace 投影",
            "thread_id": thread_id,
        },
    )
    assert presearch_response.status_code == 201
    presearch = presearch_response.json()
    store = SQLiteContentResearchStore(db_path)
    trace = TraceRecord(
        id="trace-safe-source-result",
        workflow_run_id=presearch["workflow_run_id"],
        thread_id=thread_id,
        schema_version="content_research_trace_v1",
        status="running",
        started_at=utcnow(),
        payload={
            "schema_version": "content_research_trace_payload_v1",
            "request": {"query": "RAW_TRACE_REQUEST_MUST_NOT_ESCAPE"},
        },
        metadata={"access_token": "RAW_TRACE_TOKEN_MUST_NOT_ESCAPE"},
    )
    store.save_trace(trace)
    store.append_observation_event(
        ObservationEventRecord(
            id="event-safe-source-started",
            trace_id=trace.id,
            workflow_run_id=presearch["workflow_run_id"],
            thread_id=thread_id,
            schema_version="content_research_observation_event_v1",
            status="running",
            sequence_no=1,
            event_type="task_started",
            event_name="source_collection_started",
            timestamp=utcnow(),
            payload={
                "schema_version": "content_research_observation_event_v1",
                "provider": "xiaohongshu",
                "operation": "discover_candidates",
                "request": {"query": "RAW_PROVIDER_REQUEST_MUST_NOT_ESCAPE"},
            },
        )
    )
    store.append_observation_event(
        ObservationEventRecord(
            id="event-safe-source-completed",
            trace_id=trace.id,
            workflow_run_id=presearch["workflow_run_id"],
            thread_id=thread_id,
            schema_version="content_research_observation_event_v1",
            status="completed",
            sequence_no=2,
            event_type="task_completed",
            event_name="source_collection_completed",
            timestamp=utcnow(),
            payload={
                "schema_version": "content_research_observation_event_v1",
                "source_collection": {
                    "status": "completed",
                    "items": [{"title": "RAW_SOURCE_ITEM_MUST_NOT_ESCAPE"}],
                    "metadata": {
                        "provider_response": "RAW_SOURCE_METADATA_MUST_NOT_ESCAPE"
                    },
                }
            },
        )
    )

    response = await client.get(
        f"/content-research/workflows/{presearch['workflow_run_id']}/trace"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_run_id"] == presearch["workflow_run_id"]
    assert payload["run_status"] == "waiting_user"
    assert payload["external_api_summary"] == {
        "call_count": 1,
        "completed_count": 1,
        "failed_count": 0,
        "by_provider": {"xiaohongshu": 1},
        "by_operation": {"discover_candidates": 1},
    }
    assert set(payload["runtime_steps"][0]) <= {
        "step_id",
        "step_name",
        "phase",
        "status",
        "attempt_count",
        "max_attempts",
        "started_at",
            "completed_at",
            "error_code",
            "timing",
    }
    serialized = response.text
    for forbidden in (
        "RAW_TRACE_REQUEST_MUST_NOT_ESCAPE",
        "RAW_TRACE_TOKEN_MUST_NOT_ESCAPE",
        "RAW_PROVIDER_REQUEST_MUST_NOT_ESCAPE",
        "RAW_SOURCE_ITEM_MUST_NOT_ESCAPE",
        "RAW_SOURCE_METADATA_MUST_NOT_ESCAPE",
    ):
        assert forbidden not in serialized


def test_trace_stage_never_uses_raw_trace_payload() -> None:
    trace = TraceRecord(
        id="trace-raw-stage",
        workflow_run_id="run-raw-stage",
        thread_id="thread-raw-stage",
        schema_version="content_research_trace_v1",
        status="running",
        started_at=utcnow(),
        payload={"stage": "RAW_TRACE_STAGE_MUST_NOT_ESCAPE"},
    )
    assert _derive_current_stage(run={}, steps=[], traces=[trace]) is None


@pytest.mark.asyncio
async def test_content_research_trace_api_missing_workflow_returns_404(client_with_db):
    client, _db_path = client_with_db

    response = await client.get("/content-research/workflows/run_missing/trace")

    assert response.status_code == 404
    assert response.json()["error_code"] == "CONTENT_RESEARCH_PRESEARCH_NOT_FOUND"
