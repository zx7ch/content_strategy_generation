"""Immutable, versioned contracts used by formal Content Research runs."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.content_research.admission.quote_fields import CLAIM_QUOTE_FIELDS
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
    "not_requested",
)
DIRECTION_CATALOG_V1 = (
    "product_marketing",
    "competitor_discovery",
    "content_performance",
)
DIRECTION_CATALOG_VERSION = "direction_catalog_v1"
PRIMARY_MARKETING_GOAL_CATALOG = ("content_seeding",)
ADMISSION_REASON_CODES = (
    "missing_blocking_field",
    "warning_field_unavailable",
    "comment_operation_unavailable",
    "missing_comment_field",
    "capability_unavailable",
    "query_subject_not_supported",
    "first_intent_not_supported",
)

AUTHOR_IDENTITY_SCHEMA_VERSION = "provider_author_identity_v1"


def admission_author_identity(projection: Mapping[str, Any]) -> str | None:
    """Return a conservative provider-real identity for sample independence."""
    author_id = str(projection.get("author_id") or "").strip()
    if author_id:
        return f"id:{author_id}"
    author_name = unicodedata.normalize("NFKC", str(projection.get("author") or "")).casefold()
    normalized_name = " ".join(author_name.split())
    return f"name:{normalized_name}" if normalized_name else None


def admission_author_identity_kind(projection: Mapping[str, Any]) -> str | None:
    identity = admission_author_identity(projection)
    return identity.split(":", 1)[0] if identity else None


_DIRECTION_CONTRACT_SPECS: dict[str, dict[str, Any]] = {
    "product_marketing": {
        "blocking_note_fields": (
            "title",
            "content_text",
            "tags",
            "note_type",
            "metrics",
            "metrics_observed_at",
        ),
        "warning_note_fields": ("source_published_at", "ip_location", "media"),
        "claim_rules": (
            "product_value_expression",
            "use_context",
            "target_audience_framing",
            "message_angle",
        ),
    },
    "content_performance": {
        "blocking_note_fields": (
            "title",
            "content_text",
            "note_type",
            "source_published_at",
            "metrics",
            "metrics_observed_at",
            "media",
        ),
        "warning_note_fields": ("tags", "ip_location"),
        "claim_rules": ("observed_high_engagement_sample", "visible_content_format"),
    },
    "competitor_discovery": {
        "blocking_note_fields": (
            "title",
            "content_text",
            "author",
            "tags",
            "metrics",
            "metrics_observed_at",
        ),
        "warning_note_fields": ("source_published_at", "ip_location"),
        "claim_rules": ("named_competitor", "visible_content_expression"),
    },
    "ugc_community": {
        "blocking_note_fields": ("title", "content_text", "author", "source_published_at"),
        "warning_note_fields": ("metrics", "tags"),
        "blocking_comment_fields": (
            "comment_text",
            "source_published_at",
            "like_count",
            "reply_depth",
        ),
        "claim_rules": ("observed_discussion_scenario", "interaction_pattern", "sampled_language"),
    },
    "comment_insight": {
        "blocking_note_fields": ("title", "content_text"),
        "warning_note_fields": ("metrics",),
        "blocking_comment_fields": (
            "comment_text",
            "source_published_at",
            "like_count",
            "reply_depth",
        ),
        "claim_rules": ("explicit_question", "objection_or_failure", "repeated_need_language"),
    },
    "brand_activity": {
        "blocking_note_fields": (
            "title",
            "content_text",
            "source_published_at",
            "tags",
            "note_type",
            "metrics",
            "metrics_observed_at",
        ),
        "warning_note_fields": ("ip_location", "media"),
        "claim_rules": (
            "campaign_signal",
            "launch_signal",
            "collaboration_signal",
            "dissemination_signal",
        ),
    },
    "keyword_growth": {
        "blocking_note_fields": (
            "title",
            "content_text",
            "tags",
            "source_published_at",
            "metrics",
            "metrics_observed_at",
        ),
        "warning_note_fields": ("author", "ip_location"),
        "claim_rules": ("sampled_keyword_pattern", "keyword_growth_with_comparable_baseline"),
    },
}

QUERY_RELEVANCE_SCHEMA_VERSION = "content_research_query_relevance_v2"
QUERY_RELEVANCE_MATCHING_MODE = "normalized_substring_any_anchor_v1"
QUERY_RELEVANCE_ALGORITHM_VERSION = "query_relevance_v2"
LEGACY_QUERY_RELEVANCE_VERSIONS = {
    ("content_research_query_relevance_v1", "query_relevance_v1"),
}
QUERY_SUBJECT_NOT_SUPPORTED = "query_subject_not_supported"
LOCKED_QUERY_PLAN_SCHEMA_VERSION = "content_research_locked_query_plan_v2"
ADMISSION_ALGORITHM_VERSION = "claim_admission_v2"

_CATEGORY_SYNONYM_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("短裤", ("徒步短裤", "跑步短裤", "速干短裤", "运动短裤", "五分裤")),
    ("徒步短裤", ("短裤", "速干短裤", "运动短裤", "五分裤")),
    ("跑步短裤", ("短裤", "速干短裤", "运动短裤", "五分裤")),
    ("徒步鞋", ("登山鞋", "户外鞋")),
    ("跑鞋", ("运动鞋",)),
    ("冲锋衣", ("户外夹克",)),
    ("防晒衣", ("防晒服",)),
    ("背包", ("双肩包", "徒步包")),
)


def normalize_relevance_text(value: str) -> str:
    """Normalize an anchor or direct quote without treating a full query as a key."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", normalized)


