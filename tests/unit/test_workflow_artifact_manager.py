"""Unit tests for T2.1 WorkflowRunManager artifact attachment."""

from __future__ import annotations

import pytest

from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowArtifactType, WorkflowPhase
from app.services.workflow_run_manager import WorkflowRunManager


@pytest.fixture
async def manager_with_step(tmp_path):
    db_path = str(tmp_path / "workflow_artifact_manager.db")
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id="thread-1", user_id="user-1")
        steps = await manager.initialize_steps(
            run.run_id,
            [{"step_name": "strategy.persist_strategy", "phase": WorkflowPhase.STRATEGY}],
        )
        yield manager, run, steps[0]


@pytest.mark.asyncio
async def test_attach_artifact_updates_run_version_event_and_snapshot(manager_with_step):
    manager, run, step = manager_with_step

    artifact = await manager.attach_artifact(
        run_id=run.run_id,
        artifact_type=WorkflowArtifactType.STRATEGY,
        payload={"positioning": "light outdoor"},
        summary_text="strategy",
        created_by_step_id=step.step_id,
    )
    snapshot = await manager.get_run_snapshot(run.run_id)

    assert artifact.artifact_type == WorkflowArtifactType.STRATEGY
    assert artifact.artifact_version == 1
    assert artifact.created_by_step_id == step.step_id
    assert snapshot["run"]["artifact_version"] == 1
    assert snapshot["artifacts"][0]["artifact_id"] == artifact.artifact_id

    async with WorkflowStore(manager.db_path) as store:
        events = await store.list_events(run.run_id)

    assert events[-1].event_type == "artifact_attached"
    assert events[-1].step_id == step.step_id
    assert events[-1].payload_json["artifact_id"] == artifact.artifact_id


@pytest.mark.asyncio
async def test_attach_artifact_preserves_explicit_version_for_rewrite(manager_with_step):
    manager, run, step = manager_with_step

    first = await manager.attach_artifact(
        run_id=run.run_id,
        artifact_type=WorkflowArtifactType.GENERATED_NOTE,
        artifact_version=1,
        payload={"title": "old"},
        created_by_step_id=step.step_id,
    )
    second = await manager.attach_artifact(
        run_id=run.run_id,
        artifact_type=WorkflowArtifactType.GENERATED_NOTE,
        artifact_version=2,
        parent_artifact_id=first.artifact_id,
        payload={"title": "new"},
        created_by_step_id=step.step_id,
    )

    async with WorkflowStore(manager.db_path) as store:
        refreshed = await store.get_run(run.run_id)

    assert second.artifact_version == 2
    assert second.parent_artifact_id == first.artifact_id
    assert refreshed is not None
    assert refreshed.artifact_version == 2


@pytest.mark.asyncio
async def test_attach_artifact_event_failure_rolls_back_artifact_and_run_version(
    manager_with_step, monkeypatch
):
    manager, run, step = manager_with_step

    async def fail_append_event(**kwargs):
        raise RuntimeError("event write failed")

    monkeypatch.setattr(manager, "_append_event", fail_append_event)

    with pytest.raises(RuntimeError, match="event write failed"):
        await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.STRATEGY,
            payload={"positioning": "rolled back"},
            created_by_step_id=step.step_id,
        )

    async with WorkflowStore(manager.db_path) as store:
        refreshed = await store.get_run(run.run_id)
        artifacts = await store.list_artifacts(run.run_id)

    assert refreshed is not None
    assert refreshed.artifact_version == 0
    assert artifacts == []
