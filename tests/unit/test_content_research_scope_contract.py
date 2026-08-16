from __future__ import annotations

import pytest

from app.content_research.scope_contract import (
    ScopeConstraint,
    ScopeQueryGroupInput,
    build_scope_contract,
)


def _required_constraints() -> tuple[ScopeConstraint, ...]:
    return (
        ScopeConstraint(
            id="core_object",
            label="核心对象",
            value="长袖衬衫",
            retrieval_priority="must_cover",
            evidence_gate="required",
        ),
        ScopeConstraint(
            id="season",
            label="季节",
            value="夏季",
            retrieval_priority="must_cover",
            evidence_gate="required",
        ),
        ScopeConstraint(
            id="scenario",
            label="使用场景",
            value="通勤",
            retrieval_priority="must_cover",
            evidence_gate="required",
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