def build_query_relevance_contract(
    *,
    direction_id: str,
    confirmed_subject: str,
    query_group_ids: tuple[str, ...],
    subject_structure: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the immutable core-entity gate shared by policy and admission."""
    subject_anchor = normalize_relevance_text(confirmed_subject)
    if not subject_anchor:
        raise ValueError("query relevance requires a confirmed subject anchor")
    frozen_query_group_ids = tuple(
        sorted({item.strip() for item in query_group_ids if item.strip()})
    )
    if not frozen_query_group_ids:
        raise ValueError("query relevance requires frozen query group ids")
    core_entity_anchors: list[str] = []
    legacy_category_anchors: list[str] = []
    allowed_synonyms: dict[str, list[str]] = {}
    first_intent_anchor = ""
    if subject_structure:
        research_intents = subject_structure.get("research_intents") or ()
        if research_intents:
            first_intent_anchor = normalize_relevance_text(str(research_intents[0]))
        for entity in subject_structure.get("core_entities") or ():
            if not isinstance(entity, Mapping):
                continue
            anchor = normalize_relevance_text(str(entity.get("canonical_name") or ""))
            if not anchor:
                continue
            core_entity_anchors.append(anchor)
            aliases = (subject_structure.get("synonym_groups") or {}).get(
                str(entity.get("canonical_name") or ""), ()
            )
            allowed_synonyms[anchor] = sorted(
                {
                    normalized
                    for item in aliases
                    if (normalized := normalize_relevance_text(str(item)))
                }
            )
    else:
        # Read compatibility for pre-structure runs. New runs never use this
        # fixed legacy category bridge.
        for category, synonyms in _CATEGORY_SYNONYM_GROUPS:
            normalized_category = normalize_relevance_text(category)
            if normalized_category not in subject_anchor:
                continue
            core_entity_anchors.append(normalized_category)
            legacy_category_anchors.append(normalized_category)
            allowed_synonyms[normalized_category] = sorted(
                {normalize_relevance_text(item) for item in synonyms}
            )
            break
    if not core_entity_anchors:
        core_entity_anchors = [subject_anchor]
    quote_fields = CLAIM_QUOTE_FIELDS.get(direction_id, {})
    return {
        "schema_version": QUERY_RELEVANCE_SCHEMA_VERSION,
        "algorithm_version": QUERY_RELEVANCE_ALGORITHM_VERSION,
        "subject_anchors": sorted({subject_anchor}),
        "core_entity_anchors": sorted(set(core_entity_anchors)),
        "category_anchors": sorted(set(legacy_category_anchors)),
        "allowed_synonyms": {key: allowed_synonyms[key] for key in sorted(allowed_synonyms)},
        "first_intent_anchor": first_intent_anchor,
        "matching_mode": QUERY_RELEVANCE_MATCHING_MODE,
        "query_group_ids": list(frozen_query_group_ids),
        "claim_quote_fields": {
            claim_type: sorted(fields) for claim_type, fields in sorted(quote_fields.items())
        },
        "reason_code": QUERY_SUBJECT_NOT_SUPPORTED,
    }


def frozen_query_relevance(
    contract: DirectionContract,
    policy_snapshot: RunPolicySnapshot,
) -> dict[str, Any] | None:
    """Return the run-frozen contract after checking both persisted copies agree."""
    contract_value = contract.metadata.get("query_relevance")
    policy_value = (policy_snapshot.effective_policy.get("query_relevance") or {}).get(
        contract.direction_id
    )
    if contract_value is None and policy_value is None:
        return None
    if not isinstance(contract_value, dict) or contract_value != policy_value:
        raise ValueError("direction and run policy query relevance contracts differ")
    return dict(contract_value)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def policy_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _locked_query_plan(
    *,
    requested_ids: tuple[str, ...],
    query_groups_by_direction: Mapping[str, tuple[Mapping[str, Any], ...]],
    custom_research_question: str,
) -> dict[str, Any]:
    if set(query_groups_by_direction) != set(requested_ids):
        raise ValueError("query groups must cover exactly the requested directions")
    directions: dict[str, dict[str, Any]] = {}
    for direction_id in requested_ids:
        groups: list[dict[str, Any]] = []
        for value in query_groups_by_direction[direction_id]:
            group = {
                "id": str(value.get("id") or "").strip(),
                "direction_id": str(value.get("direction_id") or "").strip(),
                "normalized_query": " ".join(str(value.get("normalized_query") or "").split()),
                "priority": int(value.get("priority", 0)),
                "sort": str(value.get("sort") or "").strip(),
                "time_window": dict(value.get("time_window") or {}),
                "candidate_cap": int(value.get("candidate_cap", 0)),
                "activation": str(value.get("activation") or "primary").strip(),
                "normalized_identity": str(value.get("normalized_identity") or "").strip(),
            }
            group["roles"] = sorted(
                {str(item).strip() for item in value.get("roles", ()) if str(item).strip()}
            ) or [f"legacy_group_{group['priority']}"]
            if not group["normalized_identity"]:
                group["normalized_identity"] = policy_hash(
                    {
                        "query": group["normalized_query"].casefold(),
                        "sort": group["sort"].casefold(),
                        "time_window": group["time_window"],
                        "candidate_cap": group["candidate_cap"],
                    }
                )
            if (
                not group["id"]
                or group["direction_id"] != direction_id
                or not group["normalized_query"]
                or not group["sort"]
                or not group["time_window"].get("end_at")
                or group["candidate_cap"] < 1
                or not group["roles"]
                or group["activation"] not in {"primary", "coverage_fallback"}
                or not group["normalized_identity"]
            ):
                raise ValueError("locked query group is incomplete")
            groups.append(group)
        groups.sort(key=lambda item: (item["priority"], item["id"]))
        if not groups or len({item["id"] for item in groups}) != len(groups):
            raise ValueError("locked query groups must be non-empty and unique")
        primary_count = sum(item["activation"] == "primary" for item in groups)
        fallback_count = sum(item["activation"] == "coverage_fallback" for item in groups)
        if primary_count not in {1, 2} or fallback_count > 1:
            raise ValueError("locked query plan exceeds the Lite 2 plus 1 cap")
        if any(item["candidate_cap"] != 20 for item in groups):
            raise ValueError("Lite query groups require candidate_cap=20")
        directions[direction_id] = {
            "query_groups": groups,
            "query_plan_hash": policy_hash({"query_groups": groups}),
        }
    return {
        "schema_version": LOCKED_QUERY_PLAN_SCHEMA_VERSION,
        "query_compiler_version": "content_research_query_compiler_v2",
        "coverage_policy_version": "content_research_coverage_policy_v1",
        "primary_query_group_cap": 2,
        "coverage_fallback_query_group_cap": 1,
        "candidate_cap_per_group": 20,
        "custom_research_question": " ".join(custom_research_question.split()),
        "directions": directions,
    }


def evaluate_capability_preflight(
    *,
    contracts: list[DirectionContract],
    provider_capabilities: dict[str, dict[str, Any]],
    provider: str = "xiaohongshu",
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
        missing_note = tuple(
            field for field in blocking_note if not detail_supported or field not in detail_fields
        )
        missing_warning = tuple(
            field for field in warning_note if not detail_supported or field not in detail_fields
        )
        missing_comment = tuple(
            field
            for field in blocking_comment
            if not comments_supported or field not in comment_fields
        )
        reasons: list[str] = []
        if missing_note:
            reasons.append(
                "capability_unavailable" if not detail_supported else "missing_blocking_field"
            )
        if missing_comment:
            reasons.append(
                "comment_operation_unavailable"
                if not comments_supported
                else "missing_comment_field"
            )
        if missing_warning:
            reasons.append("warning_field_unavailable")
        status = (
            "formal_directional_result"
            if not missing_note and not missing_comment
            else (
                "unavailable"
                if not detail_supported or (blocking_comment and not comments_supported)
                else "incomplete"
            )
        )
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
            raise ValueError(
                "SamplePolicy detail_fetch_cap must be an integer at least minimum_samples"
            )
        comment_limit = self.metadata.get("comment_limit", 30)
        if not isinstance(comment_limit, int) or comment_limit < 1:
            raise ValueError("SamplePolicy comment_limit must be a positive integer")
        top_level_only = self.metadata.get("comment_top_level_only", True)
        if not isinstance(top_level_only, bool):
            raise ValueError("SamplePolicy comment_top_level_only must be a boolean")
        reply_depth_limit = self.metadata.get("comment_reply_depth_limit", 0)
        if not isinstance(reply_depth_limit, int) or reply_depth_limit < 0:
            raise ValueError(
                "SamplePolicy comment_reply_depth_limit must be a non-negative integer"
            )
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
        required = (
            self.id,
            self.snapshot_id,
            self.direction_id,
            self.schema_version,
            self.sample_policy_id,
        )
        if not all(required):
            raise ValueError(
                "DirectionContract requires identity, snapshot, version, and sample policy"
            )
        if not self.required_note_fields:
            raise ValueError("DirectionContract requires at least one required note field")
        relevance = self.metadata.get("query_relevance")
        if relevance is not None:
            if (
                not isinstance(relevance, dict)
                or (
                    relevance.get("schema_version"),
                    relevance.get("algorithm_version"),
                )
                not in {
                    (QUERY_RELEVANCE_SCHEMA_VERSION, QUERY_RELEVANCE_ALGORITHM_VERSION),
                    *LEGACY_QUERY_RELEVANCE_VERSIONS,
                }
                or relevance.get("matching_mode") != QUERY_RELEVANCE_MATCHING_MODE
                or relevance.get("reason_code") != QUERY_SUBJECT_NOT_SUPPORTED
                or not relevance.get("subject_anchors")
                or not relevance.get("query_group_ids")
            ):
                raise ValueError("DirectionContract has an invalid query relevance contract")


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
        if not all(
            (
                self.id,
                self.workflow_run_id,
                self.research_brief_id,
                self.research_plan_id,
                self.schema_version,
            )
        ):
            raise ValueError("RunPolicySnapshot requires identity and run relationships")
        _require_aware(self.run_as_of_at, "run_as_of_at")
        _require_aware(self.created_at, "created_at")
        if self.effective_policy_hash != policy_hash(self.effective_policy):
            raise ValueError(
                "RunPolicySnapshot effective_policy_hash does not match effective_policy"
            )


def build_default_snapshot(
    *,
    snapshot_id: str,
    workflow_run_id: str,
    brief_id: str,
    plan_id: str,
    run_as_of_at: datetime | None = None,
    provider_capabilities: dict[str, dict[str, Any]] | None = None,
    direction_set_version: str = "formal_v1",
    direction_ids: tuple[str, ...] | None = None,
    direction_catalog: tuple[str, ...] | None = None,
    report_compose_mode: str = "prose",
    confirmed_subject: str | None = None,
    query_group_ids_by_direction: Mapping[str, tuple[str, ...]] | None = None,
    query_groups_by_direction: Mapping[str, tuple[Mapping[str, Any], ...]] | None = None,
    custom_research_question: str = "",
    subject_structure: Mapping[str, Any] | None = None,
    subject_structure_hash: str | None = None,
    primary_marketing_goal: str | None = None,
) -> tuple[RunPolicySnapshot, list[SamplePolicy], list[DirectionContract]]:
    from app.content_research.admission.governance_keys import GOVERNANCE_POLICY_V1
    from app.content_research.workflow.direction_registry import ResearchDirectionRegistry

    as_of = run_as_of_at or utcnow()
    _require_aware(as_of, "run_as_of_at")
    registered_definitions = ResearchDirectionRegistry().list_directions()
    definitions_by_id = {item.id: item for item in registered_definitions}
    requested_ids = direction_ids if direction_ids is not None else tuple(definitions_by_id)
    if not requested_ids:
        raise ValueError("requested direction ids must not be empty")
    if len(set(requested_ids)) != len(requested_ids):
        raise ValueError("requested direction ids must be unique")
    if not set(requested_ids).issubset(definitions_by_id):
        raise ValueError("requested direction ids contains an unknown direction")
    if direction_catalog is not None:
        if direction_catalog != DIRECTION_CATALOG_V1:
            raise ValueError("direction_catalog must match the Lite direction catalog")
        if not set(requested_ids).issubset(direction_catalog):
            raise ValueError("requested direction ids contains a direction outside the catalog")
    if report_compose_mode not in {"prose", "template_only"}:
        raise ValueError("invalid report_compose_mode")
    normalized_primary_marketing_goal = (
        primary_marketing_goal.strip() if primary_marketing_goal is not None else None
    )
    if (
        normalized_primary_marketing_goal is not None
        and normalized_primary_marketing_goal not in PRIMARY_MARKETING_GOAL_CATALOG
    ):
        raise ValueError("primary_marketing_goal must be a Lite marketing goal")
    definitions = [definitions_by_id[direction_id] for direction_id in requested_ids]
    locked_query_plan: dict[str, Any] | None = None
    if query_groups_by_direction is not None:
        locked_query_plan = _locked_query_plan(
            requested_ids=requested_ids,
            query_groups_by_direction=query_groups_by_direction,
            custom_research_question=custom_research_question,
        )
        frozen_group_ids = {
            direction_id: tuple(
                group["id"]
                for group in locked_query_plan["directions"][direction_id]["query_groups"]
            )
            for direction_id in requested_ids
        }
        if query_group_ids_by_direction is not None and {
            key: tuple(sorted(value)) for key, value in query_group_ids_by_direction.items()
        } != {key: tuple(sorted(value)) for key, value in frozen_group_ids.items()}:
            raise ValueError("query group ids differ from the locked query plan")
        query_group_ids_by_direction = frozen_group_ids
    relevance_by_direction: dict[str, dict[str, Any]] = {}
    if confirmed_subject is not None or query_group_ids_by_direction is not None:
        if not confirmed_subject or query_group_ids_by_direction is None:
            raise ValueError(
                "confirmed_subject and query_group_ids_by_direction must be frozen together"
            )
        if set(query_group_ids_by_direction) != set(requested_ids):
            raise ValueError("query group ids must cover exactly the requested directions")
        relevance_by_direction = {
            direction_id: build_query_relevance_contract(
                direction_id=direction_id,
                confirmed_subject=confirmed_subject,
                query_group_ids=tuple(query_group_ids_by_direction[direction_id]),
                subject_structure=subject_structure,
            )
            for direction_id in requested_ids
        }
    policy = {
        "schema_version": "content_research_policy_v2",
        "admission_algorithm_version": ADMISSION_ALGORITHM_VERSION,
        "direction_set_version": direction_set_version,
        "direction_ids": list(requested_ids),
        "report_compose_mode": report_compose_mode,
        "llm_cost_policy": {
            "currency": "USD",
            "warning_threshold_usd": 0.50,
            "max_report_rewrites": 1,
        },
        "governance": GOVERNANCE_POLICY_V1,
        "provider_capabilities": provider_capabilities
        or {
            "xiaohongshu": {
                "adapter_version": "xhs_adapter_v1",
                "discover_candidates": {
                    "status": "supported",
                    "fields": ["title", "author", "metrics"],
                },
                "collect_note_detail": {"status": "unavailable", "fields": []},
                "collect_comments": {
                    "status": "supported",
                    "fields": [
                        "comment_text",
                        "source_published_at",
                        "like_count",
                        "reply_depth",
                        "author",
                        "parent_note_id",
                    ],
                    "top_level_only": True,
                },
            }
        },
    }
    if relevance_by_direction:
        policy["query_relevance"] = relevance_by_direction
    if locked_query_plan is not None:
        policy["locked_query_plan"] = locked_query_plan
    if subject_structure is not None or subject_structure_hash is not None:
        if not subject_structure or not subject_structure_hash:
            raise ValueError("subject_structure and subject_structure_hash must be frozen together")
        policy["subject_structure"] = dict(subject_structure)
        policy["subject_structure_hash"] = subject_structure_hash
    if direction_catalog is not None:
        policy |= {
            "direction_catalog_version": DIRECTION_CATALOG_VERSION,
            "requested_direction_ids": list(requested_ids),
        }
    if normalized_primary_marketing_goal is not None:
        policy["marketing_conclusion_policy"] = {
            "primary_marketing_goal": normalized_primary_marketing_goal,
            "tracks": ["need", "value", "message"],
            "minimum_notes_per_conclusion": 3,
            "minimum_independent_authors_per_conclusion": 2,
            "require_core_and_first_intent_support": True,
            "maximum_primary_conclusions_per_track": 1,
        }
    policies = [
        SamplePolicy(
            id=f"sp_{snapshot_id}_{item.id}",
            schema_version="content_research_sample_policy_v1",
            direction_id=item.id,
            minimum_samples=30 if item.id in {"ugc_community", "comment_insight"} else 3,
            minimum_independent_authors=5 if item.id in {"ugc_community", "comment_insight"} else 2,
            author_cap=3,
            metadata={
                "detail_fetch_cap": 30,
                "comment_limit": 30,
                "comment_top_level_only": True,
                "comment_reply_depth_limit": 0,
            },
        )
        for item in definitions
    ]
    contracts = [
        DirectionContract(
            id=f"dc_{snapshot_id}_{item.id}",
            snapshot_id=snapshot_id,
            direction_id=item.id,
            schema_version="content_research_direction_contract_v2",
            sample_policy_id=sample_policy.id,
            required_note_fields=_DIRECTION_CONTRACT_SPECS[item.id]["blocking_note_fields"],
            optional_note_fields=_DIRECTION_CONTRACT_SPECS[item.id]["warning_note_fields"],
            required_comment_fields=_DIRECTION_CONTRACT_SPECS[item.id].get(
                "blocking_comment_fields", ()
            ),
            claim_rules=_DIRECTION_CONTRACT_SPECS[item.id]["claim_rules"],
            metadata={
                "contract_role": "admission",
                "field_vocabulary": "content_research_normalized_fields_v1",
                "author_identity": {
                    "schema_version": AUTHOR_IDENTITY_SCHEMA_VERSION,
                    "priority": ["author_id", "normalized_author_name"],
                    "same_name_policy": "collapse",
                },
                **(
                    {"query_relevance": relevance_by_direction[item.id]}
                    if relevance_by_direction
                    else {}
                ),
            },
        )
        for item, sample_policy in zip(definitions, policies, strict=True)
    ]
    preflight = evaluate_capability_preflight(
        contracts=contracts, provider_capabilities=policy["provider_capabilities"]
    )
    snapshot = RunPolicySnapshot(
        id=snapshot_id,
        workflow_run_id=workflow_run_id,
        research_brief_id=brief_id,
        research_plan_id=plan_id,
        schema_version="content_research_run_policy_snapshot_v1",
        effective_policy=policy,
        effective_policy_hash=policy_hash(policy),
        run_as_of_at=as_of,
        validation_result=preflight,
    )
    return snapshot, policies, contracts
