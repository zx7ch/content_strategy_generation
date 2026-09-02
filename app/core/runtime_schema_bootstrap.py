"""Prepare every canonical schema before the production Writer is registered."""

from __future__ import annotations

from pathlib import Path

from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.checkpoint_mutations import bootstrap_checkpoint_schema
from app.memory.job_store import JobStore
from app.memory.session_state import SessionManager
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.observe.alert_evaluator import AlertEvaluator
from app.services.llm.usage_tracker import LLMUsageTracker
from app.v2.discovery.service import DiscoveryService
from app.v2.foundation.sqlite_store import SQLiteMasterDataStore


async def bootstrap_canonical_runtime_schema(
    database_path: str | Path,
    *,
    discovery_secret: str,
) -> None:
    path = str(database_path)
    async with JobStore(path):
        pass
    async with ThreadStore(path):
        pass
    async with WorkflowStore(path):
        pass
    async with SessionManager(path):
        pass
    bootstrap_checkpoint_schema(path)
    SQLiteContentResearchStore(path)
    async with LLMUsageTracker(path):
        pass
    async with AlertEvaluator(path):
        pass
    SQLiteMasterDataStore(path)
    DiscoveryService(database_path=path, secret=discovery_secret)
