"""Session state tests for P1-2 refactor (lightweight checkpoint + data store)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.checkpoint.base import empty_checkpoint

from app.memory.session_state import SessionManager
from app.memory.job_store import JobStore
from app.models.session import (
    ContentStrategy,
    PlatformPreference,
    RetryStats,
    SessionLifecycleState,
    SessionStage,
    SpiderNote,
)


def _sample_spider_note(note_id: str) -> SpiderNote:
    return SpiderNote(
        note_id=note_id,
        title=f"title-{note_id}",
        content=f"content-{note_id}",
        tags=["tag1", "tag2"],
    )


def _sample_strategy() -> ContentStrategy:
    return ContentStrategy(
        positioning="定位",
        target_audience="受众",
        content_pillars=["A", "B"],
        key_messaging="关键信息",
        content_types=["图文"],
        posting_strategy="每周3更",
        data_source_quality=0.72,
    )


def _sample_preference() -> PlatformPreference:
    return PlatformPreference(
        avg_title_length=18,
        popular_tags=["护肤", "平价"],
        optimal_posting_times=["20:00"],
        content_patterns=["清单式"],
    )


@pytest.mark.asyncio
async def test_create_and_get_session_crud():
    async with SessionManager(":memory:") as manager:
        sid = str(uuid.uuid4())
        created = await manager.create_session(sid, "u1", "query")

        assert created.session_id == sid
        assert created.stage == SessionStage.INIT
        assert created.lifecycle_state == SessionLifecycleState.ALIVE

        got = await manager.get_session(sid)
        assert got is not None
        assert got.user_id == "u1"


@pytest.mark.asyncio
async def test_upsert_create_is_idempotent():
    async with SessionManager(":memory:") as manager:
        sid = str(uuid.uuid4())
        await manager.create_session(sid, "u1", "query-1")
        await manager.create_session(sid, "u1", "query-2")

        got = await manager.get_session(sid)
        assert got is not None
        assert got.user_query == "query-2"

        async with manager._conn.execute("SELECT COUNT(*) AS c FROM sessions WHERE session_id = ?", (sid,)) as cursor:
            row = await cursor.fetchone()
            assert row["c"] == 1


@pytest.mark.asyncio
async def test_session_data_store_roundtrip_references():
    async with SessionManager(":memory:") as manager:
        sid = str(uuid.uuid4())
        await manager.create_session(sid, "u", "q")

        notes = [_sample_spider_note("n1"), _sample_spider_note("n2")]
        updated = await manager.update_session(
            sid,
            spider_notes=notes,
            content_strategy=_sample_strategy(),
            platform_preference=_sample_preference(),
            quality_score=0.88,
            stage=SessionStage.STRATEGY,
        )

        assert updated is not None
        assert updated.spider_note_ids == ["n1", "n2"]
        assert updated.strategy_id is not None
        assert updated.content_strategy is not None
        assert updated.platform_preference is not None
        assert updated.quality_score == pytest.approx(0.88)


@pytest.mark.asyncio
async def test_json_fields_and_retry_stats_serialization():
    async with SessionManager(":memory:") as manager:
        sid = str(uuid.uuid4())
        await manager.create_session(sid, "u", "q")

        stats = RetryStats(query_expansion_count=1, spider_attempt_count=2, generation_retry_count=3)
        await manager.update_session(
            sid,
            retry_stats=stats,
            similarity_report={"max_similarity": 0.31, "status": "ok"},
            expanded_queries=["q1", "q2"],
        )

        got = await manager.get_session(sid)
        assert got is not None
        assert got.retry_stats.spider_attempt_count == 2
        assert got.similarity_report["status"] == "ok"
        assert got.expanded_queries == ["q1", "q2"]


@pytest.mark.asyncio
async def test_concurrent_updates_keep_single_row():
    async with SessionManager(":memory:") as manager:
        sid = str(uuid.uuid4())
        await manager.create_session(sid, "u", "q")

        async def worker(i: int):
            note = _sample_spider_note(f"n{i}")
            await manager.update_session(sid, spider_notes=[note])

        await asyncio.gather(*(worker(i) for i in range(8)))

        async with manager._conn.execute("SELECT COUNT(*) AS c FROM sessions WHERE session_id = ?", (sid,)) as cursor:
            row = await cursor.fetchone()
            assert row["c"] == 1


@pytest.mark.asyncio
async def test_lifecycle_transition_alive_frozen_purged():
    async with SessionManager(":memory:") as manager:
        sid = str(uuid.uuid4())
        await manager.create_session(sid, "u", "q")

        frozen_ts = (datetime.utcnow() - timedelta(hours=25)).isoformat()
        await manager._conn.execute(
            "UPDATE sessions SET last_user_activity_at = ?, last_activity_at = ? WHERE session_id = ?",
            (frozen_ts, frozen_ts, sid),
        )
        await manager._conn.commit()

        await manager.refresh_lifecycle_state(sid)
        frozen = await manager.get_session(sid)
        assert frozen is not None
        assert frozen.lifecycle_state == SessionLifecycleState.FROZEN

        purged_ts = (datetime.utcnow() - timedelta(days=11)).isoformat()
        await manager._conn.execute(
            "UPDATE sessions SET last_user_activity_at = ?, last_activity_at = ? WHERE session_id = ?",
            (purged_ts, purged_ts, sid),
        )
        await manager._conn.commit()

        await manager.refresh_lifecycle_state(sid)
        purged = await manager.get_session(sid)
        assert purged is not None
        assert purged.lifecycle_state == SessionLifecycleState.PURGED


@pytest.mark.asyncio
async def test_manual_lifecycle_override_is_recomputed_from_last_user_activity():
    async with SessionManager(":memory:") as manager:
        sid = str(uuid.uuid4())
        await manager.create_session(sid, "u", "q")

        stale_ts = (datetime.utcnow() - timedelta(hours=25)).isoformat()
        await manager.update_session(
            sid,
            lifecycle_state=SessionLifecycleState.ALIVE.value,
            last_user_activity_at=stale_ts,
        )

        await manager.refresh_lifecycle_state(sid)
        session = await manager.get_session(sid)
        assert session is not None
        assert session.lifecycle_state == SessionLifecycleState.FROZEN


@pytest.mark.asyncio
async def test_update_activity_sets_alive_and_clears_pause_requested():
    async with SessionManager(":memory:") as manager:
        sid = str(uuid.uuid4())
        await manager.create_session(sid, "u", "q")
        await manager.update_session(
            sid,
            lifecycle_state=SessionLifecycleState.FROZEN.value,
            pause_requested=True,
            pause_requested_at=datetime.utcnow().isoformat(),
            frozen_at=datetime.utcnow().isoformat(),
        )

        updated = await manager.update_activity(sid)
        assert updated is True
        session = await manager.get_session(sid)
        assert session is not None
        assert session.lifecycle_state == SessionLifecycleState.ALIVE
        assert session.pause_requested is False
        assert session.pause_requested_at is None


@pytest.mark.asyncio
async def test_pause_requested_transitions_to_frozen_without_active_jobs():
    async with SessionManager(":memory:") as manager:
        sid = str(uuid.uuid4())
        await manager.create_session(sid, "u", "q")
        await manager.update_session(
            sid,
            pause_requested=True,
            pause_requested_at=datetime.utcnow().isoformat(),
        )

        await manager.refresh_lifecycle_state(sid)
        session = await manager.get_session(sid)
        assert session is not None
        assert session.lifecycle_state == SessionLifecycleState.FROZEN


@pytest.mark.asyncio
async def test_active_jobs_keep_session_alive_even_if_user_inactive(tmp_path):
    db_path = str(tmp_path / "session-active-jobs.db")
    sid = str(uuid.uuid4())

    async with SessionManager(db_path) as manager:
        await manager.create_session(sid, "u", "q")

    async with JobStore(db_path) as store:
        await store.enqueue(session_id=sid, job_type="strategy")

    async with SessionManager(db_path) as manager:
        stale_ts = (datetime.utcnow() - timedelta(days=11)).isoformat()
        await manager._conn.execute(
            "UPDATE sessions SET last_user_activity_at = ?, last_activity_at = ? WHERE session_id = ?",
            (stale_ts, stale_ts, sid),
        )
        await manager._conn.commit()

        await manager.refresh_lifecycle_state(sid)
        session = await manager.get_session(sid)
        assert session is not None
        assert session.lifecycle_state == SessionLifecycleState.ALIVE


@pytest.mark.asyncio
async def test_paused_jobs_do_not_keep_session_alive(tmp_path):
    db_path = str(tmp_path / "session-paused-jobs.db")
    sid = str(uuid.uuid4())

    async with SessionManager(db_path) as manager:
        await manager.create_session(sid, "u", "q")

    async with JobStore(db_path) as store:
        await store.enqueue(session_id=sid, job_type="strategy")
        await store.pause_session_jobs(sid)

    async with SessionManager(db_path) as manager:
        stale_ts = (datetime.utcnow() - timedelta(days=11)).isoformat()
        await manager._conn.execute(
            "UPDATE sessions SET last_user_activity_at = ?, last_activity_at = ? WHERE session_id = ?",
            (stale_ts, stale_ts, sid),
        )
        await manager._conn.commit()

        await manager.refresh_lifecycle_state(sid)
        session = await manager.get_session(sid)
        assert session is not None
        assert session.lifecycle_state == SessionLifecycleState.PURGED


@pytest.mark.asyncio
async def test_purged_transition_cancels_unfinished_jobs(tmp_path):
    db_path = str(tmp_path / "session-purged-cancels.db")
    sid = str(uuid.uuid4())

    async with SessionManager(db_path) as manager:
        await manager.create_session(sid, "u", "q")

    async with JobStore(db_path) as store:
        queued_job, _ = await store.enqueue(session_id=sid, job_type="strategy")
        paused_job, _ = await store.enqueue(session_id=sid, job_type="generate")
        await store.pause_session_jobs(sid)

    async with SessionManager(db_path) as manager:
        stale_ts = (datetime.utcnow() - timedelta(days=11)).isoformat()
        await manager._conn.execute(
            "UPDATE sessions SET last_user_activity_at = ?, last_activity_at = ? WHERE session_id = ?",
            (stale_ts, stale_ts, sid),
        )
        await manager._conn.commit()

        with patch.object(manager, "_count_active_jobs", AsyncMock(return_value=0)):
            await manager.refresh_lifecycle_state(sid)
        session = await manager.get_session(sid)
        assert session is not None
        assert session.lifecycle_state == SessionLifecycleState.PURGED

    async with JobStore(db_path) as store:
        queued_ref = await store.get_job(queued_job.id)
        paused_ref = await store.get_job(paused_job.id)
        assert queued_ref is not None and queued_ref.status == "cancelled"
        assert queued_ref.cancel_reason == "session_purged"
        assert paused_ref is not None and paused_ref.status == "cancelled"
        assert paused_ref.cancel_reason == "session_purged"


@pytest.mark.asyncio
async def test_lifecycle_transitions_emit_frozen_and_purged_logs(tmp_path):
    db_path = str(tmp_path / "session-lifecycle-logs.db")
    sid = str(uuid.uuid4())

    async with SessionManager(db_path) as manager:
        await manager.create_session(sid, "u", "q")

        with patch("app.memory.session_state.log_event") as mocked_log_event:
            frozen_ts = (datetime.utcnow() - timedelta(hours=25)).isoformat()
            await manager._conn.execute(
                "UPDATE sessions SET last_user_activity_at = ?, last_activity_at = ? WHERE session_id = ?",
                (frozen_ts, frozen_ts, sid),
            )
            await manager._conn.commit()
            await manager.refresh_lifecycle_state(sid)

            purged_ts = (datetime.utcnow() - timedelta(days=11)).isoformat()
            await manager._conn.execute(
                "UPDATE sessions SET last_user_activity_at = ?, last_activity_at = ? WHERE session_id = ?",
                (purged_ts, purged_ts, sid),
            )
            await manager._conn.commit()
            await manager.refresh_lifecycle_state(sid)

        event_names = [call.kwargs["event_name"] for call in mocked_log_event.call_args_list]
        assert "session_frozen" in event_names
        assert "session_purged" in event_names


@pytest.mark.asyncio
async def test_checkpointer_available_after_connect():
    async with SessionManager(":memory:") as manager:
        checkpointer = manager.get_checkpointer()
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        assert isinstance(checkpointer, AsyncSqliteSaver)


@pytest.mark.asyncio
async def test_reindex_state_machine_pending_to_deadletter():
    async with SessionManager(":memory:") as manager:
        sid = str(uuid.uuid4())
        await manager.create_session(sid, "u", "q")

        await manager.mark_reindex_pending(sid)
        state = await manager.get_reindex_status(sid)
        assert state["status"] == "pending"

        await manager.mark_reindex_result(sid, success=False)
        await manager.mark_reindex_result(sid, success=False)
        await manager.mark_reindex_result(sid, success=False)

        state = await manager.get_reindex_status(sid)
        assert state["status"] == "deadletter"

        await manager.mark_reindex_result(sid, success=True)
        state = await manager.get_reindex_status(sid)
        assert state["status"] == "ok"
        assert state["attempts"] == 0


@pytest.mark.asyncio
async def test_reindex_paths_emit_required_logs():
    async with SessionManager(":memory:") as manager:
        sid = str(uuid.uuid4())
        await manager.create_session(sid, "u", "q")

        with patch("app.memory.session_state.log_event") as mocked_log_event:
            await manager.mark_reindex_pending(sid)
            await manager.mark_reindex_result(sid, success=False)
            await manager.mark_reindex_result(sid, success=False)
            await manager.mark_reindex_result(sid, success=False)
            await manager.mark_reindex_result(sid, success=True)

            async def successful_indexer():
                return None

            await manager.save_spider_results_with_consistency(
                sid,
                [_sample_spider_note("n1")],
                rag_indexer=successful_indexer,
            )

        event_names = [call.kwargs["event_name"] for call in mocked_log_event.call_args_list]
        assert "reindex_scheduled" in event_names
        assert "reindex_deadlettered" in event_names
        assert "reindex_succeeded" in event_names
        assert "reindex_started" in event_names


@pytest.mark.asyncio
async def test_legacy_rag_status_wrappers_map_to_reindex_fields():
    async with SessionManager(":memory:") as manager:
        sid = str(uuid.uuid4())
        await manager.create_session(sid, "u", "q")

        await manager.mark_rag_needs_rebuild(sid)
        state = await manager.get_rag_sync_status(sid)
        assert state == {"status": "pending", "attempts": 0}

        await manager.mark_rag_reindex_result(sid, success=False)
        state = await manager.get_rag_sync_status(sid)
        assert state == {"status": "pending", "attempts": 1}


@pytest.mark.asyncio
async def test_list_and_delete_sessions_crud_complete():
    async with SessionManager(":memory:") as manager:
        sid1 = str(uuid.uuid4())
        sid2 = str(uuid.uuid4())
        await manager.create_session(sid1, "u", "q1")
        await manager.create_session(sid2, "u", "q2")

        sessions = await manager.list_user_sessions("u", limit=10)
        ids = {s.session_id for s in sessions}
        assert {sid1, sid2}.issubset(ids)

        deleted = await manager.delete_session(sid1)
        assert deleted is True
        assert await manager.get_session(sid1) is None


@pytest.mark.asyncio
async def test_wal_mode_enabled_for_file_db(tmp_path):
    db_path = tmp_path / "session.db"
    async with SessionManager(str(db_path)) as manager:
        async with manager._conn.execute("PRAGMA journal_mode") as cursor:
            row = await cursor.fetchone()
            assert row[0].lower() == "wal"


@pytest.mark.asyncio
async def test_checkpointer_persist_and_restore_checkpoint():
    async with SessionManager(":memory:") as manager:
        cp = manager.get_checkpointer()
        cfg = {"configurable": {"thread_id": "sess-1", "checkpoint_ns": ""}}
        checkpoint = empty_checkpoint()
        new_cfg = await cp.aput(cfg, checkpoint, {"source": "unit-test", "step": 1}, {})
        loaded = await cp.aget_tuple(new_cfg)
        assert loaded is not None
        assert loaded.checkpoint["id"] == checkpoint["id"]


@pytest.mark.asyncio
async def test_checkpoint_state_is_reference_only_payload_stored_externally():
    async with SessionManager(":memory:") as manager:
        sid = str(uuid.uuid4())
        await manager.create_session(sid, "u", "q")
        note = _sample_spider_note("n1")
        await manager.save_spider_results_with_consistency(sid, [note])

        async with manager._conn.execute("SELECT spider_note_ids FROM sessions WHERE session_id = ?", (sid,)) as cursor:
            row = await cursor.fetchone()
            assert row is not None
            assert "n1" in row["spider_note_ids"]

        async with manager._conn.execute("SELECT COUNT(*) AS c FROM spider_data WHERE session_id = ?", (sid,)) as cursor:
            row = await cursor.fetchone()
            assert row["c"] == 1


@pytest.mark.asyncio
async def test_dual_storage_consistency_marks_pending_when_rag_fails():
    async with SessionManager(":memory:") as manager:
        sid = str(uuid.uuid4())
        await manager.create_session(sid, "u", "q")

        async def failing_indexer():
            raise RuntimeError("chroma down")

        note_ids = await manager.save_spider_results_with_consistency(
            sid,
            [_sample_spider_note("n1")],
            rag_indexer=failing_indexer,
        )
        assert note_ids == ["n1"]

        async with manager._conn.execute("SELECT COUNT(*) AS c FROM spider_data WHERE session_id = ?", (sid,)) as cursor:
            row = await cursor.fetchone()
            assert row["c"] == 1

        state = await manager.get_rag_sync_status(sid)
        assert state is not None
        assert state["status"] == "pending"
