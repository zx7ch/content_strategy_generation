"""Playwright-based Xiaohongshu search feed scraper.

Top-level entry: ``scrape_search_feed``.

Architecture per improvements.md (2026/05/09 chapter):
- Decision 2: extraction reuses content.js via ``page.evaluate`` injection,
  reading the ``__XHS_EXTRACTOR__`` global exposed by the refactored content.js.
- Decision 3: persistent Chrome profile dir at ``data/chrome-profile``.
- Decision 4: ``ScraperRuntime`` keeps the browser warm; we just open new pages.
- Decision 5: login is a user responsibility; we only detect via
  ``is_logged_in`` and surface ``LOGIN_REQUIRED`` without raising.

The CLI entry (``python -m experiments.xhs_extension_mvp.server.scraper KEYWORD``)
is intentionally lightweight: it reuses the same code paths as the eventual
HTTP endpoint will, so once Phase 2 lands the CLI keeps working as a debug aid.
"""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from experiments.xhs_extension_mvp.server.models import CaptureItemIn
from experiments.xhs_extension_mvp.server.scraper_login import is_logged_in
from experiments.xhs_extension_mvp.server.scraper_models import (
    ProgressCallback,
    ScrapePhase,
    ScrapeProgress,
)
from experiments.xhs_extension_mvp.server.scraper_runtime import ScraperRuntime

if TYPE_CHECKING:
    from playwright.async_api import Page


logger = logging.getLogger(__name__)


SEARCH_URL_TEMPLATE = "https://www.xiaohongshu.com/search_result?keyword={kw}"
PAGE_GOTO_TIMEOUT_MS = 20000
DEFAULT_PROFILE_DIR = Path("data/chrome-profile")


# content.js bundle (read once at import time; restart server to pick up edits)
_CONTENT_JS_FILE = (
    Path(__file__).resolve().parent.parent / "extension" / "src" / "content.js"
)


def _load_extractor_bundle() -> str:
    try:
        return _CONTENT_JS_FILE.read_text(encoding="utf-8")
    except FileNotFoundError as exc:  # pragma: no cover - dev environment guard
        raise RuntimeError(
            f"content.js missing at {_CONTENT_JS_FILE}; cannot inject extractor"
        ) from exc


_EXTRACTOR_JS_BUNDLE = _load_extractor_bundle()

# content.js references chrome.runtime.* which only exists in Chrome extension
# contexts. When injected via page.evaluate we stub the minimum surface so the
# IIFE at the top of the bundle does not crash before __XHS_EXTRACTOR__ is set.
_CHROME_STUB = """
if (typeof globalThis.chrome === 'undefined') { globalThis.chrome = {}; }
if (!globalThis.chrome.runtime) {
    globalThis.chrome.runtime = {
        onMessage: { addListener: function() {}, removeListener: function() {} },
        sendMessage: function() { return Promise.resolve(null); },
        lastError: null,
    };
}
"""


# ---------------------------------------------------------------------------
# Human-like scroll
# ---------------------------------------------------------------------------

# Behavioural-detection mitigation (decision: required, not optional).
# Calibrated for ~2-5 s per scroll with non-uniform deltas + reading pause.
_MOUSE_X_RANGE = (200, 800)
_MOUSE_Y_RANGE = (200, 600)
_MOUSE_MOVE_STEPS_RANGE = (10, 25)
_SCROLL_DELTA_RANGE = (1400, 2000)
_SCROLL_SUBSTEPS_RANGE = (3, 5)
_SCROLL_SUBSTEP_PAUSE_RANGE = (0.15, 0.4)
_READING_PAUSE_RANGE = (1.8, 3.5)


async def human_scroll(page: "Page") -> None:
    """Perform one human-like scroll cycle.

    Sequence:
      1. small bezier-style mouse movement
      2. randomized scroll delta split across 3-5 sub-wheel events
      3. reading pause

    Total duration: roughly [2.25, 5.5] s depending on RNG.
    """
    mouse_x = random.randint(*_MOUSE_X_RANGE)
    mouse_y = random.randint(*_MOUSE_Y_RANGE)
    move_steps = random.randint(*_MOUSE_MOVE_STEPS_RANGE)
    await page.mouse.move(mouse_x, mouse_y, steps=move_steps)

    delta_total = random.randint(*_SCROLL_DELTA_RANGE)
    sub_count = random.randint(*_SCROLL_SUBSTEPS_RANGE)
    sub_delta = delta_total // sub_count
    for _ in range(sub_count):
        await page.mouse.wheel(0, sub_delta)
        await asyncio.sleep(random.uniform(*_SCROLL_SUBSTEP_PAUSE_RANGE))

    await asyncio.sleep(random.uniform(*_READING_PAUSE_RANGE))


# ---------------------------------------------------------------------------
# DOM extraction
# ---------------------------------------------------------------------------


