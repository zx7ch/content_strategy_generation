from __future__ import annotations

from app.content_research.subject_structure import parse_subject_structure


def test_grounded_single_core_entity_is_confirmed_for_lite() -> None:
    decision = parse_subject_structure(
        {
            "schema_version": "content_research_subject_structure_v1",
            "canonical_subject": "防晒服饰",
            "subject_type": "category",
            "core_entities": [
                {
                    "canonical_name": "防晒服饰",
                    "raw_mentions": ["防晒穿搭"],
                }
            ],
            "research_intents": ["穿搭"],
            "context_modifiers": ["夏季"],
            "synonym_groups": {"防晒服饰": ["防晒衣", "防晒服"]},
            "ambiguities": [],
            "resolution_state": "resolved",
            "confidence": 0.01,
        },
        normalized_input="夏季防晒穿搭",
    )

    assert decision.state == "confirmed"
    assert decision.reason_codes == ()
    assert decision.structure is not None
    assert decision.structure.canonical_subject == "防晒服饰"
    assert decision.structure.core_entities[0].raw_mentions == ("防晒穿搭",)


def test_ungrounded_core_entity_needs_confirmation() -> None:
    decision = parse_subject_structure(
        {
            "canonical_subject": "防晒服饰",
            "subject_type": "category",
            "core_entities": [
                {"canonical_name": "防晒服饰", "raw_mentions": ["防晒衣"]}
            ],
            "research_intents": ["推荐"],
            "context_modifiers": ["夏季通勤"],
            "synonym_groups": {},
            "ambiguities": [],
            "resolution_state": "resolved",
        },
        normalized_input="适合夏季通勤的",
    )

    assert decision.state == "needs_confirmation"
    assert decision.reason_codes == ("core_entity_ungrounded",)


def test_multiple_or_ambiguous_core_entities_need_confirmation() -> None:
    decision = parse_subject_structure(
        {
            "canonical_subject": "苹果",
            "subject_type": "ambiguous",
            "core_entities": [
                {"canonical_name": "Apple 品牌", "raw_mentions": ["苹果"]},
                {"canonical_name": "苹果水果", "raw_mentions": ["苹果"]},
            ],
            "research_intents": ["年轻人偏好"],
            "context_modifiers": [],
            "synonym_groups": {},
            "ambiguities": ["品牌或水果"],
            "resolution_state": "needs_confirmation",
        },
        normalized_input="苹果适合年轻人吗",
    )

    assert decision.state == "needs_confirmation"
    assert decision.reason_codes == (
        "multiple_primary_entities",
        "unresolved_ambiguity",
    )


def test_synonym_groups_require_one_owner_and_unique_values() -> None:
    decision = parse_subject_structure(
        {
            "canonical_subject": "防晒服饰",
            "subject_type": "category",
            "core_entities": [
                {"canonical_name": "防晒服饰", "raw_mentions": ["防晒穿搭"]}
            ],
            "research_intents": ["穿搭"],
            "context_modifiers": ["夏季"],
            "synonym_groups": {
                "未知对象": ["防晒衣"],
                "防晒服饰": ["防晒衣", "防晒衣"],
            },
            "ambiguities": [],
            "resolution_state": "resolved",
        },
        normalized_input="夏季防晒穿搭",
    )

    assert decision.state == "needs_confirmation"
    assert decision.reason_codes == (
        "orphan_synonym_group",
        "duplicate_synonym",
    )
