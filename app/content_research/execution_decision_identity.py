"""Canonical, shared identity for one persisted coverage decision."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ExecutionDecisionIdentity:
    coverage_snapshot_id: str
    source_scope_contract_id: str
    resulting_scope_contract_id: str
    resolution: Literal["generate_limited_report", "expand_required_constraint", "relax_constraint"]
    target_constraint_id: str | None
    supplementary_queries: tuple[str, ...]
    schema: Literal["execution_decision_identity_v1"] = "execution_decision_identity_v1"

    @property
    def operation(self) -> Literal["limited_report", "supplementary_collection"]:
        return "limited_report" if self.resolution == "generate_limited_report" else "supplementary_collection"


def build_execution_decision_identity(
    *, coverage_snapshot_id: str, source_scope_contract_id: str,
    resulting_scope_contract_id: str, resolution: str,
    target_constraint_id: str | None, supplementary_queries: tuple[str, ...],
) -> tuple[ExecutionDecisionIdentity, dict[str, object], str]:
    if resolution not in {"generate_limited_report", "expand_required_constraint", "relax_constraint"}:
        raise ValueError("invalid execution decision resolution")
    if resolution == "generate_limited_report":
        if target_constraint_id is not None or supplementary_queries:
            raise ValueError("limited report has no target or supplementary queries")
    elif not target_constraint_id:
        raise ValueError("target constraint is required")
    identity = ExecutionDecisionIdentity(
        coverage_snapshot_id=coverage_snapshot_id, source_scope_contract_id=source_scope_contract_id,
        resulting_scope_contract_id=resulting_scope_contract_id, resolution=resolution,
        target_constraint_id=target_constraint_id, supplementary_queries=supplementary_queries,
    )
    payload = {"schema": identity.schema, "coverage_snapshot_id": identity.coverage_snapshot_id,
        "source_scope_contract_id": identity.source_scope_contract_id,
        "resulting_scope_contract_id": identity.resulting_scope_contract_id,
        "resolution": identity.resolution, "target_constraint_id": identity.target_constraint_id,
        "supplementary_queries": list(identity.supplementary_queries)}
    return identity, payload, hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
