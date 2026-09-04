"""Coordinator adapter for the existing workflow state machine."""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

from pydantic import BaseModel

from app.core.runtime_write_coordinator import (
    DomainMutationRejectedError,
    MutationApplication,
    MutationIdentityConflictError,
    RuntimeMutationHandler,
    TypedMutation,
)
from app.models.workflow import (
    WorkflowArtifact,
    WorkflowChildTask,
    WorkflowConstraint,
    WorkflowEvent,
    WorkflowRun,
    WorkflowStep,
)

COORDINATED_WORKFLOW_ACTIONS = frozenset(
    {
        "start_run",
        "pause_run",
        "resume_run",
        "wait_for_user_recovery",
        "wait_for_user_input",
        "cancel_run",
        "ack_pause_at_boundary",
        "ack_cancel_at_boundary",
        "complete_run",
        "begin_report_finalization",
        "complete_report_finalization",
        "retry_failed_report_finalization",
        "fail_run",
        "initialize_steps",
        "start_step",
        "record_step_execution_started",
        "record_step_execution_finished",
        "abort_step_execution",
        "complete_step",
        "retry_step",
        "fail_step",
        "cancel_step",
        "skip_step",
        "advance_to_next_step",
        "create_child_tasks",
        "start_child_task",
        "complete_child_task",
        "retry_child_task",
        "restart_step_and_retry_children",
        "fail_child_task",
        "cancel_child_task",
        "attach_artifact",
        "add_constraint",
        "mark_constraint_applied",
        "append_event",
    }
)

_MODEL_TYPES = {
    model.__name__: model
    for model in (
        WorkflowRun,
        WorkflowStep,
        WorkflowChildTask,
        WorkflowArtifact,
        WorkflowConstraint,
        WorkflowEvent,
    )
}


class _AsyncCursorFacade:
    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def __await__(self):
        async def ready() -> _AsyncCursorFacade:
            return self

        return ready().__await__()

    async def __aenter__(self) -> _AsyncCursorFacade:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self._cursor.close()

    async def fetchone(self):
        return self._cursor.fetchone()

    async def fetchall(self):
        return self._cursor.fetchall()

    def __aiter__(self):
        return self

    async def __anext__(self):
        row = self._cursor.fetchone()
        if row is None:
            raise StopAsyncIteration
        return row


class _AsyncConnectionFacade:
    """Minimal async surface used by WorkflowRunManager over Writer's connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def execute(
        self,
        statement: str,
        parameters: tuple[object, ...] | list[object] = (),
    ) -> _AsyncCursorFacade:
        return _AsyncCursorFacade(self._connection.execute(statement, parameters))

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def encode_workflow_result(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return {
            "shape": "model",
            "model": type(value).__name__,
            "value": value.model_dump(mode="json"),
        }
    if isinstance(value, list):
        return {"shape": "list", "items": [encode_workflow_result(item) for item in value]}
    if isinstance(value, tuple):
        return {"shape": "tuple", "items": [encode_workflow_result(item) for item in value]}
    return {"shape": "scalar", "value": value}


def decode_workflow_result(encoded: dict[str, Any]) -> Any:
    shape = encoded.get("shape")
    if shape == "model":
        model_name = encoded.get("model")
        model = _MODEL_TYPES.get(str(model_name))
        if model is None:
            raise RuntimeError("unknown workflow result model")
        return model.model_validate(encoded.get("value"))
    if shape == "list":
        return [decode_workflow_result(item) for item in encoded.get("items", [])]
    if shape == "tuple":
        return tuple(decode_workflow_result(item) for item in encoded.get("items", []))
    if shape == "scalar":
        return encoded.get("value")
    raise RuntimeError("invalid workflow result contract")


class _WorkflowRunMutationHandler:
    mutation_kind = "execute_workflow_command"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        payload = dict(mutation.domain_payload)
        action = payload.get("action")
        args = payload.get("args")
        kwargs = payload.get("kwargs")
        if (
            not isinstance(action, str)
            or action not in COORDINATED_WORKFLOW_ACTIONS
            or not isinstance(args, list)
            or not isinstance(kwargs, dict)
        ):
            raise MutationIdentityConflictError()

        from app.services.workflow_run_manager import (
            WorkflowRunManager,
            WorkflowTransitionError,
        )

        previous_row_factory = connection.row_factory
        connection.row_factory = sqlite3.Row
        try:
            manager = WorkflowRunManager(":coordinator:")
            manager._conn = _AsyncConnectionFacade(connection)  # type: ignore[assignment]
            manager._transaction_depth = 1

            async def invoke() -> Any:
                execution_context = payload.get("execution_context")
                dispatch_context = payload.get("dispatch_context")
                if isinstance(execution_context, dict):
                    from app.content_research.execution_lease import workflow_execution_guard
                    from app.content_research.scope_contract import ExecutionContext

                    await workflow_execution_guard(
                        ExecutionContext(**execution_context),
                        operation=action,
                    )(manager._conn)
                elif isinstance(dispatch_context, dict):
                    from app.content_research.execution_lease import workflow_dispatch_guard
                    from app.content_research.scope_contract import DispatchLeaseContext

                    await workflow_dispatch_guard(
                        DispatchLeaseContext(**dispatch_context),
                        operation=action,
                    )(manager._conn)
                method = getattr(manager, action)
                return await method(*args, **kwargs)

            try:
                value = asyncio.run(invoke())
            except WorkflowTransitionError as exc:
                raise DomainMutationRejectedError(str(exc)) from exc
            except Exception as exc:
                from app.content_research.scope_contract import ExecutionLeaseFencedError

                if isinstance(exc, ExecutionLeaseFencedError):
                    return MutationApplication(
                        result_contract="workflow_command_result",
                        result_fields={
                            "rejected": "execution_lease_fenced",
                            "message": str(exc),
                        },
                    )
                raise
        finally:
            connection.row_factory = previous_row_factory

        return MutationApplication(
            result_contract="workflow_command_result",
            result_fields=encode_workflow_result(value),
        )


def workflow_run_mutation_handlers() -> tuple[RuntimeMutationHandler, ...]:
    return (_WorkflowRunMutationHandler(),)
