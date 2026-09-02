from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.memory.workflow_mutations import workflow_mutation_handlers
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowArtifactType, WorkflowConstraintType, WorkflowPhase


def test_workflow_store_mutations_share_runtime_writer(tmp_path: Path) -> None:
    database = tmp_path / "workflow.sqlite"

    async def bootstrap() -> None:
        async with WorkflowStore(str(database)):
            pass

    async def exercise() -> None:
        writer = RuntimeWriteCoordinator(database, handlers=workflow_mutation_handlers())
        await writer.start()
        async with WorkflowStore(str(database), writer=writer) as workflows:
            run = await workflows.create_run(thread_id="thread-owned", user_id="user-owned")
            step = await workflows.create_step(
                run_id=run.run_id,
                step_name="draft",
                phase=WorkflowPhase.GENERATION,
            )
            child = await workflows.create_child_task(
                run_id=run.run_id,
                step_id=step.step_id,
                task_type="generate",
            )
            event = await workflows.append_event(
                run_id=run.run_id,
                thread_id=run.thread_id,
                event_type="owned",
                step_id=step.step_id,
                child_task_id=child.child_task_id,
            )
            artifact = await workflows.create_artifact(
                run_id=run.run_id,
                thread_id=run.thread_id,
                artifact_type=WorkflowArtifactType.GENERATED_NOTE,
                payload={"owned": True},
            )
            constraint = await workflows.create_constraint(
                run_id=run.run_id,
                thread_id=run.thread_id,
                message_id="message-owned",
                raw_text="keep concise",
                constraint_type=WorkflowConstraintType.STYLE,
                scope="run",
            )
            updated = await workflows.update_artifact_status(artifact.artifact_id, "ready")

            assert event.run_id == run.run_id
            assert constraint.run_id == run.run_id
            assert updated is not None
            assert updated.status == "ready"
        await writer.close()

    asyncio.run(bootstrap())
    asyncio.run(exercise())
