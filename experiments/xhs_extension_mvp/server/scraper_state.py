"""In-process scrape state registry.

Single-worker assumption (decision 6 in improvements.md): state lives in a
process-local dict guarded by a single asyncio.Lock. Restart loses state.
Multi-worker support requires Redis/SQLite — out of scope.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from experiments.xhs_extension_mvp.server.scraper_models import (
    ScrapePhase,
    ScrapeProgress,
    ScrapeState,
)


class ScrapeStateRegistry:
    """Tracks the in-flight scrape (at most one) plus historical task states."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._states: dict[str, ScrapeState] = {}
        self._active_task_id: Optional[str] = None

    async def try_acquire(
        self,
        *,
        task_id: str,
        keyword: str,
        scroll_total: int = 5,
    ) -> Optional[ScrapeState]:
        """Reserve the global lock and seed an initial ScrapeState.

        Returns the new state on success; returns None if another task already
        holds the lock (caller should surface a 409).
        """
        async with self._lock:
            if self._active_task_id is not None:
                return None
            state = ScrapeState(
                task_id=task_id,
                keyword=keyword,
                progress=ScrapeProgress(
                    phase=ScrapePhase.LAUNCHING,
                    scroll_total=scroll_total,
                ),
                started_at=datetime.utcnow(),
            )
            self._states[task_id] = state
            self._active_task_id = task_id
            return state

    async def update(self, task_id: str, progress: ScrapeProgress) -> None:
        """Replace the progress snapshot for a known task. No-op if unknown."""
        async with self._lock:
            state = self._states.get(task_id)
            if state is None:
                return
            state.progress = progress

    async def release(
        self,
        task_id: str,
        *,
        finished_at: Optional[datetime] = None,
    ) -> None:
        """Release the active lock. Keeps the historical state for status reads."""
        async with self._lock:
            state = self._states.get(task_id)
            if state is not None:
                state.finished_at = finished_at or datetime.utcnow()
            if self._active_task_id == task_id:
                self._active_task_id = None

    def get(self, task_id: str) -> Optional[ScrapeState]:
        """Read-only snapshot lookup. Safe without the lock."""
        return self._states.get(task_id)

    def is_busy(self) -> bool:
        return self._active_task_id is not None

    @property
    def active_task_id(self) -> Optional[str]:
        return self._active_task_id
