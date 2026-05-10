"""Data structures for the Playwright-based scraper module.

Contracts intentionally narrow:
- ``ScrapePhase`` enum values are stable; Phase 2 endpoints expose them in their
  pydantic models, Phase 3 frontend renders them. Do not rename.
- ``ScrapeProgress`` fields are append-only. Phase 2 SSE / Phase 3 polling
  consume this shape directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Awaitable, Callable, Optional


class ScrapePhase(str, Enum):
    IDLE = "idle"
    LAUNCHING = "launching"
    NAVIGATING = "navigating"
    SCROLLING = "scrolling"
    INGESTING = "ingesting"
    DONE = "done"
    ERROR = "error"
    LOGIN_REQUIRED = "login_required"


@dataclass
class ScrapeProgress:
    """Snapshot of scrape progress emitted via callback / state registry."""

    phase: ScrapePhase = ScrapePhase.IDLE
    scroll_index: int = 0
    scroll_total: int = 5
    items_count: int = 0
    error_message: str = ""

    def with_phase(
        self,
        phase: ScrapePhase,
        *,
        scroll_index: int | None = None,
        scroll_total: int | None = None,
        items_count: int | None = None,
        error_message: str | None = None,
    ) -> "ScrapeProgress":
        """Return a new progress snapshot with the given overrides."""
        return ScrapeProgress(
            phase=phase,
            scroll_index=self.scroll_index if scroll_index is None else scroll_index,
            scroll_total=self.scroll_total if scroll_total is None else scroll_total,
            items_count=self.items_count if items_count is None else items_count,
            error_message=self.error_message if error_message is None else error_message,
        )


@dataclass
class ScrapeState:
    """Per-task scrape state held by ``ScrapeStateRegistry``."""

    task_id: str
    keyword: str
    progress: ScrapeProgress
    started_at: datetime
    finished_at: Optional[datetime] = None


ProgressCallback = Optional[Callable[[ScrapeProgress], Awaitable[None]]]
