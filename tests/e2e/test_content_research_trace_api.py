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
from app.content_research.presearch.service import PresearchService
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.services.workflow_run_manager import WorkflowRunManager, WorkflowTransitionError
from app.services.llm.pricing import UsageCost
from app.services.llm.types import LLMCallContext, LLMResponse, TokenUsage
from app.services.llm.usage_tracker import LLMUsageEventInput, LLMUsageTracker


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
    presearch_response = await client.post(
        "/content-research/presearch",
        headers={"X-User-Id": "user-trace-api"},
        json={
            "seed_text": "Satisfy Running",
            "user_note": "关注跑步社群",
            "thread_id": "thread-trace-api",
        },
    )
    assert presearch_response.status_code == 201
    presearch = presearch_response.json()

    confirm_response = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "Satisfy Running",
            "subject_type": "brand",
            "selected_competitors": ["District Vision"],
            "custom_competitors": ["Salomon"],
            "selected_directions": ["product_marketing", "content_performance"],
            "custom_research_question": "关注跑步社群活动",
        },
    )
    assert confirm_response.status_code == 200
    await _record_usage(db_path, presearch["workflow_run_id"])

    trace_response = await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}/trace")

    assert trace_response.status_code == 200
    trace = trace_response.json()
    assert trace["workflow_run_id"] == presearch["workflow_run_id"]
    assert trace["thread_id"] == "thread-trace-api"
    assert trace["current_stage"] == "formal_research"
    assert trace["run_status"] == "running"
    assert trace["recoverable"] is True
    assert trace["duration_ms"] >= 0
    assert [event["event_name"] for event in trace["observation_events"]] == [
        "presearch_started",
        "presearch_completed",
    ]
    assert "child_tasks_created" in [event["event_type"] for event in trace["workflow_events"]]
    assert [step["status"] for step in trace["runtime_steps"]] == [
        "succeeded",
        "succeeded",
        "succeeded",
        "running",
    ]
    assert len(trace["runtime_child_tasks"]) == 2
    # Lite Trace intentionally exposes execution state, not LLM usage payloads.
    assert trace["usage_summary"] == {}
    assert trace["usage_steps"] == []
    assert trace["usage_events"] == []


@pytest.mark.asyncio
async def test_terminal_trace_timing_is_stable_across_repeated_reads(client_with_db):
    client, db_path = client_with_db
    presearch_response = await client.post(
        "/content-research/presearch",
        headers={"X-User-Id": "user-trace-terminal"},
        json={
            "seed_text": "Satisfy Running",
            "user_note": "验证终态冻结",
            "thread_id": "thread-trace-terminal",
        },
    )
    assert presearch_response.status_code == 201
    presearch = presearch_response.json()
    confirm_response = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "Satisfy Running",
            "subject_type": "brand",
            "selected_competitors": [],
            "custom_competitors": [],
            "selected_directions": ["product_marketing"],
            "custom_research_question": "",
        },
    )
    assert confirm_response.status_code == 200
    async with WorkflowRunManager(db_path) as manager:
        await manager.complete_run(presearch["workflow_run_id"])

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

    assert second["run_status"] == "succeeded"
    assert second["duration_ms"] == first["duration_ms"]
    assert [step["timing"] for step in second["runtime_steps"]] == [
        step["timing"] for step in first["runtime_steps"]
    ]
    assert [task["timing"] for task in second["runtime_child_tasks"]] == [
        task["timing"] for task in first["runtime_child_tasks"]
    ]


