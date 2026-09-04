"""Dormant single-connection write actor for the single-writer runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import queue
import sqlite3
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from app.core.runtime_write_registry import (
    register_runtime_writer,
    unregister_runtime_writer,
)
from app.core.sqlite_connection_roles import open_writer_database

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping


class _SafeWriterError(RuntimeError):
    error_code = "WRITER_ERROR"

    def __init__(self) -> None:
        super().__init__(self.error_code)


class PersistenceOverloadedError(_SafeWriterError):
    error_code = "LOCAL_PERSISTENCE_OVERLOADED"


class PersistenceUnavailableError(_SafeWriterError):
    error_code = "PERSISTENCE_UNAVAILABLE"


class MutationIdentityConflictError(_SafeWriterError):
    error_code = "MUTATION_IDENTITY_CONFLICT"


class DomainMutationRejectedError(_SafeWriterError):
    error_code = "DOMAIN_MUTATION_REJECTED"

    def __init__(self, safe_message: str) -> None:
        self.safe_message = safe_message
        RuntimeError.__init__(self, safe_message)


class WriterShuttingDownError(_SafeWriterError):
    error_code = "WRITER_SHUTTING_DOWN"


@dataclass(frozen=True)
class TypedMutation:
    mutation_id: str
    mutation_kind: str
    payload_fingerprint: str
    domain_payload: Mapping[str, Any]
    run_id: str | None = None
    attempt_identity: str | None = None
    expected_state_revision: int | None = None
    recovery_plan_identity: str | None = None

    @classmethod
    def create(
        cls,
        *,
        mutation_id: str,
        mutation_kind: str,
        domain_payload: Mapping[str, Any],
        run_id: str | None = None,
    ) -> TypedMutation:
        payload = dict(domain_payload)
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return cls(
            mutation_id=mutation_id,
            mutation_kind=mutation_kind,
            payload_fingerprint="sha256:" + hashlib.sha256(canonical).hexdigest(),
            domain_payload=MappingProxyType(payload),
            run_id=run_id,
        )

    @classmethod
    def for_diagnostic_fact(
        cls,
        *,
        mutation_id: str,
        run_id: str,
        value: str,
    ) -> TypedMutation:
        return cls.create(
            mutation_id=mutation_id,
            mutation_kind="record_diagnostic_fact",
            domain_payload={"value": value},
            run_id=run_id,
        )


@dataclass(frozen=True)
class CommitResult:
    mutation_id: str
    mutation_kind: str
    result_contract: str
    result_fields: Mapping[str, Any]
    committed_revision: int | None
    replayed: bool


@dataclass(frozen=True)
class MutationApplication:
    result_contract: str
    result_fields: Mapping[str, Any]
    committed_revision: int | None = None
    advances_trace_revision: bool = False


class RuntimeMutationHandler(Protocol):
    mutation_kind: str

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication: ...


class _DiagnosticFactHandler:
    mutation_kind = "record_diagnostic_fact"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        if mutation.run_id is None:
            raise MutationIdentityConflictError()
        value = mutation.domain_payload.get("value")
        if not isinstance(value, str):
            raise MutationIdentityConflictError()
        connection.execute(
            "INSERT INTO diagnostic_facts(mutation_id, run_id, value) VALUES (?, ?, ?)",
            (mutation.mutation_id, mutation.run_id, value),
        )
        prior = connection.execute(
            "SELECT revision FROM run_revisions WHERE run_id=?",
            (mutation.run_id,),
        ).fetchone()
        revision = (int(prior[0]) if prior else 0) + 1
        connection.execute(
            "INSERT INTO run_revisions(run_id, revision) VALUES (?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET revision=excluded.revision",
            (mutation.run_id, revision),
        )
        return MutationApplication(
            result_contract="diagnostic_fact_committed",
            result_fields=MappingProxyType({"fact_id": mutation.mutation_id}),
            committed_revision=revision,
        )


@dataclass(frozen=True)
class _WorkItem:
    mutation: TypedMutation
    completion: Future[CommitResult]


_STOP = object()


class RuntimeWriteCoordinator:
    """Own one bounded FIFO, one thread and one SQLite write connection."""

    def __init__(
        self,
        database_path: Path,
        *,
        queue_capacity: int = 64,
        handlers: Iterable[RuntimeMutationHandler] = (),
        fault_injector: Callable[[str, TypedMutation], None] | None = None,
    ) -> None:
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be positive")
        self._database_path = Path(database_path)
        self._queue: queue.Queue[_WorkItem | object] = queue.Queue(queue_capacity)
        self._fault_injector = fault_injector
        registered = [_DiagnosticFactHandler(), *handlers]
        self._handlers = {handler.mutation_kind: handler for handler in registered}
        if len(self._handlers) != len(registered):
            raise ValueError("duplicate mutation handler")
        self._state_lock = threading.Lock()
        self._state = "new"
        self._ready: Future[None] = Future()
        self._thread: threading.Thread | None = None

    @property
    def availability(self) -> str:
        with self._state_lock:
            if self._state == "unavailable":
                return "persistence_unavailable"
            return self._state

    async def start(self) -> None:
        with self._state_lock:
            if self._state != "new":
                raise RuntimeError("writer already started")
        # Reserve canonical ownership before a worker thread can open SQLite.
        # A duplicate coordinator must fail without briefly becoming a second
        # physical Writer or leaking an accepting background thread.
        register_runtime_writer(self._database_path, self)
        try:
            with self._state_lock:
                self._state = "starting"
                self._thread = threading.Thread(
                    target=self._run,
                    name="runtime-write-coordinator",
                    daemon=True,
                )
                self._thread.start()
            await asyncio.shield(asyncio.wrap_future(self._ready))
        except Exception as exc:
            unregister_runtime_writer(self._database_path, self)
            raise PersistenceUnavailableError() from exc

    async def submit(self, mutation: TypedMutation) -> CommitResult:
        completion: Future[CommitResult] = Future()
        self._admit(mutation, completion)
        return await asyncio.shield(asyncio.wrap_future(completion))

    def submit_sync(self, mutation: TypedMutation) -> CommitResult:
        """Submit from an existing synchronous domain boundary."""
        if threading.current_thread() is self._thread:
            raise RuntimeError("writer handlers cannot recursively submit mutations")
        completion: Future[CommitResult] = Future()
        self._admit(mutation, completion)
        return completion.result()

    def _admit(
        self,
        mutation: TypedMutation,
        completion: Future[CommitResult],
    ) -> None:
        with self._state_lock:
            if self._state == "unavailable":
                raise PersistenceUnavailableError()
            if self._state != "accepting":
                raise WriterShuttingDownError()
            try:
                self._queue.put_nowait(_WorkItem(mutation, completion))
            except queue.Full as exc:
                raise PersistenceOverloadedError() from exc

    async def close(self) -> None:
        with self._state_lock:
            if self._state == "closed":
                return
            if self._state == "new":
                self._state = "closed"
                return
            if self._state != "unavailable":
                self._state = "shutting_down"
            self._reject_queued_locked(WriterShuttingDownError)
            try:
                self._queue.put_nowait(_STOP)
            except queue.Full:
                # The only possible remaining item was claimed concurrently;
                # its completion will leave space before the thread loops.
                pass
            thread = self._thread
        if thread is not None and thread.is_alive():
            await asyncio.to_thread(thread.join)
        with self._state_lock:
            self._state = "closed"
        unregister_runtime_writer(self._database_path, self)

    def _fault(self, stage: str, mutation: TypedMutation) -> None:
        if self._fault_injector is not None:
            self._fault_injector(stage, mutation)

    def _run(self) -> None:
        try:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            connection = open_writer_database(self._database_path)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA busy_timeout=30000")
            self._bootstrap(connection)
        except BaseException as exc:
            self._mark_unavailable(exc)
            return

        with self._state_lock:
            if self._state == "starting":
                self._state = "accepting"
        if not self._ready.done():
            self._ready.set_result(None)
        try:
            while True:
                item = self._queue.get()
                if item is _STOP:
                    return
                assert isinstance(item, _WorkItem)
                try:
                    result = self._execute(connection, item.mutation)
                except (MutationIdentityConflictError, DomainMutationRejectedError) as exc:
                    item.completion.set_exception(exc)
                    continue
                except BaseException as exc:
                    item.completion.set_exception(PersistenceUnavailableError())
                    self._mark_unavailable(exc)
                    return
                item.completion.set_result(result)
        finally:
            connection.close()

    @staticmethod
    def _bootstrap(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS mutation_receipts (
                mutation_kind TEXT NOT NULL,
                mutation_id TEXT NOT NULL,
                payload_fingerprint TEXT NOT NULL,
                result_contract TEXT NOT NULL,
                result_json TEXT NOT NULL,
                committed_revision INTEGER,
                PRIMARY KEY (mutation_kind, mutation_id)
            );
            CREATE TABLE IF NOT EXISTS run_revisions (
                run_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS diagnostic_facts (
                mutation_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                value TEXT NOT NULL
            );
            """
        )
        connection.commit()

    def _execute(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> CommitResult:
        try:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT payload_fingerprint, result_contract, result_json, "
                "committed_revision FROM mutation_receipts "
                "WHERE mutation_kind=? AND mutation_id=?",
                (mutation.mutation_kind, mutation.mutation_id),
            ).fetchone()
            if prior is not None:
                connection.rollback()
                if prior[0] != mutation.payload_fingerprint:
                    raise MutationIdentityConflictError()
                return CommitResult(
                    mutation_id=mutation.mutation_id,
                    mutation_kind=mutation.mutation_kind,
                    result_contract=str(prior[1]),
                    result_fields=MappingProxyType(json.loads(prior[2])),
                    committed_revision=(int(prior[3]) if prior[3] is not None else None),
                    replayed=True,
                )
            self._fault("before_apply", mutation)
            handler = self._handlers.get(mutation.mutation_kind)
            if handler is None:
                raise MutationIdentityConflictError()
            application = handler.apply(connection, mutation)
            committed_revision = application.committed_revision
            if application.advances_trace_revision:
                if mutation.run_id is None:
                    raise MutationIdentityConflictError()
                prior_revision = connection.execute(
                    "SELECT revision FROM content_research_trace_revisions "
                    "WHERE workflow_run_id=?",
                    (mutation.run_id,),
                ).fetchone()
                committed_revision = (
                    int(prior_revision[0]) if prior_revision is not None else 0
                ) + 1
                connection.execute(
                    "INSERT INTO content_research_trace_revisions "
                    "(workflow_run_id, revision, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
                    "ON CONFLICT(workflow_run_id) DO UPDATE SET "
                    "revision=excluded.revision, updated_at=excluded.updated_at",
                    (mutation.run_id, committed_revision),
                )
            connection.execute(
                "INSERT INTO mutation_receipts VALUES (?, ?, ?, ?, ?, ?)",
                (
                    mutation.mutation_kind,
                    mutation.mutation_id,
                    mutation.payload_fingerprint,
                    application.result_contract,
                    json.dumps(
                        dict(application.result_fields),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    committed_revision,
                ),
            )
            self._fault("before_commit", mutation)
            connection.commit()
            result = CommitResult(
                mutation_id=mutation.mutation_id,
                mutation_kind=mutation.mutation_kind,
                result_contract=application.result_contract,
                result_fields=MappingProxyType(dict(application.result_fields)),
                committed_revision=committed_revision,
                replayed=False,
            )
            self._fault("after_commit_before_ack", mutation)
            return result
        except BaseException:
            connection.rollback()
            raise

    def _mark_unavailable(self, cause: BaseException) -> None:
        del cause  # raw failure detail intentionally never crosses this boundary
        with self._state_lock:
            self._state = "unavailable"
            if not self._ready.done():
                self._ready.set_exception(PersistenceUnavailableError())
            self._reject_queued_locked(PersistenceUnavailableError)
        # Keep the failed authority registered until orderly close. Otherwise
        # a newly constructed store could mistake Writer failure for bootstrap
        # time and fail open through a pre-Writer schema connection.

    def _reject_queued_locked(self, error_type: type[_SafeWriterError]) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if isinstance(item, _WorkItem) and not item.completion.done():
                item.completion.set_exception(error_type())
