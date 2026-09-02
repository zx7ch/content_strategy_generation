from __future__ import annotations

from pathlib import Path

import pytest

from app.content_research.runtime import CheckpointRuntime, LLMCostLedger
from app.content_research.runtime_mutations import content_research_runtime_handlers
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.core.sqlite_connection_roles import SQLiteConnectionOpened, observe_sqlite_connections


@pytest.mark.asyncio
async def test_cost_and_checkpoint_runtime_share_writer(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite"
    SQLiteContentResearchStore(str(database))
    opened: list[SQLiteConnectionOpened] = []

    with observe_sqlite_connections(opened.append):
        writer = RuntimeWriteCoordinator(database, handlers=content_research_runtime_handlers())
        await writer.start()
        ledger = LLMCostLedger(str(database), writer=writer)
        entry = ledger.record_actual(
            research_plan_id="plan-owned",
            usage_event_id="usage-owned",
            amount=0.1,
        )
        checkpoints = CheckpointRuntime(str(database), writer=writer)
        checkpoint = checkpoints.checkpoint(
            subagent_task_id="task-owned",
            stage_name="collect",
            input_fingerprint="sha256:owned",
            status="completed",
        )
        assert entry.status == "committed"
        assert checkpoints.is_completed(
            subagent_task_id="task-owned",
            stage_name="collect",
            input_fingerprint="sha256:owned",
        )
        assert checkpoint.status == "completed"
        await writer.close()

    assert len([event for event in opened if event.role == "writer"]) == 1
    assert {event.role for event in opened} == {"writer", "reader"}
