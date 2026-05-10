"""Per-run statistics accumulator for scraper stress testing and anti-crawl tuning."""
from __future__ import annotations

from dataclasses import dataclass, field

from experiments.xhs_extension_mvp.server.scraper_models import ScrapePhase


@dataclass
class ScrapeMetrics:
    """Accumulates per-run statistics across multiple scrape attempts.

    Designed as a pure dataclass (no I/O) so it can be unit-tested without
    Playwright and optionally serialised for persistence later.
    """

    total_runs: int = 0
    success_runs: int = 0
    login_required_runs: int = 0
    error_runs: int = 0
    # Subset of error_runs; caller sets this when captcha DOM is detected.
    captcha_runs: int = 0
    items_per_run: list[int] = field(default_factory=list)

    def record_run(self, phase: ScrapePhase, items_count: int = 0) -> None:
        """Record the terminal phase of one completed scrape attempt."""
        self.total_runs += 1
        if phase == ScrapePhase.DONE:
            self.success_runs += 1
            self.items_per_run.append(items_count)
        elif phase == ScrapePhase.LOGIN_REQUIRED:
            self.login_required_runs += 1
        else:
            self.error_runs += 1

    def record_captcha(self) -> None:
        """Mark the most recent error run as captcha-triggered.

        Call immediately after record_run(ERROR/...) when captcha DOM is
        detected on the page. captcha_runs is a subset of error_runs.
        """
        self.captcha_runs += 1

    def summary(self) -> dict:
        """Return a serialisable summary dict suitable for printing or logging."""
        success_rate = self.success_runs / self.total_runs if self.total_runs else 0.0
        avg_items = (
            sum(self.items_per_run) / len(self.items_per_run)
            if self.items_per_run
            else 0.0
        )
        return {
            "total_runs": self.total_runs,
            "success_runs": self.success_runs,
            "success_rate": round(success_rate, 3),
            "login_required_runs": self.login_required_runs,
            "error_runs": self.error_runs,
            "captcha_runs": self.captcha_runs,
            "avg_items_per_run": round(avg_items, 1),
            "items_distribution": list(self.items_per_run),
        }
