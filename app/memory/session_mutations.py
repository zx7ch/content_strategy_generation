"""Closed mutations for session rows and session-owned business data."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.core.runtime_write_coordinator import (
    MutationApplication,
    MutationIdentityConflictError,
    RuntimeMutationHandler,
    TypedMutation,
)

_SESSION_COLUMNS = {
    "user_id",
    "user_query",
    "platform",
    "mode",
    "stage",
    "lifecycle_state",
    "alive_until",
    "spider_cooldown_until",
    "purge_after",
    "frozen_at",
    "purged_at",
    "pause_requested",
    "pause_requested_at",
    "spider_note_ids",
    "strategy_id",
    "proposal_ids",
    "generated_note_ids",
    "similarity_report",
    "quality_score",
    "used_fallback",
    "retry_stats",
    "expanded_queries",
    "reindex_state",
    "reindex_attempts",
    "error",
    "error_code",
    "updated_at",
    "last_activity_at",
    "last_user_activity_at",
}


def _required(payload: dict[str, Any], name: str, expected: type[Any]) -> Any:
    value = payload.get(name)
    if not isinstance(value, expected):
        raise MutationIdentityConflictError()
    return value


class _SessionMutationHandler:
    mutation_kind = "mutate_session_record"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        payload = dict(mutation.domain_payload)
        action = _required(payload, "action", str)
        result: dict[str, Any]

        if action == "create_session":
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id, user_id, user_query, platform, mode, stage,
                    lifecycle_state, alive_until, purge_after, quality_score,
                    used_fallback, retry_stats, reindex_state, reindex_attempts,
                    created_at, updated_at, last_activity_at, last_user_activity_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    user_id=excluded.user_id, user_query=excluded.user_query,
                    platform=excluded.platform, mode=excluded.mode,
                    updated_at=excluded.updated_at,
                    last_activity_at=excluded.last_activity_at,
                    last_user_activity_at=excluded.last_user_activity_at,
                    alive_until=excluded.alive_until, purge_after=excluded.purge_after,
                    lifecycle_state='alive'
                """,
                tuple(payload[name] for name in (
                    "session_id", "user_id", "user_query", "platform", "mode", "stage",
                    "lifecycle_state", "alive_until", "purge_after", "quality_score",
                    "used_fallback", "retry_stats", "reindex_state", "reindex_attempts",
                    "created_at", "updated_at", "last_activity_at", "last_user_activity_at",
                )),
            )
            result = {"session_id": payload["session_id"]}
        elif action == "update_session":
            session_id = _required(payload, "session_id", str)
            fields = payload.get("fields")
            if not isinstance(fields, dict) or not fields:
                raise MutationIdentityConflictError()
            if any(name not in _SESSION_COLUMNS for name in fields):
                raise MutationIdentityConflictError()
            assignments = ", ".join(f"{name}=?" for name in fields)
            cursor = connection.execute(
                f"UPDATE sessions SET {assignments} WHERE session_id=?",
                (*fields.values(), session_id),
            )
            result = {"updated": cursor.rowcount > 0}
        elif action == "delete_session":
            session_id = _required(payload, "session_id", str)
            for table in ("spider_data", "strategy_data", "proposal_data", "generation_data"):
                connection.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))
            cursor = connection.execute(
                "DELETE FROM sessions WHERE session_id=?", (session_id,)
            )
            result = {"deleted": cursor.rowcount > 0}
        elif action == "refresh_lifecycle":
            session_id = _required(payload, "session_id", str)
            lifecycle = _required(payload, "lifecycle_state", str)
            fields = {
                "lifecycle_state": lifecycle,
                "updated_at": payload["now"],
                "alive_until": payload["alive_until"],
                "purge_after": payload["purge_after"],
            }
            if lifecycle == "frozen":
                fields["frozen_at"] = payload["frozen_at"]
            if lifecycle == "purged":
                fields["purged_at"] = payload["purged_at"]
                jobs_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
                ).fetchone()
                if jobs_table is not None:
                    cancelled = connection.execute(
                        """
                        UPDATE jobs SET status='cancelled', cancel_reason='session_purged',
                            lease_expires_at=NULL, updated_at=CURRENT_TIMESTAMP
                        WHERE session_id=?
                          AND status IN ('queued', 'paused', 'retrying', 'running')
                        """,
                        (session_id,),
                    ).rowcount
                else:
                    cancelled = 0
            else:
                cancelled = 0
            assignments = ", ".join(f"{name}=?" for name in fields)
            connection.execute(
                f"UPDATE sessions SET {assignments} WHERE session_id=?",
                (*fields.values(), session_id),
            )
            event = payload.get("event")
            if isinstance(event, dict):
                payload_json = event["payload_json"]
                if lifecycle == "purged":
                    event_payload = json.loads(payload_json)
                    event_payload.setdefault("details", {})["cancelled_jobs"] = cancelled
                    payload_json = json.dumps(event_payload, ensure_ascii=False)
                connection.execute(
                    """
                    INSERT INTO session_events(
                        session_id, job_id, event_name, stage, payload_json
                    ) VALUES (?, NULL, ?, ?, ?)
                    """,
                    (
                        session_id,
                        event["event_name"],
                        event.get("stage"),
                        payload_json,
                    ),
                )
            result = {"lifecycle_state": lifecycle, "cancelled_jobs": cancelled}
        elif action == "save_spider_data":
            session_id = _required(payload, "session_id", str)
            posts = payload.get("posts")
            if not isinstance(posts, list):
                raise MutationIdentityConflictError()
            ids: list[str] = []
            for post in posts:
                if not isinstance(post, dict):
                    raise MutationIdentityConflictError()
                note_id = _required(post, "note_id", str)
                connection.execute(
                    """
                    INSERT INTO spider_data(session_id, note_id, data, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(session_id, note_id) DO UPDATE SET data=excluded.data
                    """,
                    (session_id, note_id, post["data"], post["created_at"]),
                )
                ids.append(note_id)
            result = {"note_ids": ids}
        elif action == "save_strategy_data":
            connection.execute(
                """
                INSERT INTO strategy_data(
                    strategy_id, session_id, content_strategy, platform_preference, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id) DO UPDATE SET
                    content_strategy=excluded.content_strategy,
                    platform_preference=excluded.platform_preference
                """,
                (
                    payload["strategy_id"], payload["session_id"],
                    payload["content_strategy"], payload["platform_preference"],
                    payload["created_at"],
                ),
            )
            result = {"strategy_id": payload["strategy_id"]}
        elif action == "save_proposal_data":
            proposals = payload.get("proposals")
            if not isinstance(proposals, list):
                raise MutationIdentityConflictError()
            ids: list[str] = []
            for proposal in proposals:
                if not isinstance(proposal, dict):
                    raise MutationIdentityConflictError()
                connection.execute(
                    """
                    INSERT INTO proposal_data(
                        proposal_id, session_id, proposal, overall_score,
                        scored_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(proposal_id) DO UPDATE SET
                        proposal=excluded.proposal, overall_score=excluded.overall_score,
                        scored_at=excluded.scored_at
                    """,
                    (
                        proposal["proposal_id"], payload["session_id"], proposal["proposal"],
                        proposal["overall_score"], proposal["scored_at"], proposal["created_at"],
                    ),
                )
                ids.append(str(proposal["proposal_id"]))
            result = {"proposal_ids": ids}
        elif action == "save_generation_data":
            notes = payload.get("notes")
            if not isinstance(notes, list):
                raise MutationIdentityConflictError()
            ids: list[str] = []
            for note in notes:
                if not isinstance(note, dict):
                    raise MutationIdentityConflictError()
                connection.execute(
                    """
                    INSERT INTO generation_data(
                        note_id, session_id, proposal_id, generated_note,
                        similarity_check, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(note_id) DO UPDATE SET
                        generated_note=excluded.generated_note,
                        similarity_check=excluded.similarity_check
                    """,
                    (
                        note["note_id"], payload["session_id"], note.get("proposal_id"),
                        note["generated_note"], note["similarity_check"], note["created_at"],
                    ),
                )
                ids.append(str(note["note_id"]))
            result = {"note_ids": ids}
        elif action == "delete_session_data":
            session_id = _required(payload, "session_id", str)
            for table in ("spider_data", "strategy_data", "proposal_data", "generation_data"):
                connection.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))
            result = {"session_id": session_id}
        else:
            raise MutationIdentityConflictError()

        return MutationApplication(
            result_contract="session_mutation_result",
            result_fields=result,
        )


def session_mutation_handlers() -> tuple[RuntimeMutationHandler, ...]:
    return (_SessionMutationHandler(),)
