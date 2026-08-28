from __future__ import annotations

from app.content_research.marketing_evidence import (
    AtomicMarketingEvidence,
    MarketingEvidenceCluster,
    cluster_atomic_marketing_evidence,
    lexical_evidence_vectors,
    verify_marketing_candidate,
)
from app.content_research.persistence_models import MarketingConclusionCandidateRecord
from app.content_research.service import _match_analysis_atoms_to_claim_cards


def atom(
    atom_id: str,
    quote: str,
    *,
    scene: str,
    audience: str = "",
    polarity: str = "support",
):
    return AtomicMarketingEvidence(
        atom_id=atom_id,
        claim_id=f"claim-{atom_id}",
        track="value",
        note_id=f"note-{atom_id}",
        account_id=f"author-{atom_id}",
        field_path="content_text",
        quote=quote,
        text_start=0,
        text_end=len(quote),
        polarity=polarity,
        scenes=(scene,),
        audiences=(audience,) if audience else (),
    )


def test_atomic_evidence_grouping_merges_synonyms_but_partitions_incompatible_qualifiers():
    commute = atom("commute", "夏季通勤穿着凉爽透气", scene="通勤")
    commute_synonym = atom("commute-synonym", "通勤时透气而且很凉爽", scene="通勤")
    child = atom("child", "儿童运动时透气而且很凉爽", scene="运动", audience="儿童")
    atoms = (commute, commute_synonym, child)
    vectors = lexical_evidence_vectors(atoms)

    clusters = cluster_atomic_marketing_evidence(
        atoms, vectors, similarity_threshold=0.35
    )

    memberships = [set(item.atom_ids) for item in clusters]
    assert {"commute", "commute-synonym"} in memberships
    assert {"child"} in memberships


def test_atomic_evidence_grouping_rejects_missing_or_zero_vectors():
    evidence = atom("one", "通勤时凉爽", scene="通勤")
    try:
        cluster_atomic_marketing_evidence((evidence,), {})
    except ValueError as exc:
        assert "vector is missing" in str(exc)
    else:  # pragma: no cover - failure makes the assertion readable
        raise AssertionError("missing vectors must fail closed")


def test_analysis_atom_lineage_resolves_only_one_exact_manifest_owned_claim():
    evidence = AtomicMarketingEvidence(
        atom_id="atom-need",
        claim_id="synthetic-analysis-claim",
        track="need",
        note_id="note-1",
        account_id="author-1",
        field_path="content_text",
        quote="夏季通勤穿着凉爽。",
        text_start=0,
        text_end=9,
        polarity="support",
        scenes=("夏季通勤",),
        audiences=(),
    )
    cards = [{
        "claim_candidate_id": "persisted-claim-1",
        "claim_type": "use_context",
        "canonical_source_id": "note-1",
        "evidence_refs": [{
            "field_path": "content_text",
            "quote": "夏季通勤穿着凉爽",
            "text_start": 0,
            "text_end": 8,
        }],
    }]

    assert _match_analysis_atoms_to_claim_cards((evidence,), cards) == {
        "atom-need": "persisted-claim-1"
    }


def test_groundedness_verifier_marks_threshold_counter_evidence_as_contested():
    support = tuple(
        atom(f"support-{index}", "通勤时凉爽透气", scene="通勤")
        for index in range(3)
    )
    counters = tuple(
        atom(
            f"counter-{index}",
            "通勤时没有凉感",
            scene="通勤",
            polarity="counter",
        )
        for index in range(2)
    )
    atoms = (*support, *counters)
    cluster = MarketingEvidenceCluster(
        "cluster-one",
        "value",
        tuple(item.atom_id for item in atoms),
        ("通勤",),
        (),
    )
    candidate = MarketingConclusionCandidateRecord(
        "candidate-one",
        "marketing_conclusion_candidate_v1",
        {
            "statement": "样本在通勤场景提到凉爽透气",
            "supporting_claim_ids": [item.claim_id for item in support],
        },
        workflow_run_id="run-one",
        research_plan_id="plan-one",
        track="value",
    )

    verified = verify_marketing_candidate(
        candidate, atoms=atoms, clusters=(cluster,)
    )

    assert verified.state == "contested"
    assert verified.counter_note_count == 2
    assert verified.counter_author_count == 2
    assert verified.counter_atom_ids == tuple(item.atom_id for item in counters)


def test_groundedness_verifier_keeps_subthreshold_counter_as_a_visible_limitation():
    support = tuple(
        atom(f"support-low-{index}", "通勤时凉爽透气", scene="通勤")
        for index in range(3)
    )
    counter = atom(
        "counter-low",
        "通勤时没有凉感",
        scene="通勤",
        polarity="counter",
    )
    atoms = (*support, counter)
    cluster = MarketingEvidenceCluster(
        "cluster-low",
        "value",
        tuple(item.atom_id for item in atoms),
        ("通勤",),
        (),
    )
    candidate = MarketingConclusionCandidateRecord(
        "candidate-low",
        "marketing_conclusion_candidate_v1",
        {
            "statement": "样本在通勤场景提到凉爽透气",
            "supporting_claim_ids": [item.claim_id for item in support],
        },
        workflow_run_id="run-one",
        research_plan_id="plan-one",
        track="value",
    )

    verified = verify_marketing_candidate(
        candidate, atoms=atoms, clusters=(cluster,)
    )

    assert verified.state == "verified"
    assert verified.counter_atom_ids == (counter.atom_id,)
    assert verified.counter_note_count == 1
