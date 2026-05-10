"""Unit tests for scraper data structures."""

from __future__ import annotations

from datetime import datetime

import pytest

from experiments.xhs_extension_mvp.server.scraper_models import (
    ScrapePhase,
    ScrapeProgress,
    ScrapeState,
)


class TestScrapeProgress:
    def test_default_values(self) -> None:
        progress = ScrapeProgress()
        assert progress.phase == ScrapePhase.IDLE
        assert progress.scroll_index == 0
        assert progress.scroll_total == 5
        assert progress.items_count == 0
        assert progress.error_message == ""

    def test_with_phase_only_overrides_phase(self) -> None:
        original = ScrapeProgress(
            phase=ScrapePhase.SCROLLING,
            scroll_index=3,
            scroll_total=5,
            items_count=42,
            error_message="",
        )
        next_progress = original.with_phase(ScrapePhase.DONE)
        assert next_progress.phase == ScrapePhase.DONE
        assert next_progress.scroll_index == 3
        assert next_progress.scroll_total == 5
        assert next_progress.items_count == 42

    def test_with_phase_overrides_specified_fields(self) -> None:
        original = ScrapeProgress(phase=ScrapePhase.LAUNCHING)
        next_progress = original.with_phase(
            ScrapePhase.SCROLLING,
            scroll_index=2,
            items_count=10,
        )
        assert next_progress.scroll_index == 2
        assert next_progress.items_count == 10
        # Untouched fields preserved
        assert next_progress.scroll_total == original.scroll_total
        assert next_progress.error_message == original.error_message

    def test_with_phase_returns_new_instance(self) -> None:
        original = ScrapeProgress()
        next_progress = original.with_phase(ScrapePhase.DONE)
        assert next_progress is not original
        # Original untouched (dataclass is mutable but with_phase must not mutate)
        assert original.phase == ScrapePhase.IDLE


class TestScrapePhaseEnum:
    @pytest.mark.parametrize(
        "phase,expected_value",
        [
            (ScrapePhase.IDLE, "idle"),
            (ScrapePhase.LAUNCHING, "launching"),
            (ScrapePhase.NAVIGATING, "navigating"),
            (ScrapePhase.SCROLLING, "scrolling"),
            (ScrapePhase.INGESTING, "ingesting"),
            (ScrapePhase.DONE, "done"),
            (ScrapePhase.ERROR, "error"),
            (ScrapePhase.LOGIN_REQUIRED, "login_required"),
        ],
    )
    def test_string_values_are_stable(self, phase: ScrapePhase, expected_value: str) -> None:
        # These string values are part of the cross-phase contract — Phase 2/3
        # consume them. Renaming will break downstream consumers.
        assert phase.value == expected_value


class TestScrapeState:
    def test_finished_at_defaults_to_none(self) -> None:
        state = ScrapeState(
            task_id="t1",
            keyword="kw",
            progress=ScrapeProgress(),
            started_at=datetime.utcnow(),
        )
        assert state.finished_at is None
