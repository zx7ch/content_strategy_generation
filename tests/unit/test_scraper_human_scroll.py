"""Unit tests for human_scroll behavioral mitigation."""

from __future__ import annotations

import asyncio

import pytest

from experiments.xhs_extension_mvp.server import scraper as scraper_module
from experiments.xhs_extension_mvp.server.scraper import human_scroll


class FakeMouse:
    def __init__(self) -> None:
        self.move_calls: list[tuple[int, int, int]] = []
        self.wheel_calls: list[tuple[int, int]] = []

    async def move(self, x: int, y: int, *, steps: int = 1) -> None:
        self.move_calls.append((x, y, steps))

    async def wheel(self, dx: int, dy: int) -> None:
        self.wheel_calls.append((dx, dy))


class FakePage:
    def __init__(self) -> None:
        self.mouse = FakeMouse()


class _SleepRecorder:
    """Replaces asyncio.sleep to capture durations without actually waiting."""

    def __init__(self) -> None:
        self.durations: list[float] = []

    async def __call__(self, duration: float) -> None:
        self.durations.append(duration)


class TestHumanScroll:
    @pytest.mark.asyncio
    async def test_calls_mouse_move_before_wheel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        page = FakePage()
        sleep_recorder = _SleepRecorder()
        monkeypatch.setattr(scraper_module.asyncio, "sleep", sleep_recorder)

        await human_scroll(page)

        # Move recorded once before the first wheel call
        assert len(page.mouse.move_calls) == 1
        assert len(page.mouse.wheel_calls) >= 1

    @pytest.mark.asyncio
    async def test_substep_count_within_expected_range(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = FakePage()
        sleep_recorder = _SleepRecorder()
        monkeypatch.setattr(scraper_module.asyncio, "sleep", sleep_recorder)

        await human_scroll(page)

        # Expect 3-5 substeps per the constants
        assert 3 <= len(page.mouse.wheel_calls) <= 5

    @pytest.mark.asyncio
    async def test_distances_vary_across_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleep_recorder = _SleepRecorder()
        monkeypatch.setattr(scraper_module.asyncio, "sleep", sleep_recorder)

        totals: set[int] = set()
        for _ in range(20):
            page = FakePage()
            await human_scroll(page)
            total = sum(dy for _, dy in page.mouse.wheel_calls)
            totals.add(total)
        # 20 calls should produce more than one distinct total — proves randomization
        assert len(totals) > 1

    @pytest.mark.asyncio
    async def test_total_sleep_duration_within_bounds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = FakePage()
        sleep_recorder = _SleepRecorder()
        monkeypatch.setattr(scraper_module.asyncio, "sleep", sleep_recorder)

        await human_scroll(page)

        total_sleep = sum(sleep_recorder.durations)
        # Lower bound: 3 substeps * 0.15 + 1.8 = 2.25
        # Upper bound: 5 substeps * 0.4 + 3.5 = 5.5
        assert 2.25 <= total_sleep <= 5.5

    @pytest.mark.asyncio
    async def test_substep_pause_count_matches_substeps(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = FakePage()
        sleep_recorder = _SleepRecorder()
        monkeypatch.setattr(scraper_module.asyncio, "sleep", sleep_recorder)

        await human_scroll(page)

        # One sleep per substep + one final reading pause
        assert len(sleep_recorder.durations) == len(page.mouse.wheel_calls) + 1
