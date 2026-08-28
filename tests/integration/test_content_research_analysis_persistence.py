from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from app.content_research.analysis_persistence import (
    AnalysisActiveAttemptConflictError,
    AnalysisIdentityConflictError,
    AnalysisLeaseFencedError,
    FrozenEvidenceNoteInput,
    SQLiteMarketingAnalysisRepository,
)


def _connect_without_wait(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=0")
    return conn


def test_empty_analysis_queue_poll_does_not_request_a_sqlite_write_lock(
    tmp_path, monkeypatch
) -> None:
    db_path = str(tmp_path / "analysis-empty-queue.db")
    repository = SQLiteMarketingAnalysisRepository(db_path)
    monkeypatch.setattr(repository, "_connect", lambda: _connect_without_wait(db_path))

    blocker = _connect_without_wait(db_path)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        assert (
            repository.claim_next_analysis_job(
                lease_owner="analysis-worker",
                lease_token="lease-token",
                lease_expires_at=datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc),
                now=datetime(2026, 8, 26, 9, 30, tzinfo=timezone.utc),
            )
            is None
        )
    finally:
        blocker.rollback()
        blocker.close()


def test_evidence_snapshot_freezes_source_fields_hashes_and_query_provenance(tmp_path) -> None:
    repository = SQLiteMarketingAnalysisRepository(str(tmp_path / "analysis.db"))
    note = FrozenEvidenceNoteInput(
        note_id="note-1",
        account_id="account-1",
        title="凉感衬衫实穿",
        body="夏季通勤不闷，但跑步后后背会贴身。",
        source_url="https://www.xiaohongshu.com/explore/note-1",
        captured_at=datetime(2026, 8, 26, 9, 30, tzinfo=timezone.utc),
        query_provenance=("query-core", "query-experience"),
    )

    first = repository.freeze_evidence_snapshot(
        workflow_run_id="run-1",
        scope_contract_id="scope-1",
        retrieval_execution_unit_id="retrieval-unit-1",
        retrieval_attempt_no=1,
        query_groups=(
            {"id": "query-core", "query": "长袖衬衫"},
            {"id": "query-experience", "query": "长袖衬衫 凉感"},
        ),
        notes=(note,),
    )
    replayed = repository.freeze_evidence_snapshot(
        workflow_run_id="run-1",
        scope_contract_id="scope-1",
        retrieval_execution_unit_id="retrieval-unit-1",
        retrieval_attempt_no=1,
        query_groups=first.query_groups,
        notes=(note,),
    )

    assert replayed == first
    assert first.notes[0].title == "凉感衬衫实穿"
    assert first.notes[0].body == "夏季通勤不闷，但跑步后后背会贴身。"
    assert (
        first.notes[0].title_hash
        == "79b8cf0350bb98592805a6efce0a9dd0095f5c9b80388e791bfe9ad35c7ef7a2"
    )
    assert (
        first.notes[0].body_hash
        == "58c36fece3e90f38643f51534c4349a3a00d014451674b3ef0a6b6d7392bb487"
    )
    assert first.notes[0].query_provenance == ("query-core", "query-experience")

    changed_note = FrozenEvidenceNoteInput(
        note_id=note.note_id,
        account_id=note.account_id,
        title="来源后来修改的标题",
        body=note.body,
        source_url=note.source_url,
        captured_at=note.captured_at,
        query_provenance=note.query_provenance,
    )
    with pytest.raises(AnalysisIdentityConflictError, match="retrieval attempt already froze"):
        repository.freeze_evidence_snapshot(
            workflow_run_id="run-1",
            scope_contract_id="scope-1",
            retrieval_execution_unit_id="retrieval-unit-1",
            retrieval_attempt_no=1,
            query_groups=first.query_groups,
            notes=(changed_note,),
        )

    persisted = repository.get_evidence_snapshot(first.id)
    assert persisted == first


