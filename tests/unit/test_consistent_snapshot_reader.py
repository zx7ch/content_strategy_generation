from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.core.consistent_snapshot_reader import (
    ConsistentSnapshotReader,
    SnapshotBehind,
    SnapshotFound,
    SnapshotNotFound,
    SnapshotUnavailable,
)
from app.core.runtime_write_coordinator import (
    DomainMutationRejectedError,
    MutationApplication,
    MutationIdentityConflictError,
    RuntimeWriteCoordinator,
    TypedMutation,
)
from app.core.sqlite_connection_roles import open_readonly_database


def test_snapshot_reader_is_consistent_and_honors_minimum_revision(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        database = tmp_path / "snapshot.sqlite"
        writer = RuntimeWriteCoordinator(database)
        await writer.start()
        await writer.submit(
            TypedMutation.for_diagnostic_fact(
                mutation_id="fact-one",
                run_id="run-snapshot",
                value="one",
            )
        )
        reader = ConsistentSnapshotReader(database)

        first = reader.read_diagnostic_snapshot("run-snapshot", minimum_revision=1)
        assert isinstance(first, SnapshotFound)
        assert first.snapshot.observed_revision == 1
        assert first.snapshot.fact_ids == ("fact-one",)

        waiting = asyncio.create_task(
            asyncio.to_thread(
                reader.read_diagnostic_snapshot,
                "run-snapshot",
                2,
                wait_timeout=1,
            )
        )
        await asyncio.sleep(0.03)
        await writer.submit(
            TypedMutation.for_diagnostic_fact(
                mutation_id="fact-two",
                run_id="run-snapshot",
                value="two",
            )
        )
        causal = await waiting
        assert isinstance(causal, SnapshotFound)
        assert causal.snapshot.observed_revision == 2
        assert causal.snapshot.fact_ids == ("fact-one", "fact-two")

        behind = reader.read_diagnostic_snapshot(
            "run-snapshot",
            minimum_revision=3,
            wait_timeout=0,
        )
        assert behind == SnapshotBehind(observed_revision=2, minimum_revision=3)
        assert reader.read_diagnostic_snapshot("missing") == SnapshotNotFound("missing")
        await writer.close()

        unavailable = ConsistentSnapshotReader(tmp_path / "missing.sqlite")
        assert unavailable.read_diagnostic_snapshot("run") == SnapshotUnavailable()

    asyncio.run(exercise())


def test_trace_snapshot_causal_revision_and_trigger_cutover(tmp_path: Path) -> None:
    @dataclass(frozen=True)
    class DomainTrace:
        workflow_run_id: str
        trace_revision: int
        fact_ids: tuple[str, ...]

    class TraceBatchHandler:
        mutation_kind = "record_trace_batch"

        def apply(
            self,
            connection: sqlite3.Connection,
            mutation: TypedMutation,
        ) -> MutationApplication:
            action = mutation.domain_payload["action"]
            run_id = mutation.run_id or "global"
            connection.execute(
                "INSERT INTO diagnostic_facts(mutation_id, run_id, value) "
                "VALUES (?, ?, ?)",
                (f"{mutation.mutation_id}-a", run_id, "a"),
            )
            if action == "rollback":
                raise DomainMutationRejectedError("rollback requested")
            if action == "success":
                connection.execute(
                    "INSERT INTO diagnostic_facts(mutation_id, run_id, value) "
                    "VALUES (?, ?, ?)",
                    (f"{mutation.mutation_id}-b", run_id, "b"),
                )
            return MutationApplication(
                result_contract="trace_batch_committed",
                result_fields={"action": action},
                advances_trace_revision=action == "success",
            )

    async def exercise() -> None:
        database = tmp_path / "domain-trace.sqlite"
        SQLiteContentResearchStore(str(database))
        writer = RuntimeWriteCoordinator(database, handlers=(TraceBatchHandler(),))
        await writer.start()

        with open_readonly_database(database) as connection:
            triggers = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='trigger' AND name LIKE 'cr_trace_revision_%'"
            ).fetchall()
        assert triggers == []

        async def load_trace(connection, run_id: str):
            revision = connection.execute(
                "SELECT revision FROM content_research_trace_revisions "
                "WHERE workflow_run_id=?",
                (run_id,),
            ).fetchone()
            if revision is None:
                return None
            facts = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT mutation_id FROM diagnostic_facts "
                    "WHERE run_id=? ORDER BY rowid",
                    (run_id,),
                )
            )
            snapshot = DomainTrace(run_id, int(revision[0]), facts)
            return snapshot, snapshot.trace_revision

        reader = ConsistentSnapshotReader(database, domain_trace_loader=load_trace)
        missing = await reader.read_domain_trace("run-causal")
        assert missing == SnapshotNotFound("run-causal")

        first_mutation = TypedMutation.create(
            mutation_id="causal-one",
            mutation_kind="record_trace_batch",
            run_id="run-causal",
            domain_payload={"action": "success"},
        )
        first = await writer.submit(first_mutation)
        assert first.committed_revision == 1
        replay = await writer.submit(first_mutation)
        assert replay.replayed is True
        assert replay.committed_revision == 1

        with pytest.raises(MutationIdentityConflictError):
            await writer.submit(
                TypedMutation.create(
                    mutation_id="causal-one",
                    mutation_kind="record_trace_batch",
                    run_id="run-causal",
                    domain_payload={"action": "different"},
                )
            )
        with pytest.raises(DomainMutationRejectedError):
            await writer.submit(
                TypedMutation.create(
                    mutation_id="causal-rollback",
                    mutation_kind="record_trace_batch",
                    run_id="run-causal",
                    domain_payload={"action": "rollback"},
                )
            )
        global_result = await writer.submit(
            TypedMutation.create(
                mutation_id="global-one",
                mutation_kind="record_trace_batch",
                domain_payload={"action": "global"},
            )
        )
        assert global_result.committed_revision is None

        waiting = asyncio.create_task(
            reader.read_domain_trace("run-causal", minimum_revision=2, wait_timeout=1)
        )
        await asyncio.sleep(0.03)
        await writer.submit(
            TypedMutation.create(
                mutation_id="causal-two",
                mutation_kind="record_trace_batch",
                run_id="run-causal",
                domain_payload={"action": "success"},
            )
        )
        result = await waiting
        assert isinstance(result, SnapshotFound)
        assert result.snapshot == DomainTrace(
            "run-causal",
            2,
            ("causal-one-a", "causal-one-b", "causal-two-a", "causal-two-b"),
        )

        behind = await reader.read_domain_trace(
            "run-causal", minimum_revision=3, wait_timeout=0
        )
        assert behind == SnapshotBehind(observed_revision=2, minimum_revision=3)
        await writer.close()

    asyncio.run(exercise())
