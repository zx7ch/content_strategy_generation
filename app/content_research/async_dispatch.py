"""Async durable outbox/lease repository for formal Content Research work."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from app.content_research.contracts import DirectionContract, RunPolicySnapshot, SamplePolicy
from app.content_research.models import (
    ResearchBriefRecord,
    ResearchDirectionRecord,
    ResearchPlanRecord,
    SubagentTaskRecord,
)
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.scope_contract import (
    ExecutionFact,
    ScopeExecutionAttempt,
    ScopeExecutionContinuation,
)
from app.content_research.stores.sqlite_store import (
    SQLiteContentResearchStore,
    _dumps,
    _dumps_any_list,
    _fmt_dt,
    _loads_any_list,
    _parse_dt,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AsyncFormalResearchDispatchRepository:
    """The only queue/lease writer used by the async dispatcher runtime."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def persist_brief(self, conn: aiosqlite.Connection, brief: ResearchBriefRecord) -> None:
        """Persist one brief inside a transaction owned by the caller."""
        await conn.execute(
            """INSERT INTO content_research_briefs
               (id, workflow_run_id, thread_id, schema_version, status, created_at,
                updated_at, payload_json, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                 updated_at=excluded.updated_at, payload_json=excluded.payload_json,
                 metadata_json=excluded.metadata_json""",
            (
                brief.id,
                brief.workflow_run_id,
                brief.thread_id,
                brief.schema_version,
                brief.status,
                _fmt_dt(brief.created_at),
                _fmt_dt(brief.updated_at),
                _dumps(brief.payload),
                _dumps(brief.metadata),
            ),
        )

    async def persist_subject_structure_confirmation(
        self,
        conn: aiosqlite.Connection,
        *,
        brief: ResearchBriefRecord,
        checkpoint: StageCheckpointRecord,
    ) -> None:
        """Write subject confirmation records on a caller-owned transaction."""
        await self.persist_brief(conn, brief)
        await conn.execute(
            """INSERT INTO content_research_stage_checkpoints
               (id, schema_version, workflow_run_id, subagent_task_id, stage_name,
                input_fingerprint, status, retry_count, started_at, finished_at,
                payload_json, metadata_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 schema_version=excluded.schema_version,
                 workflow_run_id=excluded.workflow_run_id,
                 subagent_task_id=excluded.subagent_task_id,
                 stage_name=excluded.stage_name,
                 input_fingerprint=excluded.input_fingerprint,
                 status=excluded.status,
                 retry_count=excluded.retry_count,
                 started_at=excluded.started_at,
                 finished_at=excluded.finished_at,
                 payload_json=excluded.payload_json,
                 metadata_json=excluded.metadata_json,
                 created_at=excluded.created_at""",
            (
                checkpoint.id,
                checkpoint.schema_version,
                checkpoint.workflow_run_id,
                checkpoint.subagent_task_id,
                checkpoint.stage_name,
                checkpoint.input_fingerprint,
                checkpoint.status,
                checkpoint.retry_count,
                _fmt_dt(checkpoint.started_at) if checkpoint.started_at else None,
                _fmt_dt(checkpoint.finished_at) if checkpoint.finished_at else None,
                _dumps(checkpoint.payload),
                _dumps(checkpoint.metadata),
                _fmt_dt(checkpoint.created_at),
            ),
        )

    async def enqueue(
        self,
        *,
        workflow_run_id: str,
        provider: str,
        source_kind: str,
        limit: int,
        retry_completed: bool = False,
    ) -> dict[str, Any]:
        now = _now().isoformat()
        async with self._connect() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.execute(
                    """INSERT INTO content_research_dispatch_jobs
                       (workflow_run_id, provider, source_kind, limit_per_specialist, status,
                        attempt_count, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'queued', 0, ?, ?)
                       ON CONFLICT(workflow_run_id) DO UPDATE SET
                         provider=CASE WHEN content_research_dispatch_jobs.status IN ('queued', 'failed') OR ? THEN excluded.provider ELSE content_research_dispatch_jobs.provider END,
                         source_kind=CASE WHEN content_research_dispatch_jobs.status IN ('queued', 'failed') OR ? THEN excluded.source_kind ELSE content_research_dispatch_jobs.source_kind END,
                         limit_per_specialist=CASE WHEN content_research_dispatch_jobs.status IN ('queued', 'failed') OR ? THEN excluded.limit_per_specialist ELSE content_research_dispatch_jobs.limit_per_specialist END,
                         status=CASE WHEN content_research_dispatch_jobs.status = 'failed' OR (content_research_dispatch_jobs.status = 'completed' AND ?) THEN 'queued' ELSE content_research_dispatch_jobs.status END,
                         lease_expires_at=CASE WHEN content_research_dispatch_jobs.status = 'failed' OR (content_research_dispatch_jobs.status = 'completed' AND ?) THEN NULL ELSE content_research_dispatch_jobs.lease_expires_at END,
                         lease_owner=CASE WHEN content_research_dispatch_jobs.status = 'failed' OR (content_research_dispatch_jobs.status = 'completed' AND ?) THEN NULL ELSE content_research_dispatch_jobs.lease_owner END,
                         lease_token=CASE WHEN content_research_dispatch_jobs.status = 'failed' OR (content_research_dispatch_jobs.status = 'completed' AND ?) THEN NULL ELSE content_research_dispatch_jobs.lease_token END,
                         last_error=CASE WHEN content_research_dispatch_jobs.status = 'failed' OR (content_research_dispatch_jobs.status = 'completed' AND ?) THEN NULL ELSE content_research_dispatch_jobs.last_error END,
                         updated_at=excluded.updated_at""",
                    (
                        workflow_run_id,
                        provider,
                        source_kind,
                        limit,
                        now,
                        now,
                        *([retry_completed] * 8),
                    ),
                )
                row = await self._fetch_job(conn, workflow_run_id)
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
        return row

    async def persist_confirmation(
        self,
        conn: aiosqlite.Connection,
        *,
        brief: ResearchBriefRecord,
        plan: ResearchPlanRecord,
        snapshot: RunPolicySnapshot,
        sample_policies: list[SamplePolicy],
        direction_contracts: list[DirectionContract],
        directions: list[ResearchDirectionRecord],
        tasks: list[SubagentTaskRecord],
        workflow_child_task_ids: list[str],
    ) -> None:
        """Write the confirmed research scope on a shared unit of work.

        This method intentionally never commits: its caller owns the workflow
        transaction and decides whether the complete run becomes visible. The
        explicit start-formal-research action creates the dispatch job later.
        """
        await conn.execute(
            """INSERT INTO content_research_briefs
               (id, workflow_run_id, thread_id, schema_version, status, created_at, updated_at, payload_json, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at,
                 payload_json=excluded.payload_json, metadata_json=excluded.metadata_json""",
            (
                brief.id,
                brief.workflow_run_id,
                brief.thread_id,
                brief.schema_version,
                brief.status,
                _fmt_dt(brief.created_at),
                _fmt_dt(brief.updated_at),
                _dumps(brief.payload),
                _dumps(brief.metadata),
            ),
        )
        await conn.execute(
            """INSERT INTO content_research_plans
               (id, brief_id, workflow_run_id, thread_id, schema_version, status, created_at, updated_at, payload_json, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                plan.id,
                plan.brief_id,
                plan.workflow_run_id,
                plan.thread_id,
                plan.schema_version,
                plan.status,
                _fmt_dt(plan.created_at),
                _fmt_dt(plan.updated_at),
                _dumps(plan.payload),
                _dumps(plan.metadata),
            ),
        )
        await conn.execute(
            "INSERT INTO content_research_run_policy_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot.id,
                snapshot.workflow_run_id,
                snapshot.research_brief_id,
                snapshot.research_plan_id,
                snapshot.schema_version,
                _dumps(snapshot.effective_policy),
                snapshot.effective_policy_hash,
                _fmt_dt(snapshot.run_as_of_at),
                _dumps(snapshot.base_policy_ids_and_versions),
                _dumps(snapshot.requested_overrides),
                _dumps(snapshot.validation_result),
                _fmt_dt(snapshot.created_at),
                _dumps(snapshot.metadata),
            ),
        )
        for policy in sample_policies:
            await conn.execute(
                "INSERT INTO content_research_sample_policies VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    policy.id,
                    policy.schema_version,
                    policy.direction_id,
                    policy.minimum_samples,
                    policy.minimum_independent_authors,
                    policy.author_cap,
                    _dumps(policy.metadata),
                ),
            )
        for contract in direction_contracts:
            await conn.execute(
                "INSERT INTO content_research_direction_contracts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    contract.id,
                    contract.snapshot_id,
                    contract.direction_id,
                    contract.schema_version,
                    contract.sample_policy_id,
                    _dumps_any_list(list(contract.required_note_fields)),
                    _dumps_any_list(list(contract.optional_note_fields)),
                    _dumps_any_list(list(contract.required_comment_fields)),
                    _dumps_any_list(list(contract.claim_rules)),
                    contract.analysis_schema_version,
                    contract.resume_contract_version,
                    _dumps(contract.metadata),
                ),
            )
        for direction in directions:
            await conn.execute(
                """INSERT INTO content_research_directions
                   (id, plan_id, workflow_run_id, thread_id, schema_version, status, priority, created_at, updated_at, payload_json, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    direction.id,
                    direction.plan_id,
                    direction.workflow_run_id,
                    direction.thread_id,
                    direction.schema_version,
                    direction.status,
                    direction.priority,
                    _fmt_dt(direction.created_at),
                    _fmt_dt(direction.updated_at),
                    _dumps(direction.payload),
                    _dumps(direction.metadata),
                ),
            )
        for task, child_id in zip(tasks, workflow_child_task_ids, strict=True):
            payload = {**task.payload, "workflow_child_task_id": child_id}
            await conn.execute(
                """INSERT INTO content_research_subagent_tasks
                   (id, workflow_run_id, thread_id, schema_version, status, plan_id, direction_id, created_at, updated_at, payload_json, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task.id,
                    task.workflow_run_id,
                    task.thread_id,
                    task.schema_version,
                    task.status,
                    task.plan_id,
                    task.direction_id,
                    _fmt_dt(task.created_at),
                    _fmt_dt(task.updated_at),
                    _dumps(payload),
                    _dumps(task.metadata),
                ),
            )

    async def claim_next(self, *, owner: str, lease_seconds: int = 120) -> dict[str, Any] | None:
        now = _now()
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        token = uuid.uuid4().hex
        async with self._connect() as conn:
            # Empty-queue polling is read-only. Taking BEGIN IMMEDIATE on every
            # scan can starve unrelated workflow creation/confirmation writes,
            # especially when a test or local worker uses a short scan period.
            preflight = await conn.execute(
                """SELECT 1 FROM content_research_dispatch_jobs
                   WHERE status='queued'
                      OR (status='running' AND lease_expires_at IS NOT NULL
                          AND lease_expires_at < ?)
                   LIMIT 1""",
                (now.isoformat(),),
            )
            if await preflight.fetchone() is None:
                return None
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.execute(
                    """UPDATE content_research_dispatch_jobs
                       SET status='queued', lease_expires_at=NULL, lease_owner=NULL, lease_token=NULL,
                           updated_at=?
                       WHERE status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?""",
                    (now.isoformat(), now.isoformat()),
                )
                cursor = await conn.execute(
                    """SELECT workflow_run_id FROM content_research_dispatch_jobs
                       WHERE status='queued' ORDER BY created_at ASC LIMIT 1"""
                )
                candidate = await cursor.fetchone()
                if candidate is None:
                    await conn.commit()
                    return None
                workflow_run_id = str(candidate[0])
                result = await conn.execute(
                    """UPDATE content_research_dispatch_jobs
                       SET status='running', attempt_count=attempt_count+1, lease_owner=?, lease_token=?,
                           lease_heartbeat_at=?, lease_expires_at=?, updated_at=?
                       WHERE workflow_run_id=? AND status='queued'""",
                    (owner, token, now.isoformat(), expires, now.isoformat(), workflow_run_id),
                )
                if result.rowcount != 1:
                    await conn.commit()
                    return None
                row = await self._fetch_job(conn, workflow_run_id)
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
        return row

    async def renew(
        self, *, workflow_run_id: str, owner: str, token: str, lease_seconds: int = 120
    ) -> bool:
        now = _now()
        async with self._connect() as conn:
            result = await conn.execute(
                """UPDATE content_research_dispatch_jobs
                   SET lease_heartbeat_at=?, lease_expires_at=?, updated_at=?
                   WHERE workflow_run_id=? AND status='running' AND lease_owner=? AND lease_token=?""",
                (
                    now.isoformat(),
                    (now + timedelta(seconds=lease_seconds)).isoformat(),
                    now.isoformat(),
                    workflow_run_id,
                    owner,
                    token,
                ),
            )
            await conn.commit()
        return result.rowcount == 1

    async def complete(
        self, *, workflow_run_id: str, owner: str, token: str, error: str | None = None
    ) -> bool:
        now = _now().isoformat()
        async with self._connect() as conn:
            result = await conn.execute(
                """UPDATE content_research_dispatch_jobs
                   SET status=?, last_error=?, lease_expires_at=NULL, lease_owner=NULL, lease_token=NULL,
                       updated_at=?, completed_at=?
                   WHERE workflow_run_id=? AND status='running' AND lease_owner=? AND lease_token=?""",
                (
                    "failed" if error else "completed",
                    error,
                    now,
                    now,
                    workflow_run_id,
                    owner,
                    token,
                ),
            )
            await conn.commit()
        return result.rowcount == 1

    def _connect(self) -> aiosqlite.Connection:
        return aiosqlite.connect(self._db_path)

    async def _fetch_job(self, conn: aiosqlite.Connection, workflow_run_id: str) -> dict[str, Any]:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM content_research_dispatch_jobs WHERE workflow_run_id=?",
            (workflow_run_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError(f"dispatch job disappeared: {workflow_run_id}")
        return dict(row)


class AsyncScopeExecutionContinuationRepository:
    """Lease/claim boundary for authorization-owned continuation commands."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def claim_next(
        self, *, owner: str, lease_seconds: int = 120
    ) -> ScopeExecutionContinuation | None:
        now = _now()
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        token = uuid.uuid4().hex
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """SELECT 1 FROM content_research_scope_execution_continuations
                   WHERE state='pending'
                      OR (state='running' AND lease_expires_at IS NOT NULL
                          AND lease_expires_at < ?)
                   LIMIT 1""",
                (now.isoformat(),),
            )
            if await cursor.fetchone() is None:
                return None
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.execute(
                    """UPDATE content_research_scope_execution_continuations
                       SET state='pending', lease_owner=NULL, lease_token=NULL,
                           lease_expires_at=NULL, updated_at=?
                       WHERE state='running' AND lease_expires_at IS NOT NULL
                         AND lease_expires_at < ?""",
                    (now.isoformat(), now.isoformat()),
                )
                cursor = await conn.execute(
                    """SELECT id FROM content_research_scope_execution_continuations
                       WHERE state='pending' ORDER BY created_at ASC, id ASC LIMIT 1"""
                )
                candidate = await cursor.fetchone()
                if candidate is None:
                    await conn.commit()
                    return None
                continuation_id = str(candidate["id"])
                result = await conn.execute(
                    """UPDATE content_research_scope_execution_continuations
                       SET state='running', attempt_count=attempt_count+1,
                           lease_owner=?, lease_token=?, lease_expires_at=?, updated_at=?
                       WHERE id=? AND state='pending'""",
                    (
                        owner,
                        token,
                        expires,
                        now.isoformat(),
                        continuation_id,
                    ),
                )
                if result.rowcount != 1:
                    await conn.commit()
                    return None
                row_cursor = await conn.execute(
                    """SELECT * FROM content_research_scope_execution_continuations
                       WHERE id=?""",
                    (continuation_id,),
                )
                row = await row_cursor.fetchone()
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
        return self._row_to_continuation(row) if row is not None else None

    async def renew(
        self, *, authorization_id: str, owner: str, token: str, lease_seconds: int = 120
    ) -> bool:
        now = _now()
        async with aiosqlite.connect(self._db_path) as conn:
            result = await conn.execute(
                """UPDATE content_research_scope_execution_continuations
                   SET lease_expires_at=?, updated_at=?
                   WHERE authorization_id=? AND state='running'
                     AND lease_owner=? AND lease_token=?""",
                (
                    (now + timedelta(seconds=lease_seconds)).isoformat(),
                    now.isoformat(),
                    authorization_id,
                    owner,
                    token,
                ),
            )
            await conn.commit()
        return result.rowcount == 1

    async def complete(
        self,
        *,
        authorization_id: str,
        owner: str,
        token: str,
        error: str | None = None,
    ) -> bool:
        now = _now().isoformat()
        async with aiosqlite.connect(self._db_path) as conn:
            result = await conn.execute(
                """UPDATE content_research_scope_execution_continuations
                   SET state=?, last_error=?, lease_owner=NULL, lease_token=NULL,
                       lease_expires_at=NULL, updated_at=?, completed_at=?
                   WHERE authorization_id=? AND state='running'
                     AND lease_owner=? AND lease_token=?""",
                (
                    "failed" if error else "completed",
                    error,
                    now,
                    now,
                    authorization_id,
                    owner,
                    token,
                ),
            )
            await conn.commit()
        return result.rowcount == 1

    @staticmethod
    def _row_to_continuation(row: aiosqlite.Row) -> ScopeExecutionContinuation:
        return ScopeExecutionContinuation(
            id=str(row["id"]),
            authorization_id=str(row["authorization_id"]),
            workflow_run_id=str(row["workflow_run_id"]),
            execution_revision=int(row["execution_revision"]),
            operation=str(row["operation"]),
            supplementary_queries=tuple(
                str(item) for item in _loads_any_list(row["supplementary_queries_json"])
            ),
            state=str(row["state"]),
            created_at=_parse_dt(str(row["created_at"])),
            lease_token=str(row["lease_token"]) if row["lease_token"] else None,
            execution_unit_id=(
                str(row["execution_unit_id"])
                if "execution_unit_id" in row.keys() and row["execution_unit_id"]
                else None
            ),
        )


