from types import SimpleNamespace

import pytest

from app.content_research.admission.candidates import source_text_hash
from app.content_research.marketing_conclusions import evaluate_marketing_conclusions
from app.content_research.persistence_models import (
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
    MarketingConclusionCandidateRecord,
)


def marketing_policy():
    return {
        "marketing_conclusion_policy": {
            "primary_marketing_goal": "content_seeding",
            "tracks": ["need", "value", "message"],
            "minimum_notes_per_conclusion": 3,
            "minimum_independent_authors_per_conclusion": 2,
            "require_core_and_first_intent_support": True,
            "maximum_primary_conclusions_per_track": 1,
        }
    }


def candidate(track, claim_ids, *, statement="样本明确表达凉感", candidate_id="mc_1"):
    return MarketingConclusionCandidateRecord(
        candidate_id, "marketing_conclusion_candidate",
        {"statement": statement, "supporting_claim_ids": list(claim_ids)},
        workflow_run_id="run_1", research_plan_id="plan_1", track=track,
    )


def admitted_claims(*claim_ids, direction="product_marketing", run_id="run_1", decision="admitted"):
    return [
        (
            ClaimAdmissionDecisionRecord(
                f"decision_{claim_id}", "admission", {"policy_snapshot_hash": "frozen", "reason_codes": []},
                research_direction_id=direction, claim_candidate_id=claim_id,
                decision=decision, policy_snapshot_id="snapshot_1",
            ),
            ClaimCandidateRecord(
                claim_id, "claim", {
                    "quote_refs": [{
                        "field_path": "content_text", "quote": "凉感", "text_start": 0,
                        "text_end": 2, "source_text_hash": source_text_hash("凉感"),
                        "source_url": f"https://example/{claim_id}",
                    }],
                    "scope": {"sample": "selected_packets"},
                }, workflow_run_id=run_id, research_direction_id=direction,
                evidence_packet_id=f"packet_{claim_id}", statement="凉感", intent_id="value_proposition",
                claim_type="product_value_expression",
            ),
        )
        for claim_id in claim_ids
    ]