@pytest.mark.asyncio
async def test_content_research_trace_api_keeps_running_parent_and_safe_auth_required_child(
    client_with_db,
):
    client, db_path = client_with_db
    presearch_response = await client.post(
        "/content-research/presearch",
        headers={"X-User-Id": "user-trace-auth"},
        json={
            "seed_text": "Satisfy Running",
            "user_note": "关注登录恢复",
            "thread_id": "thread-trace-auth",
        },
    )
    assert presearch_response.status_code == 201
    presearch = presearch_response.json()
    confirm_response = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "Satisfy Running",
            "subject_type": "brand",
            "selected_competitors": [],
            "custom_competitors": [],
            "selected_directions": ["product_marketing"],
            "custom_research_question": "",
        },
    )
    assert confirm_response.status_code == 200
    child_task_id = confirm_response.json()["runtime_child_tasks"][0]["child_task_id"]
    async with WorkflowRunManager(db_path) as manager:
        await manager.start_child_task(child_task_id)
        await manager.fail_child_task(
            child_task_id,
            {"code": "auth_required", "message": "provider authentication required"},
        )
    SQLiteContentResearchStore(db_path).save_stage_checkpoint(
        StageCheckpointRecord(
            id="checkpoint-trace-auth-required",
            schema_version="content_research_stage_checkpoint_v1",
            workflow_run_id=presearch["workflow_run_id"],
            subagent_task_id=child_task_id,
            stage_name="operation",
            input_fingerprint="trace-auth-required-operation",
            status="auth_required",
            payload={
                "operation": "discover_candidates",
                "operation_fingerprint": "trace-auth-required-operation",
                "request": {"query": "RAW_PROVIDER_QUERY_MUST_NOT_ESCAPE"},
                "completion": {
                    "provider": "xiaohongshu",
                    "provider_operation": "discover_candidates",
                    "source_kind": "search_result_minimal",
                    "result_status": "failed",
                    "item_count": 0,
                    "completeness": "unavailable",
                    "failure_code": "auth_required",
                    "failure_reason": "auth_required",
                    "retryable": False,
                    "recovery_action": "更新小红书登录态后继续。",
                    "candidate_dispositions": {
                        "invalid_candidate": 2,
                        "eligible": 18,
                    },
                    "automatic_retry_count": 3,
                    "automatic_retry_limit": 3,
                },
            },
        )
    )

    response = await client.get(
        f"/content-research/workflows/{presearch['workflow_run_id']}/trace"
    )

    assert response.status_code == 200
    trace = response.json()
    assert trace["run_status"] == "running"
    failed_child = next(
        task
        for task in trace["runtime_child_tasks"]
        if task["child_task_id"] == child_task_id
    )
    assert failed_child["status"] == "failed"
    assert failed_child["error_code"] == "auth_required"
    assert failed_child["retry_counters"] == {
        "specialist_user_recovery": {"used": 0, "limit": 2},
        "workflow_child_attempt": {"used": 1, "limit": 3},
    }
    provider_operation = dict(trace["provider_operations"][0])
    assert provider_operation.pop("operation_id").startswith("op_")
    assert [provider_operation] == [
        {
            "operation_fingerprint": "trace-auth-required-operation",
            "operation": "discover_candidates",
            "provider": "xiaohongshu",
            "provider_operation": "discover_candidates",
            "source_kind": "search_result_minimal",
            "result_status": "failed",
            "status": "auth_required",
            "started_at": None,
            "finished_at": None,
            "failure_code": "auth_required",
            "retryable": False,
            "candidate_dispositions": {
                "invalid_candidate": 2,
                "eligible": 18,
            },
            "retry_counters": {
                "provider_automatic": {"used": 3, "limit": 3},
            },
        }
    ]
    assert "RAW_PROVIDER_QUERY_MUST_NOT_ESCAPE" not in response.text


@pytest.mark.asyncio
async def test_content_research_trace_api_redacts_persisted_source_results(
    client_with_db,
):
    client, db_path = client_with_db
    presearch_response = await client.post(
        "/content-research/presearch",
        headers={"X-User-Id": "user-trace-safe-projection"},
        json={
            "seed_text": "Satisfy Running",
            "user_note": "验证安全 Trace 投影",
            "thread_id": "thread-trace-safe-projection",
        },
    )
    assert presearch_response.status_code == 201
    presearch = presearch_response.json()
    store = SQLiteContentResearchStore(db_path)
    trace = TraceRecord(
        id="trace-safe-source-result",
        workflow_run_id=presearch["workflow_run_id"],
        thread_id="thread-trace-safe-projection",
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
            thread_id="thread-trace-safe-projection",
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
            thread_id="thread-trace-safe-projection",
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
    assert payload["run_status"] == "running"
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
    assert payload["runtime_steps"][0]["timing"]["timing_source"] == "recorded"
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
async def test_running_run_cannot_resume_without_auth_required_child(client_with_db):
    client, db_path = client_with_db
    presearch_response = await client.post(
        "/content-research/presearch",
        headers={"X-User-Id": "user-running-resume"},
        json={"seed_text": "Satisfy Running", "thread_id": "thread-running-resume"},
    )
    assert presearch_response.status_code == 201
    with pytest.raises(WorkflowTransitionError, match="auth-required child"):
        async with WorkflowRunManager(db_path) as manager:
            await manager.resume_run(presearch_response.json()["workflow_run_id"])


@pytest.mark.asyncio
async def test_content_research_trace_api_missing_workflow_returns_404(client_with_db):
    client, _db_path = client_with_db

    response = await client.get("/content-research/workflows/run_missing/trace")

    assert response.status_code == 404
    assert response.json()["error_code"] == "CONTENT_RESEARCH_PRESEARCH_NOT_FOUND"
