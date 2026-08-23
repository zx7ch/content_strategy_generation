from __future__ import annotations

import pytest

from app.content_research.scope_contract import (
    SCOPE_CONTRACT_SCHEMA_VERSION_V2,
    ScopeConstraint,
    ScopeQueryGroupInput,
    build_scope_contract,
    classify_query_group,
)


def _required_constraints() -> tuple[ScopeConstraint, ...]:
    return (
        ScopeConstraint(
            id="core_object",
            label="核心对象",
            value="长袖衬衫",
            mode="required",
        ),
        ScopeConstraint(
            id="season",
            label="季节",
            value="夏季",
            mode="required",
        ),
        ScopeConstraint(
            id="scenario",
            label="使用场景",
            value="通勤",
            mode="required",
        ),
    )


def test_user_query_missing_required_terms_is_exploratory_not_invalid() -> None:
    contract = build_scope_contract(
        workflow_run_id="run_1",
        research_plan_id="rp_1",
        version=1,
        constraints=_required_constraints(),
        query_groups=(
            ScopeQueryGroupInput(
                suggested_query="夏季 长袖衬衫 通勤",
                final_query="白衬衫通勤穿搭",
            ),
        ),
    )

    group = contract.query_groups[0]
    assert group.origin == "user_edited"
    assert group.execution_role == "exploratory"
    assert group.suggested_query == "夏季 长袖衬衫 通勤"
    assert group.final_query == "白衬衫通勤穿搭"


def test_full_required_query_is_coverage_group() -> None:
    contract = build_scope_contract(
        workflow_run_id="run_1",
        research_plan_id="rp_1",
        version=1,
        constraints=_required_constraints(),
        query_groups=(
            ScopeQueryGroupInput(
                suggested_query="夏季 长袖衬衫 通勤",
                final_query="夏季 长袖衬衫 通勤",
            ),
        ),
    )

    assert contract.query_groups[0].origin == "system_suggested"
    assert contract.query_groups[0].execution_role == "coverage"


def test_scope_contract_rejects_more_than_three_or_blank_queries() -> None:
    with pytest.raises(ValueError, match="at most 3"):
        build_scope_contract(
            workflow_run_id="run_1",
            research_plan_id="rp_1",
            version=1,
            constraints=_required_constraints(),
            query_groups=tuple(
                ScopeQueryGroupInput("夏季 长袖衬衫 通勤", f"检索 {index}")
                for index in range(4)
            ),
        )
    with pytest.raises(ValueError, match="final_query"):
        build_scope_contract(
            workflow_run_id="run_1",
            research_plan_id="rp_1",
            version=1,
            constraints=_required_constraints(),
            query_groups=(ScopeQueryGroupInput("夏季 长袖衬衫 通勤", "  "),),
        )


@pytest.mark.parametrize(
    "constraints",
    (
        (
            ScopeConstraint("season", "季节", "夏季", "required"),
        ),
        (
            ScopeConstraint("core_object", "核心对象", "衬衫", "required"),
            ScopeConstraint("core_object", "核心对象", "长袖衬衫", "required"),
        ),
    ),
)
def test_scope_contract_requires_exactly_one_core_object(
    constraints: tuple[ScopeConstraint, ...],
) -> None:
    with pytest.raises(ValueError, match="exactly one core_object"):
        build_scope_contract(
            workflow_run_id="run_1",
            research_plan_id="rp_1",
            version=1,
            constraints=constraints,
            query_groups=(ScopeQueryGroupInput("夏季 长袖衬衫", "夏季 长袖衬衫"),),
        )


def test_classify_query_group_marks_targeted_missing_required_term_as_supplementary() -> None:
    assert (
        classify_query_group(
            "夏季 长袖衬衫",
            required_terms=("夏季", "长袖衬衫", "通勤"),
            targeted_required_terms=("夏季",),
        )
        == "supplementary"
    )


def test_constraint_has_one_user_controlled_mode() -> None:
    constraint = ScopeConstraint("season", "季节", "夏季", "preferred")

    assert constraint.mode == "preferred"


def test_query_group_id_is_unique_to_its_scope_contract() -> None:
    first = build_scope_contract(
        workflow_run_id="run_1",
        research_plan_id="rp_1",
        version=1,
        constraints=_required_constraints(),
        query_groups=(ScopeQueryGroupInput("夏季 长袖衬衫 通勤", "夏季 长袖衬衫 通勤"),),
    )
    second = build_scope_contract(
        workflow_run_id="run_2",
        research_plan_id="rp_2",
        version=1,
        constraints=_required_constraints(),
        query_groups=(ScopeQueryGroupInput("夏季 长袖衬衫 通勤", "夏季 长袖衬衫 通勤"),),
    )

    assert first.query_groups[0].id != second.query_groups[0].id


def test_v2_product_scope_requires_only_core_object() -> None:
    contract = build_scope_contract(
        workflow_run_id="run_v2",
        research_plan_id="rp_v2",
        version=1,
        schema_version=SCOPE_CONTRACT_SCHEMA_VERSION_V2,
        constraints=(
            ScopeConstraint("core_object", "核心对象", "长袖衬衫", "required"),
        ),
        query_groups=(
            ScopeQueryGroupInput("长袖衬衫", "长袖衬衫"),
            ScopeQueryGroupInput("长袖衬衫 凉感", "长袖衬衫 凉感"),
            ScopeQueryGroupInput("长袖衬衫 夏季通勤", "长袖衬衫 夏季通勤"),
        ),
    )

    assert contract.schema_version == SCOPE_CONTRACT_SCHEMA_VERSION_V2
    assert [item.id for item in contract.constraints if item.mode == "required"] == [
        "core_object"
    ]
    assert [item.execution_role for item in contract.query_groups] == [
        "coverage",
        "coverage",
        "coverage",
    ]