def packets_with_sources_and_authors(*items):
    return [
        DirectionalEvidencePacketRecord(
            id=f"packet_{claim_id}",
            schema_version="packet",
            payload={
                "field_projection": {
                    "author_id": author_id, "content_text": "凉感", "source_url": f"https://example/{claim_id}",
                },
                "field_availability": {"content_text": "present"},
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


def test_evaluator_requires_the_exact_frozen_policy_envelope():
    with pytest.raises(ValueError, match="marketing_conclusion_policy"):
        evaluate_marketing_conclusions(
            candidates=[], admitted_claims=[], packets=[], policy=marketing_policy()["marketing_conclusion_policy"]
        )

    weak_policy = marketing_policy()
    weak_policy["marketing_conclusion_policy"] = {
        **weak_policy["marketing_conclusion_policy"],
        "minimum_notes_per_conclusion": 1,
        "minimum_independent_authors_per_conclusion": 1,
    }
    with pytest.raises(ValueError, match="frozen contract"):
        evaluate_marketing_conclusions(
            candidates=[], admitted_claims=[], packets=[], policy=weak_policy
        )


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
    assert evaluation.catalog == ()


def test_evaluator_rejects_invalid_support_with_stable_reason_codes():
    invalid = evaluate_marketing_conclusions(
        candidates=[candidate("need", ["not_admitted"])],
        admitted_claims=[],
        packets=[],
        policy=marketing_policy(),
    )
    assert invalid.catalog == ()
    assert invalid.tracks["need"].reason_codes == ("conclusion_claim_not_admitted",)

    wrong_direction = evaluate_marketing_conclusions(
        candidates=[candidate("need", ["c1"])],
        admitted_claims=admitted_claims("c1", direction="content_performance"),
        packets=packets_with_sources_and_authors(("c1", "note_1", "author_a")),
        policy=marketing_policy(),
    )
    assert wrong_direction.catalog == ()
    assert wrong_direction.tracks["need"].reason_codes == ("conclusion_claim_direction_mismatch",)

    with pytest.raises(ValueError, match="invalid marketing conclusion track"):
        candidate("outcome", ["c1"])

    causal = evaluate_marketing_conclusions(
        candidates=[candidate("need", ["c1"], statement="凉感带来转化效果提升")],
        admitted_claims=admitted_claims("c1"),
        packets=packets_with_sources_and_authors(("c1", "note_1", "author_a")),
        policy=marketing_policy(),
    )
    assert causal.catalog == ()


def test_evaluator_requires_durable_admission_claim_packet_lineage():
    conclusion = MarketingConclusionCandidateRecord(
        "mc_1", "marketing_conclusion_candidate",
        {"statement": "样本明确表达凉感", "supporting_claim_ids": ["c1", "c2", "c3"]},
        workflow_run_id="run_1", research_plan_id="plan_1", track="value",
    )
    selected = evaluate_marketing_conclusions(
        candidates=[conclusion], admitted_claims=admitted_claims("c1", "c2", "c3"),
        packets=packets_with_sources_and_authors(
            ("c1", "note_1", "author_a"), ("c2", "note_2", "author_a"),
            ("c3", "note_3", "author_b"),
        ), policy=marketing_policy(),
    )
    assert selected.tracks["value"].state == "selected"

    cross_run = evaluate_marketing_conclusions(
        candidates=[conclusion], admitted_claims=admitted_claims("c1", run_id="run_other"),
        packets=packets_with_sources_and_authors(("c1", "note_1", "author_a")),
        policy=marketing_policy(),
    )
    assert cross_run.catalog == ()
    assert cross_run.tracks["value"].reason_codes == ("conclusion_claim_run_mismatch",)

    rejected = evaluate_marketing_conclusions(
        candidates=[conclusion], admitted_claims=admitted_claims("c1", decision="rejected"),
        packets=packets_with_sources_and_authors(("c1", "note_1", "author_a")),
        policy=marketing_policy(),
    )
    assert rejected.catalog == ()
    assert rejected.tracks["value"].reason_codes == ("conclusion_claim_not_admitted",)


def test_evaluator_rejects_a_packet_shaped_object_that_is_not_a_durable_packet_record():
    conclusion = candidate("value", ["c1"])
    packet = packets_with_sources_and_authors(("c1", "note_1", "author_a"))[0]
    impostor = SimpleNamespace(
        id=packet.id,
        workflow_run_id=packet.workflow_run_id,
        research_direction_id=packet.research_direction_id,
        canonical_source_id=packet.canonical_source_id,
        field_projection_hash=packet.field_projection_hash,
        payload=packet.payload,
    )

    evaluation = evaluate_marketing_conclusions(
        candidates=[conclusion], admitted_claims=admitted_claims("c1"),
        packets={packet.id: impostor}, policy=marketing_policy(),
    )

    assert evaluation.catalog == ()
    assert evaluation.tracks["value"].reason_codes == ("conclusion_packet_not_typed",)


def test_evaluator_derives_body_quote_rank_from_validated_quote_metadata():
    def support(claim_id, note_id, author_id, field_path):
        text = "凉感" if field_path == "content_text" else "凉感标题"
        candidate_record = ClaimCandidateRecord(
            claim_id, "claim", {
                "quote_refs": [{
                    "field_path": field_path, "quote": text, "text_start": 0,
                    "text_end": len(text), "source_text_hash": source_text_hash(text),
                    "source_url": f"https://example/{claim_id}",
                }], "scope": {"sample": "selected_packets"},
            }, workflow_run_id="run_1", research_direction_id="product_marketing",
            evidence_packet_id=f"packet_{claim_id}", statement=text, intent_id="message_angle",
            claim_type="message_angle",
        )
        decision = ClaimAdmissionDecisionRecord(
            f"decision_{claim_id}", "admission", {"policy_snapshot_hash": "frozen", "reason_codes": []},
            research_direction_id="product_marketing", claim_candidate_id=claim_id,
            decision="admitted", policy_snapshot_id="snapshot_1",
        )
        packet = DirectionalEvidencePacketRecord(
            f"packet_{claim_id}", "packet", {
                "field_projection": {"author_id": author_id, field_path: text, "source_url": f"https://example/{claim_id}"},
                "field_availability": {field_path: "present"},
            }, workflow_run_id="run_1", research_direction_id="product_marketing",
            canonical_source_id=note_id, field_projection_hash=f"hash_{claim_id}",
        )
        return (decision, candidate_record), packet

    title_supports, title_packets = zip(*(support(f"t{index}", f"note_t{index}", "author_a" if index < 3 else "author_b", "title") for index in range(1, 4)))
    body_supports, body_packets = zip(*(support(f"b{index}", f"note_b{index}", "author_a" if index < 3 else "author_b", "content_text") for index in range(1, 4)))
    evaluation = evaluate_marketing_conclusions(
        candidates=[
            MarketingConclusionCandidateRecord("mc_title", "candidate", {"statement": "标题表达", "supporting_claim_ids": ["t1", "t2", "t3"]}, workflow_run_id="run_1", research_plan_id="plan_1", track="message"),
            MarketingConclusionCandidateRecord("mc_body", "candidate", {"statement": "正文表达", "supporting_claim_ids": ["b1", "b2", "b3"]}, workflow_run_id="run_1", research_plan_id="plan_1", track="message"),
        ], admitted_claims=[*title_supports, *body_supports], packets=[*title_packets, *body_packets], policy=marketing_policy(),
    )
    assert evaluation.tracks["message"].candidate_id == "mc_body"
    assert {item.candidate_id: item.body_quote_note_count for item in evaluation.catalog} == {"mc_title": 0, "mc_body": 3}

    invalid_support, invalid_packet = support("bad", "note_bad", "author_bad", "content_text")
    invalid_claim = invalid_support[1]
    invalid_claim.payload["quote_refs"][0]["field_path"] = "metrics"
    invalid = evaluate_marketing_conclusions(
        candidates=[
            MarketingConclusionCandidateRecord("mc_bad", "candidate", {"statement": "无效字段", "supporting_claim_ids": ["bad"]}, workflow_run_id="run_1", research_plan_id="plan_1", track="message")
        ], admitted_claims=[invalid_support], packets=[invalid_packet], policy=marketing_policy(),
    )
    assert invalid.catalog == ()
    assert invalid.tracks["message"].reason_codes == ("conclusion_quote_field_not_allowed",)


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
