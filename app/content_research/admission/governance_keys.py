"""Deterministic, quote-backed keys for cross-direction governance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.content_research.persistence_models import ClaimCandidateRecord

GOVERNANCE_POLICY_V1 = {
    "governance_key_version": "content_research_governance_keys_v1",
    "topics": ["sizing_fit", "price_value", "material", "durability", "comfort", "function", "style_design", "delivery_service"],
    "formats": ["comparison_demo", "tutorial", "scenario_demo", "unboxing", "review", "listicle"],
}


@dataclass(frozen=True)
class GovernanceKey:
    version: str
    aggregate_key: str | None
    reconciliation_key: str | None
    polarity: str | None
    source_field_path: str
    literal_evidence_ref: dict[str, Any]


def derive_governance_key(candidate: ClaimCandidateRecord, policy: dict[str, Any]) -> GovernanceKey | None:
    """Use only a candidate's verified literal quote and frozen vocabulary."""
    config = dict(policy.get("governance") or {})
    version = str(config.get("governance_key_version") or "")
    refs = list(candidate.payload.get("quote_refs") or [])
    if not version or len(refs) != 1:
        return None
    ref = dict(refs[0])
    field = str(ref.get("field_path") or "")
    quote = str(ref.get("quote") or "")
    if not quote or not ref.get("source_text_hash") or not ref.get("source_url"):
        return None
    scope = dict(candidate.payload.get("scope") or {})
    key: str | None = None
    polarity: str | None = None
    if candidate.research_direction_id == "brand_activity" and candidate.claim_type.endswith("_signal"):
        value = candidate.claim_type.removesuffix("_signal")
        key = f"activity_type:{value}"
    elif candidate.research_direction_id == "competitor_discovery" and candidate.claim_type == "named_competitor":
        value = str(scope.get("competitor_name") or "").strip()
        if value and value in quote:
            key = f"competitor_entity:{value.lower()}"
    elif candidate.research_direction_id == "keyword_growth":
        value = str(scope.get("keyword") or "").strip()
        if value and value in quote:
            key = f"keyword_literal:{value.lower()}"
    elif candidate.research_direction_id == "content_performance" and candidate.claim_type == "visible_content_format":
        value = next((item for item in config.get("formats", ()) if item in quote.lower()), None)
        if value:
            key = f"content_format:{value}"
    elif candidate.research_direction_id in {"comment_insight", "ugc_community"}:
        topic = next((item for item in config.get("topics", ()) if item in candidate.statement.lower()), None)
        if topic:
            key = f"comment_topic:{topic}"
            polarity = "negative" if candidate.claim_type == "objection_or_failure" else ("positive" if candidate.research_direction_id == "ugc_community" and candidate.claim_type == "sampled_language" else ("unknown" if candidate.claim_type == "explicit_question" else "requested"))
    if key is None:
        return None
    return GovernanceKey(version, key, key if polarity in {"positive", "negative"} else None, polarity, field, ref)
