"""Unit tests for ScrapeStateRegistry."""

from __future__ import annotations

import pytest

from experiments.xhs_extension_mvp.server.scraper_models import ScrapePhase, ScrapeProgress
from experiments.xhs_extension_mvp.server.scraper_state import ScrapeStateRegistry


class TestScrapeStateRegistry:
    @pytest.mark.asyncio
    async def test_try_acquire_first_call_returns_state(self) -> None:
        registry = ScrapeStateRegistry()
        state = await registry.try_acquire(task_id="t1", keyword="kw1")
        assert state is not None
        assert state.task_id == "t1"
        assert state.keyword == "kw1"
        assert state.progress.phase == ScrapePhase.LAUNCHING
        assert registry.is_busy() is True
        assert registry.active_task_id == "t1"

    @pytest.mark.asyncio
    async def test_try_acquire_second_call_returns_none(self) -> None:
        registry = ScrapeStateRegistry()
        first = await registry.try_acquire(task_id="t1", keyword="kw1")
        second = await registry.try_acquire(task_id="t2", keyword="kw2")
        assert first is not None
        assert second is None
        # Active task remains the first one
        assert registry.active_task_id == "t1"

    @pytest.mark.asyncio
    async def test_try_acquire_propagates_scroll_total(self) -> None:
        registry = ScrapeStateRegistry()
        state = await registry.try_acquire(task_id="t1", keyword="kw1", scroll_total=8)
        assert state is not None
        assert state.progress.scroll_total == 8

    @pytest.mark.asyncio
    async def test_release_clears_active_task(self) -> None:
        registry = ScrapeStateRegistry()
        await registry.try_acquire(task_id="t1", keyword="kw1")
        await registry.release("t1")
        assert registry.is_busy() is False
        assert registry.active_task_id is None
        # State is preserved historically
        state = registry.get("t1")
        assert state is not None
        assert state.finished_at is not None

    @pytest.mark.asyncio
    async def test_release_allows_new_acquire(self) -> None:
        registry = ScrapeStateRegistry()
        await registry.try_acquire(task_id="t1", keyword="kw1")
        await registry.release("t1")
        next_state = await registry.try_acquire(task_id="t2", keyword="kw2")
        assert next_state is not None

    @pytest.mark.asyncio
    async def test_update_writes_progress(self) -> None:
        registry = ScrapeStateRegistry()
        await registry.try_acquire(task_id="t1", keyword="kw1")
        new_progress = ScrapeProgress(
            phase=ScrapePhase.SCROLLING,
            scroll_index=3,
            scroll_total=5,
            items_count=42,
        )
        await registry.update("t1", new_progress)
        state = registry.get("t1")
        assert state is not None
        assert state.progress.phase == ScrapePhase.SCROLLING
        assert state.progress.items_count == 42

    @pytest.mark.asyncio
    async def test_update_unknown_task_is_noop(self) -> None:
        registry = ScrapeStateRegistry()
        # Should not raise even though no acquire happened
        await registry.update("unknown", ScrapeProgress())

    def test_get_returns_none_for_unknown(self) -> None:
        registry = ScrapeStateRegistry()
        assert registry.get("unknown") is None
