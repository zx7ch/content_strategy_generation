from __future__ import annotations

from app.content_research.subject_structure import parse_subject_structure


def test_unspaced_input_is_tokenized_before_terms_are_mapped_to_search_roles() -> None:
    decision = parse_subject_structure(
        {
            "schema_version": "content_research_subject_structure_v1",
            "canonical_subject": "夏季凉感T恤",
            "subject_type": "category",
            "source_terms": ["夏季", "凉感", "T恤"],
            "term_roles": {
                "core_object": ["T恤"],
                "product_experience": ["凉感"],
                "context_audience": ["夏季"],
            },
            # Legacy role fields must not override the grounded term mapping.
            "core_entities": [
                {"canonical_name": "夏季凉感T恤", "raw_mentions": ["夏季凉感T恤"]}
            ],
            "research_intents": ["透气性"],
            "context_modifiers": ["通勤"],
            "synonym_groups": {},
            "ambiguities": [],
            "resolution_state": "resolved",
        },
        normalized_input="夏季凉感T恤",
    )

    assert decision.state == "confirmed"
    assert decision.reason_codes == ()
    assert decision.structure is not None
    assert decision.structure.source_terms == ("夏季", "凉感", "T恤")
    assert decision.structure.core_entities[0].canonical_name == "T恤"
    assert decision.structure.research_intents == ("凉感",)
    assert decision.structure.context_modifiers == ("夏季",)


def test_spaced_input_uses_the_user_segments_without_secondary_tokenization() -> None:
    decision = parse_subject_structure(
        {
            "schema_version": "content_research_subject_structure_v1",
            "canonical_subject": "夏季 凉感 T恤",
            "subject_type": "category",
            "source_terms": ["夏季", "凉感", "T恤"],
            "term_roles": {
                "core_object": ["T恤"],
                "product_experience": ["凉感"],
                "context_audience": ["夏季"],
            },
            "core_entities": [],
            "research_intents": [],
            "context_modifiers": [],
            "synonym_groups": {},
            "ambiguities": [],
            "resolution_state": "resolved",
        },
        normalized_input="夏季 凉感 T恤",
    )

    assert decision.state == "confirmed"
    assert decision.reason_codes == ()
    assert decision.structure is not None
    assert decision.structure.source_terms == ("夏季", "凉感", "T恤")


def test_term_mapping_rejects_terms_that_tokenization_did_not_produce() -> None:
    decision = parse_subject_structure(
        {
            "schema_version": "content_research_subject_structure_v1",
            "canonical_subject": "夏季凉感T恤",
            "subject_type": "category",
            "source_terms": ["夏季", "凉感", "T恤"],
            "term_roles": {
                "core_object": ["T恤"],
                "product_experience": ["透气性", "舒适度"],
                "context_audience": ["夏季"],
            },
            "core_entities": [],
            "research_intents": [],
            "context_modifiers": [],
            "synonym_groups": {},
            "ambiguities": [],
            "resolution_state": "resolved",
        },
        normalized_input="夏季凉感T恤",
    )

    assert decision.state == "needs_confirmation"
    assert decision.reason_codes == ("source_term_mapping_invalid",)
    assert decision.structure is not None
    assert decision.structure.research_intents == ()
    assert decision.structure.context_modifiers == ("夏季",)
    assert "透气性" not in str(decision.structure)
    assert "舒适度" not in str(decision.structure)


def test_mapped_terms_are_projected_in_source_order_not_model_array_order() -> None:
    decision = parse_subject_structure(
        {
            "canonical_subject": "夏季通勤凉感T恤",
            "subject_type": "category",
            "source_terms": ["夏季", "通勤", "凉感", "T恤"],
            "term_roles": {
                "core_object": ["T恤"],
                "product_experience": ["凉感"],
                "context_audience": ["通勤", "夏季"],
            },
            "ambiguities": [],
            "resolution_state": "resolved",
        },
        normalized_input="夏季通勤凉感T恤",
    )

    assert decision.state == "confirmed"
    assert decision.structure is not None
    assert decision.structure.context_modifiers == ("夏季 通勤",)


