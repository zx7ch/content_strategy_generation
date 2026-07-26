from __future__ import annotations

import sqlite3
import threading

from app.content_research.bootstrap import bootstrap_content_research_schema


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
