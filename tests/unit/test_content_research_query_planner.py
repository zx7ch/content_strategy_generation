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


def test_compiler_freezes_two_primary_groups_and_one_inactive_fallback() -> None:
    run_as_of = datetime(2026, 8, 4, tzinfo=timezone.utc)

    plan = compile_structured_query_plan(
        direction_id="product_marketing",
        subject_structure=_structure(),
        explicit_focus="通勤",
        second_facet="使用场景",
        run_as_of_at=run_as_of,
    )

    assert [item.role for item in plan.primary_groups] == [
        "core_intent",
        "user_focus",
    ]
    assert [item.query_group.query for item in plan.primary_groups] == [
        "防晒服饰 穿搭",
        "防晒服饰 通勤",
    ]
    assert all(item.activation == "primary" for item in plan.primary_groups)
    assert all(item.query_group.candidate_limit == 20 for item in plan.primary_groups)
    assert all(
        item.query_group.time_window == {"end_at": run_as_of.isoformat()}
        for item in plan.primary_groups
    )
    assert plan.fallback_group is not None
    assert plan.fallback_group.role == "coverage_fallback"
    assert plan.fallback_group.activation == "coverage_fallback"
    assert plan.fallback_group.query_group.query == "防晒衣 夏季"
    assert plan.fallback_group.query_group.candidate_limit == 20
    assert plan.plan_hash


def test_equivalent_primary_queries_merge_and_retain_both_logical_roles() -> None:
    plan = compile_structured_query_plan(
        direction_id="product_marketing",
        subject_structure=_structure(),
        explicit_focus="穿搭！",
        second_facet="",
        run_as_of_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )

    assert len(plan.primary_groups) == 1
    assert plan.primary_groups[0].roles == ("core_intent", "user_focus")
    assert plan.primary_groups[0].query_group.query == "防晒服饰 穿搭"


def test_q2_uses_second_facet_only_when_user_focus_is_absent() -> None:
    plan = compile_structured_query_plan(
        direction_id="product_marketing",
        subject_structure=_structure(),
        explicit_focus="",
        second_facet="购买决策",
        run_as_of_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )

    assert [item.query_group.query for item in plan.primary_groups] == [
        "防晒服饰 穿搭",
        "防晒服饰 购买决策",
    ]
    assert plan.primary_groups[1].role == "direction_facet"


def test_compilation_hash_and_order_are_stable_and_synonyms_are_not_primary() -> None:
    kwargs = {
        "direction_id": "product_marketing",
        "subject_structure": _structure(),
        "explicit_focus": "通勤",
        "second_facet": "使用场景",
        "run_as_of_at": datetime(2026, 8, 4, tzinfo=timezone.utc),
    }

    first = compile_structured_query_plan(**kwargs)
    second = compile_structured_query_plan(**kwargs)

    assert first == second
    assert first.plan_hash == second.plan_hash
    assert all("防晒衣" not in item.query_group.query for item in first.primary_groups)
    assert len(first.primary_groups) <= 2
