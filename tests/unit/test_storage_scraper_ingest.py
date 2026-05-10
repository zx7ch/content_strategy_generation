"""Unit tests for MVPStorage.ingest_scraper_items (Phase 2)."""
from __future__ import annotations

import sqlite3

import pytest

from experiments.xhs_extension_mvp.server.models import CaptureItemIn
from experiments.xhs_extension_mvp.server.storage import MVPStorage


def _make_storage(tmp_path) -> MVPStorage:
    storage = MVPStorage(tmp_path / "mvp.db", secret="secret")
    storage.init_db()
    return storage


def _make_task(storage: MVPStorage) -> str:
    task_id, _, _, _ = storage.create_task("敏感肌护肤")
    return task_id


def _make_item(note_id: str, title: str = "Test Note") -> CaptureItemIn:
    return CaptureItemIn(
        source_url=f"https://www.xiaohongshu.com/explore/{note_id}",
        page_type="search_result",
        query_text="敏感肌护肤",
        note_id=note_id,
        title=title,
        author="test_author",
        likes=100,
        comments=10,
        collections=20,
    )


def test_ingest_writes_items_to_capture_table(tmp_path) -> None:
    storage = _make_storage(tmp_path)
    task_id = _make_task(storage)
    items = [_make_item(f"note_{i}") for i in range(5)]

    captured, imported = storage.ingest_scraper_items(
        task_id=task_id, keyword="敏感肌护肤", items=items
    )

    assert captured == 5
    assert imported == 5
    conn = sqlite3.connect(tmp_path / "mvp.db")
    count = conn.execute(
        "SELECT COUNT(*) FROM mvp_capture_items WHERE task_id = ?", (task_id,)
    ).fetchone()[0]
    conn.close()
    assert count == 5


def test_ingest_dedupes_by_note_id(tmp_path) -> None:
    storage = _make_storage(tmp_path)
    task_id = _make_task(storage)
    item = _make_item("same_note")

    captured1, imported1 = storage.ingest_scraper_items(
        task_id=task_id, keyword="敏感肌护肤", items=[item]
    )
    captured2, imported2 = storage.ingest_scraper_items(
        task_id=task_id, keyword="敏感肌护肤", items=[item]
    )

    assert imported1 == 1
    assert imported2 == 0  # duplicate
    conn = sqlite3.connect(tmp_path / "mvp.db")
    count = conn.execute(
        "SELECT COUNT(*) FROM mvp_capture_items WHERE task_id = ?", (task_id,)
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_ingest_increments_snapshot_version(tmp_path) -> None:
    storage = _make_storage(tmp_path)
    task_id = _make_task(storage)

    before = storage.get_task_snapshot_version(task_id)
    assert before is not None
    v_before = before.snapshot_version

    storage.ingest_scraper_items(
        task_id=task_id, keyword="敏感肌护肤", items=[_make_item("note_1")]
    )

    after = storage.get_task_snapshot_version(task_id)
    assert after is not None
    assert after.snapshot_version == v_before + 1


def test_ingest_empty_list_is_noop(tmp_path) -> None:
    storage = _make_storage(tmp_path)
    task_id = _make_task(storage)

    captured, imported = storage.ingest_scraper_items(
        task_id=task_id, keyword="敏感肌护肤", items=[]
    )

    assert captured == 0
    assert imported == 0
    conn = sqlite3.connect(tmp_path / "mvp.db")
    count = conn.execute(
        "SELECT COUNT(*) FROM mvp_capture_items WHERE task_id = ?", (task_id,)
    ).fetchone()[0]
    conn.close()
    assert count == 0


def test_ingest_uses_capture_mode_scraper(tmp_path) -> None:
    storage = _make_storage(tmp_path)
    task_id = _make_task(storage)

    storage.ingest_scraper_items(
        task_id=task_id, keyword="敏感肌护肤", items=[_make_item("note_xyz")]
    )

    conn = sqlite3.connect(tmp_path / "mvp.db")
    row = conn.execute(
        "SELECT capture_mode FROM mvp_captures WHERE task_id = ?", (task_id,)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "scraper"
