"""Immutable, user-confirmed execution scope for Lite Content Research."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.content_research.models import utcnow


SCOPE_CONTRACT_SCHEMA_VERSION = "content_research_scope_contract_v1"
MAX_LITE_QUERY_GROUPS = 3

RetrievalPriority = Literal["must_cover", "prefer_cover"]
EvidenceGate = Literal["required", "optional"]
QueryOrigin = Literal["system_suggested", "user_edited"]
QueryExecutionRole = Literal["coverage", "supplementary", "exploratory"]


@dataclass(frozen=True)
class ScopeConstraint:
    id: str
    label: str
    value: str
    retrieval_priority: RetrievalPriority
    evidence_gate: EvidenceGate
    allowed_aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.label.strip() or not self.value.strip():
            raise ValueError("scope constraint id, label, and value are required")
        if self.retrieval_priority not in {"must_cover", "prefer_cover"}:
            raise ValueError("invalid retrieval_priority")
        if self.evidence_gate not in {"required", "optional"}:
            raise ValueError("invalid evidence_gate")


@dataclass(frozen=True)
class ScopeQueryGroupInput:
    suggested_query: str
    final_query: str


@dataclass(frozen=True)
class ScopeQueryGroup:
    id: str
    suggested_query: str
    final_query: str
    origin: QueryOrigin
    execution_role: QueryExecutionRole


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
        if self.state not in {"satisfied", "awaiting_scope_decision"}:
            raise ValueError("invalid coverage snapshot state")


def build_scope_contract(
    *,
    workflow_run_id: str,
    research_plan_id: str,
    version: int,
    constraints: tuple[ScopeConstraint, ...],
    query_groups: tuple[ScopeQueryGroupInput, ...],
) -> ResearchScopeContract:
    """Build the one immutable scope that authorizes a Lite collection."""
    if not workflow_run_id.strip() or not research_plan_id.strip():
        raise ValueError("workflow_run_id and research_plan_id are required")
    if version < 1:
        raise ValueError("scope contract version must be positive")
    if not constraints:
        raise ValueError("scope contract requires constraints")
    if len({constraint.id for constraint in constraints}) != len(constraints):
        raise ValueError("scope constraint ids must be unique")
    if len(query_groups) > MAX_LITE_QUERY_GROUPS:
        raise ValueError(f"Lite scope contract allows at most {MAX_LITE_QUERY_GROUPS} query groups")
    if not query_groups:
        raise ValueError("scope contract requires query groups")

    required_terms = tuple(
        constraint.value
        for constraint in constraints
        if constraint.retrieval_priority == "must_cover"
    )
    frozen_groups = tuple(
        _build_query_group(item, index=index, required_terms=required_terms)
        for index, item in enumerate(query_groups, start=1)
    )
    contract_id = "rsc_" + _fingerprint(
        {
            "workflow_run_id": workflow_run_id,
            "research_plan_id": research_plan_id,
            "version": version,
        }
    )[:24]
    return ResearchScopeContract(
        id=contract_id,
        workflow_run_id=workflow_run_id,
        research_plan_id=research_plan_id,
        version=version,
        schema_version=SCOPE_CONTRACT_SCHEMA_VERSION,
        constraints=constraints,
        query_groups=frozen_groups,
        created_at=utcnow(),
    )


def _build_query_group(
    value: ScopeQueryGroupInput,
    *,
    index: int,
    required_terms: tuple[str, ...],
) -> ScopeQueryGroup:
    suggested_query = _clean_query(value.suggested_query, field_name="suggested_query")
    final_query = _clean_query(value.final_query, field_name="final_query")
    origin: QueryOrigin = (
        "system_suggested" if _normalized(suggested_query) == _normalized(final_query) else "user_edited"
    )
    role: QueryExecutionRole = (
        "coverage"
        if all(_normalized(term) in _normalized(final_query) for term in required_terms)
        else "exploratory"
    )
    return ScopeQueryGroup(
        id="qg_" + _fingerprint({"index": index, "final_query": final_query})[:16],
        suggested_query=suggested_query,
        final_query=final_query,
        origin=origin,
        execution_role=role,
    )


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
