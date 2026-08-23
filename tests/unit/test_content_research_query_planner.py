from __future__ import annotations

from datetime import datetime, timezone

from app.content_research.subject_structure import parse_subject_structure
from app.content_research.workflow.direction_registry import ResearchDirectionRegistry
from app.content_research.workflow.query_planner import (
    compile_product_marketing_query_portfolio,
    compile_structured_query_plan,
)


def _structure(*, context_modifiers: list[str] | None = None):
    decision = parse_subject_structure(
        {
            "schema_version": "content_research_subject_structure_v1",
            "canonical_subject": "防晒服饰",
            "subject_type": "category",
            "core_entities": [{"canonical_name": "防晒服饰", "raw_mentions": ["防晒穿搭"]}],
            "research_intents": ["穿搭"],
            "context_modifiers": context_modifiers if context_modifiers is not None else ["夏季"],
            "synonym_groups": {"防晒服饰": ["防晒衣", "防晒服"]},
            "ambiguities": [],
            "resolution_state": "resolved",
        },
        normalized_input="夏季防晒穿搭",
    )
    assert decision.structure is not None
    return decision.structure


def test_product_marketing_registry_exposes_only_the_product_value_proposition_question() -> None:
    direction = ResearchDirectionRegistry().get("product_marketing")

    assert direction is not None
    assert direction.default_questions == ["提炼小红书产品卖点表达"]


def test_product_marketing_portfolio_is_a_then_available_a_b_and_a_c() -> None:
    assert compile_product_marketing_query_portfolio(
        core_object="长袖衬衫",
        product_experience_aspect="凉感",
        context_audience_aspect="夏季 通勤",
    ) == ("长袖衬衫", "长袖衬衫 凉感", "长袖衬衫 夏季 通勤")


def test_product_marketing_portfolio_omits_missing_or_abstract_aspects() -> None:
    assert compile_product_marketing_query_portfolio(
        core_object="长袖衬衫",
        product_experience_aspect=None,
        context_audience_aspect="",
    ) == ("长袖衬衫",)
    assert compile_product_marketing_query_portfolio(
        core_object="长袖衬衫",
        product_experience_aspect="上身感受",
        context_audience_aspect="夏季通勤",
    ) == ("长袖衬衫", "长袖衬衫 夏季通勤")
    assert compile_product_marketing_query_portfolio(
        core_object="长袖衬衫",
        product_experience_aspect="产品卖点分析",
        context_audience_aspect=None,
    ) == ("长袖衬衫",)


def test_product_marketing_portfolio_preserves_user_confirmed_aspects() -> None:
    assert compile_product_marketing_query_portfolio(
        core_object="长袖衬衫",
        product_experience_aspect="产品卖点分析",
        context_audience_aspect="夏季通勤",
        preserve_explicit_aspects=True,
    ) == ("长袖衬衫", "长袖衬衫 产品卖点分析", "长袖衬衫 夏季通勤")


def test_equivalent_non_product_primary_queries_merge_and_retain_roles() -> None:
    run_as_of = datetime(2026, 8, 4, tzinfo=timezone.utc)

    plan = compile_structured_query_plan(
        direction_id="content_performance",
        subject_structure=_structure(),
        explicit_focus="穿搭！",
        run_as_of_at=run_as_of,
    )

    assert len(plan.primary_groups) == 1
    assert plan.primary_groups[0].roles == ("core_intent", "user_focus")
    assert plan.primary_groups[0].query_group.query == "防晒服饰 穿搭"
    assert plan.primary_groups[0].query_group.candidate_limit == 20
    assert plan.primary_groups[0].query_group.time_window == {"end_at": run_as_of.isoformat()}


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
        "direction_id": "content_performance",
        "subject_structure": _structure(),
        "explicit_focus": "通勤",
        "run_as_of_at": datetime(2026, 8, 4, tzinfo=timezone.utc),
    }

    first = compile_structured_query_plan(**kwargs)
    second = compile_structured_query_plan(**kwargs)

    assert first == second
    assert first.plan_hash == second.plan_hash
    assert all("防晒衣" not in item.query_group.query for item in first.primary_groups)
    assert len(first.primary_groups) <= 2
