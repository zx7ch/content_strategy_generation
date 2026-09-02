from __future__ import annotations

from pathlib import Path

import pytest

from app.content_research.async_pipeline_store import AsyncDirectionalPersistenceSession
from app.content_research.persistence_models import CanonicalSourceRecord
from app.content_research.pipeline_mutations import content_research_pipeline_handlers
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.core.sqlite_connection_roles import SQLiteConnectionOpened, observe_sqlite_connections


@pytest.mark.asyncio
async def test_directional_flush_shares_runtime_writer(tmp_path: Path) -> None:
    database = tmp_path / "directional.sqlite"
    SQLiteContentResearchStore(str(database))
    opened: list[SQLiteConnectionOpened] = []

    with observe_sqlite_connections(opened.append):
        writer = RuntimeWriteCoordinator(database, handlers=content_research_pipeline_handlers())
        await writer.start()
        session = await AsyncDirectionalPersistenceSession.open(str(database), writer=writer)
        source = CanonicalSourceRecord(
            id="source-owned",
            schema_version="content_research_canonical_source_v1",
            payload={"schema_version": "content_research_canonical_source_v1"},
            platform="xiaohongshu",
            platform_source_kind="note",
            platform_source_id="note-owned",
            canonical_url="https://example.test/note-owned",
        )
        assert session.resolve_canonical_source(source) == source
        await session.flush()
        reloaded = await AsyncDirectionalPersistenceSession.open(str(database), writer=writer)
        assert reloaded.get_typed_record(CanonicalSourceRecord, source.id) == source
        await writer.close()

    assert len([event for event in opened if event.role == "writer"]) == 1
    assert {event.role for event in opened} == {"writer", "reader"}
