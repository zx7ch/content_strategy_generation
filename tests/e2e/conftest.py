"""E2E test configuration — mock unavailable langgraph submodules before any app imports."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

# langgraph 1.1.10 in this venv ships without checkpoint.sqlite; mock it so
# app.memory.session_state (which imports AsyncSqliteSaver) can be imported.
for _mod in (
    "langgraph.checkpoint.sqlite",
    "langgraph.checkpoint.sqlite.aio",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

sys.modules["langgraph.checkpoint.sqlite"].AsyncSqliteSaver = MagicMock
sys.modules["langgraph.checkpoint.sqlite.aio"].AsyncSqliteSaver = MagicMock
