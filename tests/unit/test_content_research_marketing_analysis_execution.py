from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from app.content_research.analysis_persistence import (
    ANALYSIS_UNIT_SCHEMA_VERSION,
    EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
    AnalysisAttempt,
    AnalysisJobClaim,
    AnalysisJobContext,
    AnalysisUnit,
    EvidenceSnapshot,
    FrozenEvidenceNoteInput,
)
from app.content_research.marketing_analysis_execution import (
    ANALYSIS_ALGORITHM_VERSION,
    ANALYSIS_POLICY_VERSION,
    ANALYSIS_PROMPT_HASH,
    ANALYSIS_RESPONSE_SCHEMA_HASH,
    ANALYSIS_VERIFIER_VERSION,
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
from app.content_research.research_embedding import (
    ResearchEmbeddingRuntime,
    ResearchEmbeddingUnavailableError,
    SentenceTransformerResearchEmbeddingAdapter,
)
from app.content_research.scope_contract import (
    ResearchScopeContract,
    ScopeExecutionContinuation,
    ScopeQueryGroup,
    supplementary_scope_query_group_id,
)

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def test_freeze_snapshot_accepts_authorization_owned_supplementary_query_provenance() -> None:
    scope = ResearchScopeContract(
        id="scope",
        workflow_run_id="run",
        research_plan_id="plan",
        version=1,
        schema_version="content_research_scope_contract_v1",
        constraints=(),
        query_groups=(
            ScopeQueryGroup(
                id="query-original",
                suggested_query="T恤",
                final_query="T恤",
                origin="system_suggested",
                execution_role="coverage",
            ),
        ),
        created_at=NOW,
    )
    supplementary_query = "T恤 补充样本"
    supplementary_id = supplementary_scope_query_group_id(
        scope_contract_id=scope.id,
        authorization_id="authorization",
        query=supplementary_query,
    )
    packet = DirectionalEvidencePacketRecord(
        "packet-supplementary",
        "directional-packet-v1",
        {
            "field_projection": {
                "title": "夏季 T恤凉感体验",
                "content_text": "这件 T恤在夏季通勤中穿着凉爽。",
                "source_url": "https://example.test/supplementary",
                "author_id": "author-supplementary",
            },
            "retrieval_context": {"query_group_ids": [supplementary_id]},
        },
        workflow_run_id="run",
        research_direction_id="product_marketing",
        canonical_source_id="source-supplementary",
        field_projection_hash="projection-supplementary",
        scope_contract_id=scope.id,
        execution_unit_id="execution-unit",
        attempt_no=1,
        execution_revision=2,
        created_at=NOW,
    )
    continuation = ScopeExecutionContinuation(
        id="continuation",
        authorization_id="authorization",
        workflow_run_id="run",
        execution_revision=2,
        operation="supplementary_collection",
        supplementary_queries=(supplementary_query,),
        state="running",
        execution_unit_id="execution-unit",
    )

    class CapturingRepository:
        captured: dict[str, object] | None = None

        def freeze_evidence_snapshot(self, **kwargs):
            self.captured = kwargs
            return SimpleNamespace(id="snapshot")

    store = SimpleNamespace(
        list_scope_contracts=lambda _run_id: [scope],
        list_scope_execution_authorizations=lambda _run_id: [
            SimpleNamespace(id="authorization", execution_unit_id="execution-unit")
        ],
        list_scope_execution_continuations=lambda _run_id: [continuation],
        get_typed_record=lambda _record_type, record_id: (
            packet if record_id == packet.id else None
        ),
    )
    repository = CapturingRepository()
    service = object.__new__(MarketingAnalysisExecutionService)
    service._store = store
    service._repository = repository

    service._freeze_snapshot(
        "run",
        CoverageManifest(
            workflow_run_id="run",
            scope_contract_id=scope.id,
            execution_unit_id="execution-unit",
            attempt_no=1,
            execution_revision=2,
            packet_ids=(packet.id,),
        ),
    )

    assert repository.captured is not None
    assert repository.captured["query_groups"][-1] == {
        "id": supplementary_id,
        "suggested_query": supplementary_query,
        "final_query": supplementary_query,
        "origin": "user_edited",
        "execution_role": "supplementary",
    }
    assert repository.captured["notes"][0].query_provenance == (supplementary_id,)


def _analysis_unit(
    *, snapshot: EvidenceSnapshot, embedding_fingerprint: dict[str, object]
) -> AnalysisUnit:
    return AnalysisUnit(
        id="unit-1",
        schema_version=ANALYSIS_UNIT_SCHEMA_VERSION,
        workflow_run_id="run",
        evidence_snapshot_id=snapshot.id,
        contract_fingerprint="contract",
        policy_version=f"{ANALYSIS_POLICY_VERSION}:policy-hash",
        prompt_hash=ANALYSIS_PROMPT_HASH,
        response_schema_hash=ANALYSIS_RESPONSE_SCHEMA_HASH,
        embedding_fingerprint=embedding_fingerprint,
        algorithm_version=ANALYSIS_ALGORITHM_VERSION,
        verifier_version=ANALYSIS_VERIFIER_VERSION,
        created_at=NOW,
    )


@pytest.mark.asyncio
async def test_embedding_failure_persists_safe_attempt_checkpoint() -> None:
    class BecomesUnavailableModel:
        def __init__(self) -> None:
            self.call_count = 0

        def encode(self, texts, *, convert_to_numpy, normalize_embeddings):
            assert convert_to_numpy is True
            assert normalize_embeddings is True
            self.call_count += 1
            if self.call_count == 1:
                return np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32)
            return np.asarray([[2.0, 0.0, 0.0] for _text in texts], dtype=np.float32)

    class CapturingRepository:
        def __init__(self) -> None:
            self.failure: dict[str, object] | None = None

        def get_completed_analysis_checkpoint(self, **_kwargs):
            return None

        def fail_analysis_checkpoint(self, **kwargs):
            self.failure = kwargs

    snapshot = EvidenceSnapshot(
        id="snapshot",
        schema_version=EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
        workflow_run_id="run",
        scope_contract_id="scope",
        retrieval_execution_unit_id="retrieval",
        retrieval_attempt_no=1,
        snapshot_fingerprint="snapshot-fingerprint",
        query_groups=({"id": "query", "query": "凉感T恤"},),
        notes=(),
        created_at=NOW,
    )
    atom = AtomicMarketingEvidence(
        atom_id="atom-1",
        claim_id="claim-1",
        track="value",
        note_id="note-1",
        account_id="account-1",
        field_path="content_text",
        quote="上身凉爽",
        text_start=0,
        text_end=4,
        polarity="supporting",
        scenes=("通勤",),
        audiences=(),
        aspect="凉感",
        evidence_type="experience",
    )
    attempt = AnalysisAttempt(
        id="attempt-1",
        analysis_unit_id="unit-1",
        attempt_no=1,
        state="running",
        successor_of_attempt_id=None,
        lease_owner="worker",
        lease_token="lease",
        lease_expires_at=NOW,
        created_at=NOW,
        terminal_at=None,
    )
    repository = CapturingRepository()
    model = BecomesUnavailableModel()
    runtime = ResearchEmbeddingRuntime(
        SentenceTransformerResearchEmbeddingAdapter(
            model_name="test-model",
            model_revision="test-revision",
            expected_dimensions=3,
            model_loader=lambda _name, _revision: model,
        )
    )
    ready_health = runtime.start()
    service = object.__new__(MarketingAnalysisExecutionService)
    service._repository = repository
    service._embedding_runtime = runtime

    with pytest.raises(
        ResearchEmbeddingUnavailableError,
        match="RESEARCH_EMBEDDING_NOT_NORMALIZED",
    ):
        await service._complete_shared_embedding_checkpoint(
            snapshot=snapshot,
            atoms=(atom,),
            analysis_unit_id="unit-1",
            embedding_fingerprint=ready_health.fingerprint.as_dict(),
            attempt=attempt,
            lease_token="lease",
        )

    assert repository.failure is not None
    assert runtime.health.status == "unavailable"
    assert repository.failure["error_code"] == "RESEARCH_EMBEDDING_NOT_NORMALIZED"
    assert repository.failure["private_result"] == {
        "trace": {
            "batch_count": 1,
            "success_count": 0,
            "failure_count": 1,
            "duration_ms": repository.failure["private_result"]["trace"]["duration_ms"],
            "dimensions": 3,
        }
    }

    repository.failure = None
    with pytest.raises(
        ResearchEmbeddingUnavailableError,
        match="RESEARCH_EMBEDDING_UNAVAILABLE",
    ):
        await service._complete_shared_embedding_checkpoint(
            snapshot=snapshot,
            atoms=(atom,),
            analysis_unit_id="unit-1",
            embedding_fingerprint=ready_health.fingerprint.as_dict(),
            attempt=SimpleNamespace(id="attempt-2"),
            lease_token="lease-2",
        )
    assert repository.failure is not None
    assert repository.failure["attempt_id"] == "attempt-2"
    assert repository.failure["error_code"] == "RESEARCH_EMBEDDING_UNAVAILABLE"


