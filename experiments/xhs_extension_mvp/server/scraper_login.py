"""Login state detection for the Playwright scraper.

Decision 1 in improvements.md: detect via two complementary DOM signals.
- Logged in: left-side avatar link ``a.link-wrapper[title="我"]``
- Logged out: ``#login-btn`` button on home / sidebar

When neither signal resolves, default to False (safer: triggers
``LOGIN_REQUIRED`` in the scrape orchestration so the user is prompted to log
in via README rather than silently scraping nothing).
"""

from __future__ import annotations

LOGGED_IN_SELECTOR = 'a.link-wrapper[title="我"][href^="/user/profile/"]'
LOGGED_OUT_SELECTOR = "#login-btn"

_LOGGED_IN_TIMEOUT_MS = 3000
_LOGGED_OUT_TIMEOUT_MS = 500


async def is_logged_in(page) -> bool:
    """Return True iff the current page shows a logged-in identity marker."""
    # Import lazily so unit tests don't require the playwright runtime.
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    try:
        await page.wait_for_selector(LOGGED_IN_SELECTOR, timeout=_LOGGED_IN_TIMEOUT_MS)
        return True
    except PlaywrightTimeoutError:
        pass

    try:
        await page.wait_for_selector(LOGGED_OUT_SELECTOR, timeout=_LOGGED_OUT_TIMEOUT_MS)
        return False
    except PlaywrightTimeoutError:
        pass

    return False
