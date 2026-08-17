from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from app.content_research.models import ResearchBriefRecord
from app.content_research.scope_contract import (
    ScopeConstraint,
    ScopeDraftAuditEvent,
    ScopeQueryGroupInput,
    build_scope_draft,
)
from app.content_research.stores.sqlite_store import (
    RetryableLocalPersistenceError,
    SQLiteContentResearchStore,
)


@contextmanager
def hold_immediate_transaction(db_path: str) -> Iterator[None]:
    conn = sqlite3.connect(db_path, timeout=0)
    conn.execute("PRAGMA busy_timeout=0")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    finally:
        conn.rollback()
        conn.close()


def _connect_without_wait(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=0")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _seed_scope_draft(store: SQLiteContentResearchStore):
    draft = build_scope_draft(
        workflow_run_id="run_sqlite_write_lock",
        research_plan_id="plan_sqlite_write_lock",
        structure_hash="structure_hash_sqlite_write_lock",
        constraints=(
            ScopeConstraint("core_object", "核心对象", "长袖衬衫", "required"),
        ),
        query_groups=(
            ScopeQueryGroupInput("夏季 长袖衬衫 通勤", "夏季 长袖衬衫 通勤"),
        ),
    )
    store.save_scope_draft_with_audit_event(
        draft,
        ScopeDraftAuditEvent(
            id="scope_draft_audit_sqlite_write_lock",
            workflow_run_id=draft.workflow_run_id,
            scope_draft_id=draft.id,
            event_name="scope_suggested",
            payload={"schema_version": "content_research_scope_audit_event_v1"},
        ),
    )
    store.save_brief(
        ResearchBriefRecord(
            id="brief_sqlite_write_lock",
            workflow_run_id=draft.workflow_run_id,
            thread_id="thread_sqlite_write_lock",
            schema_version="content_research_brief_v1",
            status="ready",
            payload={
                "schema_version": "content_research_brief_v1",
                "subject_structure_hash": draft.structure_hash,
            },
        )
    )
    return draft


def test_scope_confirmation_classifies_an_exhausted_sqlite_write_lock(
    tmp_path, monkeypatch
) -> None:
    db_path = str(tmp_path / "sqlite-write-lock.db")
    store = SQLiteContentResearchStore(db_path)
    draft = _seed_scope_draft(store)
    monkeypatch.setattr(store, "_connect", lambda: _connect_without_wait(db_path))

    with hold_immediate_transaction(db_path):
        with pytest.raises(RetryableLocalPersistenceError, match="sqlite_write_locked"):
            store.confirm_scope_atomically(
                draft.id,
                final_queries=("夏季 长袖衬衫 通勤",),
                event_id="scope_audit_sqlite_write_lock",
            )

    assert store.list_scope_contracts(draft.workflow_run_id) == []
