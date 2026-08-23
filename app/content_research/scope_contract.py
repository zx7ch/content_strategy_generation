"""Immutable, user-confirmed execution scope for Lite Content Research."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Literal

from app.content_research.models import utcnow
from app.content_research.persistence_models import CoverageManifest

SCOPE_CONTRACT_SCHEMA_VERSION_V1 = "content_research_scope_contract_v1"
SCOPE_CONTRACT_SCHEMA_VERSION_V2 = "content_research_scope_contract_v2"
SCOPE_CONTRACT_SCHEMA_VERSION = SCOPE_CONTRACT_SCHEMA_VERSION_V1
MAX_LITE_QUERY_GROUPS = 3

ConstraintMode = Literal["required", "preferred"]
QueryOrigin = Literal["system_suggested", "user_edited"]
QueryExecutionRole = Literal["coverage", "supplementary", "exploratory"]


@dataclass(frozen=True)
class DispatchLeaseContext:
    """Exact ownership of one normal formal-research dispatch claim."""

    workflow_run_id: str
    lease_owner: str
    lease_token: str

    def __post_init__(self) -> None:
        if not all((self.workflow_run_id, self.lease_owner, self.lease_token)):
            raise ValueError("dispatch lease context requires workflow, owner, and token")


@dataclass(frozen=True)
class ScopeConstraint:
    id: str
    label: str
    value: str
    mode: ConstraintMode
    allowed_aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.label.strip() or not self.value.strip():
            raise ValueError("scope constraint id, label, and value are required")
        if self.mode not in {"required", "preferred"}:
            raise ValueError("invalid scope constraint mode")
        if any(not alias.strip() for alias in self.allowed_aliases):
            raise ValueError("scope constraint aliases must be non-empty")


@dataclass(frozen=True)
class ScopeQueryGroupInput:
    suggested_query: str
    final_query: str
    targeted_required_terms: tuple[str, ...] = ()
    origin: QueryOrigin | None = None


@dataclass(frozen=True)
class ScopeQueryGroup:
    id: str
    suggested_query: str
    final_query: str
    origin: QueryOrigin
    execution_role: QueryExecutionRole


@dataclass(frozen=True)
class ResearchScopeDraft:
    id: str
    workflow_run_id: str
    research_plan_id: str
    structure_hash: str
    constraints: tuple[ScopeConstraint, ...]
    query_groups: tuple[ScopeQueryGroupInput, ...]
    created_at: datetime
    schema_version: str = SCOPE_CONTRACT_SCHEMA_VERSION_V1
    core_object: str = ""
    product_experience_aspect: str | None = None
    context_audience_aspect: str | None = None


@dataclass(frozen=True)
class ScopeDraftConfirmation:
    draft_id: str
    scope_contract_id: str
    workflow_run_id: str
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not all(
            (self.draft_id.strip(), self.scope_contract_id.strip(), self.workflow_run_id.strip())
        ):
            raise ValueError("scope draft confirmation identity is required")


@dataclass(frozen=True)
class ScopeDraftAuditEvent:
    id: str
    workflow_run_id: str
    scope_draft_id: str
    event_name: str
    payload: dict[str, object]
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not all((self.id.strip(), self.workflow_run_id.strip(), self.scope_draft_id.strip())):
            raise ValueError("scope draft audit event identity is required")
        if self.event_name != "scope_suggested":
            raise ValueError("invalid scope draft audit event")
        if self.payload.get("schema_version") != "content_research_scope_audit_event_v1":
            raise ValueError("scope draft audit event payload schema version is required")


@dataclass(frozen=True)
class ResearchScopeContract:
    id: str
    workflow_run_id: str
    research_plan_id: str
    version: int
    schema_version: str
    constraints: tuple[ScopeConstraint, ...]
    query_groups: tuple[ScopeQueryGroup, ...]
    created_at: datetime


@dataclass(frozen=True)
class ScopeAuditEvent:
    id: str
    workflow_run_id: str
    scope_contract_id: str
    scope_contract_version: int
    event_name: str
    payload: dict[str, object]
    metadata: dict[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not all(
            (
                self.id.strip(),
                self.workflow_run_id.strip(),
                self.scope_contract_id.strip(),
                self.event_name.strip(),
            )
        ):
            raise ValueError("scope audit event identity is required")
        if self.scope_contract_version < 1:
            raise ValueError("scope audit event version must be positive")
        if self.payload.get("schema_version") != "content_research_scope_audit_event_v1":
            raise ValueError("scope audit event payload schema version is required")


@dataclass(frozen=True)
class CoverageSnapshot:
    id: str
    workflow_run_id: str
    scope_contract_id: str
    scope_contract_version: int
    state: Literal["satisfied", "awaiting_scope_decision"]
    constraint_counts: dict[str, dict[str, object]]
    unmet_constraint_ids: tuple[str, ...]
    execution_revision: int = 1
    execution_authorization_id: str | None = None
    source_coverage_snapshot_id: str | None = None
    manifest: CoverageManifest | None = None
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not all(
            (
                self.id.strip(),
                self.workflow_run_id.strip(),
                self.scope_contract_id.strip(),
            )
        ):
            raise ValueError("coverage snapshot identity is required")
        if self.scope_contract_version < 1:
            raise ValueError("coverage snapshot version must be positive")
        if self.execution_revision < 1:
            raise ValueError("coverage execution revision must be positive")
        if self.state not in {"satisfied", "awaiting_scope_decision"}:
            raise ValueError("invalid coverage snapshot state")
        if self.manifest is not None and (
            self.manifest.workflow_run_id != self.workflow_run_id
            or self.manifest.scope_contract_id != self.scope_contract_id
            or self.manifest.execution_revision != self.execution_revision
        ):
            raise ValueError("coverage manifest lineage does not match its snapshot")


@dataclass(frozen=True)
class ScopeExecutionAuthorization:
    """Append-only authorization for the execution selected from a coverage decision."""

    id: str
    workflow_run_id: str
    scope_contract_id: str
    scope_contract_version: int
    coverage_snapshot_id: str
    resolution: Literal[
        "expand_required_constraint",
        "generate_limited_report",
        "relax_constraint",
    ]
    execution_revision: int
    state: Literal["authorized_collection", "authorized_limited_report"]
    created_at: datetime = field(default_factory=utcnow)
    execution_unit_id: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not all(
            (
                self.id.strip(),
                self.workflow_run_id.strip(),
                self.scope_contract_id.strip(),
                self.coverage_snapshot_id.strip(),
            )
        ):
            raise ValueError("scope execution authorization identity is required")
        if self.scope_contract_version < 1 or self.execution_revision < 1:
            raise ValueError("scope execution authorization revisions must be positive")
        if self.resolution not in {
            "expand_required_constraint",
            "generate_limited_report",
            "relax_constraint",
        }:
            raise ValueError("invalid scope execution authorization resolution")
        expected_state = (
            "authorized_limited_report"
            if self.resolution == "generate_limited_report"
            else "authorized_collection"
        )
        if self.state != expected_state:
            raise ValueError("invalid scope execution authorization state")


@dataclass(frozen=True)
class ScopeExecutionContinuation:
    """Durable executable command owned by one coverage authorization."""

    id: str
    authorization_id: str
    workflow_run_id: str
    execution_revision: int
    operation: Literal["limited_report", "supplementary_collection"]
    supplementary_queries: tuple[str, ...]
    state: Literal["pending", "running", "completed", "failed"]
    created_at: datetime = field(default_factory=utcnow)
    lease_token: str | None = field(default=None, compare=False, repr=False)
    execution_unit_id: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not all(
            (
                self.id.strip(),
                self.authorization_id.strip(),
                self.workflow_run_id.strip(),
            )
        ):
            raise ValueError("scope execution continuation identity is required")
        if self.execution_revision < 1:
            raise ValueError("scope execution continuation revision must be positive")
        if self.operation not in {"limited_report", "supplementary_collection"}:
            raise ValueError("invalid scope execution continuation operation")
        if self.state not in {"pending", "running", "completed", "failed"}:
            raise ValueError("invalid scope execution continuation state")
        queries = tuple(" ".join(query.split()) for query in self.supplementary_queries)
        if any(not query for query in queries) or len(set(queries)) != len(queries):
            raise ValueError("scope execution continuation queries must be distinct and non-empty")
        if self.operation == "limited_report" and queries:
            raise ValueError("limited report continuation does not accept queries")
        object.__setattr__(self, "supplementary_queries", queries)


ExecutionUnitState = Literal["pending", "running", "completed", "failed", "outcome_unknown"]
ExecutionFactKind = Literal[
    "decision_accepted",
    "attempt_claimed",
    "provider_request_recorded",
    "provider_outcome_recorded",
    "lease_fenced",
    "coverage_persisted",
    "publication_persisted",
    "outcome_unknown",
]


@dataclass(frozen=True)
class ScopeExecutionUnit:
    """One immutable, user-accepted coverage decision and its authorized work."""

    id: str
    workflow_run_id: str
    scope_contract_id: str
    coverage_snapshot_id: str
    resolution: Literal["expand_required_constraint", "generate_limited_report", "relax_constraint"]
    operation: Literal["limited_report", "supplementary_collection"]
    state: ExecutionUnitState
    identity_schema: str = "execution_decision_identity_v1"
    identity_json: str = ""
    identity_state: Literal["canonical", "legacy_identity_incomplete"] = "canonical"
    legacy_authorization_id: str | None = None
    latest_provider_state: str | None = field(default=None, compare=False, repr=False)
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not all(
            (
                self.id.strip(),
                self.workflow_run_id.strip(),
                self.scope_contract_id.strip(),
                self.coverage_snapshot_id.strip(),
            )
        ):
            raise ValueError("scope execution unit identity is required")
        if self.resolution not in {
            "expand_required_constraint",
            "generate_limited_report",
            "relax_constraint",
        }:
            raise ValueError("invalid scope execution unit resolution")
        if self.operation not in {"limited_report", "supplementary_collection"}:
            raise ValueError("invalid scope execution unit operation")
        if self.state not in {"pending", "running", "completed", "failed", "outcome_unknown"}:
            raise ValueError("invalid scope execution unit state")
        if self.identity_schema != "execution_decision_identity_v1":
            raise ValueError("invalid scope execution unit identity schema")
        if self.identity_state not in {"canonical", "legacy_identity_incomplete"}:
            raise ValueError("invalid scope execution unit identity state")
        if self.identity_state == "canonical" and not self.identity_json:
            raise ValueError("canonical scope execution unit identity is required")

    @property
    def recovery_state(
        self,
    ) -> Literal["replayable", "outcome_unknown", "manual_recovery_required"]:
        if self.identity_state != "canonical":
            return "manual_recovery_required"
        if self.state == "outcome_unknown":
            return "outcome_unknown"
        if self.state == "failed" and self.latest_provider_state == "terminal_failed":
            return "manual_recovery_required"
        return "replayable"


@dataclass(frozen=True)
class ScopeExecutionAttempt:
    """An internal lease-fenced attempt within a stable execution unit."""

    execution_unit_id: str
    attempt_no: int
    state: ExecutionUnitState
    lease_owner: str | None = None
    lease_token: str | None = field(default=None, compare=False, repr=False)
    lease_expires_at: datetime | None = None
    provider_state: str | None = None
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not self.execution_unit_id.strip() or self.attempt_no < 0:
            raise ValueError("scope execution attempt identity is required")
        if self.state not in {"pending", "running", "completed", "failed", "outcome_unknown"}:
            raise ValueError("invalid scope execution attempt state")


@dataclass(frozen=True)
class ExecutionContext:
    """Immutable authority carried by one worker attempt through every write."""

    execution_unit_id: str
    attempt_no: int
    lease_token: str = field(repr=False)
    scope_contract_id: str

    def __post_init__(self) -> None:
        if (
            not self.execution_unit_id.strip()
            or self.attempt_no < 0
            or not self.lease_token.strip()
            or not self.scope_contract_id.strip()
        ):
            raise ValueError("execution context identity is required")


class ExecutionLeaseFencedError(RuntimeError):
    """Raised when an execution attempt no longer owns its live lease."""


@dataclass(frozen=True)
class ExecutionFact:
    """Append-only, ordered trace fact for one execution-unit attempt."""

    execution_unit_id: str
    attempt_no: int
    sequence_no: int
    kind: ExecutionFactKind
    payload: Mapping[str, object]
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not self.execution_unit_id.strip() or self.attempt_no < 0 or self.sequence_no < 1:
            raise ValueError("execution fact identity is required")
        if self.kind not in {
            "decision_accepted",
            "attempt_claimed",
            "provider_request_recorded",
            "provider_outcome_recorded",
            "lease_fenced",
            "coverage_persisted",
            "publication_persisted",
            "outcome_unknown",
        }:
            raise ValueError("invalid execution fact kind")
        object.__setattr__(self, "payload", _freeze_execution_payload(self.payload))


def _freeze_execution_payload(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_execution_payload(item) for key, item in value.items()}
        )
    if isinstance(value, list | tuple):
        return tuple(_freeze_execution_payload(item) for item in value)
    return value


def thaw_execution_payload(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: thaw_execution_payload(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_execution_payload(item) for item in value]
    return value


def build_scope_contract(
    *,
    workflow_run_id: str,
    research_plan_id: str,
    version: int,
    constraints: tuple[ScopeConstraint, ...],
    query_groups: tuple[ScopeQueryGroupInput, ...],
    schema_version: str = SCOPE_CONTRACT_SCHEMA_VERSION_V1,
) -> ResearchScopeContract:
    """Build the one immutable scope that authorizes a Lite collection."""
    if not workflow_run_id.strip() or not research_plan_id.strip():
        raise ValueError("workflow_run_id and research_plan_id are required")
    if version < 1:
        raise ValueError("scope contract version must be positive")
    if schema_version not in {
        SCOPE_CONTRACT_SCHEMA_VERSION_V1,
        SCOPE_CONTRACT_SCHEMA_VERSION_V2,
    }:
        raise ValueError("unsupported scope contract schema version")
    if not constraints:
        raise ValueError("scope contract requires constraints")
    core_object_count = sum(constraint.id == "core_object" for constraint in constraints)
    if core_object_count != 1:
        raise ValueError("scope contract requires exactly one core_object constraint")
    if len({constraint.id for constraint in constraints}) != len(constraints):
        raise ValueError("scope constraint ids must be unique")
    if len(query_groups) > MAX_LITE_QUERY_GROUPS:
        raise ValueError(f"Lite scope contract allows at most {MAX_LITE_QUERY_GROUPS} query groups")
    if not query_groups:
        raise ValueError("scope contract requires query groups")

    required_terms = tuple(
        constraint.value for constraint in constraints if constraint.mode == "required"
    )
    contract_id = (
        "rsc_"
        + _fingerprint(
            {
                "workflow_run_id": workflow_run_id,
                "research_plan_id": research_plan_id,
                "version": version,
            }
        )[:24]
    )
    frozen_groups = tuple(
        _build_query_group(
            item,
            scope_contract_id=contract_id,
            index=index,
            required_terms=required_terms,
        )
        for index, item in enumerate(query_groups, start=1)
    )
    return ResearchScopeContract(
        id=contract_id,
        workflow_run_id=workflow_run_id,
        research_plan_id=research_plan_id,
        version=version,
        schema_version=schema_version,
        constraints=constraints,
        query_groups=frozen_groups,
        created_at=utcnow(),
    )


def build_scope_draft(
    *,
    workflow_run_id: str,
    research_plan_id: str,
    structure_hash: str,
    constraints: tuple[ScopeConstraint, ...],
    query_groups: tuple[ScopeQueryGroupInput, ...],
    schema_version: str = SCOPE_CONTRACT_SCHEMA_VERSION_V1,
    core_object: str = "",
    product_experience_aspect: str | None = None,
    context_audience_aspect: str | None = None,
) -> ResearchScopeDraft:
    if not workflow_run_id.strip() or not research_plan_id.strip() or not structure_hash.strip():
        raise ValueError("scope draft identity is required")
    build_scope_contract(
        workflow_run_id=workflow_run_id,
        research_plan_id=research_plan_id,
        version=1,
        constraints=constraints,
        query_groups=query_groups,
        schema_version=schema_version,
    )
    core_constraint = next(item for item in constraints if item.id == "core_object")
    frozen_core = _clean_query(core_object or core_constraint.value, field_name="core_object")
    frozen_product_aspect = _optional_query_aspect(product_experience_aspect)
    frozen_context_aspect = _optional_query_aspect(context_audience_aspect)
    if schema_version == SCOPE_CONTRACT_SCHEMA_VERSION_V2:
        required_ids = tuple(item.id for item in constraints if item.mode == "required")
        if required_ids != ("core_object",) or _normalized(frozen_core) != _normalized(
            core_constraint.value
        ):
            raise ValueError("v2 product scope requires only its core_object")
    draft_id = (
        "rsd_"
        + _fingerprint(
            {
                "workflow_run_id": workflow_run_id,
                "research_plan_id": research_plan_id,
                "structure_hash": structure_hash,
                "constraints": [item.__dict__ for item in constraints],
                "query_groups": [item.__dict__ for item in query_groups],
                "schema_version": schema_version,
                "core_object": frozen_core,
                "product_experience_aspect": frozen_product_aspect,
                "context_audience_aspect": frozen_context_aspect,
            }
        )[:24]
    )
    return ResearchScopeDraft(
        draft_id,
        workflow_run_id,
        research_plan_id,
        structure_hash,
        constraints,
        query_groups,
        utcnow(),
        schema_version,
        frozen_core,
        frozen_product_aspect,
        frozen_context_aspect,
    )


def _optional_query_aspect(value: str | None) -> str | None:
    normalized = " ".join(str(value or "").split())
    return normalized or None


def _build_query_group(
    value: ScopeQueryGroupInput,
    *,
    scope_contract_id: str,
    index: int,
    required_terms: tuple[str, ...],
) -> ScopeQueryGroup:
    suggested_query = _clean_query(value.suggested_query, field_name="suggested_query")
    final_query = _clean_query(value.final_query, field_name="final_query")
    origin: QueryOrigin = value.origin or (
        "system_suggested"
        if _normalized(suggested_query) == _normalized(final_query)
        else "user_edited"
    )
    if origin not in {"system_suggested", "user_edited"}:
        raise ValueError("invalid query group origin")
    role = classify_query_group(
        final_query,
        required_terms=required_terms,
        targeted_required_terms=value.targeted_required_terms,
    )
    return ScopeQueryGroup(
        id="qg_"
        + _fingerprint(
            {
                "scope_contract_id": scope_contract_id,
                "index": index,
                "final_query": final_query,
            }
        )[:16],
        suggested_query=suggested_query,
        final_query=final_query,
        origin=origin,
        execution_role=role,
    )


def classify_query_group(
    final_query: str,
    *,
    required_terms: tuple[str, ...],
    targeted_required_terms: tuple[str, ...] = (),
) -> QueryExecutionRole:
    """Classify query intent without rejecting arbitrary user-authored text."""
    normalized_query = _normalized(_clean_query(final_query, field_name="final_query"))
    normalized_required = tuple(_normalized(term) for term in required_terms)
    if all(term in normalized_query for term in normalized_required):
        return "coverage"
    normalized_targets = tuple(_normalized(term) for term in targeted_required_terms)
    if normalized_targets and all(term in normalized_query for term in normalized_targets):
        return "supplementary"
    return "exploratory"


def _clean_query(value: str, *, field_name: str) -> str:
    query = " ".join(str(value).split())
    if not query:
        raise ValueError(f"{field_name} must be non-empty")
    return query


def _normalized(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", normalized)


def _fingerprint(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
