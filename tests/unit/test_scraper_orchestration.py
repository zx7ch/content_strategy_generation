"""Unit tests for scrape_search_feed orchestration."""

from __future__ import annotations

from typing import Iterable

import pytest

from experiments.xhs_extension_mvp.server import scraper as scraper_module
from experiments.xhs_extension_mvp.server.models import CaptureItemIn
from experiments.xhs_extension_mvp.server.scraper import scrape_search_feed
from experiments.xhs_extension_mvp.server.scraper_models import ScrapePhase, ScrapeProgress


# --- Fakes -----------------------------------------------------------------


class FakePage:
    def __init__(self, *, goto_raises: Exception | None = None) -> None:
        self.closed = False
        self.goto_calls: list[tuple[str, dict]] = []
        self._goto_raises = goto_raises

    async def goto(self, url: str, *, wait_until: str = "load", timeout: int = 0):
        self.goto_calls.append((url, {"wait_until": wait_until, "timeout": timeout}))
        if self._goto_raises is not None:
            raise self._goto_raises

    async def close(self):
        self.closed = True


class FakeRuntime:
    def __init__(self, page: FakePage) -> None:
        self._page = page
        self.ensure_started_calls = 0

    async def ensure_started(self):
        self.ensure_started_calls += 1
        return object()  # context placeholder

    async def acquire_page(self):
        return self._page


def _build_capture_item(**overrides) -> CaptureItemIn:
    base = {
        "source_url": f"https://www.xiaohongshu.com/explore/{overrides.get('note_id', 'x')}",
        "page_type": "search_result",
        "title": overrides.get("title", "title"),
        "note_id": overrides.get("note_id", "x"),
        "likes": overrides.get("likes", 100),
    }
    base.update(overrides)
    return CaptureItemIn(**base)


def _make_extractor(items_per_call: Iterable[list[CaptureItemIn]]):
    """Return an async extractor that yields a different list each invocation."""
    iterator = iter(items_per_call)

    async def _extract(page, keyword):
        try:
            return next(iterator)
        except StopIteration:
            return []

    return _extract


def _capture_progress():
    """Build (callback, list) pair for capturing progress updates."""
    captured: list[ScrapeProgress] = []

    async def _cb(progress: ScrapeProgress) -> None:
        # Snapshot via copy so later mutations don't affect the captured value
        captured.append(
            ScrapeProgress(
                phase=progress.phase,
                scroll_index=progress.scroll_index,
                scroll_total=progress.scroll_total,
                items_count=progress.items_count,
                error_message=progress.error_message,
            )
        )

    return _cb, captured


# --- Tests -----------------------------------------------------------------


