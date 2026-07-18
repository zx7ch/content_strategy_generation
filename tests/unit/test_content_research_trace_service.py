from __future__ import annotations

import json

import pytest

from app.content_research.api_schemas import ContentResearchBriefConfirmRequest
from app.content_research.presearch.service import PresearchService
from app.content_research.service import (
    ContentResearchNotFoundError,
    ContentResearchService,
    WorkflowRunManagerRuntime,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.services.llm.pricing import UsageCost
from app.services.llm.types import LLMCallContext, LLMResponse, TokenUsage
from app.services.llm.usage_tracker import LLMUsageEventInput, LLMUsageTracker


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
def db_path(tmp_path):
    return str(tmp_path / "content_research.db")


@pytest.fixture()
def store(db_path):
    return SQLiteContentResearchStore(db_path)


@pytest.fixture()
def service(db_path, store):
    return ContentResearchService(
        store=store,
        presearch=PresearchService(FakeLLM(), first_feedback_timeout_seconds=0.05, hard_cutoff_seconds=0.1),
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
    )


async def _confirmed_workflow(service):
    presearch = await service.submit_presearch(
        seed_text="徒步短裤",
        user_note="关注夏季",
        thread_id="thread-trace-unit",
        user_id="user-trace-unit",
    )
    await service.confirm_brief(
        brief_id=presearch.brief_id,
        confirmation_request=ContentResearchBriefConfirmRequest(
            confirmed_subject="徒步短裤",
            subject_type="category",
            selected_competitors=["迪卡侬"],
            custom_competitors=["凯乐石"],
            selected_directions=["product_marketing", "comment_insight"],
            custom_research_question="关注轻量速干",
        ),
    )
    return presearch


async def _record_usage(db_path: str, workflow_run_id: str) -> None:
    async with LLMUsageTracker(db_path) as tracker:
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(
                    session_id="thread-trace-unit",
                    job_id=workflow_run_id,
                    step_id="presearch",
                    step_name="presearch",
                    agent_name="PresearchAgent",
                ),
                provider="fake",
                model="fake-model",
                model_policy="test",
                usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
                cost=UsageCost(input_cost=0.01, output_cost=0.02, total_cost=0.03),
                latency_ms=123,
                status="success",
            )
        )
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(
                    session_id="thread-trace-unit",
                    job_id=workflow_run_id,
                    step_id="source_collect_minimal",
                    step_name="source_collect_minimal",
                    agent_name="CommentInsightAgent",
                ),
                provider="fake",
                model="fake-model",
                model_policy="test",
                usage=TokenUsage(prompt_tokens=5, completion_tokens=0, total_tokens=5),
                cost=UsageCost(total_cost=0.0),
                latency_ms=50,
                status="error",
                error_message="temporary failure",
            )
        )


@pytest.mark.asyncio
async def test_trace_aggregates_runtime_observations_and_usage(db_path, service):
    presearch = await _confirmed_workflow(service)
    await _record_usage(db_path, presearch.workflow_run_id)

    trace = await service.get_workflow_trace(presearch.workflow_run_id)

    assert trace.workflow_run_id == presearch.workflow_run_id
    assert trace.thread_id == "thread-trace-unit"
    assert trace.current_stage == "formal_research"
    assert trace.run_status == "running"
    assert trace.recoverable is True
    assert trace.duration_ms >= 0
    assert trace.error_count >= 1
    assert trace.retry_count >= 1
    assert [item["event_name"] for item in trace.observation_events] == [
        "presearch_started",
        "presearch_completed",
    ]
    assert "child_tasks_created" in [event["event_type"] for event in trace.workflow_events]
    assert [step["step_name"] for step in trace.runtime_steps] == [
        "presearch",
        "brief_confirm",
        "plan_build",
        "formal_research",
    ]
    assert len(trace.runtime_child_tasks) == 2
    assert trace.usage_summary["total_calls"] == 2
    assert trace.usage_summary["total_tokens"] == 35
    assert [step["agent_name"] for step in trace.usage_steps] == [
        "PresearchAgent",
        "CommentInsightAgent",
    ]
    assert trace.usage_events[1]["error_message"] == "temporary failure"


@pytest.mark.asyncio
async def test_trace_returns_zero_usage_defaults_when_no_usage_rows(service):
    presearch = await _confirmed_workflow(service)

    trace = await service.get_workflow_trace(presearch.workflow_run_id)

    assert trace.usage_summary["total_calls"] == 0
    assert trace.usage_summary["total_tokens"] == 0
    assert trace.usage_steps == []
    assert trace.usage_events == []


@pytest.mark.asyncio
async def test_trace_missing_workflow_raises_content_research_not_found(service):
    with pytest.raises(ContentResearchNotFoundError):
        await service.get_workflow_trace("run_missing")
