"""Integration tests for T7 generation-side workflow step executors."""

from __future__ import annotations

import pytest

from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowArtifactType, WorkflowPhase, WorkflowStepStatus
from app.services.step_executors import GenerationStepExecutor
from app.services.workflow_run_manager import WorkflowRunManager


async def _seed_run(db_path: str):
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(title="Generation Steps")
        message = await thread_store.append_message(
            thread_id=thread["id"],
            role="user",
            text="帮我生成两篇防晒衣笔记",
            intent="start_workflow",
        )
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(
            thread_id=thread["id"],
            user_id="user-1",
            user_message_id=message["id"],
        )
        steps = await manager.initialize_steps(
            run.run_id,
            [
                {"step_name": "generation.plan_proposals", "phase": WorkflowPhase.GENERATION},
                {"step_name": "generation.generate_notes_parallel", "phase": WorkflowPhase.GENERATION},
                {"step_name": "generation.similarity_check", "phase": WorkflowPhase.GENERATION},
                {"step_name": "generation.rewrite_or_reselect", "phase": WorkflowPhase.GENERATION},
            ],
        )
    async with WorkflowStore(db_path) as store:
        await store.create_artifact(
            run_id=run.run_id,
            thread_id=run.thread_id,
            artifact_type=WorkflowArtifactType.STRATEGY,
            payload={"positioning": "轻户外"},
        )
    return run, {step.step_name: step for step in steps}


@pytest.mark.asyncio
async def test_generation_parallel_child_tasks_partially_retry_and_recover(tmp_path):
    db_path = str(tmp_path / "workflow_generation_steps.db")
    run, steps = await _seed_run(db_path)

    async def fake_proposals(context):
        return [
            {"proposal_id": "p1", "title": "通勤防晒衣"},
            {"proposal_id": "p2", "title": "露营防晒衣"},
        ]

    proposal_result = await GenerationStepExecutor(
        db_path=db_path,
        proposal_runner=fake_proposals,
    ).execute(run.run_id, "generation.plan_proposals")
    assert len(proposal_result.artifact_refs) == 2

    calls: list[int] = []

    async def fake_note(context, target, index):
        calls.append(index)
        if index == 1:
            raise RuntimeError("temporary note failure")
        return {"title": f"note-{index}", "content": target["payload_json"]["title"]}

    first_generation = await GenerationStepExecutor(
        db_path=db_path,
        note_runner=fake_note,
    ).execute(run.run_id, "generation.generate_notes_parallel")

    async with WorkflowStore(db_path) as store:
        child_tasks = await store.list_child_tasks(run.run_id)
        artifacts = await store.list_artifacts(run.run_id)
        events = await store.list_events(run.run_id)

    assert calls == [0, 1]
    assert len(first_generation.artifact_refs) == 1
    assert [task.status for task in child_tasks] == [
        WorkflowStepStatus.SUCCEEDED,
        WorkflowStepStatus.RETRYING,
    ]
    assert [artifact.artifact_type.value for artifact in artifacts].count("generated_note") == 1
    event_types = [event.event_type for event in events]
    assert "child_tasks_created" in event_types
    assert "child_task_completed" in event_types
    assert "child_task_retry_scheduled" in event_types
    assert "artifact_attached" in event_types

    retry_calls: list[int] = []

    async def retry_note(context, target, index):
        retry_calls.append(index)
        return {"title": f"retry-note-{index}", "content": target["payload_json"]["title"]}

    second_generation = await GenerationStepExecutor(
        db_path=db_path,
        note_runner=retry_note,
    ).execute(run.run_id, "generation.generate_notes_parallel")

    async with WorkflowStore(db_path) as store:
        child_tasks = await store.list_child_tasks(run.run_id)
        artifacts = await store.list_artifacts(run.run_id)
        events = await store.list_events(run.run_id)

    assert retry_calls == [1]
    assert len(second_generation.artifact_refs) == 2
    assert len(second_generation.skipped_child_tasks) == 1
    assert all(task.status == WorkflowStepStatus.SUCCEEDED for task in child_tasks)
    assert [artifact.artifact_type.value for artifact in artifacts].count("generated_note") == 2
    assert steps["generation.generate_notes_parallel"].step_id == child_tasks[0].step_id
    assert [event.event_type for event in events].count("child_task_completed") == 2


@pytest.mark.asyncio
async def test_similarity_rewrite_creates_new_generated_note_version(tmp_path):
    db_path = str(tmp_path / "workflow_generation_rewrite.db")
    run, _steps = await _seed_run(db_path)

    async with WorkflowStore(db_path) as store:
        parent = await store.create_artifact(
            run_id=run.run_id,
            thread_id=run.thread_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            artifact_version=1,
            payload={"title": "old note", "content": "old"},
        )
        await store.create_artifact(
            run_id=run.run_id,
            thread_id=run.thread_id,
            artifact_type=WorkflowArtifactType.SIMILARITY_REPORT,
            payload={"should_rewrite": True, "target_artifact_id": parent.artifact_id},
        )

    async def fake_similarity(context):
        return {"max_similarity": 0.81, "should_rewrite": True}

    similarity = await GenerationStepExecutor(
        db_path=db_path,
        similarity_runner=fake_similarity,
    ).execute(run.run_id, "generation.similarity_check")
    assert similarity.artifact_refs[0]["artifact_type"] == "similarity_report"

    async def fake_rewrite(context):
        assert context.revision_targets[0]["artifact_id"] == parent.artifact_id
        return {"title": "new note", "content": "new"}

    rewrite = await GenerationStepExecutor(
        db_path=db_path,
        rewrite_runner=fake_rewrite,
    ).execute(run.run_id, "generation.rewrite_or_reselect")

    async with WorkflowStore(db_path) as store:
        artifacts = await store.list_artifacts(run.run_id)

    new_ref = rewrite.artifact_refs[0]
    assert new_ref["artifact_type"] == "generated_note"
    assert new_ref["artifact_version"] == 2
    assert new_ref["parent_artifact_id"] == parent.artifact_id
    assert [artifact.artifact_type.value for artifact in artifacts].count("generated_note") == 2
