from __future__ import annotations

from pathlib import Path

import pytest

from app.content_research.async_dispatch import AsyncFormalResearchDispatchRepository
from app.content_research.dispatch_mutations import content_research_dispatch_handlers
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.core.sqlite_connection_roles import SQLiteConnectionOpened, observe_sqlite_connections


@pytest.mark.asyncio
async def test_dispatch_queue_and_lease_share_runtime_writer(tmp_path: Path) -> None:
    database = tmp_path / "dispatch.sqlite"
    SQLiteContentResearchStore(str(database))
    opened: list[SQLiteConnectionOpened] = []

    with observe_sqlite_connections(opened.append):
        writer = RuntimeWriteCoordinator(database, handlers=content_research_dispatch_handlers())
        await writer.start()
        repository = AsyncFormalResearchDispatchRepository(str(database), writer=writer)
        assert await repository.claim_next(owner="worker-owned") is None
        await repository.enqueue(
            workflow_run_id="run-owned",
            provider="xiaohongshu",
            source_kind="search_result",
            limit=10,
        )
        claim = await repository.claim_next(owner="worker-owned")
        assert claim is not None
        assert claim["status"] == "running"
        assert await repository.complete(
            workflow_run_id="run-owned",
            owner="worker-owned",
            token=claim["lease_token"],
        )
        await writer.close()

    assert len([event for event in opened if event.role == "writer"]) == 1
    assert {event.role for event in opened} == {"writer", "reader"}
