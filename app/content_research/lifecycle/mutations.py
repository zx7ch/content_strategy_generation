"""Writer adapter for Content Research lifecycle transactions."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import asdict
from datetime import datetime
from typing import Any

from app.content_research.lifecycle.models import (
    ContentResearchState,
    LifecycleCommand,
    RecoveryPlan,
    RunProjection,
)
from app.core.runtime_write_coordinator import (
    DomainMutationRejectedError,
    MutationApplication,
    MutationIdentityConflictError,
    RuntimeMutationHandler,
    TypedMutation,
)


class AsyncSQLiteCursorFacade:
    def __init__(self, cursor: sqlite3.Cursor | None = None) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount if self._cursor is not None else -1

    def __await__(self):
        async def ready() -> AsyncSQLiteCursorFacade:
            return self

        return ready().__await__()

    async def __aenter__(self) -> AsyncSQLiteCursorFacade:
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        if self._cursor is not None:
            self._cursor.close()

    async def fetchone(self):
        return self._cursor.fetchone() if self._cursor is not None else None

    async def fetchall(self):
        return self._cursor.fetchall() if self._cursor is not None else []


class AsyncSQLiteConnectionFacade:
    """Borrow Writer's transaction through the narrow aiosqlite surface in use."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> AsyncSQLiteConnectionFacade:
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def execute(
        self,
        statement: str,
        parameters: tuple[object, ...] | list[object] = (),
    ) -> AsyncSQLiteCursorFacade:
        if statement.lstrip().upper().startswith("BEGIN"):
            return AsyncSQLiteCursorFacade()
        return AsyncSQLiteCursorFacade(self._connection.execute(statement, parameters))

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def close(self) -> None:
        return None


def encode_run_projection(projection: RunProjection) -> dict[str, Any]:
    recovery_plan = asdict(projection.recovery_plan) if projection.recovery_plan else None
    return {
        "run_id": projection.run_id,
        "thread_id": projection.thread_id,
        "state": projection.state.value,
        "state_revision": projection.state_revision,
        "entered_at": projection.entered_at.isoformat(),
        "allowed_actions": list(projection.allowed_actions),
        "recovery_plan": recovery_plan,
        "reason_code": projection.reason_code,
        "error": dict(projection.error) if projection.error is not None else None,
        "brief_id": projection.brief_id,
        "scope_contract_id": projection.scope_contract_id,
        "execution_attempt_id": projection.execution_attempt_id,
        "coverage_snapshot_id": projection.coverage_snapshot_id,
        "publication_id": projection.publication_id,
    }


def decode_run_projection(payload: dict[str, Any]) -> RunProjection:
    recovery = payload.get("recovery_plan")
    if isinstance(recovery, dict):
        recovery = {**recovery, "checkpoint_references": tuple(recovery["checkpoint_references"])}
        recovery_plan = RecoveryPlan(**recovery)
    else:
        recovery_plan = None
    return RunProjection(
        run_id=str(payload["run_id"]),
        thread_id=str(payload["thread_id"]),
        state=ContentResearchState(str(payload["state"])),
        state_revision=int(payload["state_revision"]),
        entered_at=datetime.fromisoformat(str(payload["entered_at"])),
        allowed_actions=tuple(str(item) for item in payload["allowed_actions"]),
        recovery_plan=recovery_plan,
        reason_code=payload.get("reason_code"),
        error=payload.get("error"),
        brief_id=payload.get("brief_id"),
        scope_contract_id=payload.get("scope_contract_id"),
        execution_attempt_id=payload.get("execution_attempt_id"),
        coverage_snapshot_id=payload.get("coverage_snapshot_id"),
        publication_id=payload.get("publication_id"),
    )


def _decode_command(payload: dict[str, Any]) -> LifecycleCommand:
    command = payload.get("command")
    if not isinstance(command, dict):
        raise MutationIdentityConflictError()
    expected_state = command.get("expected_state")
    return LifecycleCommand(
        command_id=str(command["command_id"]),
        run_id=str(command["run_id"]),
        expected_state=(ContentResearchState(str(expected_state)) if expected_state else None),
        expected_revision=int(command["expected_revision"]),
        kind=str(command["kind"]),
        payload=dict(command.get("payload") or {}),
    )


class _ContentResearchLifecycleMutationHandler:
    mutation_kind = "execute_content_research_lifecycle"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        from app.content_research.lifecycle.coordinator import (
            ContentResearchPersistenceCoordinator,
            LifecycleCommandConflict,
        )
        from app.content_research.lifecycle.transitions import LifecycleTransitionError

        payload = dict(mutation.domain_payload)
        action = payload.get("action")
        command = _decode_command(payload)
        previous_row_factory = connection.row_factory
        connection.row_factory = sqlite3.Row
        try:
            coordinator = ContentResearchPersistenceCoordinator(":coordinator:")
            coordinator._borrowed_connection = AsyncSQLiteConnectionFacade(connection)

            async def invoke() -> tuple[RunProjection, str | None]:
                if action == "apply":
                    return await coordinator._apply_once(command), None
                if action == "retry_analysis":
                    projection, attempt_id = await coordinator._retry_analysis_once(
                        command,
                        expected_attempt_id=str(payload["expected_attempt_id"]),
                        expected_contract_fingerprint=str(payload["expected_contract_fingerprint"]),
                    )
                    return projection, attempt_id
                if action == "fail_analysis_attempt":
                    projection = await coordinator._fail_analysis_attempt_once(
                        command,
                        attempt_id=str(payload["attempt_id"]),
                        lease_token=(str(payload["lease_token"]) if payload.get("lease_token") else None),
                        allow_expired_lease=bool(payload.get("allow_expired_lease", False)),
                    )
                    return projection, None
                raise MutationIdentityConflictError()

            try:
                projection, attempt_id = asyncio.run(invoke())
            except (LifecycleCommandConflict, LifecycleTransitionError, ValueError) as exc:
                raise DomainMutationRejectedError(str(exc)) from exc
        finally:
            connection.row_factory = previous_row_factory

        return MutationApplication(
            result_contract="content_research_lifecycle_result",
            result_fields={
                "projection": encode_run_projection(projection),
                "attempt_id": attempt_id,
            },
            committed_revision=projection.state_revision,
            advances_trace_revision=True,
        )


def content_research_lifecycle_handlers() -> tuple[RuntimeMutationHandler, ...]:
    return (_ContentResearchLifecycleMutationHandler(),)
