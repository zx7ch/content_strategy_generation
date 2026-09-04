"""Writer-owned runtime usage and alert mutations."""

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


class _RuntimeAccountingMutationHandler:
    mutation_kind = "mutate_runtime_accounting"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        payload = dict(mutation.domain_payload)
        action = _required(payload, "action", str)
        result: dict[str, Any]
        if action == "record_llm_usage":
            fields = payload.get("fields")
            if not isinstance(fields, list) or len(fields) != 22:
                raise MutationIdentityConflictError()
            connection.execute(
                """
                INSERT INTO llm_usage_events (
                    id, session_id, job_id, step_id, step_name, agent_name, tenant_id,
                    user_id, provider, model, model_policy, prompt_tokens,
                    completion_tokens, total_tokens, input_cost, output_cost, total_cost,
                    currency, latency_ms, status, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                fields,
            )
            result = {"event_id": fields[0]}
        elif action == "upsert_llm_configuration":
            fields = payload.get("fields")
            if not isinstance(fields, list) or len(fields) != 10:
                raise MutationIdentityConflictError()
            connection.execute(
                """
                INSERT INTO content_research_llm_configurations (
                    workspace_id, user_id, base_url, model, api_key, validation_status,
                    validated_at, last_validation_error_code, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, user_id) DO UPDATE SET
                    base_url=excluded.base_url, model=excluded.model,
                    api_key=excluded.api_key, validation_status=excluded.validation_status,
                    validated_at=excluded.validated_at,
                    last_validation_error_code=excluded.last_validation_error_code,
                    updated_at=excluded.updated_at
                """,
                fields,
            )
            result = {"workspace_id": fields[0], "user_id": fields[1]}
        elif action == "delete_llm_configuration":
            cursor = connection.execute(
                """
                DELETE FROM content_research_llm_configurations
                WHERE workspace_id=? AND user_id=?
                """,
                (payload["workspace_id"], payload["user_id"]),
            )
            result = {"deleted": cursor.rowcount > 0}
        elif action == "replace_xhs_credential":
            connection.execute(
                """
                INSERT INTO xhs_local_credentials (
                    singleton, cookie, source, status, created_at, updated_at, failure_code
                ) VALUES (1, ?, ?, 'authenticated', ?, ?, NULL)
                ON CONFLICT(singleton) DO UPDATE SET cookie=excluded.cookie,
                    source=excluded.source, status=excluded.status,
                    updated_at=excluded.updated_at, failure_code=NULL
                """,
                (payload["cookie"], payload["source"], payload["now"], payload["now"]),
            )
            result = {"status": "authenticated"}
        elif action == "mark_xhs_credential_stale":
            connection.execute(
                """
                UPDATE xhs_local_credentials SET status='stale', failure_code=?, updated_at=?
                WHERE singleton=1 AND status='authenticated'
                """,
                (payload["failure_code"], payload["now"]),
            )
            result = {"status": "stale"}
        elif action == "clear_xhs_credential":
            connection.execute("DELETE FROM xhs_local_credentials WHERE singleton=1")
            result = {"status": "missing"}
        elif action == "insert_alert":
            cursor = connection.execute(
                """
                INSERT INTO alerts(rule_name, severity, status, minute_bucket, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    payload["rule_name"],
                    payload["severity"],
                    payload["status"],
                    payload["minute_bucket"],
                    payload["payload_json"],
                ),
            )
            result = {"alert_id": int(cursor.lastrowid)}
        elif action == "update_alert_payload":
            alert_id = _required(payload, "alert_id", int)
            connection.execute(
                "UPDATE alerts SET payload_json=? WHERE id=?",
                (payload["payload_json"], alert_id),
            )
            result = {"alert_id": alert_id}
        elif action == "promote_alert":
            alert_id = _required(payload, "alert_id", int)
            connection.execute(
                """
                UPDATE alerts SET status='open', severity=?, minute_bucket=?, payload_json=?
                WHERE id=?
                """,
                (
                    payload["severity"],
                    payload["minute_bucket"],
                    payload["payload_json"],
                    alert_id,
                ),
            )
            result = {"alert_id": alert_id}
        elif action == "resolve_alert":
            alert_id = _required(payload, "alert_id", int)
            connection.execute(
                """
                UPDATE alerts SET status='resolved', resolved_at=?, payload_json=?
                WHERE id=?
                """,
                (payload["resolved_at"], payload["payload_json"], alert_id),
            )
            result = {"alert_id": alert_id}
        else:
            raise MutationIdentityConflictError()
        return MutationApplication(
            result_contract="runtime_accounting_mutation_result",
            result_fields=result,
        )


def runtime_accounting_mutation_handlers() -> tuple[RuntimeMutationHandler, ...]:
    return (_RuntimeAccountingMutationHandler(),)
