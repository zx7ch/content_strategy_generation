"""Immutable, versioned contracts used by formal Content Research runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.content_research.models import utcnow

CLAIM_EVIDENCE_STATES = (
    "case_level",
    "repeated_observation",
    "provisional",
    "insufficient_evidence",
)
DIRECTION_RESULT_STATES = (
    "formal_directional_result",
    "incomplete",
    "insufficient_evidence",
    "unavailable",
)
ADMISSION_REASON_CODES = (
    "missing_blocking_field",
    "warning_field_unavailable",
    "comment_operation_unavailable",
    "missing_comment_field",
    "capability_unavailable",
)


_DIRECTION_CONTRACT_SPECS: dict[str, dict[str, tuple[str, ...]]] = {
    "product_marketing": {"blocking_note_fields": ("title", "content_text", "tags", "note_type", "metrics", "metrics_observed_at"), "warning_note_fields": ("source_published_at", "ip_location", "media"), "claim_rules": ("product_value_expression", "use_context", "target_audience_framing", "message_angle")},
    "content_performance": {"blocking_note_fields": ("title", "content_text", "note_type", "source_published_at", "metrics", "metrics_observed_at", "media"), "warning_note_fields": ("tags", "ip_location"), "claim_rules": ("observed_high_engagement_sample", "visible_content_format")},
    "competitor_discovery": {"blocking_note_fields": ("title", "content_text", "author", "tags", "metrics", "metrics_observed_at"), "warning_note_fields": ("source_published_at", "ip_location"), "claim_rules": ("named_competitor", "visible_content_expression")},
    "ugc_community": {"blocking_note_fields": ("title", "content_text", "author", "source_published_at"), "warning_note_fields": ("metrics", "tags"), "blocking_comment_fields": ("comment_text", "source_published_at", "like_count", "reply_depth"), "claim_rules": ("observed_discussion_scenario", "interaction_pattern", "sampled_language")},
    "comment_insight": {"blocking_note_fields": ("title", "content_text"), "warning_note_fields": ("metrics",), "blocking_comment_fields": ("comment_text", "source_published_at", "like_count", "reply_depth"), "claim_rules": ("explicit_question", "objection_or_failure", "repeated_need_language")},
    "brand_activity": {"blocking_note_fields": ("title", "content_text", "source_published_at", "tags", "note_type", "metrics", "metrics_observed_at"), "warning_note_fields": ("ip_location", "media"), "claim_rules": ("campaign_signal", "launch_signal", "collaboration_signal", "dissemination_signal")},
    "keyword_growth": {"blocking_note_fields": ("title", "content_text", "tags", "source_published_at", "metrics", "metrics_observed_at"), "warning_note_fields": ("author", "ip_location"), "claim_rules": ("sampled_keyword_pattern", "keyword_growth_with_comparable_baseline")},
}


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def policy_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def evaluate_capability_preflight(
    *, contracts: list[DirectionContract], provider_capabilities: dict[str, dict[str, Any]], provider: str = "xiaohongshu",
) -> dict[str, Any]:
    """Freeze admission eligibility from contract requirements and adapter capabilities."""
    capabilities = provider_capabilities.get(provider, {})
    detail = dict(capabilities.get("collect_note_detail") or {})
    comments = dict(capabilities.get("collect_comments") or {})
    directions: dict[str, dict[str, Any]] = {}
    for contract in contracts:
        blocking_note = tuple(contract.required_note_fields)
        warning_note = tuple(contract.optional_note_fields)
        blocking_comment = tuple(contract.required_comment_fields)
        detail_fields = set(detail.get("fields") or ())
        comment_fields = set(comments.get("fields") or ())
        detail_supported = detail.get("status") == "supported"
        comments_supported = comments.get("status") == "supported"
        missing_note = tuple(field for field in blocking_note if not detail_supported or field not in detail_fields)
        missing_warning = tuple(field for field in warning_note if not detail_supported or field not in detail_fields)
        missing_comment = tuple(field for field in blocking_comment if not comments_supported or field not in comment_fields)
        reasons: list[str] = []
        if missing_note:
            reasons.append("capability_unavailable" if not detail_supported else "missing_blocking_field")
        if missing_comment:
            reasons.append("comment_operation_unavailable" if not comments_supported else "missing_comment_field")
        if missing_warning:
            reasons.append("warning_field_unavailable")
        status = "formal_directional_result" if not missing_note and not missing_comment else ("unavailable" if not detail_supported or (blocking_comment and not comments_supported) else "incomplete")
        directions[contract.direction_id] = {
            "status": status,
            "formal_eligible": status == "formal_directional_result",
            "missing_blocking_note_fields": list(missing_note),
            "missing_warning_note_fields": list(missing_warning),
            "missing_blocking_comment_fields": list(missing_comment),
            "reason_codes": reasons,
            "provider": provider,
        }
    return {
        "schema_version": "content_research_admission_capability_preflight_v1",
        "provider": provider,
        "directions": directions,
    }


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
        detail_fetch_cap = self.metadata.get("detail_fetch_cap", self.minimum_samples)
        if not isinstance(detail_fetch_cap, int) or detail_fetch_cap < self.minimum_samples:
            raise ValueError("SamplePolicy detail_fetch_cap must be an integer at least minimum_samples")
        comment_limit = self.metadata.get("comment_limit", 30)
        if not isinstance(comment_limit, int) or comment_limit < 1:
            raise ValueError("SamplePolicy comment_limit must be a positive integer")
        top_level_only = self.metadata.get("comment_top_level_only", True)
        if not isinstance(top_level_only, bool):
            raise ValueError("SamplePolicy comment_top_level_only must be a boolean")
        reply_depth_limit = self.metadata.get("comment_reply_depth_limit", 0)
        if not isinstance(reply_depth_limit, int) or reply_depth_limit < 0:
            raise ValueError("SamplePolicy comment_reply_depth_limit must be a non-negative integer")
        if top_level_only and reply_depth_limit != 0:
            raise ValueError("SamplePolicy top-level comment collection requires reply depth 0")

    @property
    def detail_fetch_cap(self) -> int:
        return int(self.metadata.get("detail_fetch_cap", self.minimum_samples))

    @property
    def comment_limit(self) -> int:
        return int(self.metadata.get("comment_limit", 30))

    @property
    def comment_top_level_only(self) -> bool:
        return bool(self.metadata.get("comment_top_level_only", True))

    @property
    def comment_reply_depth_limit(self) -> int:
        return int(self.metadata.get("comment_reply_depth_limit", 0))


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


def build_default_snapshot(*, snapshot_id: str, workflow_run_id: str, brief_id: str, plan_id: str, run_as_of_at: datetime | None = None, provider_capabilities: dict[str, dict[str, Any]] | None = None, direction_set_version: str = "formal_v1", direction_ids: tuple[str, ...] | None = None, report_compose_mode: str = "prose") -> tuple[RunPolicySnapshot, list[SamplePolicy], list[DirectionContract]]:
    from app.content_research.admission.governance_keys import GOVERNANCE_POLICY_V1
    from app.content_research.workflow.direction_registry import ResearchDirectionRegistry

    as_of = run_as_of_at or utcnow()
    _require_aware(as_of, "run_as_of_at")
    definitions = ResearchDirectionRegistry().list_directions()
    selected_ids = direction_ids or tuple(item.id for item in definitions)
    if report_compose_mode not in {"prose", "template_only"}:
        raise ValueError("invalid report_compose_mode")
    definitions = [item for item in definitions if item.id in selected_ids]
    if len(definitions) != len(selected_ids):
        raise ValueError("direction_ids contains an unknown direction")
    policy = {
        "schema_version": "content_research_policy_v2",
        "direction_set_version": direction_set_version,
        "direction_ids": list(selected_ids),
        "report_compose_mode": report_compose_mode,
        "llm_cost_policy": {
            "currency": "USD",
            "warning_threshold_usd": 0.50,
            "max_report_rewrites": 1,
        },
        "governance": GOVERNANCE_POLICY_V1,
        "provider_capabilities": provider_capabilities or {
            "xiaohongshu": {
                "adapter_version": "xhs_adapter_v1",
                "discover_candidates": {"status": "supported", "fields": ["title", "author", "metrics"]},
                "collect_note_detail": {"status": "unavailable", "fields": []},
                "collect_comments": {"status": "supported", "fields": ["comment_text", "source_published_at", "like_count", "reply_depth", "author", "parent_note_id"], "top_level_only": True},
            }
        },
    }
    policies = [SamplePolicy(id=f"sp_{snapshot_id}_{item.id}", schema_version="content_research_sample_policy_v1", direction_id=item.id, minimum_samples=30 if item.id in {"ugc_community", "comment_insight"} else 3, minimum_independent_authors=5 if item.id in {"ugc_community", "comment_insight"} else 2, author_cap=3, metadata={"detail_fetch_cap": 30, "comment_limit": 30, "comment_top_level_only": True, "comment_reply_depth_limit": 0}) for item in definitions]
    contracts = [
        DirectionContract(
            id=f"dc_{snapshot_id}_{item.id}", snapshot_id=snapshot_id, direction_id=item.id,
            schema_version="content_research_direction_contract_v2", sample_policy_id=sample_policy.id,
            required_note_fields=_DIRECTION_CONTRACT_SPECS[item.id]["blocking_note_fields"],
            optional_note_fields=_DIRECTION_CONTRACT_SPECS[item.id]["warning_note_fields"],
            required_comment_fields=_DIRECTION_CONTRACT_SPECS[item.id].get("blocking_comment_fields", ()),
            claim_rules=_DIRECTION_CONTRACT_SPECS[item.id]["claim_rules"],
            metadata={"contract_role": "admission", "field_vocabulary": "content_research_normalized_fields_v1"},
        )
        for item, sample_policy in zip(definitions, policies, strict=True)
    ]
    preflight = evaluate_capability_preflight(contracts=contracts, provider_capabilities=policy["provider_capabilities"])
    snapshot = RunPolicySnapshot(id=snapshot_id, workflow_run_id=workflow_run_id, research_brief_id=brief_id, research_plan_id=plan_id, schema_version="content_research_run_policy_snapshot_v1", effective_policy=policy, effective_policy_hash=policy_hash(policy), run_as_of_at=as_of, validation_result=preflight)
    return snapshot, policies, contracts
