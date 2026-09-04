from __future__ import annotations

from pathlib import Path

import pytest

from app.content_research.lifecycle.coordinator import ContentResearchPersistenceCoordinator
from app.content_research.lifecycle.models import ContentResearchState, LifecycleCommand
from app.content_research.lifecycle.mutations import content_research_lifecycle_handlers
from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.core.sqlite_connection_roles import SQLiteConnectionOpened, observe_sqlite_connections
from app.memory.thread_store import ThreadStore


@pytest.mark.asyncio
async def test_content_research_lifecycle_commands_share_runtime_writer(tmp_path: Path) -> None:
    database = tmp_path / "content-research.sqlite"
    async with ThreadStore(str(database)) as threads:
        thread = await threads.create_thread(
            title="单写者生命周期",
            workspace_id="workspace-owned",
            brand_id="brand-owned",
        )
    await ContentResearchPersistenceCoordinator(str(database))._ensure_schema()
    opened: list[SQLiteConnectionOpened] = []

    with observe_sqlite_connections(opened.append):
        writer = RuntimeWriteCoordinator(
            database,
            handlers=content_research_lifecycle_handlers(),
        )
        await writer.start()
        coordinator = ContentResearchPersistenceCoordinator(str(database), writer=writer)
        created = await coordinator.apply(
            LifecycleCommand(
                command_id="submit-owned",
                run_id="run-owned",
                expected_state=None,
                expected_revision=0,
                kind="submit_research_subject",
                payload={
                    "thread_id": str(thread["id"]),
                    "user_id": "user-owned",
                    "seed_text": "夏季通勤穿搭",
                },
            )
        )
        completed = await coordinator.apply(
            LifecycleCommand(
                command_id="presearch-owned",
                run_id="run-owned",
                expected_state=ContentResearchState.PRESEARCH_RUNNING,
                expected_revision=1,
                kind="presearch_completed",
                payload={
                    "brief_id": "brief-owned",
                    "schema_version": "content_research_brief_v1",
                    "status": "draft",
                    "subject": "夏季通勤穿搭",
                    "competitors": [],
                    "directions": ["product_marketing"],
                    "attempt_id": "attempt-owned",
                },
            )
        )
        loaded = await coordinator.load("run-owned")
        await writer.close()

    assert created.state is ContentResearchState.PRESEARCH_RUNNING
    assert completed.state is ContentResearchState.BRIEF_CONFIRMATION_REQUIRED
    assert loaded == completed
    assert len([event for event in opened if event.role == "writer"]) == 1
    assert {event.role for event in opened} == {"writer", "reader"}