def test_analysis_attempt_enforces_one_active_successor_and_reuses_completed_track(
    tmp_path,
) -> None:
    repository = SQLiteMarketingAnalysisRepository(str(tmp_path / "analysis-attempt.db"))
    snapshot = repository.freeze_evidence_snapshot(
        workflow_run_id="run-1",
        scope_contract_id="scope-1",
        retrieval_execution_unit_id="retrieval-unit-1",
        retrieval_attempt_no=1,
        query_groups=({"id": "query-core", "query": "长袖衬衫"},),
        notes=(
            FrozenEvidenceNoteInput(
                note_id="note-1",
                account_id="account-1",
                title="凉感衬衫",
                body="夏季通勤不闷。",
                source_url="https://www.xiaohongshu.com/explore/note-1",
                captured_at=datetime(2026, 8, 26, 9, 30, tzinfo=timezone.utc),
                query_provenance=("query-core",),
            ),
        ),
    )
    unit = repository.get_or_create_analysis_unit(
        evidence_snapshot_id=snapshot.id,
        policy_version="marketing-policy-v1",
        prompt_hash="prompt-hash-1",
        response_schema_hash="schema-hash-1",
        embedding_fingerprint={
            "provider": "sentence_transformers",
            "model": "research-model",
            "revision": "revision-7",
            "dimensions": 3,
            "normalization": "l2",
            "input_format_version": "research_note_title_body_v1",
        },
        algorithm_version="analysis-v1",
        verifier_version="verifier-v1",
    )
    assert (
        repository.get_or_create_analysis_unit(
            evidence_snapshot_id=snapshot.id,
            policy_version="marketing-policy-v1",
            prompt_hash="prompt-hash-1",
            response_schema_hash="schema-hash-1",
            embedding_fingerprint=unit.embedding_fingerprint,
            algorithm_version="analysis-v1",
            verifier_version="verifier-v1",
        )
        == unit
    )

    first_attempt = repository.create_analysis_attempt(unit.id)
    with pytest.raises(AnalysisActiveAttemptConflictError, match="active analysis attempt"):
        repository.create_analysis_attempt(unit.id)
    first_attempt = repository.claim_analysis_attempt(
        first_attempt.id,
        lease_owner="worker-1",
        lease_token="lease-1",
        lease_expires_at=datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc),
        now=datetime(2026, 8, 26, 9, 31, tzinfo=timezone.utc),
    )
    need_checkpoint = repository.complete_analysis_checkpoint(
        analysis_unit_id=unit.id,
        attempt_id=first_attempt.id,
        lease_token="lease-1",
        track="need",
        stage="verifier",
        input_fingerprint="need-input-1",
        output_refs=("decision-need-1",),
        result_checksum="need-checksum-1",
        now=datetime(2026, 8, 26, 9, 32, tzinfo=timezone.utc),
    )
    repository.fail_analysis_attempt(
        first_attempt.id,
        lease_token="lease-1",
        now=datetime(2026, 8, 26, 9, 33, tzinfo=timezone.utc),
    )

    successor = repository.create_analysis_attempt(
        unit.id,
        successor_of_attempt_id=first_attempt.id,
    )
    assert successor.attempt_no == 2
    assert successor.successor_of_attempt_id == first_attempt.id
    assert (
        repository.create_analysis_attempt(
            unit.id,
            successor_of_attempt_id=first_attempt.id,
        )
        == successor
    )
    assert (
        repository.get_completed_analysis_checkpoint(
            analysis_unit_id=unit.id,
            track="need",
            stage="verifier",
            input_fingerprint="need-input-1",
        )
        == need_checkpoint
    )

    with pytest.raises(AnalysisLeaseFencedError, match="not the active lease attempt"):
        repository.complete_analysis_checkpoint(
            analysis_unit_id=unit.id,
            attempt_id=first_attempt.id,
            lease_token="lease-1",
            track="value",
            stage="verifier",
            input_fingerprint="value-input-1",
            output_refs=("decision-value-1",),
            result_checksum="value-checksum-1",
            now=datetime(2026, 8, 26, 9, 34, tzinfo=timezone.utc),
        )


