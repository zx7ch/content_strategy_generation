from datetime import datetime, timezone

from app.content_research.workflow.directional_pipeline import (
    DirectionCoverageCounts,
    build_packet,
    compile_query_groups,
    evaluate_direction_coverage,
    select_candidates,
)


def test_coverage_decision_reports_each_frozen_requirement_independently():
    decision = evaluate_direction_coverage(
        counts=DirectionCoverageCounts(
            discovered=8,
            deduplicated=6,
            relevant=2,
            detail_eligible=2,
            admitted=1,
            independent_authors=1,
            direct_core_support=0,
            explicit_focus_support=0,
            replacement_capacity=0,
        ),
        minimum_samples=3,
        minimum_independent_authors=2,
        requires_explicit_focus=True,
    )

    assert decision.satisfied is False
    assert decision.reason_codes == (
        "minimum_relevant_samples_unmet",
        "minimum_independent_authors_unmet",
        "direct_core_support_unmet",
        "explicit_user_focus_unmet",
        "replacement_capacity_exhausted",
    )


def test_coverage_decision_is_satisfied_without_optional_focus_requirement():
    decision = evaluate_direction_coverage(
        counts=DirectionCoverageCounts(
            discovered=3,
            deduplicated=3,
            relevant=3,
            detail_eligible=3,
            admitted=3,
            independent_authors=2,
            direct_core_support=3,
            explicit_focus_support=0,
            replacement_capacity=1,
        ),
        minimum_samples=3,
        minimum_independent_authors=2,
        requires_explicit_focus=False,
    )

    assert decision.satisfied is True
    assert decision.reason_codes == ()


def test_selection_is_stable_when_candidate_order_changes():
    groups = compile_query_groups(
        direction_id="product_marketing", subject="短裤", questions=["卖点"], competitors=[]
    )
    candidates = [
        {
            "canonical_id": "note_b",
            "query_group_id": groups[0].id,
            "relevance": 1,
            "author_id": "a",
        },
        {
            "canonical_id": "note_a",
            "query_group_id": groups[0].id,
            "relevance": 1,
            "author_id": "b",
        },
    ]
    first = select_candidates(groups=groups, candidates=candidates, author_cap=1)
    second = select_candidates(groups=groups, candidates=list(reversed(candidates)), author_cap=1)
    assert first == second
    assert [item.canonical_source_id for item in first.decisions if item.selected] == [
        "note_a",
        "note_b",
    ]


def test_author_cap_excludes_extra_candidate_without_breaking_selection():
    groups = compile_query_groups(
        direction_id="product_marketing", subject="短裤", questions=["卖点", "口碑"], competitors=[]
    )
    result = select_candidates(
        groups=groups,
        candidates=[
            {"canonical_id": "note_1", "query_group_id": groups[0].id, "author_id": "same"},
            {"canonical_id": "note_2", "query_group_id": groups[1].id, "author_id": "same"},
        ],
        author_cap=1,
    )
    assert result.selected_source_count == 1
    assert "author_cap_reached" in result.decisions[1].reasons
    assert result.coverage_unmet_query_group_ids == (groups[1].id,)
    assert result.status == "incomplete"


def test_packet_hash_changes_only_with_projected_evidence():
    context = {"query": "短裤", "rank": 1}
    first = build_packet(
        direction_id="product_marketing",
        canonical_source_id="note_1",
        fields={"content_text": "轻量"},
        availability={"content_text": "present"},
        retrieval_context=context,
    )
    second = build_packet(
        direction_id="product_marketing",
        canonical_source_id="note_1",
        fields={"content_text": "轻量"},
        availability={"content_text": "present"},
        retrieval_context=context,
    )
    assert first["field_projection_hash"] == second["field_projection_hash"]