class AsyncScopeExecutionUnitRepository:
    """Async façade for the execution-unit lease and trace seam.

    Continuation repository methods remain available as compatibility aliases
    while callers migrate to the stable execution-unit identity.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def claim_execution_unit(
        self, *, execution_unit_id: str, owner: str, lease_seconds: int = 120
    ) -> ScopeExecutionAttempt | None:
        return await asyncio.to_thread(
            SQLiteContentResearchStore(self._db_path).claim_execution_unit,
            execution_unit_id=execution_unit_id,
            owner=owner,
            lease_seconds=lease_seconds,
        )

    async def renew_execution_unit_lease(
        self,
        *,
        execution_unit_id: str,
        attempt_no: int,
        owner: str,
        lease_token: str,
        lease_seconds: int = 120,
    ) -> bool:
        return await asyncio.to_thread(
            SQLiteContentResearchStore(self._db_path).renew_execution_unit_lease,
            execution_unit_id=execution_unit_id,
            attempt_no=attempt_no,
            owner=owner,
            lease_token=lease_token,
            lease_seconds=lease_seconds,
        )

    async def record_provider_request(
        self,
        *,
        execution_unit_id: str,
        attempt_no: int,
        lease_token: str,
        payload: dict[str, object],
    ) -> bool:
        return await asyncio.to_thread(
            SQLiteContentResearchStore(self._db_path).record_provider_request,
            execution_unit_id=execution_unit_id,
            attempt_no=attempt_no,
            lease_token=lease_token,
            payload=payload,
        )

    async def record_provider_outcome(
        self,
        *,
        execution_unit_id: str,
        attempt_no: int,
        lease_token: str,
        provider_state: str,
        payload: dict[str, object],
    ) -> bool:
        return await asyncio.to_thread(
            SQLiteContentResearchStore(self._db_path).record_provider_outcome,
            execution_unit_id=execution_unit_id,
            attempt_no=attempt_no,
            lease_token=lease_token,
            provider_state=provider_state,
            payload=payload,
        )

    async def complete_execution_unit(
        self,
        *,
        execution_unit_id: str,
        attempt_no: int,
        owner: str,
        lease_token: str,
        state: str,
    ) -> bool:
        return await asyncio.to_thread(
            SQLiteContentResearchStore(self._db_path).complete_execution_unit,
            execution_unit_id=execution_unit_id,
            attempt_no=attempt_no,
            owner=owner,
            lease_token=lease_token,
            state=state,
        )

    async def execution_trace(self, execution_unit_id: str) -> list[ExecutionFact]:
        return await asyncio.to_thread(
            SQLiteContentResearchStore(self._db_path).execution_trace, execution_unit_id
        )