async def extract_visible_items(page: "Page", keyword: str) -> list[CaptureItemIn]:
    """Inject content.js into the page and call its extractor.

    Filters out the synthetic ``search_page_context`` placeholder item that
    content.js appends for debug purposes — it is not a real note.
    """
    raw_items: list[dict] = await page.evaluate(
        f"""
        () => {{
            {_CHROME_STUB}
            {_EXTRACTOR_JS_BUNDLE}
            return globalThis.__XHS_EXTRACTOR__.extractSearchResultItems();
        }}
        """
    )

    items: list[CaptureItemIn] = []
    for raw in raw_items or []:
        if raw.get("debug_url_source") == "search_page_context":
            continue
        try:
            items.append(CaptureItemIn(**raw))
        except Exception as exc:  # noqa: BLE001 - tolerate one bad row, keep rest
            # Single-item parse failures must not abort the scroll loop.
            logger.warning(
                "Failed to parse capture item from extractor",
                extra={"raw_keys": list(raw.keys()), "error": str(exc), "keyword": keyword},
            )
    return items


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


async def _emit(progress: ScrapeProgress, callback: ProgressCallback) -> None:
    if callback is None:
        return
    try:
        await callback(progress)
    except Exception:  # noqa: BLE001 - never let progress callback abort scrape
        logger.exception("Progress callback failed")


async def scrape_search_feed(
    keyword: str,
    *,
    runtime: ScraperRuntime,
    scroll_count: int = 5,
    on_progress: ProgressCallback = None,
) -> list[CaptureItemIn]:
    """Drive a full search-feed scrape and return deduped items.

    Returns ``[]`` when login is required; the caller should observe
    ``ScrapePhase.LOGIN_REQUIRED`` via ``on_progress`` to distinguish from a
    legitimate empty result page.

    Raises only on infrastructure-level failures (browser launch, navigation
    timeout). Per-item parse failures are logged and skipped.
    """
    progress = ScrapeProgress(phase=ScrapePhase.LAUNCHING, scroll_total=scroll_count)
    await _emit(progress, on_progress)

    await runtime.ensure_started()
    page = await runtime.acquire_page()

    try:
        progress = progress.with_phase(ScrapePhase.NAVIGATING)
        await _emit(progress, on_progress)
        try:
            await page.goto(
                SEARCH_URL_TEMPLATE.format(kw=quote(keyword)),
                wait_until="domcontentloaded",
                timeout=PAGE_GOTO_TIMEOUT_MS,
            )
        except Exception as exc:  # noqa: BLE001
            progress = progress.with_phase(
                ScrapePhase.ERROR, error_message=f"navigation_failed: {exc}"
            )
            await _emit(progress, on_progress)
            raise

        if not await is_logged_in(page):
            progress = progress.with_phase(
                ScrapePhase.LOGIN_REQUIRED,
                error_message="未登录小红书，请按 README 完成登录",
            )
            await _emit(progress, on_progress)
            return []

        # Dedupe across scrolls. Keep insertion order via dict.
        collected: dict[str, CaptureItemIn] = {}

        for scroll_idx in range(1, scroll_count + 1):
            progress = progress.with_phase(
                ScrapePhase.SCROLLING,
                scroll_index=scroll_idx,
                items_count=len(collected),
            )
            await _emit(progress, on_progress)

            await human_scroll(page)
            items = await extract_visible_items(page, keyword)
            for item in items:
                key = item.note_id or item.source_url or item.title
                if not key:
                    continue
                collected[key] = item

            progress = progress.with_phase(
                ScrapePhase.SCROLLING,
                scroll_index=scroll_idx,
                items_count=len(collected),
            )
            await _emit(progress, on_progress)

        progress = progress.with_phase(
            ScrapePhase.DONE,
            scroll_index=scroll_count,
            items_count=len(collected),
        )
        await _emit(progress, on_progress)
        return list(collected.values())

    finally:
        try:
            await page.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            logger.exception("Failed to close scrape page")


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


async def _cli_main(keyword: str, scroll_count: int = 5) -> None:
    """Smoke-test the scrape pipeline from the command line.

    Usage:
        python -m experiments.xhs_extension_mvp.server.scraper "敏感肌护肤"
        python -m experiments.xhs_extension_mvp.server.scraper "敏感肌护肤" 3
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    runtime = ScraperRuntime(profile_dir=DEFAULT_PROFILE_DIR)

    async def _print_progress(p: ScrapeProgress) -> None:
        print(
            f"  phase={p.phase.value:<16} "
            f"scroll={p.scroll_index}/{p.scroll_total} "
            f"items={p.items_count} "
            f"err={p.error_message or '-'}"
        )

    print(f"[scraper.cli] starting scrape for '{keyword}' (scroll_count={scroll_count})")
    try:
        items = await scrape_search_feed(
            keyword,
            runtime=runtime,
            scroll_count=scroll_count,
            on_progress=_print_progress,
        )
    finally:
        await runtime.shutdown()

    print(f"[scraper.cli] collected {len(items)} unique items")
    for item in items[:5]:
        print(f"    - [{item.likes:>4} likes] {item.title[:60]}")
    if len(items) > 5:
        print(f"    ... and {len(items) - 5} more")


def _cli_entrypoint() -> None:
    import sys

    args = sys.argv[1:]
    if not args:
        print("Usage: python -m experiments.xhs_extension_mvp.server.scraper KEYWORD [SCROLL_COUNT]")
        raise SystemExit(2)
    keyword = args[0]
    scroll_count = int(args[1]) if len(args) > 1 else 5
    asyncio.run(_cli_main(keyword, scroll_count))


if __name__ == "__main__":  # pragma: no cover
    _cli_entrypoint()