def test_time_window_and_unavailable_blocking_fields_are_explicit_exclusions():
    groups = compile_query_groups(
        direction_id="product_marketing", subject="短裤", questions=["卖点"], competitors=[]
    )
    result = select_candidates(
        groups=groups,
        candidates=[
            {
                "canonical_id": "late",
                "query_group_id": groups[0].id,
                "source_published_at": "2026-07-02T00:00:00+00:00",
            },
            {
                "canonical_id": "missing",
                "query_group_id": groups[0].id,
                "blocking_unavailable": True,
            },
        ],
        author_cap=1,
        run_as_of_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    assert all(not item.selected for item in result.decisions)
    reasons = {item.canonical_source_id: item.reasons for item in result.decisions}
    assert "out_of_time_window" in reasons["late"]
    assert "blocking_field_unavailable" in reasons["missing"]
    assert result.status == "insufficient_evidence"


def test_packet_excludes_raw_payload_and_tokens_from_projection_and_context():
    packet = build_packet(
        direction_id="product_marketing",
        canonical_source_id="note_1",
        fields={"content_text": "轻量", "raw_payload": {"secret": True}},
        availability={"content_text": "present"},
        retrieval_context={
            "query": "短裤",
            "access_token": "secret",
            "raw_payload": {"secret": True},
        },
    )
    assert packet["field_projection"] == {"content_text": "轻量"}
    assert packet["retrieval_context"] == {"query": "短裤"}


def test_contract_minimum_sample_and_author_targets_stop_selection():
    groups = compile_query_groups(
        direction_id="product_marketing", subject="短裤", questions=["卖点"], competitors=[]
    )
    result = select_candidates(
        groups=groups,
        candidates=[
            {"canonical_id": "note_a", "query_group_id": groups[0].id, "author_id": "a"},
            {"canonical_id": "note_b", "query_group_id": groups[0].id, "author_id": "b"},
            {"canonical_id": "note_c", "query_group_id": groups[0].id, "author_id": "c"},
        ],
        author_cap=1,
        minimum_samples=2,
        minimum_independent_authors=2,
    )
    assert [item.canonical_source_id for item in result.decisions if item.selected] == [
        "note_a",
        "note_b",
    ]
    assert "retrieval_target_reached" in result.decisions[2].reasons


def test_query_coverage_selects_a_later_uncovered_group_after_global_target():
    groups = compile_query_groups(
        direction_id="product_marketing", subject="短裤", questions=["卖点", "穿搭"], competitors=[]
    )
    candidates = [
        {
            "canonical_id": "group-one-a",
            "query_group_id": groups[0].id,
            "relevance": 4,
            "author_id": "a",
        },
        {
            "canonical_id": "group-one-b",
            "query_group_id": groups[0].id,
            "relevance": 3,
            "author_id": "b",
        },
        {
            "canonical_id": "group-one-surplus",
            "query_group_id": groups[0].id,
            "relevance": 2,
            "author_id": "c",
        },
        {
            "canonical_id": "group-two-a",
            "query_group_id": groups[1].id,
            "relevance": 1,
            "author_id": "d",
        },
    ]

    result = select_candidates(
        groups=groups,
        candidates=candidates,
        author_cap=1,
        minimum_samples=2,
        minimum_independent_authors=2,
    )

    assert [item.canonical_source_id for item in result.decisions if item.selected] == [
        "group-one-a",
        "group-one-b",
        "group-two-a",
    ]
    surplus = next(
        item for item in result.decisions if item.canonical_source_id == "group-one-surplus"
    )
    assert surplus.reasons == ("retrieval_target_reached",)
    assert result.coverage_unmet_query_group_ids == ()
    assert result.status == "complete"


def test_query_coverage_remains_incomplete_when_author_cap_blocks_its_only_candidate():
    groups = compile_query_groups(
        direction_id="product_marketing", subject="短裤", questions=["卖点", "穿搭"], competitors=[]
    )

    result = select_candidates(
        groups=groups,
        candidates=[
            {"canonical_id": "group-one", "query_group_id": groups[0].id, "author_id": "same"},
            {"canonical_id": "group-two", "query_group_id": groups[1].id, "author_id": "same"},
        ],
        author_cap=1,
        minimum_samples=1,
        minimum_independent_authors=1,
    )

    blocked = next(item for item in result.decisions if item.canonical_source_id == "group-two")
    assert "author_cap_reached" in blocked.reasons
    assert "retrieval_target_reached" not in blocked.reasons
    assert result.coverage_unmet_query_group_ids == (groups[1].id,)
    assert result.status == "incomplete"


def test_query_coverage_selection_is_stable_when_candidate_input_order_changes():
    groups = compile_query_groups(
        direction_id="product_marketing", subject="短裤", questions=["卖点", "穿搭"], competitors=[]
    )
    candidates = [
        {
            "canonical_id": "group-one-a",
            "query_group_id": groups[0].id,
            "relevance": 2,
            "author_id": "a",
        },
        {
            "canonical_id": "group-one-b",
            "query_group_id": groups[0].id,
            "relevance": 1,
            "author_id": "b",
        },
        {
            "canonical_id": "group-two",
            "query_group_id": groups[1].id,
            "relevance": 1,
            "author_id": "c",
        },
    ]

    first = select_candidates(
        groups=groups,
        candidates=candidates,
        author_cap=1,
        minimum_samples=2,
        minimum_independent_authors=2,
    )
    second = select_candidates(
        groups=groups,
        candidates=list(reversed(candidates)),
        author_cap=1,
        minimum_samples=2,
        minimum_independent_authors=2,
    )

    assert first == second


def test_incomplete_when_nonempty_candidates_do_not_meet_frozen_sample_and_author_minima():
    groups = compile_query_groups(
        direction_id="product_marketing", subject="短裤", questions=["卖点"], competitors=[]
    )
    result = select_candidates(
        groups=groups,
        candidates=[{"canonical_id": "note_a", "query_group_id": groups[0].id, "author_id": "a"}],
        author_cap=2,
        minimum_samples=2,
        minimum_independent_authors=2,
        detail_fetch_cap=3,
    )

    assert result.selected_source_count == 1
    assert result.eligible_source_count == 1
    assert result.independent_source_count == 1
    assert result.status == "incomplete"


def test_detail_fetch_cap_is_deterministic_and_visible_as_an_exclusion_reason():
    groups = compile_query_groups(
        direction_id="product_marketing", subject="短裤", questions=["卖点"], competitors=[]
    )
    candidates = [
        {
            "canonical_id": "note_c",
            "query_group_id": groups[0].id,
            "relevance": 1,
            "author_id": "c",
        },
        {
            "canonical_id": "note_a",
            "query_group_id": groups[0].id,
            "relevance": 3,
            "author_id": "a",
        },
        {
            "canonical_id": "note_b",
            "query_group_id": groups[0].id,
            "relevance": 2,
            "author_id": "b",
        },
    ]
    result = select_candidates(
        groups=groups,
        candidates=candidates,
        author_cap=1,
        minimum_samples=3,
        minimum_independent_authors=3,
        detail_fetch_cap=2,
    )

    assert [item.canonical_source_id for item in result.decisions if item.selected] == [
        "note_a",
        "note_b",
    ]
    assert "detail_fetch_cap_reached" in result.decisions[2].reasons
    assert result.status == "incomplete"
