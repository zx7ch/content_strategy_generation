"""Single frozen field-policy registry for direction claim quotes."""

from __future__ import annotations

CLAIM_QUOTE_FIELDS: dict[str, dict[str, frozenset[str]]] = {
    "product_marketing": {
        "product_value_expression": frozenset({"content_text"}),
        "use_context": frozenset({"content_text"}),
        "target_audience_framing": frozenset({"content_text"}),
        "message_angle": frozenset({"title", "content_text"}),
    },
    "content_performance": {
        "observed_high_engagement_sample": frozenset({"title", "content_text"}),
        "visible_content_format": frozenset({"title", "content_text"}),
    },
    "competitor_discovery": {
        "named_competitor": frozenset({"title", "content_text", "tags"}),
        "visible_content_expression": frozenset({"title", "content_text", "tags"}),
    },
    "ugc_community": {
        "observed_discussion_scenario": frozenset({"comment_text"}),
        "interaction_pattern": frozenset({"comment_text"}),
        "sampled_language": frozenset({"comment_text"}),
    },
    "comment_insight": {
        "explicit_question": frozenset({"comment_text"}),
        "objection_or_failure": frozenset({"comment_text"}),
        "repeated_need_language": frozenset({"comment_text"}),
    },
    "brand_activity": {
        "campaign_signal": frozenset({"title", "content_text", "tags"}),
        "launch_signal": frozenset({"title", "content_text", "tags"}),
        "collaboration_signal": frozenset({"title", "content_text", "tags"}),
        "dissemination_signal": frozenset({"title", "content_text", "tags"}),
    },
    "keyword_growth": {
        "sampled_keyword_pattern": frozenset({"title", "content_text", "tags"}),
        "keyword_growth_with_comparable_baseline": frozenset({"title", "content_text", "tags"}),
    },
}


def quote_fields_for_claim(direction_id: str, claim_type: str) -> frozenset[str]:
    return CLAIM_QUOTE_FIELDS.get(direction_id, {}).get(claim_type, frozenset())


def quote_fields_for_direction(direction_id: str) -> frozenset[str]:
    return frozenset(
        field
        for fields in CLAIM_QUOTE_FIELDS.get(direction_id, {}).values()
        for field in fields
    )
