import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app.api.routes.router import app

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def isolated_router_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep dependency-injected API unit tests independent of app.main import order."""

    @asynccontextmanager
    async def no_runtime_startup(_application):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", no_runtime_startup)
