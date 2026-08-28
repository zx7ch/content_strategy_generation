from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.content_research.analysis_persistence import (
    ANALYSIS_UNIT_SCHEMA_VERSION,
    EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
    AnalysisAttempt,
    AnalysisJobContext,
    AnalysisUnit,
    EvidenceSnapshot,
    FrozenEvidenceNoteInput,
)
from app.content_research.marketing_analysis_execution import (
    MarketingAnalysisExecutionService,
)
from app.content_research.marketing_evidence import AtomicMarketingEvidence
from app.content_research.marketing_evidence_extraction import (
    project_snapshot_analysis_inputs,
)
from app.content_research.persistence_models import (
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
    CoverageManifest,
    DirectionalEvidencePacketRecord,
)

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


class FailedAttemptRepository:
    def __init__(
        self,
        *,
        unit: AnalysisUnit,
        context: AnalysisJobContext,
        attempt: AnalysisAttempt,
    ) -> None:
        self.unit = unit
        self.context = context
        self.attempt = attempt
        self.successor_created = False

    def get_or_create_analysis_unit(self, **_kwargs):
        return self.unit

    def save_analysis_job_context(self, **_kwargs):
        return self.context

    def get_latest_attempt_for_unit(self, analysis_unit_id: str):
        assert analysis_unit_id == self.unit.id
        return self.attempt

    def recover_expired_analysis_jobs(self, **_kwargs):
        raise AssertionError("prepare must not own lease recovery")

    def create_analysis_attempt(self, *_args, **_kwargs):
        self.successor_created = True
        raise AssertionError("only an explicit retry command may create a successor")


@pytest.mark.asyncio
async def test_prepare_does_not_create_successor_for_failed_attempt() -> None:
    snapshot = EvidenceSnapshot(
        id="snapshot",
        schema_version=EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
        workflow_run_id="run",
        scope_contract_id="scope",
        retrieval_execution_unit_id="retrieval",
        retrieval_attempt_no=1,
        snapshot_fingerprint="snapshot-fingerprint",
        query_groups=({"id": "query", "query": "T恤"},),
        notes=(),
        created_at=NOW,
    )
    unit = AnalysisUnit(
        id="unit",
        schema_version=ANALYSIS_UNIT_SCHEMA_VERSION,
        workflow_run_id="run",
        evidence_snapshot_id=snapshot.id,
        contract_fingerprint="contract",
        policy_version="policy",
        prompt_hash="prompt",
        response_schema_hash="schema",
        embedding_fingerprint={"model": "test"},
        algorithm_version="algorithm",
        verifier_version="verifier",
        created_at=NOW,
    )
    context = AnalysisJobContext(
        analysis_unit_id=unit.id,
        workflow_run_id="run",
        research_plan_id="plan",
        coverage_snapshot_id="coverage",
        execution_authorization_id=None,
        manifest={},
        created_at=NOW,
    )
    failed = AnalysisAttempt(
        id="attempt-failed",
        analysis_unit_id=unit.id,
        attempt_no=1,
        state="failed",
        successor_of_attempt_id=None,
        lease_owner="worker",
        lease_token="lease",
        lease_expires_at=NOW,
        created_at=NOW,
        terminal_at=NOW,
    )
    repository = FailedAttemptRepository(unit=unit, context=context, attempt=failed)
    service = object.__new__(MarketingAnalysisExecutionService)
    service._store = SimpleNamespace(
        get_run_policy_snapshot_for_workflow=lambda _run_id: SimpleNamespace(
            effective_policy_hash="policy-hash"
        )
    )
    service._repository = repository
    service._freeze_snapshot = lambda _run_id, _manifest: snapshot
    service._embedding_fingerprint = lambda: {"model": "test"}

    preparation = await service.prepare(
        workflow_run_id="run",
        research_plan_id="plan",
        coverage_snapshot_id="coverage",
        execution_authorization_id=None,
        manifest=CoverageManifest(
            workflow_run_id="run",
            scope_contract_id="scope",
            execution_unit_id="retrieval",
            attempt_no=1,
            execution_revision=1,
        ),
    )

    assert preparation.attempt.id == failed.id
    assert preparation.attempt.state == "failed"
    assert repository.successor_created is False


def test_projected_analysis_evidence_is_persisted_idempotently() -> None:
    snapshot = EvidenceSnapshot(
        id="snapshot",
        schema_version=EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
        workflow_run_id="run",
        scope_contract_id="scope",
        retrieval_execution_unit_id="retrieval",
        retrieval_attempt_no=1,
        snapshot_fingerprint="snapshot-fingerprint",
        query_groups=({"id": "query", "query": "夏季凉感T恤"},),
        notes=(
            FrozenEvidenceNoteInput(
                note_id="source-1",
                account_id="id:author-1",
                title="夏季凉感T恤",
                body="上身凉爽，通勤不闷。",
                source_url="https://example.test/source-1",
                captured_at=NOW,
                query_provenance=("query",),
            ),
        ),
        created_at=NOW,
    )
    atoms = (
        AtomicMarketingEvidence(
            atom_id="atom-1",
            claim_id="claim-1",
            track="value",
            note_id="source-1",
            account_id="id:author-1",
            field_path="content_text",
            quote="上身凉爽",
            text_start=0,
            text_end=4,
            polarity="supporting",
            scenes=("通勤",),
            audiences=(),
            aspect="凉感",
            evidence_type="experience",
        ),
    )
    admitted, packets = project_snapshot_analysis_inputs(
        snapshot,
        atoms,
        policy_snapshot_id="policy",
        policy_snapshot_hash="policy-hash",
        manifest=CoverageManifest(
            workflow_run_id="run",
            scope_contract_id="scope",
            execution_unit_id=None,
            attempt_no=0,
            execution_revision=1,
        ),
    )

    class CapturingStore:
        def __init__(self) -> None:
            self.records: dict[tuple[type, str], object] = {}
            self.saved: list[object] = []

        def get_typed_record(self, record_type, record_id):
            return self.records.get((record_type, record_id))

        def _save(self, record):
            self.records[(type(record), record.id)] = record
            self.saved.append(record)
            return record

        save_directional_evidence_packet = _save
        save_claim_candidate = _save
        save_claim_admission_decision = _save

    store = CapturingStore()
    service = object.__new__(MarketingAnalysisExecutionService)
    service._store = store

    service._persist_projected_analysis_inputs(admitted, packets)
    service._persist_projected_analysis_inputs(admitted, packets)

    assert [type(item) for item in store.saved] == [
        DirectionalEvidencePacketRecord,
        ClaimCandidateRecord,
        ClaimAdmissionDecisionRecord,
    ]
    packet, candidate, _decision = store.saved
    assert packet.execution_unit_id is None
    assert packet.attempt_no == 0
    assert candidate.execution_unit_id is None
    assert candidate.attempt_no == 0