class TestScrapeSearchFeed:
    @pytest.mark.asyncio
    async def test_login_required_returns_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = FakePage()
        runtime = FakeRuntime(page)
        cb, captured = _capture_progress()

        async def _logged_in(p):
            return False

        monkeypatch.setattr(scraper_module, "is_logged_in", _logged_in)

        items = await scrape_search_feed(
            "敏感肌",
            runtime=runtime,
            scroll_count=5,
            on_progress=cb,
        )

        assert items == []
        phases = [p.phase for p in captured]
        assert ScrapePhase.LOGIN_REQUIRED in phases
        # Should not have entered scroll phase
        assert ScrapePhase.SCROLLING not in phases
        assert page.closed is True

    @pytest.mark.asyncio
    async def test_full_scroll_loop_collects_unique_items(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = FakePage()
        runtime = FakeRuntime(page)

        # 5 scrolls × 5 unique items each = 25 unique
        per_scroll = [
            [_build_capture_item(note_id=f"s{scroll_idx}_n{i}") for i in range(5)]
            for scroll_idx in range(5)
        ]

        async def _logged_in(p):
            return True

        async def _no_op_scroll(p):
            return None

        monkeypatch.setattr(scraper_module, "is_logged_in", _logged_in)
        monkeypatch.setattr(scraper_module, "human_scroll", _no_op_scroll)
        monkeypatch.setattr(scraper_module, "extract_visible_items", _make_extractor(per_scroll))

        cb, captured = _capture_progress()
        items = await scrape_search_feed(
            "敏感肌",
            runtime=runtime,
            scroll_count=5,
            on_progress=cb,
        )

        assert len(items) == 25
        assert page.closed is True
        # Last progress emit should be DONE with the right counts
        done_emits = [p for p in captured if p.phase == ScrapePhase.DONE]
        assert done_emits, "expected at least one DONE progress emission"
        assert done_emits[-1].items_count == 25

    @pytest.mark.asyncio
    async def test_dedupe_by_note_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        page = FakePage()
        runtime = FakeRuntime(page)

        # Same note_id repeated across scrolls — dedup by key keeps last write
        repeated = [_build_capture_item(note_id="dup", title="v1")]
        again = [_build_capture_item(note_id="dup", title="v2")]
        per_scroll = [repeated, again, repeated]

        async def _logged_in(p):
            return True

        async def _no_op_scroll(p):
            return None

        monkeypatch.setattr(scraper_module, "is_logged_in", _logged_in)
        monkeypatch.setattr(scraper_module, "human_scroll", _no_op_scroll)
        monkeypatch.setattr(scraper_module, "extract_visible_items", _make_extractor(per_scroll))

        items = await scrape_search_feed(
            "敏感肌", runtime=runtime, scroll_count=3
        )

        assert len(items) == 1
        assert items[0].note_id == "dup"

    @pytest.mark.asyncio
    async def test_progress_callback_emits_expected_phases(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = FakePage()
        runtime = FakeRuntime(page)

        async def _logged_in(p):
            return True

        async def _no_op_scroll(p):
            return None

        async def _empty_extract(p, keyword):
            return []

        monkeypatch.setattr(scraper_module, "is_logged_in", _logged_in)
        monkeypatch.setattr(scraper_module, "human_scroll", _no_op_scroll)
        monkeypatch.setattr(scraper_module, "extract_visible_items", _empty_extract)

        cb, captured = _capture_progress()
        await scrape_search_feed("kw", runtime=runtime, scroll_count=2, on_progress=cb)

        emitted_phases = [p.phase for p in captured]
        assert ScrapePhase.LAUNCHING in emitted_phases
        assert ScrapePhase.NAVIGATING in emitted_phases
        assert ScrapePhase.SCROLLING in emitted_phases
        assert ScrapePhase.DONE in emitted_phases

    @pytest.mark.asyncio
    async def test_navigation_failure_emits_error_phase_and_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = FakePage(goto_raises=RuntimeError("network down"))
        runtime = FakeRuntime(page)
        cb, captured = _capture_progress()

        with pytest.raises(RuntimeError, match="network down"):
            await scrape_search_feed(
                "kw",
                runtime=runtime,
                scroll_count=5,
                on_progress=cb,
            )

        error_emits = [p for p in captured if p.phase == ScrapePhase.ERROR]
        assert error_emits, "expected ERROR progress emission"
        assert "navigation_failed" in error_emits[-1].error_message
        assert page.closed is True

    @pytest.mark.asyncio
    async def test_progress_callback_failure_does_not_abort_scrape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = FakePage()
        runtime = FakeRuntime(page)

        async def _logged_in(p):
            return True

        async def _no_op_scroll(p):
            return None

        async def _empty_extract(p, keyword):
            return []

        async def _bad_callback(progress):
            raise RuntimeError("subscriber broken")

        monkeypatch.setattr(scraper_module, "is_logged_in", _logged_in)
        monkeypatch.setattr(scraper_module, "human_scroll", _no_op_scroll)
        monkeypatch.setattr(scraper_module, "extract_visible_items", _empty_extract)

        # Should complete without raising
        items = await scrape_search_feed(
            "kw", runtime=runtime, scroll_count=2, on_progress=_bad_callback
        )
        assert items == []
        assert page.closed is True

    @pytest.mark.asyncio
    async def test_url_includes_encoded_keyword(self, monkeypatch: pytest.MonkeyPatch) -> None:
        page = FakePage()
        runtime = FakeRuntime(page)

        async def _logged_in(p):
            return True

        async def _no_op_scroll(p):
            return None

        async def _empty_extract(p, keyword):
            return []

        monkeypatch.setattr(scraper_module, "is_logged_in", _logged_in)
        monkeypatch.setattr(scraper_module, "human_scroll", _no_op_scroll)
        monkeypatch.setattr(scraper_module, "extract_visible_items", _empty_extract)

        await scrape_search_feed("敏感肌 护肤", runtime=runtime, scroll_count=1)

        assert page.goto_calls, "expected page.goto to be called"
        called_url, _ = page.goto_calls[0]
        # Spaces and CJK must be percent-encoded
        assert "%E6" in called_url  # 敏 starts with E6 in UTF-8 percent encoding
        assert " " not in called_url
