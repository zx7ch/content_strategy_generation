from __future__ import annotations

from datetime import datetime, timezone

from app.content_research.subject_structure import parse_subject_structure
from app.content_research.workflow.query_planner import compile_structured_query_plan


def _structure():
    decision = parse_subject_structure(
        {
            "schema_version": "content_research_subject_structure_v1",
            "canonical_subject": "防晒服饰",
            "subject_type": "category",
            "core_entities": [{"canonical_name": "防晒服饰", "raw_mentions": ["防晒穿搭"]}],
            "research_intents": ["穿搭"],
            "context_modifiers": ["夏季"],
            "synonym_groups": {"防晒服饰": ["防晒衣", "防晒服"]},
            "ambiguities": [],
            "resolution_state": "resolved",
        },
        normalized_input="夏季防晒穿搭",
    )
    assert decision.structure is not None
    return decision.structure


def test_product_marketing_q2_keeps_intent_and_uses_goal_facet() -> None:
    plan = compile_structured_query_plan(
        direction_id="product_marketing",
        subject_structure=_structure(),
        primary_marketing_goal="content_seeding",
        run_as_of_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )

    assert [group.query_group.query for group in plan.primary_groups] == [
        "防晒服饰 穿搭",
        "防晒服饰 穿搭 上身感受",
    ]
    assert plan.primary_groups[1].role == "goal_facet"


def test_product_marketing_custom_focus_replaces_only_the_facet() -> None:
    plan = compile_structured_query_plan(
        direction_id="product_marketing",
        subject_structure=_structure(),
        explicit_focus="通勤",
        primary_marketing_goal="content_seeding",
        run_as_of_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )

    assert plan.primary_groups[1].query_group.query == "防晒服饰 穿搭 通勤"
    assert plan.primary_groups[1].role == "user_focus"


def test_non_product_marketing_query_compilation_is_unchanged() -> None:
    plan = compile_structured_query_plan(
        direction_id="content_performance",
        subject_structure=_structure(),
        explicit_focus="通勤",
        second_facet="使用场景",
        run_as_of_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )

    assert plan.primary_groups[1].query_group.query == "防晒服饰 通勤"


def test_compilation_hash_and_order_are_stable_and_synonyms_are_not_primary() -> None:
    kwargs = {
        "direction_id": "product_marketing",
        "subject_structure": _structure(),
        "explicit_focus": "通勤",
        "second_facet": "使用场景",
        "primary_marketing_goal": "content_seeding",
        "run_as_of_at": datetime(2026, 8, 4, tzinfo=timezone.utc),
    }

    first = compile_structured_query_plan(**kwargs)
    second = compile_structured_query_plan(**kwargs)

    assert first == second
    assert first.plan_hash == second.plan_hash
    assert all("防晒衣" not in item.query_group.query for item in first.primary_groups)
    assert len(first.primary_groups) <= 2
