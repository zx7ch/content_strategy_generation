"""Read-only, deterministic governance across admitted directional claims."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.content_research.admission.governance_keys import GovernanceKey, derive_governance_key
from app.content_research.persistence_models import (
    AggregateClaimRecord,
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
    CrossDirectionRecord,
    DirectionalEvidencePacketRecord,
    StageCheckpointRecord,
)
from app.content_research.runtime import canonical_fingerprint
from app.content_research.stores.base import ContentResearchStore


@dataclass(frozen=True)
class ActionHypothesisRequest:
    statement: str
    claim_ids: tuple[str, ...]
    derivation_method: str = "explicit_action_hypothesis"
    request_origin: str = "user_requested_next_steps"


@dataclass(frozen=True)
class GovernedClaim:
    candidate: ClaimCandidateRecord
    decision: ClaimAdmissionDecisionRecord
    canonical_source_id: str
    governance_key: GovernanceKey | None

    @property
    def scope(self) -> dict[str, Any]:
        return dict(self.candidate.payload.get("scope") or {})


@dataclass(frozen=True)
class GovernanceOutput:
    overlaps: tuple[CrossDirectionRecord, ...]
    contradictions: tuple[CrossDirectionRecord, ...]
    aggregates: tuple[AggregateClaimRecord, ...]
    replayed: bool


class CrossDirectionGovernanceService:
    """Produces append-only records without changing direction-level evidence."""

    def __init__(self, store: ContentResearchStore) -> None:
        self._store = store

    def execute(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str,
        subagent_task_id: str,
        action_hypotheses: Iterable[ActionHypothesisRequest] = (),
    ) -> GovernanceOutput:
        snapshot = self._store.get_run_policy_snapshot_for_workflow(workflow_run_id)
        if snapshot is None or snapshot.research_plan_id != research_plan_id:
            raise ValueError("governance requires the run's frozen policy snapshot")
        claims = self._admitted_claims(workflow_run_id, snapshot.effective_policy)
        hypotheses = tuple(action_hypotheses)
        fingerprint = canonical_fingerprint({
            "workflow_run_id": workflow_run_id,
            "research_plan_id": research_plan_id,
            "claims": [(item.candidate.id, item.decision.id) for item in claims],
            "hypotheses": [(item.statement, item.claim_ids, item.derivation_method) for item in hypotheses],
        })
        checkpoint_id = _checkpoint_id(workflow_run_id, subagent_task_id, "aggregate", fingerprint)
        existing = self._store.get_typed_record(StageCheckpointRecord, checkpoint_id)
        if existing is not None:
            records = [
                self._store.get_typed_record(CrossDirectionRecord, item)
                for item in existing.payload.get("cross_direction_record_ids", [])
            ]
            aggregates = [
                self._store.get_typed_record(AggregateClaimRecord, item)
                for item in existing.payload.get("aggregate_claim_ids", [])
            ]
            return GovernanceOutput(
                tuple(item for item in records if item and item.record_type == "overlap"),
                tuple(item for item in records if item and item.record_type == "contradiction"),
                tuple(item for item in aggregates if item),
                True,
            )

        reconcile_started_at = _utcnow()
        overlaps = self._overlaps(research_plan_id, workflow_run_id, claims)
        contradictions = self._contradictions(research_plan_id, workflow_run_id, claims)
        for record in (*overlaps, *contradictions):
            if self._store.get_typed_record(CrossDirectionRecord, record.id) is None:
                self._store.save_cross_direction_record(record)
        reconcile_fingerprint = canonical_fingerprint({"aggregate_input": fingerprint, "kind": "reconcile"})
        self._save_checkpoint(
            workflow_run_id, subagent_task_id, "reconcile", reconcile_fingerprint,
            {"cross_direction_record_ids": [item.id for item in (*overlaps, *contradictions)]},
            started_at=reconcile_started_at,
        )

        aggregate_started_at = _utcnow()
        aggregates = self._aggregates(
            research_plan_id, workflow_run_id, claims, contradictions, hypotheses,
        )
        for record in aggregates:
            if self._store.get_typed_record(AggregateClaimRecord, record.id) is None:
                self._store.save_aggregate_claim(record)
        self._save_checkpoint(
            workflow_run_id, subagent_task_id, "aggregate", fingerprint,
            {
                "cross_direction_record_ids": [item.id for item in (*overlaps, *contradictions)],
                "aggregate_claim_ids": [item.id for item in aggregates],
            },
            started_at=aggregate_started_at,
        )
        return GovernanceOutput(overlaps, contradictions, aggregates, False)

    def _admitted_claims(self, workflow_run_id: str, policy: dict[str, Any]) -> list[GovernedClaim]:
        candidates = {
            item.id: item for item in self._store.list_typed_records(ClaimCandidateRecord)
            if item.workflow_run_id == workflow_run_id
        }
        packets = {
            item.id: item for item in self._store.list_typed_records(DirectionalEvidencePacketRecord)
            if item.workflow_run_id == workflow_run_id
        }
        result: list[GovernedClaim] = []
        for decision in self._store.list_typed_records(ClaimAdmissionDecisionRecord):
            candidate = candidates.get(decision.claim_candidate_id)
            if decision.decision != "admitted" or candidate is None:
                continue
            packet = packets.get(candidate.evidence_packet_id)
            if packet is not None:
                result.append(GovernedClaim(candidate, decision, packet.canonical_source_id, derive_governance_key(candidate, policy)))
        return sorted(result, key=lambda item: item.candidate.id)

    def _overlaps(self, plan_id: str, run_id: str, claims: list[GovernedClaim]) -> tuple[CrossDirectionRecord, ...]:
        by_source: dict[str, list[GovernedClaim]] = {}
        for claim in claims:
            by_source.setdefault(claim.canonical_source_id, []).append(claim)
        records: list[CrossDirectionRecord] = []
        for source_id, members in by_source.items():
            if len({item.candidate.research_direction_id for item in members}) < 2:
                continue
            claim_ids = tuple(sorted(item.candidate.id for item in members))
            records.append(_relationship_record(
                plan_id, run_id, "overlap", claim_ids, (source_id,), "shared_canonical_source",
                "same_canonical_source_across_directions",
                governance_keys=tuple(item.governance_key for item in members if item.governance_key),
            ))
        return tuple(records)

    def _contradictions(self, plan_id: str, run_id: str, claims: list[GovernedClaim]) -> tuple[CrossDirectionRecord, ...]:
        by_key: dict[str, list[GovernedClaim]] = {}
        for claim in claims:
            key = claim.governance_key.reconciliation_key if claim.governance_key else None
            polarity = claim.governance_key.polarity if claim.governance_key else None
            if key and polarity in {"positive", "negative"}:
                by_key.setdefault(key, []).append(claim)
        records: list[CrossDirectionRecord] = []
        for key, members in by_key.items():
            positive = [item for item in members if item.governance_key and item.governance_key.polarity == "positive"]
            negative = [item for item in members if item.governance_key and item.governance_key.polarity == "negative"]
            if not positive or not negative:
                continue
            selected = tuple(item for item in (*positive, *negative) if item.candidate.research_direction_id != "")
            if len({item.candidate.research_direction_id for item in selected}) < 2 or len({item.canonical_source_id for item in selected}) < 2:
                continue
            records.append(_relationship_record(
                plan_id, run_id, "contradiction", tuple(sorted(item.candidate.id for item in selected)),
                tuple(sorted({item.canonical_source_id for item in selected})),
                "explicit_opposed_polarity", f"explicit_reconciliation_key:{key}",
                governance_keys=tuple(item.governance_key for item in selected if item.governance_key),
            ))
        return tuple(records)

    def _aggregates(self, plan_id: str, run_id: str, claims: list[GovernedClaim], contradictions: tuple[CrossDirectionRecord, ...], hypotheses: Iterable[ActionHypothesisRequest]) -> tuple[AggregateClaimRecord, ...]:
        by_key: dict[str, list[GovernedClaim]] = {}
        for claim in claims:
            key = claim.governance_key.aggregate_key if claim.governance_key else None
            if key:
                by_key.setdefault(key, []).append(claim)
        records: list[AggregateClaimRecord] = []
        for key, members in by_key.items():
            source_ids = tuple(sorted({item.canonical_source_id for item in members}))
            if len(source_ids) < 2:
                continue
            records.append(_aggregate_record(plan_id, run_id, "cross_direction_corroboration", tuple(sorted(item.candidate.id for item in members)), source_ids, "shared_explicit_aggregate_key", f"Independent admitted observations share aggregate key: {key}", members))
        by_id = {item.candidate.id: item for item in claims}
        for record in contradictions:
            members = [by_id[item] for item in record.payload["claim_ids"]]
            records.append(_aggregate_record(plan_id, run_id, "cross_direction_tension", tuple(record.payload["claim_ids"]), tuple(record.payload["canonical_source_ids"]), "explicit_contradiction_record", "Admitted claims contain an explicit unresolved tension.", members))
        for request in hypotheses:
            members = [by_id[item] for item in request.claim_ids if item in by_id]
            if len(members) != len(request.claim_ids) or not request.statement.strip():
                raise ValueError("action hypothesis requires admitted claim ids and a statement")
            records.append(_aggregate_record(plan_id, run_id, "action_hypothesis", request.claim_ids, tuple(sorted({item.canonical_source_id for item in members})), request.derivation_method, request.statement, members, hypothesis_only=True, request_origin=request.request_origin))
        return tuple(records)

    def _save_checkpoint(self, run_id: str, task_id: str, stage: str, fingerprint: str, payload: dict[str, Any], *, started_at: datetime | None = None) -> None:
        record_id = _checkpoint_id(run_id, task_id, stage, fingerprint)
        if self._store.get_typed_record(StageCheckpointRecord, record_id) is None:
            self._store.save_stage_checkpoint(StageCheckpointRecord(record_id, "content_research_stage_checkpoint_v1", {"workflow_run_id": run_id, **payload}, workflow_run_id=run_id, subagent_task_id=task_id, stage_name=stage, input_fingerprint=fingerprint, status="completed", started_at=started_at, finished_at=_utcnow() if started_at else None))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _relationship_record(
    plan_id: str, run_id: str, record_type: str, claim_ids: tuple[str, ...], source_ids: tuple[str, ...],
    classification: str, reason: str, *, governance_keys: tuple[GovernanceKey, ...] = (),
) -> CrossDirectionRecord:
    record_id = "cdr_" + canonical_fingerprint({"plan": plan_id, "run": run_id, "type": record_type, "claims": claim_ids, "reason": reason})[:24]
    payload = {"schema_version": "content_research_cross_direction_record_v1", "workflow_run_id": run_id, "claim_ids": list(claim_ids), "canonical_source_ids": list(source_ids), "classification": classification, "reason": reason, "resolution_state": "open"}
    if governance_keys:
        payload["governance_keys"] = [
            {"governance_key_version": item.version, "aggregate_key": item.aggregate_key,
             "reconciliation_key": item.reconciliation_key, "reconciliation_polarity": item.polarity,
             "source_field_path": item.source_field_path, "literal_evidence_ref": item.literal_evidence_ref}
            for item in governance_keys
        ]
    return CrossDirectionRecord(record_id, "content_research_cross_direction_record_v1", payload, research_plan_id=plan_id, record_type=record_type)


def _aggregate_record(plan_id: str, run_id: str, aggregate_type: str, claim_ids: tuple[str, ...], source_ids: tuple[str, ...], method: str, statement: str, claims: list[GovernedClaim], *, hypothesis_only: bool = False, request_origin: str | None = None) -> AggregateClaimRecord:
    limitations = sorted({code for item in claims for code in item.decision.payload.get("reason_codes", [])} | ({"hypothesis_only"} if hypothesis_only else set()))
    record_id = "ac_" + canonical_fingerprint({"plan": plan_id, "run": run_id, "type": aggregate_type, "claims": claim_ids, "method": method, "statement": statement})[:24]
    keys = [item.governance_key for item in claims if item.governance_key]
    payload = {"schema_version": "content_research_aggregate_claim_v1", "workflow_run_id": run_id, "statement": statement, "source_claim_ids": list(claim_ids), "canonical_source_ids": list(source_ids), "derivation_method": method, "scope_intersection": {}, "inherited_limitations": limitations, "hypothesis_only": hypothesis_only, "governance_keys": [{"governance_key_version": item.version, "aggregate_key": item.aggregate_key, "reconciliation_key": item.reconciliation_key, "reconciliation_polarity": item.polarity, "source_field_path": item.source_field_path, "literal_evidence_ref": item.literal_evidence_ref} for item in keys]}
    if request_origin is not None:
        payload["request_origin"] = request_origin
    return AggregateClaimRecord(record_id, "content_research_aggregate_claim_v1", payload, research_plan_id=plan_id, aggregate_type=aggregate_type)


def _checkpoint_id(run_id: str, task_id: str, stage: str, fingerprint: str) -> str:
    return "scp_" + canonical_fingerprint({"run": run_id, "task": task_id, "stage": stage, "input": fingerprint})[:24]
