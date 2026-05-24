"""Unit tests for T1 workflow schema and compatibility migrations."""

from __future__ import annotations

import aiosqlite
import pytest

from app.memory.job_store import JobStore
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore


async def _columns(db_path: str, table_name: str) -> set[str]:
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute(f"PRAGMA table_info({table_name})") as cursor:
            return {row[1] async for row in cursor}


async def _table_exists(db_path: str, table_name: str) -> bool:
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ) as cursor:
            return await cursor.fetchone() is not None


@pytest.mark.asyncio
async def test_empty_db_initialization_creates_workflow_tables(tmp_path):
    db_path = str(tmp_path / "workflow.db")

    async with WorkflowStore(db_path):
        pass

    for table_name in (
        "workflow_runs",
        "workflow_steps",
        "workflow_child_tasks",
        "workflow_events",
        "workflow_artifacts",
        "workflow_constraints",
    ):
        assert await _table_exists(db_path, table_name)

    run_columns = await _columns(db_path, "workflow_runs")
    assert {
        "run_id",
        "thread_id",
        "user_id",
        "status",
        "phase",
        "current_step",
        "active_job_id",
        "active_job_type",
        "constraint_version",
        "artifact_version",
        "interrupt_policy",
        "source_message_id",
    } <= run_columns


@pytest.mark.asyncio
async def test_thread_store_migrates_old_creator_threads_schema(tmp_path):
    db_path = str(tmp_path / "threads.db")
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """
            CREATE TABLE creator_threads (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                active_workflow_session_id TEXT,
                active_job_id TEXT,
                accepted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await conn.commit()

    async with ThreadStore(db_path):
        pass

    assert "active_run_id" in await _columns(db_path, "creator_threads")


@pytest.mark.asyncio
async def test_job_store_migrates_old_jobs_schema(tmp_path):
    db_path = str(tmp_path / "jobs.db")
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                job_type TEXT NOT NULL CHECK (job_type IN ('strategy', 'generate')),
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('queued', 'paused', 'running', 'retrying', 'succeeded', 'failed', 'cancelled')
                ),
                priority INTEGER NOT NULL DEFAULT 100,
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 5,
                not_before TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                lease_expires_at TIMESTAMP,
                idempotency_key TEXT,
                last_error_code TEXT,
                last_error_message TEXT,
                cancel_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await conn.commit()

    async with JobStore(db_path):
        pass

    columns = await _columns(db_path, "jobs")
    assert {"run_id", "step_id", "child_task_id"} <= columns


@pytest.mark.asyncio
async def test_workflow_and_legacy_initialization_is_idempotent(tmp_path):
    db_path = str(tmp_path / "idempotent.db")

    async with WorkflowStore(db_path) as store:
        await store.initialize_schema()
        await store.initialize_schema()

    async with ThreadStore(db_path):
        pass
    async with ThreadStore(db_path):
        pass

    async with JobStore(db_path):
        pass
    async with JobStore(db_path):
        pass

    assert "active_run_id" in await _columns(db_path, "creator_threads")
    assert {"run_id", "step_id", "child_task_id"} <= await _columns(db_path, "jobs")
