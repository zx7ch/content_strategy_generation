from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.content_research.analysis_mutations import content_research_analysis_handlers
from app.content_research.analysis_persistence import (
    FrozenEvidenceNoteInput,
    SQLiteMarketingAnalysisRepository,
)
from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.core.sqlite_connection_roles import SQLiteConnectionOpened, observe_sqlite_connections


@pytest.mark.asyncio
async def test_analysis_identities_share_runtime_writer(tmp_path: Path) -> None:
    database = tmp_path / "analysis.sqlite"
    SQLiteMarketingAnalysisRepository(str(database))
    opened: list[SQLiteConnectionOpened] = []

    with observe_sqlite_connections(opened.append):
        writer = RuntimeWriteCoordinator(database, handlers=content_research_analysis_handlers())
        await writer.start()
        repository = SQLiteMarketingAnalysisRepository(str(database), writer=writer)
        snapshot = repository.freeze_evidence_snapshot(
            workflow_run_id="run-owned",
            scope_contract_id="scope-owned",
            retrieval_execution_unit_id="unit-owned",
            retrieval_attempt_no=1,
            query_groups=[{"id": "query-owned", "query": "通勤穿搭"}],
            notes=[
                FrozenEvidenceNoteInput(
                    note_id="note-owned",
                    account_id="account-owned",
                    title="通勤穿搭",
                    body="正文",
                    source_url="https://example.test/note-owned",
                    captured_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                    query_provenance=("query-owned",),
                )
            ],
        )

        assert repository.get_evidence_snapshot(snapshot.id) == snapshot
        await writer.close()

    assert len([event for event in opened if event.role == "writer"]) == 1
    assert {event.role for event in opened} == {"writer", "reader"}