def test_failed_checkpoint_is_attempt_scoped_and_does_not_poison_retry(tmp_path) -> None:
    repository = SQLiteMarketingAnalysisRepository(str(tmp_path / "analysis-failure.db"))
    snapshot = repository.freeze_evidence_snapshot(
        workflow_run_id="run-failure",
        scope_contract_id="scope-failure",
        retrieval_execution_unit_id="retrieval-failure",
        retrieval_attempt_no=1,
        query_groups=({"id": "query-core", "query": "凉感T恤"},),
        notes=(),
    )
    unit = repository.get_or_create_analysis_unit(
        evidence_snapshot_id=snapshot.id,
        policy_version="policy-v1",
        prompt_hash="prompt-v1",
        response_schema_hash="schema-v1",
        embedding_fingerprint={"model": "test", "dimensions": 3},
        algorithm_version="algorithm-v1",
        verifier_version="verifier-v1",
    )
    first = repository.create_analysis_attempt(unit.id)
    first = repository.claim_analysis_attempt(
        first.id,
        lease_owner="worker-1",
        lease_token="lease-1",
        lease_expires_at=datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc),
        now=datetime(2026, 8, 26, 9, 30, tzinfo=timezone.utc),
    )
    failed = repository.fail_analysis_checkpoint(
        analysis_unit_id=unit.id,
        attempt_id=first.id,
        lease_token="lease-1",
        track="shared",
        stage="embedding",
        input_fingerprint="embedding-input",
        error_code="RESEARCH_EMBEDDING_NOT_NORMALIZED",
        private_result={"trace": {"failure_count": 1}},
        now=datetime(2026, 8, 26, 9, 31, tzinfo=timezone.utc),
    )
    assert failed.status == "failed"
    assert failed.private_result["error_code"] == ("RESEARCH_EMBEDDING_NOT_NORMALIZED")
    repository.fail_analysis_attempt(
        first.id,
        lease_token="lease-1",
        now=datetime(2026, 8, 26, 9, 32, tzinfo=timezone.utc),
    )

    successor = repository.create_analysis_attempt(unit.id, successor_of_attempt_id=first.id)
    successor = repository.claim_analysis_attempt(
        successor.id,
        lease_owner="worker-2",
        lease_token="lease-2",
        lease_expires_at=datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc),
        now=datetime(2026, 8, 26, 9, 33, tzinfo=timezone.utc),
    )
    completed = repository.complete_analysis_checkpoint(
        analysis_unit_id=unit.id,
        attempt_id=successor.id,
        lease_token="lease-2",
        track="shared",
        stage="embedding",
        input_fingerprint="embedding-input",
        output_refs=("document-fingerprint",),
        result_checksum="success-checksum",
        private_result={"vectors": {"atom-1": [1.0, 0.0, 0.0]}},
        now=datetime(2026, 8, 26, 9, 34, tzinfo=timezone.utc),
    )

    assert completed.status == "completed"
    assert completed.id != failed.id
    assert (
        repository.get_completed_analysis_checkpoint(
            analysis_unit_id=unit.id,
            track="shared",
            stage="embedding",
            input_fingerprint="embedding-input",
        )
        == completed
    )


def test_analysis_attempt_succeeds_only_after_all_tracks_and_expired_lease_is_fenced(
    tmp_path,
) -> None:
    repository = SQLiteMarketingAnalysisRepository(str(tmp_path / "analysis-terminal.db"))
    snapshot = repository.freeze_evidence_snapshot(
        workflow_run_id="run-terminal",
        scope_contract_id="scope-terminal",
        retrieval_execution_unit_id="retrieval-terminal",
        retrieval_attempt_no=1,
        query_groups=({"id": "query-core", "query": "T恤"},),
        notes=(),
    )
    unit = repository.get_or_create_analysis_unit(
        evidence_snapshot_id=snapshot.id,
        policy_version="policy-v1",
        prompt_hash="prompt-v1",
        response_schema_hash="schema-v1",
        embedding_fingerprint={"model": "deterministic", "dimensions": 3},
        algorithm_version="algorithm-v1",
        verifier_version="verifier-v1",
    )
    attempt = repository.create_analysis_attempt(unit.id)
    attempt = repository.claim_analysis_attempt(
        attempt.id,
        lease_owner="worker",
        lease_token="lease",
        lease_expires_at=datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc),
        now=datetime(2026, 8, 26, 9, 30, tzinfo=timezone.utc),
    )
    with pytest.raises(AnalysisIdentityConflictError, match="every planned track"):
        repository.succeed_analysis_attempt(
            attempt.id,
            lease_token="lease",
            now=datetime(2026, 8, 26, 9, 31, tzinfo=timezone.utc),
        )
    for track in ("need", "value", "message"):
        repository.complete_analysis_checkpoint(
            analysis_unit_id=unit.id,
            attempt_id=attempt.id,
            lease_token="lease",
            track=track,
            stage="verifier",
            input_fingerprint=f"{track}-input",
            output_refs=(f"decision-{track}",),
            result_checksum=f"{track}-checksum",
            now=datetime(2026, 8, 26, 9, 32, tzinfo=timezone.utc),
        )
    succeeded = repository.succeed_analysis_attempt(
        attempt.id,
        lease_token="lease",
        now=datetime(2026, 8, 26, 9, 33, tzinfo=timezone.utc),
    )
    assert succeeded.state == "succeeded"
    # Isolated repository fixtures intentionally omit workflow_runs; only a Run
    # pointer can define the public effective attempt.
    assert repository.get_effective_attempt_for_run("run-terminal") is None
    assert repository.get_latest_attempt_for_unit(unit.id) == succeeded

    second_snapshot = repository.freeze_evidence_snapshot(
        workflow_run_id="run-expired",
        scope_contract_id="scope-expired",
        retrieval_execution_unit_id="retrieval-expired",
        retrieval_attempt_no=1,
        query_groups=({"id": "query-core", "query": "T恤"},),
        notes=(),
    )
    second_unit = repository.get_or_create_analysis_unit(
        evidence_snapshot_id=second_snapshot.id,
        policy_version="policy-v1",
        prompt_hash="prompt-v1",
        response_schema_hash="schema-v1",
        embedding_fingerprint={"model": "deterministic", "dimensions": 3},
        algorithm_version="algorithm-v1",
        verifier_version="verifier-v1",
    )
    expired = repository.create_analysis_attempt(second_unit.id)
    repository.claim_analysis_attempt(
        expired.id,
        lease_owner="worker",
        lease_token="expired-lease",
        lease_expires_at=datetime(2026, 8, 26, 9, 40, tzinfo=timezone.utc),
        now=datetime(2026, 8, 26, 9, 30, tzinfo=timezone.utc),
    )
    assert [
        item.id
        for item in repository.expire_analysis_attempts(
            now=datetime(2026, 8, 26, 9, 41, tzinfo=timezone.utc)
        )
    ] == [expired.id]
    with pytest.raises(AnalysisLeaseFencedError):
        repository.complete_analysis_checkpoint(
            analysis_unit_id=second_unit.id,
            attempt_id=expired.id,
            lease_token="expired-lease",
            track="need",
            stage="verifier",
            input_fingerprint="late",
            output_refs=("late",),
            result_checksum="late",
            now=datetime(2026, 8, 26, 9, 42, tzinfo=timezone.utc),
        )


