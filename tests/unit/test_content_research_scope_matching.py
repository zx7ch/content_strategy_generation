from __future__ import annotations

from app.content_research.admission.relevance import evaluate_scope_match
from app.content_research.scope_contract import (
    ScopeConstraint,
    ScopeQueryGroupInput,
    build_scope_contract,
)


def _summer_commute_contract():
    return build_scope_contract(
        workflow_run_id="run_scope_matching",
        research_plan_id="rp_scope_matching",
        version=1,
        constraints=(
            ScopeConstraint(
                "core_object",
                "核心对象",
                "长袖衬衫",
                "required",
                ("衬衫",),
            ),
            ScopeConstraint("season", "季节", "夏季", "required"),
            ScopeConstraint("scenario", "使用场景", "通勤", "required"),
        ),
        query_groups=(
            ScopeQueryGroupInput(
                "夏季 长袖衬衫 通勤",
                "夏季 长袖衬衫 通勤",
                ("长袖衬衫", "夏季", "通勤"),
            ),
        ),
    )


def test_core_alias_and_required_contexts_admit_a_candidate() -> None:
    contract = _summer_commute_contract()

    match = evaluate_scope_match(
        source={
            "canonical_source_id": "note_summer",
            "title": "夏季通勤衬衫",
            "content_text": "轻薄不易皱",
            "tags": [],
            "source_metadata": {"author_id": "author_summer"},
            "retrieval_context": {
                "query_group_ids": [contract.query_groups[0].id],
            },
        },
        contract=contract,
    )

    assert match.constraint_matches["core_object"].status == "matched"
    assert match.constraint_matches["core_object"].evidence == ("衬衫",)
    assert match.constraint_matches["season"].status == "matched"
    assert match.constraint_matches["season"].evidence == ("夏季",)
    assert match.constraint_matches["scenario"].status == "matched"
    assert match.constraint_matches["scenario"].evidence == ("通勤",)
    assert match.query_group_hits == (contract.query_groups[0].id,)
    assert match.scope_contract_version == 1
    assert match.eligibility == "eligible"
    assert match.exclusion_reasons == ()


def test_missing_required_summer_excludes_an_autumn_only_candidate() -> None:
    contract = _summer_commute_contract()

    match = evaluate_scope_match(
        source={
            "canonical_source_id": "note_autumn",
            "title": "秋季通勤衬衫",
            "content_text": "适合早秋办公室",
            "tags": ["通勤", "衬衫"],
            "source_metadata": {"season": "秋季", "author_id": "author_autumn"},
            "retrieval_context": {
                "query_group_ids": [contract.query_groups[0].id],
            },
        },
        contract=contract,
    )

    assert match.constraint_matches["season"].status == "unmatched"
    assert match.eligibility == "excluded"
    assert match.exclusion_reasons == ("required_constraint_unmatched:season",)
