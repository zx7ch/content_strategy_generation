"""Shared store-side lease predicates for continuation-owned transactions."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TypeVar

import aiosqlite

from app.content_research.scope_contract import (
    DispatchLeaseContext,
    ExecutionContext,
    ExecutionLeaseFencedError,
)
from app.services.workflow_run_manager import WorkflowRunManager

T = TypeVar("T")


class LeaseFencedWorkflowRunManager(WorkflowRunManager):
    """Workflow manager whose every outer transaction owns an exact live lease."""

    def __init__(
        self,
        db_path: str,
        *,
        execution_context: ExecutionContext,
        operation: str,
    ) -> None:
        super().__init__(db_path)
        self._execution_guard = workflow_execution_guard(
            execution_context,
            operation=operation,
        )

    async def _transaction(self, fn: Callable[[], Awaitable[T]]) -> T:
        assert self._conn is not None
        if self._transaction_depth:
            return await fn()
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            await self._execution_guard(self._conn)
        except ExecutionLeaseFencedError:
            await self._conn.commit()
            raise
        except Exception:
            await self._conn.rollback()
            raise
        self._transaction_depth += 1
        try:
            result = await fn()
        except Exception:
            await self._conn.rollback()
            raise
        finally:
            self._transaction_depth -= 1
        await self._conn.commit()
        return result


class DispatchLeaseFencedWorkflowRunManager(WorkflowRunManager):
    """Workflow manager whose writes are conditional on a normal dispatch claim."""

    def __init__(
        self,
        db_path: str,
        *,
        dispatch_context: DispatchLeaseContext,
        operation: str,
    ) -> None:
        super().__init__(db_path)
        self._dispatch_guard = workflow_dispatch_guard(
            dispatch_context,
            operation=operation,
        )

    async def _transaction(self, fn: Callable[[], Awaitable[T]]) -> T:
        assert self._conn is not None
        if self._transaction_depth:
            return await fn()
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            await self._dispatch_guard(self._conn)
        except Exception:
            await self._conn.rollback()
            raise
        self._transaction_depth += 1
        try:
            result = await fn()
        except Exception:
            await self._conn.rollback()
            raise
        finally:
            self._transaction_depth -= 1
        await self._conn.commit()
        return result


def workflow_execution_guard(
    context: ExecutionContext,
    *,
    operation: str,
) -> Callable[[aiosqlite.Connection], Awaitable[None]]:
    """Return a transaction guard that commits only a fence fact when stale."""

    async def guard(conn: aiosqlite.Connection) -> None:
        cursor = await conn.execute(
            """SELECT 1 FROM content_research_scope_execution_attempts AS attempt
               JOIN content_research_scope_execution_units AS unit
                 ON unit.id=attempt.execution_unit_id
               WHERE attempt.execution_unit_id=? AND attempt.attempt_no=?
                 AND attempt.attempt_no=(
                   SELECT MAX(latest.attempt_no)
                   FROM content_research_scope_execution_attempts AS latest
                   WHERE latest.execution_unit_id=attempt.execution_unit_id
                 )
                 AND attempt.state='running' AND unit.state='running'
                 AND attempt.lease_token=? AND attempt.lease_expires_at > ?
                 AND unit.scope_contract_id=?""",
            (
                context.execution_unit_id,
                context.attempt_no,
                context.lease_token,
                datetime.now(timezone.utc).isoformat(),
                context.scope_contract_id,
            ),
        )
        if await cursor.fetchone() is not None:
            return
        sequence_cursor = await conn.execute(
            """SELECT COALESCE(MAX(sequence_no), 0) + 1
               FROM content_research_execution_facts
               WHERE execution_unit_id=? AND attempt_no=?""",
            (context.execution_unit_id, context.attempt_no),
        )
        sequence_no = int((await sequence_cursor.fetchone())[0])
        await conn.execute(
            """INSERT INTO content_research_execution_facts
               (execution_unit_id, attempt_no, sequence_no, kind, payload_json, created_at)
               VALUES (?, ?, ?, 'lease_fenced', ?, ?)""",
            (
                context.execution_unit_id,
                context.attempt_no,
                sequence_no,
                json.dumps({"operation": operation}, sort_keys=True),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        raise ExecutionLeaseFencedError(
            f"execution attempt lease was fenced before {operation}"
        )

    return guard


def workflow_dispatch_guard(
    context: DispatchLeaseContext,
    *,
    operation: str,
) -> Callable[[aiosqlite.Connection], Awaitable[None]]:
    """Return a transaction guard for the exact live normal-dispatch lease."""

    async def guard(conn: aiosqlite.Connection) -> None:
        cursor = await conn.execute(
            """SELECT 1 FROM content_research_dispatch_jobs
               WHERE workflow_run_id=? AND status='running'
                 AND lease_owner=? AND lease_token=?
                 AND lease_expires_at IS NOT NULL AND lease_expires_at > ?""",
            (
                context.workflow_run_id,
                context.lease_owner,
                context.lease_token,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        if await cursor.fetchone() is None:
            raise ExecutionLeaseFencedError(
                f"dispatch lease was fenced before {operation}"
            )

    return guard
