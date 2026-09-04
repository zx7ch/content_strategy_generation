"""SQLite-backed LLM usage event tracker."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from app.config import settings
from app.core.runtime_write_coordinator import RuntimeWriteCoordinator, TypedMutation
from app.core.runtime_write_registry import get_runtime_writer
from app.core.sqlite_connection_roles import (
    open_bootstrap_async_database,
    open_readonly_async_database,
)
from app.services.llm.pricing import UsageCost
from app.services.llm.types import LLMCallContext, TokenUsage


@dataclass(frozen=True)
class LLMUsageEventInput:
    context: LLMCallContext | None
    provider: str
    model: str
    model_policy: str | None
    usage: TokenUsage
    cost: UsageCost
    latency_ms: int | None
    status: str
    error_message: str | None = None


@dataclass(frozen=True)
class LLMUsageSummary:
    total_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    currency: str = "USD"
    latency_ms: int = 0


@dataclass(frozen=True)
class LLMUsageStepSummary:
    step_id: str | None = None
    step_name: str | None = None
    agent_name: str | None = None
    total_calls: int = 0
    failed_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    currency: str = "USD"
    latency_ms: int = 0


@dataclass(frozen=True)
class LLMUsageEvent:
    id: str
    session_id: str | None
    job_id: str | None
    step_id: str | None
    step_name: str | None
    agent_name: str | None
    provider: str
    model: str
    model_policy: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    total_cost: float
    currency: str
    latency_ms: int | None
    status: str
    error_message: str | None
    created_at: str


class LLMUsageTracker:
    def __init__(
        self,
        db_path: Optional[str] = None,
        *,
        read_only: bool = False,
        writer: RuntimeWriteCoordinator | None = None,
    ) -> None:
        self.db_path = db_path or settings.SQLITE_DB_PATH
        self.read_only = read_only
        self._writer = writer or get_runtime_writer(self.db_path)
        self._conn: aiosqlite.Connection | None = None
        self._table_available = True

    async def __aenter__(self) -> "LLMUsageTracker":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._conn is not None:
            return
        if self.read_only or self._writer is not None:
            self._conn = await open_readonly_async_database(self.db_path)
        else:
            self._conn = await open_bootstrap_async_database(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        if self.read_only or self._writer is not None:
            await self._conn.execute("PRAGMA query_only=ON")
            async with self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_usage_events'"
            ) as cursor:
                self._table_available = await cursor.fetchone() is not None
        else:
            await self._init_tables()

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    async def _init_tables(self) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_usage_events (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                job_id TEXT,
                step_id TEXT,
                step_name TEXT,
                agent_name TEXT,
                tenant_id TEXT,
                user_id TEXT,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                model_policy TEXT,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                input_cost REAL DEFAULT 0,
                output_cost REAL DEFAULT 0,
                total_cost REAL DEFAULT 0,
                currency TEXT DEFAULT 'USD',
                latency_ms INTEGER,
                status TEXT DEFAULT 'success',
                error_message TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_usage_events_session_id ON llm_usage_events(session_id)"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_usage_events_job_id ON llm_usage_events(job_id)"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_usage_events_created_at ON llm_usage_events(created_at)"
        )
        await self._conn.commit()

    async def record(self, event: LLMUsageEventInput) -> str:
        assert self._conn is not None
        event_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        context = event.context
        fields = [
            event_id,
            context.session_id if context else None,
            context.job_id if context else None,
            context.step_id if context else None,
            context.step_name if context else None,
            context.agent_name if context else None,
            context.tenant_id if context else None,
            context.user_id if context else None,
            event.provider,
            event.model,
            event.model_policy,
            event.usage.prompt_tokens,
            event.usage.completion_tokens,
            event.usage.total_tokens,
            event.cost.input_cost,
            event.cost.output_cost,
            event.cost.total_cost,
            event.cost.currency,
            event.latency_ms,
            event.status,
            event.error_message,
            created_at,
        ]
        if self._writer is not None:
            await self._writer.submit(
                TypedMutation.create(
                    mutation_id=event_id,
                    mutation_kind="mutate_runtime_accounting",
                    domain_payload={"action": "record_llm_usage", "fields": fields},
                    run_id=context.job_id if context else None,
                )
            )
            return event_id
        await self._conn.execute(
            """
            INSERT INTO llm_usage_events (
                id, session_id, job_id, step_id, step_name, agent_name, tenant_id, user_id,
                provider, model, model_policy,
                prompt_tokens, completion_tokens, total_tokens,
                input_cost, output_cost, total_cost, currency,
                latency_ms, status, error_message, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            fields,
        )
        await self._conn.commit()
        return event_id

    async def summarize_session(self, session_id: str) -> LLMUsageSummary:
        return await self._summarize("session_id", session_id)

    async def summarize_job(self, job_id: str) -> LLMUsageSummary:
        return await self._summarize("job_id", job_id)

    async def summarize_session_steps(self, session_id: str) -> list[LLMUsageStepSummary]:
        return await self._summarize_steps("session_id", session_id)

    async def summarize_job_steps(self, job_id: str) -> list[LLMUsageStepSummary]:
        return await self._summarize_steps("job_id", job_id)

    async def list_session_events(self, session_id: str) -> list[LLMUsageEvent]:
        return await self._list_events("session_id", session_id)

    async def list_job_events(self, job_id: str) -> list[LLMUsageEvent]:
        return await self._list_events("job_id", job_id)

    async def _summarize(self, field: str, value: str) -> LLMUsageSummary:
        assert self._conn is not None
        if field not in {"session_id", "job_id"}:
            raise ValueError(f"Unsupported summary field: {field}")
        if not self._table_available:
            return LLMUsageSummary()
        async with self._conn.execute(
            f"""
            SELECT
                COUNT(*) AS total_calls,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                COALESCE(SUM(total_cost), 0) AS total_cost,
                COALESCE(SUM(latency_ms), 0) AS latency_ms,
                COALESCE(MAX(currency), 'USD') AS currency
            FROM llm_usage_events
            WHERE {field} = ?
            """,
            (value,),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return LLMUsageSummary()
        return LLMUsageSummary(
            total_calls=int(row["total_calls"] or 0),
            prompt_tokens=int(row["prompt_tokens"] or 0),
            completion_tokens=int(row["completion_tokens"] or 0),
            total_tokens=int(row["total_tokens"] or 0),
            total_cost=float(row["total_cost"] or 0),
            currency=row["currency"] or "USD",
            latency_ms=int(row["latency_ms"] or 0),
        )

    async def _summarize_steps(self, field: str, value: str) -> list[LLMUsageStepSummary]:
        assert self._conn is not None
        if field not in {"session_id", "job_id"}:
            raise ValueError(f"Unsupported step summary field: {field}")
        if not self._table_available:
            return []

        async with self._conn.execute(
            f"""
            SELECT
                step_id,
                step_name,
                agent_name,
                COUNT(*) AS total_calls,
                COALESCE(SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END), 0) AS failed_calls,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                COALESCE(SUM(total_cost), 0) AS total_cost,
                COALESCE(SUM(latency_ms), 0) AS latency_ms,
                COALESCE(MAX(currency), 'USD') AS currency,
                MIN(created_at) AS first_created_at
            FROM llm_usage_events
            WHERE {field} = ?
            GROUP BY COALESCE(step_id, '__unknown__'), step_name, agent_name
            ORDER BY first_created_at ASC, COALESCE(step_id, '__unknown__') ASC
            """,
            (value,),
        ) as cursor:
            rows = await cursor.fetchall()

        return [
            LLMUsageStepSummary(
                step_id=row["step_id"],
                step_name=row["step_name"],
                agent_name=row["agent_name"],
                total_calls=int(row["total_calls"] or 0),
                failed_calls=int(row["failed_calls"] or 0),
                prompt_tokens=int(row["prompt_tokens"] or 0),
                completion_tokens=int(row["completion_tokens"] or 0),
                total_tokens=int(row["total_tokens"] or 0),
                total_cost=float(row["total_cost"] or 0),
                currency=row["currency"] or "USD",
                latency_ms=int(row["latency_ms"] or 0),
            )
            for row in rows
        ]

    async def _list_events(self, field: str, value: str) -> list[LLMUsageEvent]:
        assert self._conn is not None
        if field not in {"session_id", "job_id"}:
            raise ValueError(f"Unsupported event list field: {field}")
        if not self._table_available:
            return []

        async with self._conn.execute(
            f"""
            SELECT
                id,
                session_id,
                job_id,
                step_id,
                step_name,
                agent_name,
                provider,
                model,
                model_policy,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                total_cost,
                currency,
                latency_ms,
                status,
                error_message,
                created_at
            FROM llm_usage_events
            WHERE {field} = ?
            ORDER BY created_at ASC, id ASC
            """,
            (value,),
        ) as cursor:
            rows = await cursor.fetchall()

        return [
            LLMUsageEvent(
                id=row["id"],
                session_id=row["session_id"],
                job_id=row["job_id"],
                step_id=row["step_id"],
                step_name=row["step_name"],
                agent_name=row["agent_name"],
                provider=row["provider"],
                model=row["model"],
                model_policy=row["model_policy"],
                prompt_tokens=int(row["prompt_tokens"] or 0),
                completion_tokens=int(row["completion_tokens"] or 0),
                total_tokens=int(row["total_tokens"] or 0),
                total_cost=float(row["total_cost"] or 0),
                currency=row["currency"] or "USD",
                latency_ms=int(row["latency_ms"]) if row["latency_ms"] is not None else None,
                status=row["status"] or "success",
                error_message=row["error_message"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
