from dataclasses import replace

import pytest

from app.content_research.models import ResearchResultSnapshotRecord
from app.content_research.reporting.composer import ResearchReportComposer


def _snapshot(**changes: object) -> ResearchResultSnapshotRecord:
    governed = {
        "policy_scope": {"effective_policy_hash": "policy_hash", "run_as_of_at": "2026-07-19T00:00:00+00:00"},
        "claim_cards": [
            {
                "claim_candidate_id": "cc_1",
                "admission_decision_id": "cad_1",
                "admission_state": "admitted",
                "direction_id": "product_marketing",
                "claim_type": "observation",
                "statement": "样本直接提到通勤场景。",
            }
        ],
        "citation_groups": [
            {
                "citation_group_id": "citation_7",
                "display_index": 7,
                "claim_candidate_id": "cc_1",
                "evidence_refs": [{"field_path": "content_text", "quote": "通勤", "text_start": 0, "text_end": 2, "source_text_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "source_url": "https://example.test/note"}],
            }
        ],
        "weak_signals": [
            {"weak_signal_id": "ws_1", "claim_candidate_id": "cc_weak"},
        ],
        "cross_direction_records": [
            {
                "cross_direction_record_id": "cdr_1",
                "record_type": "contradiction",
                "source_claim_ids": ["cc_1"],
            },
            {
                "cross_direction_record_id": "cdr_same_direction",
                "record_type": "same_direction_tension",
                "source_claim_ids": ["cc_1"],
            },
        ],
        "aggregate_claims": [
            {
                "aggregate_claim_id": "ac_requested",
                "aggregate_type": "action_hypothesis",
                "request_origin": "user_requested_next_steps",
                "source_claim_ids": ["cc_1"],
            },
            {
                "aggregate_claim_id": "ac_unrequested",
                "aggregate_type": "action_hypothesis",
                "request_origin": "automatic_from_overlap",
                "source_claim_ids": ["cc_1"],
            },
        ],
        "limitations_recovery": [
            {"direction_id": "ugc_community", "limitations": ["sample_insufficient"], "recovery_actions": ["collect_more"]},
        ],
    }
    values = {
        "id": "rrs_1",
        "workflow_run_id": "run_1",
        "research_brief_id": "rb_1",
        "research_plan_id": "rp_1",
        "schema_version": "content_research_governed_snapshot_v2",
        "snapshot_version": "2",
        "result_type": "topic_research",
        "status": "partial_verified_report",
        "title": "Research",
        "executive_summary": "",
        "metadata": {"governed_snapshot": governed, "governed_input_fingerprint": "frozen_snapshot_hash"},
    }
    return ResearchResultSnapshotRecord(**(values | changes))


def _sections(draft):
    return {section.section_kind: section for section in draft.sections}


def test_composer_uses_one_governed_snapshot_with_stable_sections_and_anchors():
    composer = ResearchReportComposer()
    snapshot = _snapshot()

    draft = composer.compose(snapshot)
    replay = composer.compose(snapshot)
    sections = _sections(draft)

    assert draft.id == replay.id
    assert draft.workflow_run_id == snapshot.workflow_run_id
    assert draft.governed_snapshot_id == snapshot.id
    assert draft.input_fingerprint == "frozen_snapshot_hash"
    assert set(sections) == {
        "core_conclusions", "main_findings", "cross_direction_tensions", "weak_signals", "next_steps", "limitations_scope",
    }
    assert sections["main_findings"].citation_group_ids == ("citation_7",)
    assert sections["main_findings"].citation_anchors[0].citation_group_id == "citation_7"
    assert sections["main_findings"].citation_anchors[0].text_end == len(sections["main_findings"].prose)
    assert sections["next_steps"].aggregate_claim_ids == ("ac_requested",)
    assert "ac_unrequested" not in sections["next_steps"].aggregate_claim_ids
    assert sections["cross_direction_tensions"].cross_direction_record_ids == ("cdr_1",)


def test_composer_never_promotes_weak_signal_or_relabels_same_direction_tension():
    governed = _snapshot().metadata["governed_snapshot"]
    snapshot = _snapshot(metadata={"governed_snapshot": {**governed, "claim_cards": [], "citation_groups": [], "cross_direction_records": [governed["cross_direction_records"][1]], "aggregate_claims": []}, "governed_input_fingerprint": "empty_hash"})

    sections = _sections(ResearchReportComposer().compose(snapshot))

    assert sections["core_conclusions"].prose is None
    assert sections["main_findings"].prose is None
    assert "weak_signals" in sections
    assert "cross_direction_tensions" not in sections


@pytest.mark.parametrize(
    "change, expected",
    [
        ({"schema_version": "legacy_bundle_snapshot"}, "governed snapshot v2"),
        ({"research_plan_id": None}, "research_plan_id"),
        ({"metadata": {"governed_snapshot": {}, "governed_input_fingerprint": "fp"}}, "policy_scope"),
    ],
)
def test_composer_rejects_invalid_snapshot_identity_or_shape(change, expected):
    with pytest.raises(ValueError, match=expected):
        ResearchReportComposer().compose(_snapshot(**change))


def test_composer_rejects_material_without_frozen_citation_and_keeps_display_index_frozen():
    snapshot = _snapshot()
    governed = snapshot.metadata["governed_snapshot"]
    missing_citation = replace(snapshot, metadata={"governed_snapshot": {**governed, "citation_groups": []}, "governed_input_fingerprint": "changed"})

    with pytest.raises(ValueError, match="no frozen citation group"):
        ResearchReportComposer().compose(missing_citation)

    draft = ResearchReportComposer().compose(snapshot)
    assert draft.sections[0].citation_group_ids == ("citation_7",)


def test_citation_anchor_contract_rejects_unpersisted_or_unknown_anchor_reference():
    draft = ResearchReportComposer().compose(_snapshot())
    section = _sections(draft)["core_conclusions"]

    with pytest.raises(ValueError, match="declared citation group"):
        replace(section, citation_anchors=(replace(section.citation_anchors[0], citation_group_id="citation_unknown"),))
    with pytest.raises(ValueError, match="partially overlap"):
        replace(
            section,
            citation_anchors=(
                section.citation_anchors[0],
                replace(section.citation_anchors[0], anchor_id="anchor_partial", text_start=1),
            ),
        )
