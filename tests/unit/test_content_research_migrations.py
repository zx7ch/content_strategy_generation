from __future__ import annotations

import sqlite3
import threading

from app.content_research.bootstrap import bootstrap_content_research_schema


def test_migration_0015_creates_scoped_configuration_table(tmp_path):
    db_path = str(tmp_path / "content_research.db")
    bootstrap_content_research_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(content_research_llm_configurations)")
        }
        versions = {
            row[0]
            for row in conn.execute("SELECT version FROM content_research_schema_migrations")
        }
    assert {
        "workspace_id",
        "user_id",
        "base_url",
        "model",
        "api_key",
        "validation_status",
    } <= columns
    assert "0015" in versions


def test_current_schema_bootstrap_does_not_wait_for_active_writer(tmp_path):
    """A read-path store construction must not reacquire a migration write lock."""
    db_path = tmp_path / "content_research.db"
    bootstrap_content_research_schema(str(db_path))

    writer = sqlite3.connect(db_path)
    writer.execute("BEGIN IMMEDIATE")
    completed = threading.Event()
    failure: list[BaseException] = []

    def bootstrap_again() -> None:
        try:
            bootstrap_content_research_schema(str(db_path))
        except BaseException as exc:  # pragma: no cover - assertion re-raises it below
            failure.append(exc)
        finally:
            completed.set()

    worker = threading.Thread(target=bootstrap_again)
    worker.start()
    try:
        assert completed.wait(timeout=0.5), "current schemas must not wait for a migration write lock"
        assert failure == []
    finally:
        writer.rollback()
        writer.close()
        worker.join(timeout=1)
