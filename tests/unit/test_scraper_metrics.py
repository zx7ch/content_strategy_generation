"""Unit tests for ScrapeMetrics (Phase 4 scraper_metrics.py).

Pure dataclass logic — no Playwright, no network, no fixtures needed.
"""
from __future__ import annotations

from experiments.xhs_extension_mvp.server.scraper_metrics import ScrapeMetrics
from experiments.xhs_extension_mvp.server.scraper_models import ScrapePhase


class TestRecordRun:
    def test_done_increments_success_and_items(self) -> None:
        m = ScrapeMetrics()
        m.record_run(ScrapePhase.DONE, items_count=42)

        assert m.total_runs == 1
        assert m.success_runs == 1
        assert m.items_per_run == [42]
        assert m.login_required_runs == 0
        assert m.error_runs == 0

    def test_login_required_increments_counter(self) -> None:
        m = ScrapeMetrics()
        m.record_run(ScrapePhase.LOGIN_REQUIRED)

        assert m.total_runs == 1
        assert m.login_required_runs == 1
        assert m.success_runs == 0
        assert m.items_per_run == []

    def test_error_increments_error_runs(self) -> None:
        m = ScrapeMetrics()
        m.record_run(ScrapePhase.ERROR)

        assert m.total_runs == 1
        assert m.error_runs == 1
        assert m.success_runs == 0

    def test_multiple_runs_accumulate_correctly(self) -> None:
        m = ScrapeMetrics()
        m.record_run(ScrapePhase.DONE, items_count=80)
        m.record_run(ScrapePhase.DONE, items_count=95)
        m.record_run(ScrapePhase.LOGIN_REQUIRED)

        assert m.total_runs == 3
        assert m.success_runs == 2
        assert m.items_per_run == [80, 95]
        assert m.login_required_runs == 1

    def test_record_captcha_increments_captcha_runs(self) -> None:
        m = ScrapeMetrics()
        m.record_run(ScrapePhase.ERROR)
        m.record_captcha()

        assert m.captcha_runs == 1
        assert m.error_runs == 1


class TestSummary:
    def test_empty_metrics_no_division_error(self) -> None:
        m = ScrapeMetrics()
        s = m.summary()

        assert s["total_runs"] == 0
        assert s["success_rate"] == 0.0
        assert s["avg_items_per_run"] == 0.0
        assert s["items_distribution"] == []

    def test_success_rate_calculation(self) -> None:
        m = ScrapeMetrics()
        m.record_run(ScrapePhase.DONE, items_count=50)
        m.record_run(ScrapePhase.DONE, items_count=60)
        m.record_run(ScrapePhase.LOGIN_REQUIRED)
        s = m.summary()

        assert s["success_rate"] == round(2 / 3, 3)
        assert s["success_runs"] == 2
        assert s["total_runs"] == 3

    def test_avg_items_per_run_calculation(self) -> None:
        m = ScrapeMetrics()
        m.record_run(ScrapePhase.DONE, items_count=80)
        m.record_run(ScrapePhase.DONE, items_count=100)
        s = m.summary()

        assert s["avg_items_per_run"] == 90.0

    def test_summary_contains_all_required_keys(self) -> None:
        m = ScrapeMetrics()
        s = m.summary()

        required_keys = {
            "total_runs",
            "success_runs",
            "success_rate",
            "login_required_runs",
            "error_runs",
            "captcha_runs",
            "avg_items_per_run",
            "items_distribution",
        }
        assert required_keys.issubset(s.keys())

    def test_items_distribution_is_a_copy(self) -> None:
        m = ScrapeMetrics()
        m.record_run(ScrapePhase.DONE, items_count=10)
        s = m.summary()
        s["items_distribution"].append(999)

        # Mutating the summary dict must not affect the internal list
        assert m.items_per_run == [10]
