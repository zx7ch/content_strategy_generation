"""Immutable, versioned contracts used by formal Content Research runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.content_research.models import utcnow


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def policy_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SamplePolicy:
    id: str
    schema_version: str
    direction_id: str
    minimum_samples: int
    minimum_independent_authors: int
    author_cap: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.schema_version or not self.direction_id:
            raise ValueError("SamplePolicy requires id, schema_version, and direction_id")
        if min(self.minimum_samples, self.minimum_independent_authors, self.author_cap) < 1:
            raise ValueError("SamplePolicy thresholds must be positive")


@dataclass(frozen=True)
class DirectionContract:
    id: str
    snapshot_id: str
    direction_id: str
    schema_version: str
    sample_policy_id: str
    required_note_fields: tuple[str, ...]
    optional_note_fields: tuple[str, ...] = ()
    required_comment_fields: tuple[str, ...] = ()
    claim_rules: tuple[str, ...] = ()
    analysis_schema_version: str = "direction_analysis_v1"
    resume_contract_version: str = "direction_resume_v1"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        required = (self.id, self.snapshot_id, self.direction_id, self.schema_version, self.sample_policy_id)
        if not all(required):
            raise ValueError("DirectionContract requires identity, snapshot, version, and sample policy")
        if not self.required_note_fields:
            raise ValueError("DirectionContract requires at least one required note field")


@dataclass(frozen=True)
class RunPolicySnapshot:
    id: str
    workflow_run_id: str
    research_brief_id: str
    research_plan_id: str
    schema_version: str
    effective_policy: dict[str, Any]
    effective_policy_hash: str
    run_as_of_at: datetime
    base_policy_ids_and_versions: dict[str, str] = field(default_factory=dict)
    requested_overrides: dict[str, Any] = field(default_factory=dict)
    validation_result: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not all((self.id, self.workflow_run_id, self.research_brief_id, self.research_plan_id, self.schema_version)):
            raise ValueError("RunPolicySnapshot requires identity and run relationships")
        _require_aware(self.run_as_of_at, "run_as_of_at")
        _require_aware(self.created_at, "created_at")
        if self.effective_policy_hash != policy_hash(self.effective_policy):
            raise ValueError("RunPolicySnapshot effective_policy_hash does not match effective_policy")


def build_default_snapshot(*, snapshot_id: str, workflow_run_id: str, brief_id: str, plan_id: str, run_as_of_at: datetime | None = None) -> tuple[RunPolicySnapshot, list[SamplePolicy], list[DirectionContract]]:
    from app.content_research.workflow.direction_registry import ResearchDirectionRegistry

    as_of = run_as_of_at or utcnow()
    _require_aware(as_of, "run_as_of_at")
    definitions = ResearchDirectionRegistry().list_directions()
    policy = {
        "schema_version": "content_research_policy_v2",
        "direction_ids": [item.id for item in definitions],
        "llm_cost_policy": {
            "currency": "USD",
            "warning_threshold_usd": 0.50,
            "max_report_rewrites": 1,
        },
    }
    snapshot = RunPolicySnapshot(id=snapshot_id, workflow_run_id=workflow_run_id, research_brief_id=brief_id, research_plan_id=plan_id, schema_version="content_research_run_policy_snapshot_v1", effective_policy=policy, effective_policy_hash=policy_hash(policy), run_as_of_at=as_of)
    policies = [SamplePolicy(id=f"sp_{snapshot_id}_{item.id}", schema_version="content_research_sample_policy_v1", direction_id=item.id, minimum_samples=30 if item.id in {"ugc_community", "comment_insight"} else 3, minimum_independent_authors=5 if item.id in {"ugc_community", "comment_insight"} else 2, author_cap=3) for item in definitions]
    contracts = [DirectionContract(id=f"dc_{snapshot_id}_{item.id}", snapshot_id=snapshot.id, direction_id=item.id, schema_version="content_research_direction_contract_v1", sample_policy_id=policy.id, required_note_fields=("source_id", "source_url", "body"), required_comment_fields=("comment_text", "parent_note_id") if item.id in {"ugc_community", "comment_insight"} else ()) for item, policy in zip(definitions, policies, strict=True)]
    return snapshot, policies, contracts
