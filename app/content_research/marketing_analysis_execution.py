"""Reliable Task 3.1-B execution over one immutable evidence snapshot."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import timedelta

from app.content_research.admission.candidates import source_text_hash
from app.content_research.analysis import DirectionalAnalysisLLM, TrackedDirectionalAnalysisLLM
from app.content_research.analysis_persistence import (
    AnalysisAttempt,
    AnalysisJobClaim,
    AnalysisJobContext,
    AnalysisUnit,
    EvidenceSnapshot,
    FrozenEvidenceNoteInput,
    SQLiteMarketingAnalysisRepository,
)
from app.content_research.contracts import admission_author_identity
from app.content_research.marketing_conclusion_analysis import (
    MARKETING_CONCLUSION_SYSTEM_PROMPT,
    MarketingConclusionAnalysisError,
    MarketingConclusionAnalysisService,
)
from app.content_research.marketing_conclusions import (
    MARKETING_CONCLUSION_TRACKS,
    MarketingConclusionTrackEvaluation,
    evaluate_marketing_conclusions,
)
from app.content_research.marketing_evidence import (
    AtomicMarketingEvidence,
    cluster_atomic_marketing_evidence,
    verify_marketing_candidate,
)
from app.content_research.marketing_evidence_extraction import (
    MARKETING_EVIDENCE_EXTRACTION_PROMPT,
    MARKETING_EVIDENCE_EXTRACTION_RESPONSE_FORMAT,
    MarketingEvidenceExtractionService,
    deserialize_atoms,
    project_snapshot_analysis_inputs,
    serialize_atoms,
)
from app.content_research.models import utcnow
from app.content_research.persistence_models import (
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
    CoverageManifest,
    DirectionalEvidencePacketRecord,
    MarketingConclusionCandidateRecord,
    MarketingConclusionDecisionRecord,
    StageCheckpointRecord,
)
from app.content_research.research_embedding import (
    ResearchEmbeddingDocument,
    ResearchEmbeddingRuntime,
    ResearchEmbeddingUnavailableError,
)
from app.content_research.runtime import canonical_fingerprint
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.services.llm.failures import LLMProviderFailure

ANALYSIS_POLICY_VERSION = "marketing_analysis_policy_v1"
ANALYSIS_PROMPT_HASH = source_text_hash(
    MARKETING_EVIDENCE_EXTRACTION_PROMPT + "\n" + MARKETING_CONCLUSION_SYSTEM_PROMPT
)
ANALYSIS_RESPONSE_SCHEMA_HASH = source_text_hash(
    "marketing_conclusion_candidate_v2\n"
    + json.dumps(
        MARKETING_EVIDENCE_EXTRACTION_RESPONSE_FORMAT,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
)
ANALYSIS_ALGORITHM_VERSION = "atomic_qualifier_cluster_v2"
ANALYSIS_VERIFIER_VERSION = "groundedness_counter_evidence_v2"
ANALYSIS_LEASE_SECONDS = 120


@dataclass(frozen=True)
class MarketingAnalysisExecutionResult:
    evidence_snapshot: EvidenceSnapshot
    attempt: AnalysisAttempt
    checkpoint: StageCheckpointRecord
    reused_tracks: tuple[str, ...]


@dataclass(frozen=True)
class MarketingTrackExecutionOutput:
    candidates: tuple[MarketingConclusionCandidateRecord, ...]
    decision: MarketingConclusionDecisionRecord


class MarketingAnalysisExecutionError(RuntimeError):
    """At least one planned track failed technically; publication is forbidden."""

    def __init__(self, attempt_id: str, failures: Mapping[str, str]) -> None:
        super().__init__("one or more planned marketing analysis tracks failed")
        self.attempt_id = attempt_id
        self.failures = dict(failures)


class AnalysisContractIncompatibleError(RuntimeError):
    """A retry tried to reinterpret a frozen Snapshot under a new contract."""


@dataclass(frozen=True)
class MarketingAnalysisPreparation:
    context: AnalysisJobContext
    attempt: AnalysisAttempt
    snapshot: EvidenceSnapshot
    unit: AnalysisUnit


class MarketingAnalysisExecutionService:
    """Own snapshot, attempt, per-track checkpoint, and terminal gating."""

    def __init__(
        self,
        *,
        store: SQLiteContentResearchStore,
        llm: DirectionalAnalysisLLM | None,
        embedding_runtime: ResearchEmbeddingRuntime | None,
        llm_scope: Mapping[str, object] | None = None,
    ) -> None:
        self._store = store
        self._repository = SQLiteMarketingAnalysisRepository(store._db_path)
        self._llm = (
            TrackedDirectionalAnalysisLLM(llm=llm, db_path=store._db_path)
            if llm is not None
            else None
        )
        self._embedding_runtime = embedding_runtime
        self._llm_scope = llm_scope

    async def execute(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str,
        manifest: CoverageManifest,
    ) -> MarketingAnalysisExecutionResult:
        effective = await asyncio.to_thread(
            self._repository.get_effective_attempt_for_run, workflow_run_id
        )
        if effective is not None and effective.state == "succeeded":
            unit = await asyncio.to_thread(
                self._repository.get_analysis_unit, effective.analysis_unit_id
            )
            context = await asyncio.to_thread(
                self._repository.get_analysis_job_context,
                effective.analysis_unit_id,
            )
            if unit is None or context is None:
                raise RuntimeError("succeeded analysis is missing durable context")
            snapshot = await asyncio.to_thread(
                self._repository.get_evidence_snapshot, unit.evidence_snapshot_id
            )
            if snapshot is None:
                raise RuntimeError("succeeded analysis is missing its evidence snapshot")
            frozen_manifest = self._manifest_from_context(context)
            checkpoint = await asyncio.to_thread(
                self._load_terminal_checkpoint,
                workflow_run_id,
                research_plan_id,
                frozen_manifest,
                unit.contract_fingerprint,
            )
            return MarketingAnalysisExecutionResult(
                snapshot,
                effective,
                checkpoint,
                MARKETING_CONCLUSION_TRACKS,
            )
        preparation = await self.prepare(
            workflow_run_id=workflow_run_id,
            research_plan_id=research_plan_id,
            coverage_snapshot_id=(
                "coverage_"
                + canonical_fingerprint(asdict(manifest))[:24]
            ),
            execution_authorization_id=None,
            manifest=manifest,
        )
        if preparation.attempt.state == "succeeded":
            checkpoint = await asyncio.to_thread(
                self._load_terminal_checkpoint,
                workflow_run_id,
                research_plan_id,
                manifest,
                preparation.unit.contract_fingerprint,
            )
            return MarketingAnalysisExecutionResult(
                preparation.snapshot,
                preparation.attempt,
                checkpoint,
                MARKETING_CONCLUSION_TRACKS,
            )
        if preparation.attempt.state != "queued":
            raise RuntimeError("marketing analysis attempt is already active")
        token = uuid.uuid4().hex
        attempt = await asyncio.to_thread(
            self._repository.claim_analysis_attempt,
            preparation.attempt.id,
            lease_owner=f"marketing-analysis:{uuid.uuid4().hex}",
            lease_token=token,
            lease_expires_at=utcnow() + timedelta(seconds=ANALYSIS_LEASE_SECONDS),
        )
        return await self.execute_claimed(
            AnalysisJobClaim(context=preparation.context, attempt=attempt)
        )

    async def prepare(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str,
        coverage_snapshot_id: str,
        execution_authorization_id: str | None,
        manifest: CoverageManifest,
    ) -> MarketingAnalysisPreparation:
        policy = self._store.get_run_policy_snapshot_for_workflow(workflow_run_id)
        if policy is None:
            raise ValueError("marketing analysis requires the frozen run policy")
        snapshot = await asyncio.to_thread(self._freeze_snapshot, workflow_run_id, manifest)
        embedding_fingerprint = self._embedding_fingerprint()
        unit = await asyncio.to_thread(
            self._repository.get_or_create_analysis_unit,
            evidence_snapshot_id=snapshot.id,
            policy_version=f"{ANALYSIS_POLICY_VERSION}:{policy.effective_policy_hash}",
            prompt_hash=ANALYSIS_PROMPT_HASH,
            response_schema_hash=ANALYSIS_RESPONSE_SCHEMA_HASH,
            embedding_fingerprint=embedding_fingerprint,
            algorithm_version=ANALYSIS_ALGORITHM_VERSION,
            verifier_version=ANALYSIS_VERIFIER_VERSION,
        )
        context = await asyncio.to_thread(
            self._repository.save_analysis_job_context,
            analysis_unit_id=unit.id,
            workflow_run_id=workflow_run_id,
            research_plan_id=research_plan_id,
            coverage_snapshot_id=coverage_snapshot_id,
            execution_authorization_id=execution_authorization_id,
            manifest=asdict(manifest),
        )
        latest = await asyncio.to_thread(self._repository.get_latest_attempt_for_unit, unit.id)
        if latest is not None and latest.state == "succeeded":
            return MarketingAnalysisPreparation(context, latest, snapshot, unit)
        if latest is not None:
            return MarketingAnalysisPreparation(context, latest, snapshot, unit)
        attempt = await asyncio.to_thread(
            self._repository.create_analysis_attempt,
            unit.id,
        )
        return MarketingAnalysisPreparation(context, attempt, snapshot, unit)

    async def execute_claimed(
        self, claim: AnalysisJobClaim
    ) -> MarketingAnalysisExecutionResult:
        attempt = claim.attempt
        token = str(attempt.lease_token or "")
        if attempt.state != "running" or not token:
            raise RuntimeError("marketing analysis requires a claimed attempt")
        unit = await asyncio.to_thread(
            self._repository.get_analysis_unit, attempt.analysis_unit_id
        )
        if unit is None:
            raise RuntimeError("marketing analysis unit disappeared")
        snapshot = await asyncio.to_thread(
            self._repository.get_evidence_snapshot, unit.evidence_snapshot_id
        )
        if snapshot is None:
            raise RuntimeError("marketing analysis evidence snapshot disappeared")
        manifest = self._manifest_from_context(claim.context)
        workflow_run_id = claim.context.workflow_run_id
        research_plan_id = claim.context.research_plan_id
        policy = self._store.get_run_policy_snapshot_for_workflow(workflow_run_id)
        if policy is None:
            raise ValueError("marketing analysis requires the frozen run policy")
        self._require_compatible_contract(unit, policy.effective_policy_hash)
        atoms = await self._complete_shared_extraction_checkpoint(
            snapshot=snapshot,
            analysis_unit_id=unit.id,
            attempt=attempt,
            lease_token=token,
        )
        vectors = await self._complete_shared_embedding_checkpoint(
            snapshot=snapshot,
            atoms=atoms,
            analysis_unit_id=unit.id,
            attempt=attempt,
            lease_token=token,
        )
        admitted_claims, packets = project_snapshot_analysis_inputs(
            snapshot,
            atoms,
            policy_snapshot_id=policy.id,
            policy_snapshot_hash=policy.effective_policy_hash,
            manifest=manifest,
        )
        await asyncio.to_thread(
            self._persist_projected_analysis_inputs,
            admitted_claims,
            packets,
        )
        failures: dict[str, str] = {}
        reused_tracks: list[str] = []
        try:
            for track in MARKETING_CONCLUSION_TRACKS:
                track_input = canonical_fingerprint(
                    {
                        "contract": unit.contract_fingerprint,
                        "snapshot": snapshot.snapshot_fingerprint,
                        "track": track,
                        "admitted_claim_ids": [claim.id for _decision, claim in admitted_claims],
                    }
                )
                existing = await asyncio.to_thread(
                    self._repository.get_completed_analysis_checkpoint,
                    analysis_unit_id=unit.id,
                    track=track,
                    stage="verifier",
                    input_fingerprint=track_input,
                )
                if existing is not None:
                    reused_tracks.append(track)
                    continue
                await asyncio.to_thread(
                    self._repository.renew_analysis_attempt,
                    attempt.id,
                    lease_token=token,
                    lease_expires_at=utcnow() + timedelta(seconds=ANALYSIS_LEASE_SECONDS),
                )
                try:
                    output = await self._execute_track(
                        workflow_run_id=workflow_run_id,
                        research_plan_id=research_plan_id,
                        track=track,
                        policy=policy.effective_policy,
                        contract_fingerprint=unit.contract_fingerprint,
                        admitted_claims=admitted_claims,
                        packets=packets,
                        atoms=atoms,
                        vectors=vectors,
                    )
                    await asyncio.to_thread(
                        self._repository.complete_analysis_track,
                        analysis_unit_id=unit.id,
                        attempt_id=attempt.id,
                        lease_token=token,
                        track=track,
                        input_fingerprint=track_input,
                        candidates=output.candidates,
                        decision=output.decision,
                        result_checksum=canonical_fingerprint(
                            {
                                "decision_id": output.decision.id,
                                "payload": output.decision.payload,
                            }
                        ),
                    )
                except (LLMProviderFailure, MarketingConclusionAnalysisError, ResearchEmbeddingUnavailableError) as exc:
                    failure_code, failure_detail = self._safe_failure(exc)
                    failures[track] = failure_code
                    await asyncio.to_thread(
                        self._persist_failed_track,
                        workflow_run_id=workflow_run_id,
                        research_plan_id=research_plan_id,
                        track=track,
                        contract_fingerprint=unit.contract_fingerprint,
                        attempt_id=attempt.id,
                        failure_code=failure_code,
                        failure_detail=failure_detail,
                    )
            if failures:
                raise MarketingAnalysisExecutionError(attempt.id, failures)
            checkpoint = await asyncio.to_thread(
                self._build_terminal_checkpoint,
                workflow_run_id=workflow_run_id,
                research_plan_id=research_plan_id,
                manifest=manifest,
                snapshot=snapshot,
                attempt=attempt,
                contract_fingerprint=unit.contract_fingerprint,
                projected_packet_ids=tuple(sorted(packets)),
            )
            attempt, checkpoint = await asyncio.to_thread(
                self._repository.succeed_analysis_attempt_with_checkpoint,
                attempt.id,
                lease_token=token,
                checkpoint=checkpoint,
            )
            return MarketingAnalysisExecutionResult(
                snapshot, attempt, checkpoint, tuple(reused_tracks)
            )
        except MarketingAnalysisExecutionError:
            raise
        except Exception:
            raise

    def _persist_projected_analysis_inputs(
        self,
        admitted_claims: tuple[
            tuple[ClaimAdmissionDecisionRecord, ClaimCandidateRecord], ...
        ],
        packets: Mapping[str, DirectionalEvidencePacketRecord],
    ) -> None:
        """Materialize analysis evidence into the governed report authority.

        Projection identifiers are deterministic over the frozen Snapshot.  A
        retry may therefore encounter an already-materialized prefix after a
        crash; exact replays are skipped while identity collisions fail closed.
        """

        def save_once(record: object, save) -> None:
            existing = self._store.get_typed_record(type(record), record.id)
            if existing is None:
                save(record)
                return
            expected = asdict(record)
            actual = asdict(existing)
            expected.pop("created_at", None)
            actual.pop("created_at", None)
            if actual != expected:
                raise AnalysisContractIncompatibleError(
                    "ANALYSIS_PROJECTED_EVIDENCE_CONFLICT"
                )

        for packet in sorted(packets.values(), key=lambda item: item.id):
            save_once(packet, self._store.save_directional_evidence_packet)
        for decision, candidate in admitted_claims:
            save_once(candidate, self._store.save_claim_candidate)
            save_once(decision, self._store.save_claim_admission_decision)

    async def assert_retry_compatible(self, analysis_unit_id: str) -> AnalysisUnit:
        unit = await asyncio.to_thread(
            self._repository.get_analysis_unit, analysis_unit_id
        )
        if unit is None:
            raise AnalysisContractIncompatibleError(
                "ANALYSIS_CONTRACT_INCOMPATIBLE"
            )
        policy = self._store.get_run_policy_snapshot_for_workflow(unit.workflow_run_id)
        if policy is None:
            raise AnalysisContractIncompatibleError(
                "ANALYSIS_CONTRACT_INCOMPATIBLE"
            )
        self._require_compatible_contract(unit, policy.effective_policy_hash)
        return unit

    def _require_compatible_contract(
        self, unit: AnalysisUnit, effective_policy_hash: str
    ) -> None:
        if (
            unit.policy_version
            != f"{ANALYSIS_POLICY_VERSION}:{effective_policy_hash}"
            or unit.prompt_hash != ANALYSIS_PROMPT_HASH
            or unit.response_schema_hash != ANALYSIS_RESPONSE_SCHEMA_HASH
            or unit.embedding_fingerprint != self._embedding_fingerprint()
            or unit.algorithm_version != ANALYSIS_ALGORITHM_VERSION
            or unit.verifier_version != ANALYSIS_VERIFIER_VERSION
        ):
            raise AnalysisContractIncompatibleError(
                "ANALYSIS_CONTRACT_INCOMPATIBLE"
            )

    @staticmethod
    def _manifest_from_context(context: AnalysisJobContext) -> CoverageManifest:
        return CoverageManifest(
            workflow_run_id=str(context.manifest["workflow_run_id"]),
            scope_contract_id=str(context.manifest["scope_contract_id"]),
            execution_unit_id=(
                str(context.manifest["execution_unit_id"])
                if context.manifest.get("execution_unit_id")
                else None
            ),
            attempt_no=int(context.manifest["attempt_no"]),
            execution_revision=int(context.manifest["execution_revision"]),
            packet_ids=tuple(context.manifest.get("packet_ids") or ()),
            checkpoint_ids=tuple(context.manifest.get("checkpoint_ids") or ()),
        )

    def _freeze_snapshot(
        self, workflow_run_id: str, manifest: CoverageManifest
    ) -> EvidenceSnapshot:
        retrieval_execution_unit_id = manifest.execution_unit_id or (
            "dispatch_" + canonical_fingerprint({"workflow_run_id": workflow_run_id})[:24]
        )
        retrieval_attempt_no = manifest.attempt_no if manifest.attempt_no >= 1 else 1
        scope = next(
            (
                item
                for item in self._store.list_scope_contracts(workflow_run_id)
                if item.id == manifest.scope_contract_id
            ),
            None,
        )
        if scope is None:
            raise ValueError("marketing analysis Scope contract was not found")
        query_groups = tuple(asdict(group) for group in scope.query_groups)
        allowed_query_ids = {str(group["id"]) for group in query_groups}
        frozen: dict[str, FrozenEvidenceNoteInput] = {}
        for packet_id in manifest.packet_ids:
            packet = self._store.get_typed_record(DirectionalEvidencePacketRecord, packet_id)
            if (
                packet is None
                or packet.research_direction_id != "product_marketing"
                or not manifest.owns(packet)
            ):
                continue
            projection = dict(packet.payload.get("field_projection") or {})
            retrieval = dict(packet.payload.get("retrieval_context") or {})
            query_ids = tuple(
                sorted(
                    {
                        str(item)
                        for item in retrieval.get("query_group_ids") or ()
                        if str(item) in allowed_query_ids
                    }
                )
            )
            if not query_ids:
                raise ValueError("evidence packet is missing frozen query provenance")
            account_id = admission_author_identity(projection)
            source_url = str(projection.get("source_url") or "")
            if not account_id or not source_url:
                raise ValueError("evidence packet is missing account or source identity")
            note = FrozenEvidenceNoteInput(
                note_id=packet.canonical_source_id,
                account_id=account_id,
                title=str(projection.get("title") or ""),
                body=str(projection.get("content_text") or ""),
                source_url=source_url,
                captured_at=packet.created_at,
                query_provenance=query_ids,
            )
            existing = frozen.get(note.note_id)
            if existing is not None and existing != note:
                merged_queries = tuple(sorted({*existing.query_provenance, *note.query_provenance}))
                note = FrozenEvidenceNoteInput(
                    note_id=existing.note_id,
                    account_id=existing.account_id,
                    title=existing.title,
                    body=existing.body,
                    source_url=existing.source_url,
                    captured_at=existing.captured_at,
                    query_provenance=merged_queries,
                )
            frozen[note.note_id] = note
        return self._repository.freeze_evidence_snapshot(
            workflow_run_id=workflow_run_id,
            scope_contract_id=manifest.scope_contract_id,
            retrieval_execution_unit_id=retrieval_execution_unit_id,
            retrieval_attempt_no=retrieval_attempt_no,
            query_groups=query_groups,
            notes=tuple(frozen.values()),
        )

    def _embedding_fingerprint(self) -> dict[str, object]:
        if self._embedding_runtime is None:
            raise ResearchEmbeddingUnavailableError("RESEARCH_EMBEDDING_UNAVAILABLE")
        health = self._embedding_runtime.health
        if health.status != "ready":
            raise ResearchEmbeddingUnavailableError(
                health.error_code or "RESEARCH_EMBEDDING_UNAVAILABLE"
            )
        return health.fingerprint.as_dict()

    async def _complete_shared_extraction_checkpoint(
        self,
        *,
        snapshot: EvidenceSnapshot,
        analysis_unit_id: str,
        attempt: AnalysisAttempt,
        lease_token: str,
    ) -> tuple[AtomicMarketingEvidence, ...]:
        input_fingerprint = canonical_fingerprint(
            {
                "snapshot": snapshot.snapshot_fingerprint,
                "stage": "structured_extraction",
                "prompt": source_text_hash(MARKETING_EVIDENCE_EXTRACTION_PROMPT),
                "response_schema": source_text_hash(
                    json.dumps(
                        MARKETING_EVIDENCE_EXTRACTION_RESPONSE_FORMAT,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                ),
            }
        )
        existing = await asyncio.to_thread(
            self._repository.get_completed_analysis_checkpoint,
            analysis_unit_id=analysis_unit_id,
            track="shared",
            stage="structured_extraction",
            input_fingerprint=input_fingerprint,
        )
        if existing is not None:
            return deserialize_atoms(existing.private_result)
        if snapshot.notes:
            if self._llm is None:
                raise LLMProviderFailure(
                    "llm_configuration_scope_missing",
                    "模型配置作用域不可用",
                    True,
                    None,
                )
            atoms = await MarketingEvidenceExtractionService(
                llm=self._llm,
                llm_scope=self._llm_scope,
            ).extract(snapshot)
        else:
            atoms = ()
        private_result = serialize_atoms(atoms)
        checksum = canonical_fingerprint(private_result)
        await asyncio.to_thread(
            self._repository.complete_analysis_checkpoint,
            analysis_unit_id=analysis_unit_id,
            attempt_id=attempt.id,
            lease_token=lease_token,
            track="shared",
            stage="structured_extraction",
            input_fingerprint=input_fingerprint,
            output_refs=tuple(atom.atom_id for atom in atoms),
            result_checksum=checksum,
            private_result=private_result,
        )
        return atoms

    async def _complete_shared_embedding_checkpoint(
        self,
        *,
        snapshot: EvidenceSnapshot,
        atoms: tuple[AtomicMarketingEvidence, ...],
        analysis_unit_id: str,
        attempt: AnalysisAttempt,
        lease_token: str,
    ) -> dict[str, tuple[float, ...]]:
        input_fingerprint = canonical_fingerprint(
            {
                "snapshot": snapshot.snapshot_fingerprint,
                "stage": "embedding",
                "atoms": [atom.atom_id for atom in atoms],
                "embedding": self._embedding_fingerprint(),
            }
        )
        existing = await asyncio.to_thread(
            self._repository.get_completed_analysis_checkpoint,
            analysis_unit_id=analysis_unit_id,
            track="shared",
            stage="embedding",
            input_fingerprint=input_fingerprint,
        )
        if existing is not None:
            raw_vectors = existing.private_result.get("vectors")
            if not isinstance(raw_vectors, dict):
                raise RuntimeError("analysis embedding checkpoint is missing vectors")
            return {
                str(atom_id): tuple(float(value) for value in values)
                for atom_id, values in raw_vectors.items()
                if isinstance(values, list)
            }
        if atoms:
            assert self._embedding_runtime is not None
            batch = await asyncio.to_thread(
                self._embedding_runtime.embed_documents,
                tuple(
                    ResearchEmbeddingDocument(atom.atom_id, atom.aspect, atom.quote)
                    for atom in atoms
                ),
            )
            output_refs = batch.input_fingerprints
            vectors = {
                atom_id: tuple(vector)
                for atom_id, vector in zip(batch.document_ids, batch.vectors, strict=True)
            }
            checksum = hashlib.sha256(
                json.dumps(batch.vectors, separators=(",", ":")).encode()
            ).hexdigest()
        else:
            output_refs = ()
            vectors = {}
            checksum = hashlib.sha256(b"[]").hexdigest()
        await asyncio.to_thread(
            self._repository.complete_analysis_checkpoint,
            analysis_unit_id=analysis_unit_id,
            attempt_id=attempt.id,
            lease_token=lease_token,
            track="shared",
            stage="embedding",
            input_fingerprint=input_fingerprint,
            output_refs=output_refs,
            result_checksum=checksum,
            private_result={"vectors": {key: list(value) for key, value in vectors.items()}},
        )
        return vectors

    async def _execute_track(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str,
        track: str,
        policy: Mapping[str, object],
        contract_fingerprint: str,
        admitted_claims: tuple[tuple[ClaimAdmissionDecisionRecord, ClaimCandidateRecord], ...],
        packets: Mapping[str, DirectionalEvidencePacketRecord],
        atoms: tuple[AtomicMarketingEvidence, ...],
        vectors: Mapping[str, tuple[float, ...]],
    ) -> MarketingTrackExecutionOutput:
        if admitted_claims:
            if self._llm is None:
                raise LLMProviderFailure(
                    "llm_configuration_scope_missing",
                    "模型配置作用域不可用",
                    True,
                    None,
                )
            generated = await MarketingConclusionAnalysisService(
                llm=self._llm, llm_scope=self._llm_scope
            ).generate(
                workflow_run_id=workflow_run_id,
                research_plan_id=research_plan_id,
                policy=policy,
                admitted_claims=admitted_claims,
                track=track,
            )
        else:
            generated = ()
        clusters = cluster_atomic_marketing_evidence(atoms, vectors)
        verifications = {
            candidate.id: verify_marketing_candidate(
                candidate, atoms=atoms, clusters=clusters
            )
            for candidate in generated
        }
        governed_candidates = tuple(
            candidate
            for candidate in generated
            if verifications[candidate.id].state != "failed"
        )
        evaluation = evaluate_marketing_conclusions(
            candidates=governed_candidates,
            admitted_claims=admitted_claims,
            packets=packets,
            policy=policy,
        ).tracks[track]
        if generated and not governed_candidates:
            evaluation = MarketingConclusionTrackEvaluation(
                "insufficient_evidence",
                None,
                0,
                0,
                0,
                ("groundedness_verifier_rejected",),
                verifier_state="rejected",
            )
        elif evaluation.candidate_id is not None:
            verification = verifications[evaluation.candidate_id]
            atom_by_id = {atom.atom_id: atom for atom in atoms}
            counter_claim_ids = tuple(
                sorted(
                    {
                        atom_by_id[atom_id].claim_id
                        for atom_id in verification.counter_atom_ids
                    }
                )
            )
            evaluation = replace(
                evaluation,
                state=(
                    "contested"
                    if evaluation.state == "selected"
                    and verification.state == "contested"
                    else evaluation.state
                ),
                verifier_state=verification.state,
                cluster_ids=verification.cluster_ids,
                supporting_atom_ids=verification.supporting_atom_ids,
                counter_atom_ids=verification.counter_atom_ids,
                counter_claim_ids=counter_claim_ids,
                counter_note_count=verification.counter_note_count,
                counter_author_count=verification.counter_author_count,
                reason_codes=tuple(
                    sorted(
                        {
                            *evaluation.reason_codes,
                            *verification.reason_codes,
                        }
                    )
                ),
            )
        decision = self._build_completed_track(
            workflow_run_id=workflow_run_id,
            research_plan_id=research_plan_id,
            track=track,
            evaluation=evaluation,
            contract_fingerprint=contract_fingerprint,
        )
        return MarketingTrackExecutionOutput(tuple(generated), decision)

    def _build_completed_track(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str,
        track: str,
        evaluation: MarketingConclusionTrackEvaluation,
        contract_fingerprint: str,
    ) -> MarketingConclusionDecisionRecord:
        decision = (
            evaluation.state
            if evaluation.state in {"selected", "directional", "contested"}
            else "no_publishable_conclusion"
        )
        compatibility_state = (
            decision
            if decision in {"selected", "directional", "contested"}
            else "insufficient_evidence"
        )
        role = (
            "verified"
            if decision == "selected"
            else "verified_with_limits"
            if decision == "contested"
            else "lead"
            if decision == "directional"
            else "omitted"
        )
        decision_id = "mcd_" + canonical_fingerprint(
            {"contract": contract_fingerprint, "track": track, "decision": decision}
        )[:24]
        record = MarketingConclusionDecisionRecord(
            decision_id,
            "marketing_conclusion_decision_v2",
            {
                "input_fingerprint": contract_fingerprint,
                "execution": "completed",
                "decision": decision,
                "publication_role": role,
                "reason_codes": list(evaluation.reason_codes),
                "supporting_note_count": evaluation.supporting_note_count,
                "independent_author_count": evaluation.independent_author_count,
                "body_quote_note_count": evaluation.body_quote_note_count,
                "additional_qualified_count": 0,
                "verifier_state": evaluation.verifier_state,
                "cluster_ids": list(evaluation.cluster_ids),
                "supporting_atom_ids": list(evaluation.supporting_atom_ids),
                "counter_atom_ids": list(evaluation.counter_atom_ids),
                "counter_claim_ids": list(evaluation.counter_claim_ids),
                "counter_note_count": evaluation.counter_note_count,
                "counter_author_count": evaluation.counter_author_count,
            },
            workflow_run_id=workflow_run_id,
            research_plan_id=research_plan_id,
            candidate_id=evaluation.candidate_id,
            track=track,
            state=compatibility_state,
        )
        return record

    def _persist_failed_track(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str,
        track: str,
        contract_fingerprint: str,
        attempt_id: str,
        failure_code: str,
        failure_detail: str | None,
    ) -> None:
        decision_id = "mcd_" + canonical_fingerprint(
            {"attempt": attempt_id, "track": track, "decision": "analysis_failed"}
        )[:24]
        self._store.save_marketing_conclusion_decision(
            MarketingConclusionDecisionRecord(
                decision_id,
                "marketing_conclusion_decision_v2",
                {
                    "input_fingerprint": contract_fingerprint,
                    "analysis_attempt_id": attempt_id,
                    "execution": "failed",
                    "decision": "analysis_failed",
                    "publication_role": "omitted",
                    "reason_codes": ["marketing_analysis_unavailable"],
                    "failure_code": failure_code,
                    **(
                        {"failure_detail": failure_detail}
                        if failure_detail is not None
                        else {}
                    ),
                    "recovery_action": "repair_model_configuration_and_resume",
                },
                workflow_run_id=workflow_run_id,
                research_plan_id=research_plan_id,
                candidate_id=None,
                track=track,
                state="analysis_unavailable",
            )
        )

    def _build_terminal_checkpoint(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str,
        manifest: CoverageManifest,
        snapshot: EvidenceSnapshot,
        attempt: AnalysisAttempt,
        contract_fingerprint: str,
        projected_packet_ids: tuple[str, ...],
    ) -> StageCheckpointRecord:
        decisions = {
            item.track: item
            for item in self._store.list_marketing_conclusion_decisions(
                workflow_run_id, research_plan_id
            )
            if item.payload.get("input_fingerprint") == contract_fingerprint
            and item.payload.get("execution") == "completed"
        }
        if set(decisions) != set(MARKETING_CONCLUSION_TRACKS):
            raise RuntimeError("TRACK_COVERAGE_INCONSISTENT")
        tracks = {
            track: {
                "execution": "completed",
                "decision": decisions[track].payload["decision"],
                "publication_role": decisions[track].payload["publication_role"],
                "state": decisions[track].state,
                "supporting_note_count": int(
                    decisions[track].payload.get("supporting_note_count") or 0
                ),
                "independent_author_count": int(
                    decisions[track].payload.get("independent_author_count") or 0
                ),
                "counter_note_count": int(
                    decisions[track].payload.get("counter_note_count") or 0
                ),
                "counter_author_count": int(
                    decisions[track].payload.get("counter_author_count") or 0
                ),
                "verifier_state": decisions[track].payload.get("verifier_state"),
                "reason_codes": list(decisions[track].payload.get("reason_codes") or ()),
            }
            for track in MARKETING_CONCLUSION_TRACKS
        }
        status = (
            "completed"
            if any(
                item["decision"] in {"selected", "contested"}
                for item in tracks.values()
            )
            else "insufficient"
        )
        checkpoint = StageCheckpointRecord(
            "scp_" + canonical_fingerprint(
                {"run": workflow_run_id, "stage": "marketing_conclusion", "input": contract_fingerprint}
            )[:24],
            "content_research_stage_checkpoint_v1",
            {
                "schema_version": "content_research_marketing_conclusion_checkpoint_v2",
                "analysis_attempt_id": attempt.id,
                "analysis_attempt_no": attempt.attempt_no,
                "analysis_contract_fingerprint": contract_fingerprint,
                "evidence_snapshot_id": snapshot.id,
                "evidence_snapshot_fingerprint": snapshot.snapshot_fingerprint,
                "retrieval_execution_unit_id": snapshot.retrieval_execution_unit_id,
                "projected_packet_ids": list(projected_packet_ids),
                "embedding": {
                    "fingerprint": self._embedding_fingerprint(),
                    "document_count": len(snapshot.notes),
                },
                "tracks": tracks,
            },
            workflow_run_id=workflow_run_id,
            subagent_task_id=f"marketing-conclusion:{research_plan_id}",
            stage_name="marketing_conclusion",
            input_fingerprint=contract_fingerprint,
            status=status,
            retry_count=attempt.attempt_no - 1,
            started_at=attempt.created_at,
            finished_at=utcnow(),
            scope_contract_id=manifest.scope_contract_id,
            execution_unit_id=manifest.execution_unit_id,
            attempt_no=manifest.attempt_no,
            execution_revision=manifest.execution_revision,
        )
        return checkpoint

    def _load_terminal_checkpoint(
        self,
        workflow_run_id: str,
        research_plan_id: str,
        manifest: CoverageManifest,
        contract_fingerprint: str,
    ) -> StageCheckpointRecord:
        checkpoint_id = "scp_" + canonical_fingerprint(
            {"run": workflow_run_id, "stage": "marketing_conclusion", "input": contract_fingerprint}
        )[:24]
        checkpoint = self._store.get_typed_record(StageCheckpointRecord, checkpoint_id)
        if checkpoint is None or not manifest.matches(checkpoint):
            raise RuntimeError("TRACK_COVERAGE_INCONSISTENT")
        return checkpoint

    @staticmethod
    def _safe_failure(exc: Exception) -> tuple[str, str | None]:
        if isinstance(exc, LLMProviderFailure):
            return exc.code, None
        if isinstance(exc, MarketingConclusionAnalysisError):
            return "llm_protocol_incompatible", exc.detail_code
        return "RESEARCH_EMBEDDING_UNAVAILABLE", None
