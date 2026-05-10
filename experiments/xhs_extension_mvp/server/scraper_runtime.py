"""Keep-alive Playwright runtime for the scraper.

Decision 4 in improvements.md: a single ``BrowserContext`` lives for the
lifetime of the FastAPI process. Each scrape opens a fresh ``Page`` on the
shared context (cheap, ~tens of ms) instead of re-launching Chromium
(~3-5 s + binary download caching warmth).

Single-worker assumption (decision 6) prevents Chrome profile lock collisions.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page, Playwright


logger = logging.getLogger(__name__)


DEFAULT_CHROME_ARGS = (
    "--window-position=-2000,-2000",
    "--disable-blink-features=AutomationControlled",
)
DEFAULT_VIEWPORT = {"width": 1440, "height": 900}


class ScraperRuntimeError(RuntimeError):
    """Raised when the scraper runtime fails to start the browser."""


class ScraperRuntime:
    """Process-wide Playwright + persistent Chromium context."""

    def __init__(
        self,
        profile_dir: Path,
        *,
        headless: bool = False,
        chrome_args: tuple[str, ...] = DEFAULT_CHROME_ARGS,
        viewport: Optional[dict] = None,
    ) -> None:
        self._profile_dir = Path(profile_dir)
        self._headless = headless
        self._chrome_args = tuple(chrome_args)
        self._viewport = viewport or DEFAULT_VIEWPORT
        self._playwright: Optional["Playwright"] = None
        self._context: Optional["BrowserContext"] = None
        self._lock = asyncio.Lock()

    @property
    def is_started(self) -> bool:
        return self._context is not None

    async def ensure_started(self) -> "BrowserContext":
        """Idempotent: launches once, returns the live context.

        Auto-reset path: if the context was closed (browser crash, manual
        close), we throw away references and relaunch.
        """
        async with self._lock:
            if self._context is not None and not self._is_context_closed(self._context):
                return self._context

            # Either first launch or stale references after crash.
            if self._context is not None:
                logger.warning("ScraperRuntime detected closed context; relaunching")
                await self._cleanup_locked()

            self._profile_dir.mkdir(parents=True, exist_ok=True)
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:  # pragma: no cover - import guard
                raise ScraperRuntimeError(
                    "playwright is not installed. Run `pip install playwright` "
                    "and `playwright install chromium`."
                ) from exc

            self._playwright = await async_playwright().start()
            try:
                self._context = await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self._profile_dir),
                    headless=self._headless,
                    args=list(self._chrome_args),
                    viewport=self._viewport,
                )
            except Exception as exc:
                # Clean partial state so the next call retries fresh.
                await self._playwright.stop()
                self._playwright = None
                raise ScraperRuntimeError(
                    f"Failed to launch persistent Chromium context: {exc}"
                ) from exc
            return self._context

    async def acquire_page(self) -> "Page":
        """Open a fresh page on the shared context."""
        ctx = await self.ensure_started()
        return await ctx.new_page()

    async def shutdown(self) -> None:
        """Close the context and stop Playwright. Safe to call repeatedly."""
        async with self._lock:
            await self._cleanup_locked()

    async def _cleanup_locked(self) -> None:
        """Internal cleanup; caller must hold ``self._lock``."""
        if self._context is not None:
            try:
                if not self._is_context_closed(self._context):
                    await self._context.close()
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.exception("Error closing browser context")
            self._context = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.exception("Error stopping playwright")
            self._playwright = None

    @staticmethod
    def _is_context_closed(context) -> bool:
        """Best-effort liveness check that survives missing attributes."""
        # Newer Playwright versions expose `_closed_or_closing` via internal API;
        # we fall back to attempting a cheap operation when the attribute is absent.
        is_closed = getattr(context, "is_closed", None)
        if callable(is_closed):
            try:
                return bool(is_closed())
            except Exception:
                return True
        # Legacy fallback: assume alive if no liveness check is available.
        return False