def test_analysis_job_claim_is_durable_and_expired_attempt_requires_explicit_recovery(
    tmp_path,
) -> None:
    repository = SQLiteMarketingAnalysisRepository(str(tmp_path / "analysis-worker.db"))
    snapshot = repository.freeze_evidence_snapshot(
        workflow_run_id="run-worker",
        scope_contract_id="scope-worker",
        retrieval_execution_unit_id="retrieval-worker",
        retrieval_attempt_no=1,
        query_groups=({"id": "query-core", "query": "T恤"},),
        notes=(),
    )
    unit = repository.get_or_create_analysis_unit(
        evidence_snapshot_id=snapshot.id,
        policy_version="policy-v1",
        prompt_hash="prompt-v1",
        response_schema_hash="schema-v1",
        embedding_fingerprint={"model": "deterministic", "dimensions": 3},
        algorithm_version="algorithm-v1",
        verifier_version="verifier-v1",
    )
    context = repository.save_analysis_job_context(
        analysis_unit_id=unit.id,
        workflow_run_id="run-worker",
        research_plan_id="plan-worker",
        coverage_snapshot_id="coverage-worker",
        execution_authorization_id=None,
        manifest={
            "workflow_run_id": "run-worker",
            "scope_contract_id": "scope-worker",
            "execution_unit_id": "retrieval-worker",
            "attempt_no": 1,
            "execution_revision": 1,
            "packet_ids": [],
            "checkpoint_ids": [],
        },
    )
    queued = repository.create_analysis_attempt(unit.id)

    claimed = repository.claim_next_analysis_job(
        lease_owner="analysis-worker-1",
        lease_token="lease-1",
        lease_expires_at=datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc),
        now=datetime(2026, 8, 26, 9, 30, tzinfo=timezone.utc),
    )

    assert claimed is not None
    assert claimed.attempt.id == queued.id
    assert claimed.context == context
    assert (
        repository.claim_next_analysis_job(
            lease_owner="analysis-worker-2",
            lease_token="lease-2",
            lease_expires_at=datetime(2026, 8, 26, 10, 1, tzinfo=timezone.utc),
            now=datetime(2026, 8, 26, 9, 31, tzinfo=timezone.utc),
        )
        is None
    )

    expired = repository.recover_expired_analysis_jobs(
        now=datetime(2026, 8, 26, 10, 1, tzinfo=timezone.utc)
    )
    assert [item.id for item in expired] == [queued.id]
    terminal = repository.get_latest_attempt_for_unit(unit.id)
    assert terminal is not None
    assert terminal.id == queued.id
    assert terminal.state == "failed"
    assert terminal.attempt_no == 1

    assert (
        repository.claim_next_analysis_job(
            lease_owner="analysis-worker-2",
            lease_token="lease-2",
            lease_expires_at=datetime(2026, 8, 26, 10, 3, tzinfo=timezone.utc),
            now=datetime(2026, 8, 26, 10, 2, tzinfo=timezone.utc),
        )
        is None
    )
