from __future__ import annotations

import uuid

import pytest

from app.config import settings
from app.memory.session_state import SessionManager
from app.models.session import SpiderNote


def _note(note_id: str) -> SpiderNote:
    return SpiderNote(
        note_id=note_id,
        title=f"title-{note_id}",
        content=f"content-{note_id}",
        tags=["tag-a"],
    )


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "reindex-compensation.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    return str(db_path)


async def _create_session(db_path: str, session_id: str) -> None:
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "u1", "补偿测试")


@pytest.mark.asyncio
async def test_reindex_failure_marks_pending_but_returns_main_flow_result(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    async def failing_indexer():
        raise RuntimeError("chroma unavailable")

    async with SessionManager(isolated_db) as manager:
        note_ids = await manager.save_spider_results_with_consistency(
            session_id,
            [_note("n1"), _note("n2")],
            rag_indexer=failing_indexer,
        )
        state = await manager.get_reindex_status(session_id)
        session = await manager.get_session(session_id)

    assert note_ids == ["n1", "n2"]
    assert state == {"status": "pending", "attempts": 0}
    assert session is not None
    assert session.spider_note_ids == ["n1", "n2"]


@pytest.mark.asyncio
async def test_reindex_compensation_retry_success_resets_state(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    async def failing_indexer():
        raise RuntimeError("chroma unavailable")

    async with SessionManager(isolated_db) as manager:
        await manager.save_spider_results_with_consistency(
            session_id,
            [_note("n1")],
            rag_indexer=failing_indexer,
        )
        await manager.mark_reindex_result(session_id, success=True)
        state = await manager.get_reindex_status(session_id)

    assert state == {"status": "ok", "attempts": 0}


@pytest.mark.asyncio
async def test_reindex_compensation_reaches_deadletter_after_max_attempts(isolated_db, monkeypatch):
    monkeypatch.setattr(settings, "REINDEX_MAX_ATTEMPTS", 3)
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    async with SessionManager(isolated_db) as manager:
        await manager.mark_reindex_pending(session_id)
        await manager.mark_reindex_result(session_id, success=False)
        await manager.mark_reindex_result(session_id, success=False)
        await manager.mark_reindex_result(session_id, success=False)
        state = await manager.get_reindex_status(session_id)

    assert state == {"status": "deadletter", "attempts": 3}
