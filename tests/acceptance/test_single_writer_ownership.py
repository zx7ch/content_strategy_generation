from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from app.api.routes.router import app
from app.content_research.analysis_persistence import SQLiteMarketingAnalysisRepository
from app.content_research.async_dispatch import (
    AsyncFormalResearchDispatchRepository,
    AsyncScopeExecutionContinuationRepository,
)
from app.content_research.async_pipeline_store import AsyncDirectionalPersistenceSession
from app.content_research.lifecycle.coordinator import ContentResearchPersistenceCoordinator
from app.content_research.runtime import CheckpointRuntime, LLMCostLedger
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.core.consistent_snapshot_reader import ConsistentSnapshotReader, SnapshotFound
from app.core.runtime_schema_bootstrap import bootstrap_canonical_runtime_schema
from app.core.runtime_write_coordinator import RuntimeWriteCoordinator, TypedMutation
from app.core.sqlite_connection_roles import (
    SQLiteConnectionOpened,
    observe_sqlite_connections,
)
from app.memory.job_store import JobStore
from app.memory.session_state import SessionManager
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.observe.alert_evaluator import AlertEvaluator
from app.runtime_write_handlers import production_runtime_write_handlers
from app.services.llm.configuration_store import SQLiteLLMConfigurationStore
from app.services.llm.usage_tracker import LLMUsageTracker
from app.services.workflow_run_manager import WorkflowRunManager
from app.services.xhs_credentials import XHSCredentialStore
from app.v2.discovery.service import DiscoveryService
from app.v2.foundation.sqlite_store import SQLiteMasterDataStore

_DIRECT_CONNECTION_AUTHORITIES = {
    "app/core/sqlite_connection_roles.py",
}
_SQLITE_MODULE_NAMES = {"sqlite3", "aiosqlite", "_aiosqlite"}


def _direct_connection_factories(repository: Path) -> list[str]:
    findings: list[str] = []
    for path in sorted((repository / "app").rglob("*.py")):
        relative = path.relative_to(repository).as_posix()
        if relative in _DIRECT_CONNECTION_AUTHORITIES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if (
                node.func.attr == "connect"
                and isinstance(owner, ast.Name)
                and owner.id in _SQLITE_MODULE_NAMES
            ):
                findings.append(f"{relative}:{node.lineno}")
    return findings


@pytest.mark.acceptance
def test_all_runtime_write_factories_share_one_writer(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    opened: list[SQLiteConnectionOpened] = []

    async def exercise() -> None:
        database = tmp_path / "single-writer-ownership.sqlite"
        writer = RuntimeWriteCoordinator(database)
        await writer.start()
        result = await writer.submit(
            TypedMutation.for_diagnostic_fact(
                mutation_id="ownership-fact",
                run_id="ownership-run",
                value="committed by the sole writer",
            )
        )
        snapshot = ConsistentSnapshotReader(database).read_diagnostic_snapshot(
            "ownership-run",
            minimum_revision=result.committed_revision,
        )
        assert isinstance(snapshot, SnapshotFound)
        await writer.close()

    with observe_sqlite_connections(opened.append):
        asyncio.run(exercise())

    write_connections = [event for event in opened if event.role == "writer"]
    assert len(write_connections) == 1
    assert len({event.connection_identity for event in write_connections}) == 1
    assert {event.role for event in opened} == {"reader", "writer"}

    assert _direct_connection_factories(repository) == []


@pytest.mark.acceptance
def test_runtime_db_has_no_legacy_write_connection_path(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[2]
    legacy_symbols = (
        "open_legacy_runtime_database",
        "open_legacy_runtime_async_database",
        '"legacy_writer"',
        "'legacy_writer'",
    )
    legacy_findings = {
        path.relative_to(repository).as_posix(): symbol
        for path in sorted((repository / "app").rglob("*.py"))
        for symbol in legacy_symbols
        if symbol in path.read_text(encoding="utf-8")
    }
    assert legacy_findings == {}

    opened: list[SQLiteConnectionOpened] = []

    async def exercise() -> None:
        database = tmp_path / "production-activation.sqlite"
        await bootstrap_canonical_runtime_schema(database, discovery_secret="acceptance-secret")

        with observe_sqlite_connections(opened.append):
            writer = RuntimeWriteCoordinator(
                database,
                handlers=production_runtime_write_handlers(),
            )
            await writer.start()
            try:
                stores = (
                    JobStore(str(database)),
                    ThreadStore(str(database)),
                    WorkflowStore(str(database)),
                    SessionManager(str(database)),
                    WorkflowRunManager(str(database)),
                    AlertEvaluator(str(database)),
                    LLMUsageTracker(str(database)),
                    SQLiteLLMConfigurationStore(str(database)),
                    XHSCredentialStore(str(database)),
                    SQLiteMasterDataStore(str(database)),
                    DiscoveryService(
                        database_path=database,
                        secret="acceptance-secret",
                    ),
                    SQLiteContentResearchStore(str(database)),
                    ContentResearchPersistenceCoordinator(str(database)),
                    SQLiteMarketingAnalysisRepository(str(database)),
                    AsyncFormalResearchDispatchRepository(str(database)),
                    AsyncScopeExecutionContinuationRepository(str(database)),
                    AsyncDirectionalPersistenceSession(str(database)),
                    LLMCostLedger(str(database)),
                    CheckpointRuntime(str(database)),
                )
                assert all(store._writer is writer for store in stores)

                async_stores = stores[:7]
                for store in async_stores:
                    await store.connect()
                for store in reversed(async_stores):
                    await store.close()

                credential_status = await stores[8].replace_async(
                    "a1=activation; web_session=activation",
                    "manual_cookie",
                )
                assert credential_status.authenticated is True

                result = await writer.submit(
                    TypedMutation.for_diagnostic_fact(
                        mutation_id="production-activation",
                        run_id="production-activation-run",
                        value="canonical writer active",
                    )
                )
                assert result.replayed is False
            finally:
                await writer.close()

    asyncio.run(exercise())

    assert [event.role for event in opened].count("writer") == 1
    assert "bootstrap" not in {event.role for event in opened}
    assert "migration" not in {event.role for event in opened}
    assert {event.role for event in opened} == {"reader", "writer"}


@pytest.mark.acceptance
def test_release_business_surface_has_no_transitional_worker_projection() -> None:
    repository = Path(__file__).resolve().parents[2]
    backend_contract = (
        repository / "app/content_research/api_schemas.py"
    ).read_text(encoding="utf-8")
    frontend_contract = (
        repository / "frontend/src/lib/content-research-api.ts"
    ).read_text(encoding="utf-8")
    route_paths = {
        route.path
        for route in app.routes
        if hasattr(route, "path")
    }

    assert "runtime_steps" not in backend_contract
    assert "runtime_child_tasks" not in backend_contract
    assert "runtime_steps" not in frontend_contract
    assert "runtime_child_tasks" not in frontend_contract
    assert "/threads/{thread_id}/workflow" not in route_paths