@pytest.mark.asyncio
async def test_execute_claimed_persists_embedding_failure_when_runtime_starts_unavailable(
    monkeypatch,
) -> None:
    class UnavailableModel:
        def encode(self, _texts, *, convert_to_numpy, normalize_embeddings):
            raise RuntimeError("loader detail token=secret")

    runtime = ResearchEmbeddingRuntime(
        SentenceTransformerResearchEmbeddingAdapter(
            model_name="test-model",
            model_revision="test-revision",
            expected_dimensions=3,
            model_loader=lambda _name, _revision: UnavailableModel(),
        )
    )
    unavailable_health = runtime.start()
    assert unavailable_health.status == "unavailable"
    snapshot = EvidenceSnapshot(
        id="snapshot",
        schema_version=EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
        workflow_run_id="run",
        scope_contract_id="scope",
        retrieval_execution_unit_id="retrieval",
        retrieval_attempt_no=1,
        snapshot_fingerprint="snapshot-fingerprint",
        query_groups=({"id": "query", "query": "凉感T恤"},),
        notes=(),
        created_at=NOW,
    )
    unit = _analysis_unit(
        snapshot=snapshot,
        embedding_fingerprint=unavailable_health.fingerprint.as_dict(),
    )
    attempt = AnalysisAttempt(
        id="attempt-unavailable",
        analysis_unit_id=unit.id,
        attempt_no=1,
        state="running",
        successor_of_attempt_id=None,
        lease_owner="worker",
        lease_token="lease",
        lease_expires_at=NOW,
        created_at=NOW,
        terminal_at=None,
    )

    class Repository:
        failure: dict[str, object] | None = None

        def get_analysis_unit(self, _analysis_unit_id):
            return unit

        def get_evidence_snapshot(self, _snapshot_id):
            return snapshot

        def get_completed_analysis_checkpoint(self, **_kwargs):
            return None

        def fail_analysis_checkpoint(self, **kwargs):
            self.failure = kwargs

    repository = Repository()
    service = object.__new__(MarketingAnalysisExecutionService)
    service._repository = repository
    service._embedding_runtime = runtime
    service._store = SimpleNamespace(
        get_run_policy_snapshot_for_workflow=lambda _run_id: SimpleNamespace(
            effective_policy_hash="policy-hash"
        )
    )

    async def completed_extraction(**_kwargs):
        return (
            AtomicMarketingEvidence(
                atom_id="atom-1",
                claim_id="claim-1",
                track="value",
                note_id="note-1",
                account_id="account-1",
                field_path="content_text",
                quote="凉爽",
                text_start=0,
                text_end=2,
                polarity="supporting",
                scenes=(),
                audiences=(),
                aspect="凉感",
                evidence_type="experience",
            ),
        )

    monkeypatch.setattr(service, "_complete_shared_extraction_checkpoint", completed_extraction)
    context = AnalysisJobContext(
        analysis_unit_id=unit.id,
        workflow_run_id="run",
        research_plan_id="plan",
        coverage_snapshot_id="coverage",
        execution_authorization_id=None,
        manifest={
            "workflow_run_id": "run",
            "scope_contract_id": "scope",
            "execution_unit_id": "retrieval",
            "attempt_no": 1,
            "execution_revision": 1,
        },
        created_at=NOW,
    )

    with pytest.raises(
        ResearchEmbeddingUnavailableError,
        match="RESEARCH_EMBEDDING_UNAVAILABLE",
    ):
        await service.execute_claimed(AnalysisJobClaim(context=context, attempt=attempt))

    assert repository.failure is not None
    assert repository.failure["attempt_id"] == attempt.id
    assert repository.failure["error_code"] == "RESEARCH_EMBEDDING_UNAVAILABLE"


