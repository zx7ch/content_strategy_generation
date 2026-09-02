"""Writer-owned LangGraph checkpoint persistence."""

from __future__ import annotations

import base64
import json
import sqlite3
import uuid
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import (
    WRITES_IDX_MAP,
    AsyncSqliteSaver,
    get_checkpoint_metadata,
)

from app.core.runtime_write_coordinator import (
    MutationApplication,
    MutationIdentityConflictError,
    RuntimeMutationHandler,
    RuntimeWriteCoordinator,
    TypedMutation,
)
from app.core.sqlite_connection_roles import open_bootstrap_database


def bootstrap_checkpoint_schema(database_path: str | Path) -> None:
    connection = open_bootstrap_database(database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                parent_checkpoint_id TEXT,
                type TEXT,
                checkpoint BLOB,
                metadata BLOB,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
            );
            CREATE TABLE IF NOT EXISTS writes (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                idx INTEGER NOT NULL,
                channel TEXT NOT NULL,
                type TEXT,
                value BLOB,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


def _decode(value: object) -> bytes:
    if not isinstance(value, str):
        raise MutationIdentityConflictError()
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise MutationIdentityConflictError() from exc


class _CheckpointMutationHandler:
    mutation_kind = "mutate_graph_checkpoint"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        payload = dict(mutation.domain_payload)
        action = payload.get("action")
        if action == "put_checkpoint":
            connection.execute(
                """
                INSERT OR REPLACE INTO checkpoints (
                    thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
                    type, checkpoint, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["thread_id"],
                    payload["checkpoint_ns"],
                    payload["checkpoint_id"],
                    payload.get("parent_checkpoint_id"),
                    payload["type"],
                    _decode(payload.get("checkpoint")),
                    _decode(payload.get("metadata")),
                ),
            )
            result = {"checkpoint_id": payload["checkpoint_id"]}
        elif action == "put_writes":
            rows = payload.get("rows")
            replace = payload.get("replace")
            if not isinstance(rows, list) or not isinstance(replace, bool):
                raise MutationIdentityConflictError()
            statement = (
                "INSERT OR REPLACE INTO writes "
                if replace
                else "INSERT OR IGNORE INTO writes "
            ) + """(
                thread_id, checkpoint_ns, checkpoint_id, task_id, idx,
                channel, type, value
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""
            decoded_rows: list[tuple[object, ...]] = []
            for row in rows:
                if not isinstance(row, dict):
                    raise MutationIdentityConflictError()
                decoded_rows.append(
                    (
                        row["thread_id"],
                        row["checkpoint_ns"],
                        row["checkpoint_id"],
                        row["task_id"],
                        row["idx"],
                        row["channel"],
                        row["type"],
                        _decode(row.get("value")),
                    )
                )
            connection.executemany(statement, decoded_rows)
            result = {"writes": len(decoded_rows)}
        elif action == "delete_thread":
            thread_id = payload.get("thread_id")
            if not isinstance(thread_id, str):
                raise MutationIdentityConflictError()
            connection.execute("DELETE FROM checkpoints WHERE thread_id=?", (thread_id,))
            connection.execute("DELETE FROM writes WHERE thread_id=?", (thread_id,))
            result = {"thread_id": thread_id}
        else:
            raise MutationIdentityConflictError()
        return MutationApplication(
            result_contract="graph_checkpoint_mutation_result",
            result_fields=result,
        )


class CoordinatorCheckpointSaver(AsyncSqliteSaver):
    """Keep LangGraph's read/serde contract while routing writes through one Writer."""

    def __init__(
        self,
        connection: aiosqlite.Connection,
        writer: RuntimeWriteCoordinator,
    ) -> None:
        super().__init__(connection)
        self._writer = writer

    async def setup(self) -> None:
        self.is_setup = True

    async def aput(self, config, checkpoint, metadata, new_versions):
        del new_versions
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        checkpoint_ns = str(configurable.get("checkpoint_ns", ""))
        type_, serialized_checkpoint = self.serde.dumps_typed(checkpoint)
        serialized_metadata = json.dumps(
            get_checkpoint_metadata(config, metadata), ensure_ascii=False
        ).encode("utf-8", "ignore")
        checkpoint_id = str(checkpoint["id"])
        await self._writer.submit(
            TypedMutation.create(
                mutation_id=f"checkpoint_{thread_id}_{checkpoint_ns}_{checkpoint_id}",
                mutation_kind="mutate_graph_checkpoint",
                domain_payload={
                    "action": "put_checkpoint",
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                    "parent_checkpoint_id": configurable.get("checkpoint_id"),
                    "type": type_,
                    "checkpoint": base64.b64encode(serialized_checkpoint).decode("ascii"),
                    "metadata": base64.b64encode(serialized_metadata).decode("ascii"),
                },
            )
        )
        return {
            "configurable": {
                "thread_id": configurable["thread_id"],
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def aput_writes(self, config, writes, task_id, task_path="") -> None:
        del task_path
        configurable = config["configurable"]
        rows: list[dict[str, object]] = []
        for index, (channel, value) in enumerate(writes):
            type_, serialized = self.serde.dumps_typed(value)
            rows.append(
                {
                    "thread_id": str(configurable["thread_id"]),
                    "checkpoint_ns": str(configurable.get("checkpoint_ns", "")),
                    "checkpoint_id": str(configurable["checkpoint_id"]),
                    "task_id": task_id,
                    "idx": WRITES_IDX_MAP.get(channel, index),
                    "channel": channel,
                    "type": type_,
                    "value": base64.b64encode(serialized).decode("ascii"),
                }
            )
        await self._writer.submit(
            TypedMutation.create(
                mutation_id=f"checkpoint_writes_{uuid.uuid4().hex}",
                mutation_kind="mutate_graph_checkpoint",
                domain_payload={
                    "action": "put_writes",
                    "replace": all(channel in WRITES_IDX_MAP for channel, _ in writes),
                    "rows": rows,
                },
            )
        )

    async def adelete_thread(self, thread_id: str) -> None:
        await self._writer.submit(
            TypedMutation.create(
                mutation_id=f"delete_checkpoints_{thread_id}_{uuid.uuid4().hex}",
                mutation_kind="mutate_graph_checkpoint",
                domain_payload={"action": "delete_thread", "thread_id": str(thread_id)},
            )
        )


def checkpoint_mutation_handlers() -> tuple[RuntimeMutationHandler, ...]:
    return (_CheckpointMutationHandler(),)
