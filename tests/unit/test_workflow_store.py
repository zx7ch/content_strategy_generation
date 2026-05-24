"""Unit tests for T1 WorkflowStore CRUD primitives."""

from __future__ import annotations

import pytest

from app.memory.workflow_store import WorkflowStore
from app.models.workflow import (
    WorkflowArtifactType,
    WorkflowConstraintType,
    WorkflowPhase,
    WorkflowRunStatus,
    WorkflowStepStatus,
)


@pytest.fixture
async def workflow_store(tmp_path):
    db_path = str(tmp_path / "workflow_store.db")
    async with WorkflowStore(db_path) as store:
        yield store


@pytest.mark.asyncio
async def test_create_and_read_workflow_run(workflow_store):
    run = await workflow_store.create_run(
        thread_id="thread-1",
        user_id="user-1",
        source_message_id="msg-1",
    )

    fetched = await workflow_store.get_run(run.run_id)

    assert fetched is not None
    assert fetched.run_id == run.run_id
    assert fetched.thread_id == "thread-1"
    assert fetched.user_id == "user-1"
    assert fetched.status == WorkflowRunStatus.CREATED
    assert fetched.phase == WorkflowPhase.INTAKE
    assert fetched.source_message_id == "msg-1"


@pytest.mark.asyncio
async def test_create_and_read_workflow_step(workflow_store):
    run = await workflow_store.create_run(thread_id="thread-1", user_id="user-1")

    step = await workflow_store.create_step(
        run_id=run.run_id,
        step_name="strategy.llm_synthesize",
        phase=WorkflowPhase.STRATEGY,
        checkpoint={"prompt": "ready"},
    )

    fetched = await workflow_store.get_step(step.step_id)
    assert fetched is not None
    assert fetched.step_name == "strategy.llm_synthesize"
    assert fetched.phase == WorkflowPhase.STRATEGY
    assert fetched.status == WorkflowStepStatus.PENDING
    assert fetched.checkpoint_json == {"prompt": "ready"}


@pytest.mark.asyncio
async def test_create_and_read_child_task(workflow_store):
    run = await workflow_store.create_run(thread_id="thread-1", user_id="user-1")
    step = await workflow_store.create_step(
        run_id=run.run_id,
        step_name="generation.generate_notes_parallel",
        phase=WorkflowPhase.GENERATION,
    )

    child = await workflow_store.create_child_task(
        run_id=run.run_id,
        step_id=step.step_id,
        task_type="note_generation",
        slot_index=2,
        proposal_id="proposal-2",
    )

    fetched = await workflow_store.get_child_task(child.child_task_id)
    assert fetched is not None
    assert fetched.run_id == run.run_id
    assert fetched.step_id == step.step_id
    assert fetched.task_type == "note_generation"
    assert fetched.slot_index == 2
    assert fetched.proposal_id == "proposal-2"
    assert fetched.status == WorkflowStepStatus.PENDING


@pytest.mark.asyncio
async def test_append_and_list_workflow_events(workflow_store):
    run = await workflow_store.create_run(thread_id="thread-1", user_id="user-1")
    first = await workflow_store.append_event(
        run_id=run.run_id,
        thread_id=run.thread_id,
        event_type="workflow_run_created",
        payload={"status": "created"},
    )
    second = await workflow_store.append_event(
        run_id=run.run_id,
        thread_id=run.thread_id,
        event_type="workflow_step_created",
        payload={"step": "intake.capture_request"},
    )

    all_events = await workflow_store.list_events(run.run_id)
    replay_events = await workflow_store.list_events(run.run_id, after_event_id=first.event_id)

    assert [event.event_id for event in all_events] == [first.event_id, second.event_id]
    assert [event.event_id for event in replay_events] == [second.event_id]
    assert first.payload_json == {"status": "created"}


@pytest.mark.asyncio
async def test_create_and_read_artifact(workflow_store):
    run = await workflow_store.create_run(thread_id="thread-1", user_id="user-1")

    artifact = await workflow_store.create_artifact(
        run_id=run.run_id,
        thread_id=run.thread_id,
        artifact_type=WorkflowArtifactType.STRATEGY,
        payload={"positioning": "lightweight"},
        storage_table="strategy_data",
        storage_key="strategy-1",
        summary_text="strategy summary",
    )

    fetched = await workflow_store.get_artifact(artifact.artifact_id)
    assert fetched is not None
    assert fetched.artifact_type == WorkflowArtifactType.STRATEGY
    assert fetched.artifact_version == 1
    assert fetched.payload_json == {"positioning": "lightweight"}
    assert fetched.storage_table == "strategy_data"
    assert fetched.storage_key == "strategy-1"


@pytest.mark.asyncio
async def test_create_and_read_constraint(workflow_store):
    run = await workflow_store.create_run(thread_id="thread-1", user_id="user-1")

    constraint = await workflow_store.create_constraint(
        run_id=run.run_id,
        thread_id=run.thread_id,
        message_id="msg-2",
        raw_text="风格更生活化",
        constraint_type=WorkflowConstraintType.STYLE,
        scope="run",
        normalized={"tone": "lifestyle"},
        confidence=0.87,
    )

    fetched = await workflow_store.get_constraint(constraint.constraint_id)
    assert fetched is not None
    assert fetched.constraint_type == WorkflowConstraintType.STYLE
    assert fetched.constraint_version == 1
    assert fetched.raw_text == "风格更生活化"
    assert fetched.normalized_json == {"tone": "lifestyle"}
    assert fetched.confidence == 0.87
