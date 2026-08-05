from app.content_research.marketing_conclusions import (
    AdmittedMarketingClaim,
    MarketingConclusionProposal,
    evaluate_marketing_conclusions,
)
from app.content_research.persistence_models import (
    DirectionalEvidencePacketRecord,
    MarketingConclusionCandidateRecord,
)


def marketing_policy():
    return {
        "tracks": ["need", "value", "message"],
        "minimum_notes_per_conclusion": 3,
        "minimum_independent_authors_per_conclusion": 2,
        "require_core_and_first_intent_support": True,
        "maximum_primary_conclusions_per_track": 1,
    }


def candidate(track, claim_ids, *, statement="样本明确表达凉感", candidate_id="mc_1"):
    return MarketingConclusionProposal(
        id=candidate_id,
        track=track,
        statement=statement,
        supporting_claim_ids=tuple(claim_ids),
    )


def admitted_claims(*claim_ids, direction="product_marketing"):
    return [
        AdmittedMarketingClaim(
            claim_id=claim_id,
            research_direction_id=direction,
            evidence_packet_id=f"packet_{claim_id}",
        )
        for claim_id in claim_ids
    ]


def packets_with_sources_and_authors(*items):
    return [
        DirectionalEvidencePacketRecord(
            id=f"packet_{claim_id}",
            schema_version="packet",
            payload={
                "field_projection": {"author_id": author_id},
                "canonical_note_id": note_id,
                "body_quote_count": 1,
            },
            workflow_run_id="run_1",
            research_direction_id="product_marketing",
            canonical_source_id=note_id,
            field_projection_hash=f"hash_{claim_id}",
        )
        for claim_id, note_id, author_id in items
    ]


def test_evaluator_selects_one_conclusion_from_three_notes_and_two_authors():
    evaluation = evaluate_marketing_conclusions(
        candidates=[candidate("need", ["c1", "c2", "c3"])],
        admitted_claims=admitted_claims("c1", "c2", "c3"),
        packets=packets_with_sources_and_authors(
            ("c1", "note_1", "author_a"),
            ("c2", "note_2", "author_a"),
            ("c3", "note_3", "author_b"),
        ),
        policy=marketing_policy(),
    )

    assert evaluation.tracks["need"].state == "selected"
    assert evaluation.tracks["need"].supporting_note_count == 3
    assert evaluation.tracks["need"].independent_author_count == 2


def test_evaluator_accepts_durable_candidate_record():
    evaluation = evaluate_marketing_conclusions(
        candidates=[
            MarketingConclusionCandidateRecord(
                "mc_1", "marketing_conclusion_candidate",
                {"statement": "样本明确表达凉感", "supporting_claim_ids": ["c1", "c2", "c3"]},
                workflow_run_id="run_1", research_plan_id="plan_1", track="need",
            )
        ],
        admitted_claims=admitted_claims("c1", "c2", "c3"),
        packets=packets_with_sources_and_authors(
            ("c1", "note_1", "author_a"),
            ("c2", "note_2", "author_a"),
            ("c3", "note_3", "author_b"),
        ),
        policy=marketing_policy(),
    )

    assert evaluation.tracks["need"].state == "selected"


def test_evaluator_does_not_count_multiple_claims_from_one_note_twice():
    evaluation = evaluate_marketing_conclusions(
        candidates=[candidate("value", ["c1", "c2", "c3"])],
        admitted_claims=admitted_claims("c1", "c2", "c3"),
        packets=packets_with_sources_and_authors(
            ("c1", "note_1", "author_a"),
            ("c2", "note_1", "author_a"),
            ("c3", "note_2", "author_b"),
        ),
        policy=marketing_policy(),
    )

    assert evaluation.tracks["value"].state == "insufficient_evidence"
    assert evaluation.tracks["value"].reason_codes == ("conclusion_note_count_unmet",)