def test_terminal_checkpoint_uses_durable_embedding_identity() -> None:
    fingerprint = {
        "provider": "sentence_transformers",
        "model": "test-model",
        "revision": "test-revision",
        "dimensions": 3,
        "normalization": "l2",
        "input_format_version": "v1",
    }
    decisions = [
        SimpleNamespace(
            track=track,
            state="insufficient",
            payload={
                "input_fingerprint": "contract",
                "execution": "completed",
                "decision": "no_publishable_conclusion",
                "publication_role": "omitted",
            },
        )
        for track in ("need", "value", "message")
    ]
    service = object.__new__(MarketingAnalysisExecutionService)
    service._embedding_runtime = None
    service._store = SimpleNamespace(
        list_marketing_conclusion_decisions=lambda _run, _plan: decisions
    )
    service._repository = SimpleNamespace(list_analysis_checkpoints=lambda _unit: ())
    snapshot = EvidenceSnapshot(
        id="snapshot",
        schema_version=EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
        workflow_run_id="run",
        scope_contract_id="scope",
        retrieval_execution_unit_id="retrieval",
        retrieval_attempt_no=1,
        snapshot_fingerprint="snapshot-fingerprint",
        query_groups=(),
        notes=(),
        created_at=NOW,
    )
    attempt = AnalysisAttempt(
        id="attempt",
        analysis_unit_id="unit",
        attempt_no=1,
        state="running",
        successor_of_attempt_id=None,
        lease_owner="worker",
        lease_token="lease",
        lease_expires_at=NOW,
        created_at=NOW,
        terminal_at=None,
    )

    checkpoint = service._build_terminal_checkpoint(
        workflow_run_id="run",
        research_plan_id="plan",
        manifest=CoverageManifest(
            workflow_run_id="run",
            scope_contract_id="scope",
            execution_unit_id="retrieval",
            attempt_no=1,
            execution_revision=1,
        ),
        snapshot=snapshot,
        attempt=attempt,
        contract_fingerprint="contract",
        embedding_fingerprint=fingerprint,
        projected_packet_ids=(),
    )

    assert checkpoint.payload["embedding"]["fingerprint"] == fingerprint


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
