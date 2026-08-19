"""Canonical, shared identity for one persisted coverage decision."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

DecisionResolution = Literal[
    "generate_limited_report",
    "expand_required_constraint",
    "relax_constraint",
]
DecisionOperation = Literal["limited_report", "supplementary_collection"]
IdentityState = Literal["canonical", "legacy_identity_incomplete"]


@dataclass(frozen=True)
class ExecutionDecisionIdentity:
    schema: Literal["execution_decision_identity_v1"]
    coverage_snapshot_id: str
    source_scope_contract_id: str
    resulting_scope_contract_id: str
    resolution: DecisionResolution
    target_constraint_id: str | None
    supplementary_queries: tuple[str, ...]

    @property
    def operation(self) -> DecisionOperation:
        """Execution mechanics are derived and never identity-bearing."""
        return _operation_for_resolution(self.resolution)


@dataclass(frozen=True)
class ExecutionDecisionIdentityResult:
    identity: ExecutionDecisionIdentity
    payload: dict[str, object]
    canonical_json: str
    decision_fingerprint: str
    execution_unit_id: str


@dataclass(frozen=True)
class LegacyDecisionInput:
    """The trusted fields reconstructed from legacy persisted records."""

    legacy_authorization_id: str
    coverage_snapshot_id: str
    source_scope_contract_id: str
    resulting_scope_contract_id: str
    resolution: str
    operation: str
    target_constraint_id: str | None
    supplementary_queries: tuple[str, ...]


@dataclass(frozen=True)
class LegacyDecisionIdentityResult:
    identity_state: IdentityState
    identity_schema: str
    identity_json: str
    decision_fingerprint: str
    execution_unit_id: str
    canonical: ExecutionDecisionIdentityResult | None


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _clean_queries(values: tuple[str, ...]) -> tuple[str, ...]:
    queries = tuple(" ".join(str(value).split()) for value in values)
    if any(not query for query in queries):
        raise ValueError("supplementary queries must be non-empty")
    if len(set(queries)) != len(queries):
        raise ValueError("supplementary queries must be distinct after normalization")
    return queries


def _operation_for_resolution(resolution: str) -> DecisionOperation:
    if resolution == "generate_limited_report":
        return "limited_report"
    if resolution in {"expand_required_constraint", "relax_constraint"}:
        return "supplementary_collection"
    raise ValueError("invalid execution decision resolution")


def build_execution_decision_identity(
    *,
    coverage_snapshot_id: str,
    source_scope_contract_id: str,
    resulting_scope_contract_id: str,
    resolution: str,
    target_constraint_id: str | None,
    supplementary_queries: tuple[str, ...],
) -> ExecutionDecisionIdentityResult:
    """Normalize, validate, serialize, and hash one complete decision."""
    if not all(
        value.strip()
        for value in (
            coverage_snapshot_id,
            source_scope_contract_id,
            resulting_scope_contract_id,
        )
    ):
        raise ValueError("execution decision identity fields must be non-empty")
    _operation_for_resolution(resolution)
    queries = _clean_queries(supplementary_queries)
    if resolution == "generate_limited_report":
        if target_constraint_id is not None or queries:
            raise ValueError("limited report has no target or supplementary queries")
    elif not target_constraint_id or not target_constraint_id.strip():
        raise ValueError("target constraint is required")
    elif resolution == "relax_constraint" and queries:
        raise ValueError("constraint relaxation has no supplementary queries")
    elif resolution == "expand_required_constraint" and not queries:
        raise ValueError("constraint expansion requires supplementary queries")
    if resolution == "relax_constraint":
        if resulting_scope_contract_id == source_scope_contract_id:
            raise ValueError("constraint relaxation requires a resulting scope")
    elif resulting_scope_contract_id != source_scope_contract_id:
        raise ValueError("non-relaxation decisions preserve the source scope")

    identity = ExecutionDecisionIdentity(
        schema="execution_decision_identity_v1",
        coverage_snapshot_id=coverage_snapshot_id.strip(),
        source_scope_contract_id=source_scope_contract_id.strip(),
        resulting_scope_contract_id=resulting_scope_contract_id.strip(),
        resolution=resolution,  # type: ignore[arg-type]
        target_constraint_id=target_constraint_id.strip() if target_constraint_id else None,
        supplementary_queries=queries,
    )
    payload = asdict(identity)
    payload["supplementary_queries"] = list(identity.supplementary_queries)
    canonical_json = _canonical_json(payload)
    fingerprint = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return ExecutionDecisionIdentityResult(
        identity=identity,
        payload=payload,
        canonical_json=canonical_json,
        decision_fingerprint=fingerprint,
        execution_unit_id="seu_" + fingerprint[:24],
    )


def build_legacy_execution_decision_identity(
    value: LegacyDecisionInput,
) -> LegacyDecisionIdentityResult:
    """Build a canonical alias, or an explicitly non-replayable legacy identity."""
    expected_operation = _operation_for_resolution(value.resolution)
    if value.operation != expected_operation:
        raise ValueError("legacy execution operation does not match resolution")
    if value.resolution == "generate_limited_report" or value.target_constraint_id:
        canonical = build_execution_decision_identity(
            coverage_snapshot_id=value.coverage_snapshot_id,
            source_scope_contract_id=value.source_scope_contract_id,
            resulting_scope_contract_id=value.resulting_scope_contract_id,
            resolution=value.resolution,
            target_constraint_id=value.target_constraint_id,
            supplementary_queries=value.supplementary_queries,
        )
        return LegacyDecisionIdentityResult(
            identity_state="canonical",
            identity_schema=canonical.identity.schema,
            identity_json=canonical.canonical_json,
            decision_fingerprint=canonical.decision_fingerprint,
            execution_unit_id=canonical.execution_unit_id,
            canonical=canonical,
        )

    incomplete_payload: dict[str, object] = {
        "schema": "execution_decision_identity_v1",
        "coverage_snapshot_id": value.coverage_snapshot_id,
        "source_scope_contract_id": value.source_scope_contract_id,
        "resulting_scope_contract_id": value.resulting_scope_contract_id,
        "resolution": value.resolution,
        "target_constraint_id": None,
        "supplementary_queries": list(_clean_queries(value.supplementary_queries)),
    }
    surrogate = hashlib.sha256(
        f"legacy-authorization:{value.legacy_authorization_id}".encode()
    ).hexdigest()
    return LegacyDecisionIdentityResult(
        identity_state="legacy_identity_incomplete",
        identity_schema="execution_decision_identity_v1",
        identity_json=_canonical_json(incomplete_payload),
        decision_fingerprint=surrogate,
        execution_unit_id="seu_legacy_" + surrogate[:17],
        canonical=None,
    )
