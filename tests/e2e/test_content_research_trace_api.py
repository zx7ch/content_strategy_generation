from __future__ import annotations

import json

import httpx
import pytest

from app.api.routes.router import app
from app.content_research.presearch.service import PresearchService
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.services.workflow_run_manager import WorkflowRunManager
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
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
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
    assert trace["usage_summary"]["total_calls"] == 1
    assert trace["usage_summary"]["total_tokens"] == 33
    assert trace["usage_steps"][0]["agent_name"] == "PresearchAgent"
    assert trace["usage_events"][0]["job_id"] == presearch["workflow_run_id"]


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
    assert trace["provider_operations"] == [
        {
            "operation_fingerprint": "trace-auth-required-operation",
            "operation": "discover_candidates",
            "provider": "xiaohongshu",
            "provider_operation": "discover_candidates",
            "source_kind": "search_result_minimal",
            "result_status": "failed",
            "item_count": 0,
            "completeness": "unavailable",
            "cookie_status": None,
            "status": "auth_required",
            "started_at": None,
            "finished_at": None,
            "failure_code": "auth_required",
            "failure_reason": "auth_required",
            "retryable": False,
            "recovery_action": "更新小红书登录态后继续。",
        }
    ]
    assert "RAW_PROVIDER_QUERY_MUST_NOT_ESCAPE" not in response.text


@pytest.mark.asyncio
async def test_content_research_trace_api_missing_workflow_returns_404(client_with_db):
    client, _db_path = client_with_db

    response = await client.get("/content-research/workflows/run_missing/trace")

    assert response.status_code == 404
    assert response.json()["error_code"] == "CONTENT_RESEARCH_PRESEARCH_NOT_FOUND"
