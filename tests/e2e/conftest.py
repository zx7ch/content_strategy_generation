"""Shared E2E test configuration."""
from __future__ import annotations

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _enable_f003_lite_preview_for_content_research_e2e(
    monkeypatch: pytest.MonkeyPatch,
):
    """F003 E2E exercises the internal preview unless a test explicitly disables it."""
    monkeypatch.setattr(settings, "F003_LITE_PREVIEW_ENABLED", True)
