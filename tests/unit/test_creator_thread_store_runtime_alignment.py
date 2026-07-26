from app.config import settings
from app.memory.thread_store import ThreadStore


def test_default_creator_thread_store_uses_the_content_research_runtime_database(
    monkeypatch, tmp_path
):
    runtime_db = str(tmp_path / "runtime.db")
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", runtime_db)

    store = ThreadStore()

    assert store.db_path == runtime_db
