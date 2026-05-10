"""Unit tests for Phase 2 scraper endpoints: readiness, auto-scrape, scrape-status."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from experiments.xhs_extension_mvp.server.app import create_app
from experiments.xhs_extension_mvp.server.models import CaptureItemIn
from experiments.xhs_extension_mvp.server.scraper_models import ScrapePhase, ScrapeProgress


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(note_id: str) -> CaptureItemIn:
    return CaptureItemIn(
        source_url=f"https://www.xiaohongshu.com/explore/{note_id}",
        page_type="search_result",
        query_text="敏感肌护肤",
        note_id=note_id,
        title=f"Note {note_id}",
        author="test_author",
        likes=100,
    )


def _make_app(tmp_path: Path, profile_dir: Optional[Path] = None):
    return create_app(
        database_path=tmp_path / "mvp.db",
        secret="secret",
        profile_dir=profile_dir or (tmp_path / "nonexistent-profile"),
    )


def _create_task(client: TestClient) -> str:
    resp = client.post("/mvp/tasks", json={"topic": "敏感肌护肤"})
    assert resp.status_code == 200
    return resp.json()["task_id"]


# ---------------------------------------------------------------------------
# Readiness endpoint
# ---------------------------------------------------------------------------


def test_readiness_returns_profile_missing(tmp_path) -> None:
    """Profile dir does not exist → profile_exists=False, logged_in=False."""
    app = _make_app(tmp_path, profile_dir=tmp_path / "no-such-dir")
    with TestClient(app) as client:
        resp = client.get("/api/scraper/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_exists"] is False
    assert data["logged_in"] is False


def test_readiness_caches_result_within_ttl(tmp_path, monkeypatch) -> None:
    """Two consecutive calls within TTL return the cached value (no second probe)."""
    app = _make_app(tmp_path, profile_dir=tmp_path / "no-such-dir")
    with TestClient(app) as client:
        resp1 = client.get("/api/scraper/readiness")
        resp2 = client.get("/api/scraper/readiness")
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    # Both calls return the same last_checked_at (cache hit on second)
    assert resp1.json()["last_checked_at"] == resp2.json()["last_checked_at"]


# ---------------------------------------------------------------------------
# auto-scrape endpoint
# ---------------------------------------------------------------------------


def test_auto_scrape_returns_202_on_first_call(tmp_path, monkeypatch) -> None:
    """First trigger returns 202 with accepted=True."""
    async def fake_scrape(keyword, *, runtime, scroll_count, on_progress=None):
        if on_progress:
            await on_progress(ScrapeProgress(phase=ScrapePhase.DONE, scroll_total=scroll_count))
        return []

    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.app.scrape_search_feed", fake_scrape
    )

    app = _make_app(tmp_path)
    with TestClient(app) as client:
        task_id = _create_task(client)
        resp = client.post(
            f"/api/tasks/{task_id}/auto-scrape",
            json={"keyword": "敏感肌护肤", "scroll_count": 2},
        )

    assert resp.status_code == 202
    data = resp.json()
    assert data["accepted"] is True
    assert data["task_id"] == task_id


def test_auto_scrape_returns_404_for_unknown_task(tmp_path, monkeypatch) -> None:
    async def fake_scrape(*_, **__):
        return []

    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.app.scrape_search_feed", fake_scrape
    )

    app = _make_app(tmp_path)
    with TestClient(app) as client:
        resp = client.post(
            "/api/tasks/nonexistent-id/auto-scrape",
            json={"keyword": "敏感肌护肤", "scroll_count": 2},
        )
    assert resp.status_code == 404


def test_auto_scrape_returns_409_when_busy(tmp_path, monkeypatch) -> None:
    """Second trigger while first is running returns 409."""
    trigger_event = asyncio.Event()
    release_event = asyncio.Event()

    async def blocking_scrape(keyword, *, runtime, scroll_count, on_progress=None):
        trigger_event.set()
        await release_event.wait()
        return []

    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.app.scrape_search_feed", blocking_scrape
    )

    app = _make_app(tmp_path)
    with TestClient(app) as client:
        task_id = _create_task(client)

        # Acquire the registry lock manually to simulate a running scrape
        registry = app.state.scrape_state_registry
        asyncio.run(
            registry.try_acquire(task_id=task_id, keyword="敏感肌护肤", scroll_total=5)
        )

        # Second call should see registry busy → 409
        resp = client.post(
            f"/api/tasks/{task_id}/auto-scrape",
            json={"keyword": "敏感肌护肤", "scroll_count": 2},
        )
        assert resp.status_code == 409

        # Release lock so shutdown doesn't hang
        asyncio.run(registry.release(task_id))


def test_auto_scrape_completes_and_ingests_items(tmp_path, monkeypatch) -> None:
    """Mock scraper returns 5 items; storage gains 5 rows after background task."""
    fake_items = [_make_item(f"n{i}") for i in range(5)]

    async def fake_scrape(keyword, *, runtime, scroll_count, on_progress=None):
        if on_progress:
            await on_progress(
                ScrapeProgress(
                    phase=ScrapePhase.DONE,
                    scroll_total=scroll_count,
                    items_count=len(fake_items),
                )
            )
        return fake_items

    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.app.scrape_search_feed", fake_scrape
    )

    app = _make_app(tmp_path)
    with TestClient(app) as client:
        task_id = _create_task(client)
        resp = client.post(
            f"/api/tasks/{task_id}/auto-scrape",
            json={"keyword": "敏感肌护肤", "scroll_count": 2},
        )
        assert resp.status_code == 202
        # Background task completes before TestClient exits context manager

    # Verify storage has 5 new items
    from experiments.xhs_extension_mvp.server.storage import MVPStorage
    storage = MVPStorage(tmp_path / "mvp.db", secret="secret")
    snapshot = storage.get_task_snapshot(task_id)
    assert snapshot is not None
    assert snapshot.imported_item_count == 5


def test_auto_scrape_handles_login_required(tmp_path, monkeypatch) -> None:
    """Scraper returns [] (login required path) — no items written, no crash."""
    async def fake_scrape(keyword, *, runtime, scroll_count, on_progress=None):
        if on_progress:
            await on_progress(
                ScrapeProgress(
                    phase=ScrapePhase.LOGIN_REQUIRED,
                    scroll_total=scroll_count,
                    error_message="未登录",
                )
            )
        return []

    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.app.scrape_search_feed", fake_scrape
    )

    app = _make_app(tmp_path)
    with TestClient(app) as client:
        task_id = _create_task(client)
        resp = client.post(
            f"/api/tasks/{task_id}/auto-scrape",
            json={"keyword": "敏感肌护肤", "scroll_count": 2},
        )
        assert resp.status_code == 202

    from experiments.xhs_extension_mvp.server.storage import MVPStorage
    storage = MVPStorage(tmp_path / "mvp.db", secret="secret")
    snapshot = storage.get_task_snapshot(task_id)
    assert snapshot is not None
    assert snapshot.imported_item_count == 0


def test_auto_scrape_handles_scrape_exception(tmp_path, monkeypatch) -> None:
    """If scraper raises, registry enters ERROR phase and releases the lock."""
    async def crashing_scrape(keyword, *, runtime, scroll_count, on_progress=None):
        raise RuntimeError("playwright crashed")

    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.app.scrape_search_feed", crashing_scrape
    )

    app = _make_app(tmp_path)
    with TestClient(app) as client:
        task_id = _create_task(client)
        resp = client.post(
            f"/api/tasks/{task_id}/auto-scrape",
            json={"keyword": "敏感肌护肤", "scroll_count": 2},
        )
        assert resp.status_code == 202
        # Background task completes (with error) before TestClient exits

    registry = app.state.scrape_state_registry
    assert registry.is_busy() is False
    state = registry.get(task_id)
    assert state is not None
    assert state.progress.phase == ScrapePhase.ERROR
    assert "playwright crashed" in state.progress.error_message


# ---------------------------------------------------------------------------
# scrape-status endpoint
# ---------------------------------------------------------------------------


def test_scrape_status_returns_404_for_unknown_task(tmp_path) -> None:
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/tasks/nonexistent-id/scrape-status")
    assert resp.status_code == 404


def test_scrape_status_returns_progress_after_trigger(tmp_path, monkeypatch) -> None:
    """After trigger completes, status endpoint returns task's final state."""
    async def fake_scrape(keyword, *, runtime, scroll_count, on_progress=None):
        if on_progress:
            await on_progress(
                ScrapeProgress(
                    phase=ScrapePhase.DONE,
                    scroll_total=scroll_count,
                    items_count=3,
                )
            )
        return [_make_item(f"n{i}") for i in range(3)]

    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.app.scrape_search_feed", fake_scrape
    )

    app = _make_app(tmp_path)
    with TestClient(app) as client:
        task_id = _create_task(client)
        client.post(
            f"/api/tasks/{task_id}/auto-scrape",
            json={"keyword": "敏感肌护肤", "scroll_count": 2},
        )
        # Background done at context exit; query status
        status_resp = client.get(f"/api/tasks/{task_id}/scrape-status")

    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["task_id"] == task_id
    assert data["phase"] in ("done", "launching", "scrolling", "ingesting")
    assert data["finished_at"] is not None
