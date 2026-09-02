"""Closed mutations for workflow records."""

from __future__ import annotations

import sqlite3
from typing import Any

from app.core.runtime_write_coordinator import (
    MutationApplication,
    MutationIdentityConflictError,
    RuntimeMutationHandler,
    TypedMutation,
)


def _required(payload: dict[str, Any], name: str, expected: type[Any]) -> Any:
    value = payload.get(name)
    if not isinstance(value, expected):
        raise MutationIdentityConflictError()
    return value


class _WorkflowMutationHandler:
    mutation_kind = "mutate_workflow_record"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        payload = dict(mutation.domain_payload)
        action = _required(payload, "action", str)
        result: dict[str, Any]

        if action == "delete_run":
            run_id = _required(payload, "run_id", str)
            for table in (
                "workflow_constraints",
                "workflow_artifacts",
                "workflow_events",
                "workflow_child_tasks",
                "workflow_steps",
                "workflow_runs",
            ):
                connection.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))
            result = {"run_id": run_id}
        elif action == "create_run":
            run_id = _required(payload, "run_id", str)
            connection.execute(
                """
                INSERT INTO workflow_runs (
                    run_id, thread_id, user_id, status, phase, interrupt_policy,
                    source_message_id
                ) VALUES (?, ?, ?, 'created', 'intake', 'safe_boundary', ?)
                """,
                (
                    run_id,
                    _required(payload, "thread_id", str),
                    _required(payload, "user_id", str),
                    payload.get("source_message_id"),
                ),
            )
            result = {"run_id": run_id}
        elif action == "create_step":
            step_id = _required(payload, "step_id", str)
            connection.execute(
                """
                INSERT INTO workflow_steps (
                    step_id, run_id, step_name, phase, status, max_attempts, checkpoint_json
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    step_id,
                    _required(payload, "run_id", str),
                    _required(payload, "step_name", str),
                    _required(payload, "phase", str),
                    _required(payload, "max_attempts", int),
                    payload.get("checkpoint_json"),
                ),
            )
            result = {"step_id": step_id}
        elif action == "create_child_task":
            child_task_id = _required(payload, "child_task_id", str)
            connection.execute(
                """
                INSERT INTO workflow_child_tasks (
                    child_task_id, run_id, step_id, task_type, slot_index, proposal_id,
                    status, max_attempts
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    child_task_id,
                    _required(payload, "run_id", str),
                    _required(payload, "step_id", str),
                    _required(payload, "task_type", str),
                    payload.get("slot_index"),
                    payload.get("proposal_id"),
                    _required(payload, "max_attempts", int),
                ),
            )
            result = {"child_task_id": child_task_id}
        elif action == "append_event":
            row = connection.execute(
                """
                INSERT INTO workflow_events (
                    run_id, thread_id, step_id, child_task_id, job_id,
                    event_type, event_level, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING event_id
                """,
                (
                    _required(payload, "run_id", str),
                    _required(payload, "thread_id", str),
                    payload.get("step_id"),
                    payload.get("child_task_id"),
                    payload.get("job_id"),
                    _required(payload, "event_type", str),
                    _required(payload, "event_level", str),
                    _required(payload, "payload_json", str),
                ),
            ).fetchone()
            if row is None:
                raise MutationIdentityConflictError()
            result = {"event_id": int(row[0])}
        elif action == "create_artifact":
            artifact_id = _required(payload, "artifact_id", str)
            connection.execute(
                """
                INSERT INTO workflow_artifacts (
                    artifact_id, run_id, thread_id, artifact_type, artifact_version,
                    parent_artifact_id, status, payload_mode, storage_table, storage_key,
                    payload_json, summary_text, created_by_step_id
                ) VALUES (?, ?, ?, ?, ?, ?, 'created', ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    _required(payload, "run_id", str),
                    _required(payload, "thread_id", str),
                    _required(payload, "artifact_type", str),
                    _required(payload, "artifact_version", int),
                    payload.get("parent_artifact_id"),
                    _required(payload, "payload_mode", str),
                    payload.get("storage_table"),
                    payload.get("storage_key"),
                    payload.get("payload_json"),
                    payload.get("summary_text"),
                    payload.get("created_by_step_id"),
                ),
            )
            result = {"artifact_id": artifact_id}
        elif action == "update_artifact_status":
            artifact_id = _required(payload, "artifact_id", str)
            connection.execute(
                """
                UPDATE workflow_artifacts SET status=?, updated_at=CURRENT_TIMESTAMP
                WHERE artifact_id=?
                """,
                (_required(payload, "status", str), artifact_id),
            )
            result = {"artifact_id": artifact_id}
        elif action == "create_constraint":
            constraint_id = _required(payload, "constraint_id", str)
            connection.execute(
                """
                INSERT INTO workflow_constraints (
                    constraint_id, run_id, thread_id, message_id, constraint_version,
                    raw_text, constraint_type, scope, impact_level, status, confidence,
                    normalized_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'medium', 'active', ?, ?)
                """,
                (
                    constraint_id,
                    _required(payload, "run_id", str),
                    _required(payload, "thread_id", str),
                    _required(payload, "message_id", str),
                    _required(payload, "constraint_version", int),
                    _required(payload, "raw_text", str),
                    _required(payload, "constraint_type", str),
                    _required(payload, "scope", str),
                    payload.get("confidence"),
                    _required(payload, "normalized_json", str),
                ),
            )
            result = {"constraint_id": constraint_id}
        else:
            raise MutationIdentityConflictError()

        return MutationApplication(
            result_contract="workflow_mutation_result",
            result_fields=result,
        )


def workflow_mutation_handlers() -> tuple[RuntimeMutationHandler, ...]:
    return (_WorkflowMutationHandler(),)
