from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.core.sqlite_connection_roles import (
    SQLiteConnectionOpened,
    observe_sqlite_connections,
)
from app.services.workflow_run_manager import WorkflowRunManager, WorkflowTransitionError
from app.services.workflow_run_mutations import workflow_run_mutation_handlers


def test_workflow_commands_preserve_atomic_state_machine_on_runtime_writer(
    tmp_path: Path,
) -> None:
    database = tmp_path / "workflow-manager.sqlite"

    async def bootstrap() -> None:
        async with WorkflowRunManager(str(database)):
            pass

    async def exercise() -> None:
        writer = RuntimeWriteCoordinator(
            database,
            handlers=workflow_run_mutation_handlers(),
        )
        await writer.start()
        async with WorkflowRunManager(str(database), writer=writer) as manager:
            run = await manager.start_run(
                thread_id="thread-owned",
                user_id="user-owned",
                activate_thread=False,
            )
            steps = await manager.initialize_steps(
                run.run_id,
                [{"step_name": "draft", "phase": "generation", "max_attempts": 1}],
            )
            started = await manager.start_step(run.run_id, "draft")
            completed = await manager.complete_step(run.run_id, "draft")
            snapshot = await manager.get_run_snapshot(run.run_id)
            events = await manager.list_events(run.run_id)

            assert steps[0].status.value == "pending"
            assert started.status.value == "running"
            assert completed.status.value == "succeeded"
            assert snapshot["steps"][0]["status"] == "succeeded"
            assert [event.event_type for event in events] == [
                "run_started",
                "steps_initialized",
                "step_started",
                "step_completed",
            ]
        await writer.close()

    asyncio.run(bootstrap())
    opened: list[SQLiteConnectionOpened] = []
    with observe_sqlite_connections(opened.append):
        asyncio.run(exercise())
    assert [event.role for event in opened].count("writer") == 1
    assert {event.role for event in opened} == {"reader", "writer"}


def test_rejected_workflow_command_does_not_kill_runtime_writer(tmp_path: Path) -> None:
    database = tmp_path / "workflow-rejection.sqlite"

    async def bootstrap() -> None:
        async with WorkflowRunManager(str(database)):
            pass

    async def exercise() -> None:
        writer = RuntimeWriteCoordinator(database, handlers=workflow_run_mutation_handlers())
        await writer.start()
        async with WorkflowRunManager(str(database), writer=writer) as manager:
            run = await manager.start_run(
                thread_id="thread-rejection",
                user_id="user-rejection",
                activate_thread=False,
            )
            await manager.initialize_steps(
                run.run_id,
                [{"step_name": "draft", "phase": "generation", "max_attempts": 1}],
            )
            try:
                await manager.complete_step(run.run_id, "draft")
            except WorkflowTransitionError:
                pass
            else:
                raise AssertionError("invalid transition must be rejected")

            started = await manager.start_step(run.run_id, "draft")
            assert started.status.value == "running"
            assert writer.availability == "accepting"
        await writer.close()

    asyncio.run(bootstrap())
    asyncio.run(exercise())
