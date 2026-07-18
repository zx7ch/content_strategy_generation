from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from app.content_research.api_schemas import (
    ContentResearchBriefConfirmRequest,
    ContentResearchSourceCollectionRequest,
)
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.sources import SourceAdapterRegistry
from app.content_research.sources.base import SourceCollectionResult
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.workflow_store import WorkflowStore
from app.services.llm.types import LLMResponse, TokenUsage


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


class BarrierSourceAdapter:
    async def collect(self, request):
        return SourceCollectionResult(
            provider="xiaohongshu",
            source_kind=request.source_kind,
            status="completed",
            cookie_status="valid",
            items=[{"canonical_id": "note-1", "title": "徒步短裤"}],
        )


class BarrierTaskRouter:
    """Fails under serial dispatch: both tasks must enter before either returns."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.source_results: list[object | None] = []
        self._both_started = asyncio.Event()

    async def execute_task(self, task, **kwargs):
        self.started.append(task.id)
        self.source_results.append(kwargs.get("source_result"))
        if len(self.started) == 2:
            self._both_started.set()
        await self._both_started.wait()
        return replace(
            task,
            status="completed",
            payload={
                **task.payload,
                "output_payload": {
                    "schema_version": "content_research_subagent_output_v1",
                    "evidence_bundle_id": None,
                },
            },
        )


class RetryableTaskRouter:
    """Persists one failed specialist, then succeeds it on the next attempt."""

    def __init__(self, store) -> None:
        self.store = store
        self.attempts: dict[str, int] = {}

    async def execute_task(self, task, **_kwargs):
        attempt = self.attempts.get(task.id, 0) + 1
        self.attempts[task.id] = attempt
        should_fail = task.direction_id == "product_marketing" and attempt == 1
        terminal = replace(
            task,
            status="failed" if should_fail else "completed",
            payload={
                **task.payload,
                "output_payload": {
                    "schema_version": "content_research_subagent_output_v1",
                    "evidence_bundle_id": f"eb_{task.id}" if not should_fail else None,
                    "error_message": "temporary specialist failure" if should_fail else None,
                },
            },
        )
        self.store.save_subagent_task(terminal)
        return terminal


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


@pytest.mark.asyncio
async def test_presearch_initializes_p0_runtime_steps(db_path, service):
    response = await service.submit_presearch(
        seed_text="徒步短裤",
        user_note="关注夏季",
        thread_id="thread-runtime-1",
        user_id="user-runtime-1",
    )

    async with WorkflowStore(db_path) as workflow_store:
        run = await workflow_store.get_run(response.workflow_run_id)
        steps = await workflow_store.list_steps(response.workflow_run_id)
        events = await workflow_store.list_events(response.workflow_run_id)

    assert run is not None
    assert run.status == "running"
    assert run.current_step == "brief_confirm"
    assert [step.step_name for step in steps] == [
        "presearch",
        "brief_confirm",
        "plan_build",
        "formal_research",
    ]
    assert steps[0].status == "succeeded"
    assert [step.status for step in steps[1:]] == ["pending", "pending", "pending"]
    assert [event.event_type for event in events] == [
        "run_started",
        "steps_initialized",
        "step_started",
        "step_completed",
        "run_advanced",
    ]


@pytest.mark.asyncio
async def test_confirm_brief_advances_runtime_and_links_child_tasks(db_path, store, service):
    presearch = await service.submit_presearch(
        seed_text="徒步短裤",
        user_note=None,
        thread_id="thread-runtime-2",
        user_id="user-runtime-2",
    )

    summary = await service.confirm_brief(
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

    async with WorkflowStore(db_path) as workflow_store:
        run = await workflow_store.get_run(presearch.workflow_run_id)
        steps = await workflow_store.list_steps(presearch.workflow_run_id)
        child_tasks = await workflow_store.list_child_tasks(presearch.workflow_run_id)
        events = await workflow_store.list_events(presearch.workflow_run_id)

    assert run is not None
    assert run.current_step == "formal_research"
    assert {step.step_name: step.status for step in steps} == {
        "presearch": "succeeded",
        "brief_confirm": "succeeded",
        "plan_build": "succeeded",
        "formal_research": "running",
    }
    assert len(child_tasks) == 2
    assert [task.task_type for task in child_tasks] == [
        "product_marketing_research",
        "comment_insight_research",
    ]
    assert all(task.checkpoint_json for task in child_tasks)
    assert [task.payload["workflow_child_task_id"] for task in store.list_subagent_tasks_for_workflow(presearch.workflow_run_id)] == [
        task.child_task_id for task in child_tasks
    ]
    assert len(summary.runtime_child_tasks) == 2
    assert summary.subagent_tasks[0].payload["workflow_child_task_id"].startswith("child_")
    assert "child_tasks_created" in [event.event_type for event in events]


@pytest.mark.asyncio
async def test_formal_research_pause_acknowledges_at_safe_boundary_then_resumes(db_path, service):
    presearch = await service.submit_presearch(
        seed_text="徒步短裤", user_note=None, thread_id="thread-runtime-pause", user_id="user-runtime-pause",
    )
    await service.confirm_brief(
        brief_id=presearch.brief_id,
        confirmation_request=ContentResearchBriefConfirmRequest(
            confirmed_subject="徒步短裤", selected_directions=["product_marketing"],
        ),
    )
    runtime = WorkflowRunManagerRuntime(db_path)
    assert (await runtime.pause_content_research_run(workflow_run_id=presearch.workflow_run_id))["status"] == "pausing"
    assert (await runtime.acknowledge_pause_at_safe_boundary(workflow_run_id=presearch.workflow_run_id))["status"] == "paused"
    assert (await runtime.resume_content_research_run(workflow_run_id=presearch.workflow_run_id))["status"] == "running"


@pytest.mark.asyncio
async def test_formal_research_dispatches_all_selected_subagents_before_completion(db_path, service):
    presearch = await service.submit_presearch(
        seed_text="徒步短裤", user_note=None, thread_id="thread-runtime-parallel", user_id="user-runtime-parallel"
    )
    await service.confirm_brief(
        brief_id=presearch.brief_id,
        confirmation_request=ContentResearchBriefConfirmRequest(
            confirmed_subject="徒步短裤",
            selected_directions=["product_marketing", "comment_insight"],
        ),
    )
    router = BarrierTaskRouter()
    service._task_router = router
    service._source_registry = SourceAdapterRegistry({"xiaohongshu": BarrierSourceAdapter()})

    await asyncio.wait_for(
        service.start_formal_research(
            workflow_run_id=presearch.workflow_run_id,
            request=ContentResearchSourceCollectionRequest(),
        ),
        timeout=1,
    )
    assert len(router.started) == 2
    assert router.source_results == [None, None]
    async with WorkflowStore(db_path) as workflow_store:
        run = await workflow_store.get_run(presearch.workflow_run_id)
        child_tasks = await workflow_store.list_child_tasks(presearch.workflow_run_id)
    assert run is not None and run.status == "succeeded"
    assert {task.status for task in child_tasks} == {"succeeded"}


@pytest.mark.asyncio
async def test_failed_specialist_blocks_parent_completion_and_retry_reuses_same_child_task(db_path, store, service):
    presearch = await service.submit_presearch(
        seed_text="徒步短裤", user_note=None, thread_id="thread-runtime-retry", user_id="user-runtime-retry"
    )
    await service.confirm_brief(
        brief_id=presearch.brief_id,
        confirmation_request=ContentResearchBriefConfirmRequest(
            confirmed_subject="徒步短裤",
            selected_directions=["product_marketing", "comment_insight"],
        ),
    )
    router = RetryableTaskRouter(store)
    service._task_router = router
    service._source_registry = SourceAdapterRegistry({"xiaohongshu": BarrierSourceAdapter()})

    await service.start_formal_research(
        workflow_run_id=presearch.workflow_run_id,
        request=ContentResearchSourceCollectionRequest(),
    )

    async with WorkflowStore(db_path) as workflow_store:
        run = await workflow_store.get_run(presearch.workflow_run_id)
        steps = await workflow_store.list_steps(presearch.workflow_run_id)
        first_children = await workflow_store.list_child_tasks(presearch.workflow_run_id)
        first_events = await workflow_store.list_events(presearch.workflow_run_id)
    assert run is not None and run.status == "running"
    assert next(step for step in steps if step.step_name == "formal_research").status == "running"
    assert {child.status for child in first_children} == {"succeeded", "failed"}
    assert store.list_result_snapshots_for_workflow(presearch.workflow_run_id) == []
    assert "formal_research_needs_retry" in [event.event_type for event in first_events]

    await service.start_formal_research(
        workflow_run_id=presearch.workflow_run_id,
        request=ContentResearchSourceCollectionRequest(),
    )

    async with WorkflowStore(db_path) as workflow_store:
        run = await workflow_store.get_run(presearch.workflow_run_id)
        steps = await workflow_store.list_steps(presearch.workflow_run_id)
        second_children = await workflow_store.list_child_tasks(presearch.workflow_run_id)
    assert run is not None and run.status == "succeeded"
    assert next(step for step in steps if step.step_name == "formal_research").status == "succeeded"
    assert all(child.status == "succeeded" for child in second_children)
    assert [child.child_task_id for child in second_children] == [child.child_task_id for child in first_children]
    assert router.attempts and sorted(router.attempts.values()) == [1, 2]
