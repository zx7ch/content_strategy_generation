"""Unit tests for is_logged_in detection."""

from __future__ import annotations

import pytest

from experiments.xhs_extension_mvp.server.scraper_login import (
    LOGGED_IN_SELECTOR,
    LOGGED_OUT_SELECTOR,
    is_logged_in,
)

# Import lazily-resolved exception
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


class FakePage:
    """Minimal stand-in for playwright.Page used by ``is_logged_in``."""

    def __init__(self, *, present_selectors: set[str]) -> None:
        self._present = set(present_selectors)
        self.calls: list[tuple[str, int]] = []

    async def wait_for_selector(self, selector: str, timeout: int = 0):
        self.calls.append((selector, timeout))
        if selector in self._present:
            return object()  # truthy ElementHandle stand-in
        raise PlaywrightTimeoutError(f"timeout waiting for {selector}")


class TestIsLoggedIn:
    @pytest.mark.asyncio
    async def test_returns_true_when_logged_in_selector_present(self) -> None:
        page = FakePage(present_selectors={LOGGED_IN_SELECTOR})
        assert await is_logged_in(page) is True
        # Optimisation: should not even probe the logout selector
        assert all(call[0] == LOGGED_IN_SELECTOR for call in page.calls)

    @pytest.mark.asyncio
    async def test_returns_false_when_logout_selector_present(self) -> None:
        page = FakePage(present_selectors={LOGGED_OUT_SELECTOR})
        assert await is_logged_in(page) is False
        # First probes logged-in (timeout), then logout (success)
        assert page.calls[0][0] == LOGGED_IN_SELECTOR
        assert page.calls[1][0] == LOGGED_OUT_SELECTOR

    @pytest.mark.asyncio
    async def test_returns_false_when_neither_signal_present(self) -> None:
        page = FakePage(present_selectors=set())
        # Defaults to False — safer fallback (caller will surface LOGIN_REQUIRED)
        assert await is_logged_in(page) is False
