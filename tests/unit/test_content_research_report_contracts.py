from dataclasses import replace

import pytest

from app.content_research.reporting.contracts import (
    CitationAnchor,
    ReportDraft,
    ReportFaithfulnessDecision,
    ReportPublication,
    ReportSection,
)


def _section(kind: str, *, prose: str | None = None) -> ReportSection:
    section_id = f"sec_{kind}"
    common = {
        "section_id": section_id,
        "section_kind": kind,
        "prose": prose,
        "citation_anchors": (
            CitationAnchor("anchor_1", section_id, "block_1", 0, len(prose), "cg_1"),
        ) if prose else (),
    }
    if kind == "limitations_scope":
        return ReportSection(**common, limitation_ids=("lim_1",), citation_group_ids=("cg_1",) if prose else ())
    if kind == "cross_direction_tensions":
        return ReportSection(**common, cross_direction_record_ids=("cdr_1",), citation_group_ids=("cg_1",) if prose else ())
    if kind == "weak_signals":
        return ReportSection(**common, weak_signal_ids=("ws_1",), citation_group_ids=("cg_1",) if prose else ())
    if kind == "next_steps":
        return ReportSection(**common, aggregate_claim_ids=("ac_1",), citation_group_ids=("cg_1",) if prose else ())
    return ReportSection(**common, claim_candidate_ids=("cc_1",), citation_group_ids=("cg_1",) if prose else ())


def _draft(*, snapshot_id: str = "rrs_1", policy_version: str = "policy_v1") -> ReportDraft:
    return ReportDraft(
        workflow_run_id="run_1",
        research_plan_id="rp_1",
        governed_snapshot_id=snapshot_id,
        governed_snapshot_version="1",
        input_fingerprint="snapshot_hash",
        policy_version=policy_version,
        algorithm_version="report_v1",
        sections=(_section("core_conclusions", prose="Core conclusion."), _section("main_findings", prose="Main finding."), _section("limitations_scope", prose="Scope limit.")),
    )


def _decision(draft: ReportDraft) -> ReportFaithfulnessDecision:
    return ReportFaithfulnessDecision(
        workflow_run_id=draft.workflow_run_id,
        research_plan_id=draft.research_plan_id,
        governed_snapshot_id=draft.governed_snapshot_id,
        governed_snapshot_version=draft.governed_snapshot_version,
        input_fingerprint=draft.input_fingerprint,
        policy_version=draft.policy_version,
        algorithm_version=draft.algorithm_version,
        report_draft_id=draft.id,
        audit_state="passed",
    )


def _publication(draft: ReportDraft, decision: ReportFaithfulnessDecision, **changes: object) -> ReportPublication:
    values = {
        "workflow_run_id": draft.workflow_run_id,
        "research_plan_id": draft.research_plan_id,
        "governed_snapshot_id": draft.governed_snapshot_id,
        "governed_snapshot_version": draft.governed_snapshot_version,
        "input_fingerprint": draft.input_fingerprint,
        "policy_version": draft.policy_version,
        "algorithm_version": draft.algorithm_version,
        "report_draft_id": draft.id,
        "faithfulness_decision_id": decision.id,
        "publication_state": "complete_verified_report",
        "verified_section_ids": tuple(section.section_id for section in draft.sections),
        "verified_section_kinds": tuple(section.section_kind for section in draft.sections),
        "structured_card_section_ids": tuple(section.section_id for section in draft.sections),
        "audit_recovery_state": "all_required_sections_passed",
        "has_free_prose": True,
    }
    return ReportPublication(**(values | changes))


def test_draft_identity_is_stable_and_changes_for_frozen_inputs():
    draft = _draft()

    assert _draft().id == draft.id
    assert _draft(snapshot_id="rrs_2").id != draft.id
    assert _draft(policy_version="policy_v2").id != draft.id
    assert replace(draft, previous_version_id="rpd_old").id != draft.id


def test_section_requires_governed_provenance_and_optional_section_specific_provenance():
    with pytest.raises(ValueError, match="governed-snapshot references"):
        ReportSection(section_id="sec", section_kind="main_findings")
    with pytest.raises(ValueError, match="citation-group"):
        ReportSection(
            section_id="sec_prose",
            section_kind="main_findings",
            prose="Unsupported prose",
            claim_candidate_ids=("cc_1",),
        )
    with pytest.raises(ValueError, match="cross-direction records"):
        ReportSection(section_id="sec_tension", section_kind="cross_direction_tensions", claim_candidate_ids=("cc_1",))
    with pytest.raises(ValueError, match="action or recovery"):
        ReportSection(section_id="sec_next", section_kind="next_steps", claim_candidate_ids=("cc_1",))


def test_publication_state_matrix_requires_core_sections_and_excludes_evidence_only_prose():
    draft = _draft()
    decision = _decision(draft)
    assert _publication(draft, decision).publication_state == "complete_verified_report"

    with pytest.raises(ValueError, match="core audited"):
        _publication(draft, decision, verified_section_kinds=("core_conclusions", "main_findings"))
    with pytest.raises(ValueError, match="partial report requires omitted"):
        _publication(
            draft,
            decision,
            publication_state="partial_verified_report",
            verified_section_kinds=("core_conclusions",),
        )
    with pytest.raises(ValueError, match="cannot contain free prose"):
        _publication(
            draft,
            decision,
            publication_state="evidence_only_report",
            verified_section_kinds=(),
            has_free_prose=True,
        )
    with pytest.raises(ValueError, match="materialized snapshot"):
        _publication(draft, decision, artifact_kind="draft")
    with pytest.raises(ValueError, match="exactly one artifact-result"):
        _publication(draft, decision, final_message_count=2)


def test_partial_and_evidence_only_contracts_preserve_their_respective_boundaries():
    draft = _draft()
    decision = _decision(draft)

    partial = _publication(
        draft,
        decision,
        publication_state="partial_verified_report",
        verified_section_kinds=("core_conclusions",),
        omitted_section_ids=("sec_main_findings",),
        audit_recovery_state="semantic_audit_unavailable",
    )
    evidence_only = _publication(
        draft,
        decision,
        publication_state="evidence_only_report",
        verified_section_ids=(),
        verified_section_kinds=(),
        structured_card_section_ids=("sec_core_conclusions",),
        audit_recovery_state="compose_unavailable",
        has_free_prose=False,
    )

    assert partial.omitted_section_ids == ("sec_main_findings",)
    assert evidence_only.has_free_prose is False