def test_evaluator_rejects_invalid_support_with_stable_reason_codes():
    invalid = evaluate_marketing_conclusions(
        candidates=[candidate("need", ["not_admitted"])],
        admitted_claims=[],
        packets=[],
        policy=marketing_policy(),
    )
    assert invalid.catalog[0].reason_codes == ("conclusion_claim_not_admitted",)

    wrong_direction = evaluate_marketing_conclusions(
        candidates=[candidate("need", ["c1"])],
        admitted_claims=admitted_claims("c1", direction="content_performance"),
        packets=packets_with_sources_and_authors(("c1", "note_1", "author_a")),
        policy=marketing_policy(),
    )
    assert wrong_direction.catalog[0].reason_codes == ("conclusion_claim_direction_mismatch",)

    unsupported = evaluate_marketing_conclusions(
        candidates=[candidate("outcome", ["c1"])],
        admitted_claims=admitted_claims("c1"),
        packets=packets_with_sources_and_authors(("c1", "note_1", "author_a")),
        policy=marketing_policy(),
    )
    assert unsupported.catalog[0].reason_codes == ("conclusion_track_not_supported",)

    causal = evaluate_marketing_conclusions(
        candidates=[candidate("need", ["c1"], statement="凉感带来转化效果提升")],
        admitted_claims=admitted_claims("c1"),
        packets=packets_with_sources_and_authors(("c1", "note_1", "author_a")),
        policy=marketing_policy(),
    )
    assert causal.catalog[0].reason_codes == ("conclusion_statement_outcome_term_prohibited",)


def test_evaluator_merges_duplicate_statement_with_same_normalized_support_set():
    evaluation = evaluate_marketing_conclusions(
        candidates=[
            candidate("message", ["c2", "c1", "c3"], candidate_id="mc_z"),
            candidate("message", ["c1", "c2", "c3"], candidate_id="mc_a"),
        ],
        admitted_claims=admitted_claims("c1", "c2", "c3"),
        packets=packets_with_sources_and_authors(
            ("c1", "note_1", "author_a"),
            ("c2", "note_2", "author_a"),
            ("c3", "note_3", "author_b"),
        ),
        policy=marketing_policy(),
    )

    assert len(evaluation.catalog) == 1
    assert evaluation.tracks["message"].state == "selected"


def test_evaluator_never_uses_candidate_id_to_break_equal_support_ties():
    evaluation = evaluate_marketing_conclusions(
        candidates=[
            candidate("need", ["c1", "c2", "c3"], statement="表达一", candidate_id="mc_z"),
            candidate("need", ["c1", "c2", "c3"], statement="表达二", candidate_id="mc_a"),
        ],
        admitted_claims=admitted_claims("c1", "c2", "c3"),
        packets=packets_with_sources_and_authors(
            ("c1", "note_1", "author_a"),
            ("c2", "note_2", "author_a"),
            ("c3", "note_3", "author_b"),
        ),
        policy=marketing_policy(),
    )

    assert evaluation.tracks["need"].state == "no_single_primary_conclusion"
    assert evaluation.tracks["need"].candidate_id is None


def test_safe_trace_payload_exposes_only_counts_and_reason_codes():
    evaluation = evaluate_marketing_conclusions(
        candidates=[candidate("need", ["c1", "c2", "c3"], statement="不得泄露")],
        admitted_claims=admitted_claims("c1", "c2", "c3"),
        packets=packets_with_sources_and_authors(
            ("c1", "note_private", "author_private"),
            ("c2", "note_private2", "author_private"),
            ("c3", "note_private3", "author_other"),
        ),
        policy=marketing_policy(),
    )

    payload = evaluation.safe_trace_payload()
    assert payload == {
        "tracks": {
            "need": {
                "state": "selected",
                "supporting_note_count": 3,
                "independent_author_count": 2,
                "body_quote_note_count": 3,
                "reason_codes": (),
            },
            "value": {
                "state": "insufficient_evidence",
                "supporting_note_count": 0,
                "independent_author_count": 0,
                "body_quote_note_count": 0,
                "reason_codes": ("conclusion_no_qualified_candidate",),
            },
            "message": {
                "state": "insufficient_evidence",
                "supporting_note_count": 0,
                "independent_author_count": 0,
                "body_quote_note_count": 0,
                "reason_codes": ("conclusion_no_qualified_candidate",),
            },
        }
    }