def test_invalid_mapped_core_cannot_fall_back_to_a_legacy_executable_entity() -> None:
    decision = parse_subject_structure(
        {
            "canonical_subject": "夏季凉感T恤",
            "source_terms": ["夏季", "凉感", "T恤"],
            "term_roles": {
                "core_object": ["服装"],
                "product_experience": ["凉感"],
                "context_audience": ["夏季"],
            },
            "core_entities": [
                {"canonical_name": "服装", "raw_mentions": ["服装"]}
            ],
            "ambiguities": [],
            "resolution_state": "resolved",
        },
        normalized_input="夏季凉感T恤",
    )

    assert decision.state == "needs_confirmation"
    assert decision.structure is not None
    assert decision.structure.core_entities == ()
    assert decision.structure.research_intents == ("凉感",)
    assert "core_entity_missing" in decision.reason_codes


def test_required_term_mapping_never_uses_legacy_executable_fields() -> None:
    decision = parse_subject_structure(
        {
            "canonical_subject": "夏季凉感T恤",
            "core_entities": [
                {"canonical_name": "服装", "raw_mentions": ["服装"]}
            ],
            "research_intents": ["透气性"],
            "context_modifiers": ["年轻人"],
            "ambiguities": [],
            "resolution_state": "resolved",
        },
        normalized_input="夏季凉感T恤",
        require_term_mapping=True,
    )

    assert decision.state == "needs_confirmation"
    assert decision.structure is not None
    assert decision.structure.core_entities == ()
    assert decision.structure.research_intents == ()
    assert decision.structure.context_modifiers == ()
    assert "source_terms_missing" in decision.reason_codes
    assert "core_entity_missing" in decision.reason_codes


def test_compound_core_is_reduced_by_terms_already_mapped_to_product_and_context() -> None:
    decision = parse_subject_structure(
        {
            "schema_version": "content_research_subject_structure_v1",
            "canonical_subject": "凉感T恤",
            "subject_type": "category",
            "source_terms": ["夏季", "凉感", "T恤"],
            "term_roles": {
                "core_object": ["凉感T恤"],
                "product_experience": ["凉感"],
                "context_audience": ["夏季"],
            },
            "core_entities": [
                {"canonical_name": "凉感T恤", "raw_mentions": ["凉感T恤"]}
            ],
            "research_intents": ["凉感"],
            "context_modifiers": ["夏季"],
            "synonym_groups": {"凉感T恤": ["清凉T恤"]},
            "ambiguities": [],
            "resolution_state": "resolved",
        },
        normalized_input="夏季凉感T恤",
    )

    assert decision.state == "confirmed"
    assert decision.reason_codes == ()
    assert decision.structure is not None
    assert decision.structure.core_entities[0].canonical_name == "T恤"
    assert decision.structure.research_intents == ("凉感",)
    assert decision.structure.context_modifiers == ("夏季",)
    assert decision.structure.synonym_groups == ()


def test_grounded_single_core_entity_is_confirmed_for_lite() -> None:
    decision = parse_subject_structure(
        {
            "schema_version": "content_research_subject_structure_v1",
            "canonical_subject": "防晒服饰",
            "subject_type": "category",
            "core_entities": [
                {
                    "canonical_name": "防晒服饰",
                    "raw_mentions": ["防晒"],
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
    assert decision.structure.core_entities[0].raw_mentions == ("防晒",)


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
                {"canonical_name": "防晒服饰", "raw_mentions": ["防晒"]}
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


def test_complete_brand_name_is_not_rejected_for_an_unmentioned_analysis_role() -> None:
    decision = parse_subject_structure(
        {
            "canonical_subject": "Satisfy Running",
            "subject_type": "brand",
            "core_entities": [
                {
                    "canonical_name": "Satisfy Running",
                    "raw_mentions": ["Satisfy Running"],
                }
            ],
            "research_intents": ["品牌内容"],
            "context_modifiers": [],
            "synonym_groups": {},
            "ambiguities": [],
            "resolution_state": "resolved",
        },
        normalized_input="Satisfy Running",
    )

    assert decision.state == "confirmed"
    assert decision.reason_codes == ()


def test_product_experience_term_is_optional() -> None:
    decision = parse_subject_structure(
        {
            "canonical_subject": "T恤",
            "subject_type": "category",
            "core_entities": [{"canonical_name": "T恤", "raw_mentions": ["T恤"]}],
            "research_intents": ["", "凉感"],
            "context_modifiers": ["夏季"],
            "synonym_groups": {},
            "ambiguities": [],
            "resolution_state": "resolved",
        },
        normalized_input="夏季凉感T恤",
    )

    assert decision.state == "confirmed"
    assert decision.reason_codes == ()
