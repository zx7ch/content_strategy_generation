"""Build governed direction results from immutable admission decisions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.content_research.persistence_models import (
    ClaimAdmissionDecisionRecord,
    DirectionResultDecisionRecord,
    WeakSignalRecord,
)


@dataclass(frozen=True)
class DirectionAdmissionOutput:
    direction_result: DirectionResultDecisionRecord
    weak_signals: tuple[WeakSignalRecord, ...]


def build_direction_result(*, direction_id: str, policy_snapshot_id: str, decisions: list[ClaimAdmissionDecisionRecord]) -> DirectionAdmissionOutput:
    admitted = [item for item in decisions if item.decision == "admitted"]
    weak: list[WeakSignalRecord] = []
    for decision in decisions:
        if decision.decision == "admitted":
            continue
        weak_id = "ws_" + hashlib.sha256(decision.id.encode()).hexdigest()[:24]
        weak.append(WeakSignalRecord(weak_id, "content_research_weak_signal_v1", {"schema_version": "content_research_weak_signal_v1", "claim_candidate_id": decision.claim_candidate_id, "reason_codes": decision.payload.get("reason_codes", []), "recovery_action": decision.payload.get("recovery_action")}, admission_decision_id=decision.id))
    state = "formal_directional_result" if admitted else "insufficient_evidence"
    result_id = "drd_" + hashlib.sha256(f"{direction_id}:{policy_snapshot_id}".encode()).hexdigest()[:24]
    result = DirectionResultDecisionRecord(result_id, "content_research_direction_result_v1", {"schema_version": "content_research_direction_result_v1", "state": state, "admitted_claim_ids": [item.claim_candidate_id for item in admitted], "weak_signal_ids": [item.id for item in weak], "limitations": [code for item in decisions for code in item.payload.get("reason_codes", [])], "recovery_actions": [item.payload.get("recovery_action") for item in decisions if item.payload.get("recovery_action")]}, research_direction_id=direction_id, policy_snapshot_id=policy_snapshot_id)
    return DirectionAdmissionOutput(result, tuple(weak))
