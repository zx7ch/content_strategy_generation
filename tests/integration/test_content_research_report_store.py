import sqlite3
from dataclasses import replace

import pytest

from app.content_research.lifecycle.coordinator import (
    ContentResearchPersistenceCoordinator,
    LifecycleCommandConflict,
)
from app.content_research.lifecycle.models import ContentResearchState, LifecycleCommand
from app.content_research.models import ResearchResultSnapshotRecord
from app.content_research.persistence_models import (
    MarketingConclusionCandidateRecord,
    MarketingConclusionDecisionRecord,
    ReportDraftRecord,
    ReportPublicationRecord,
)
from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.reporting.contracts import (
    ReportDraft,
    ReportFaithfulnessDecision,
    ReportPublication,
    ReportSection,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore


def test_marketing_conclusion_records_round_trip_all_terminal_states(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "marketing-conclusions.db"))
    candidate = MarketingConclusionCandidateRecord(
        "mc_1", "marketing_conclusion_candidate", {"statement": "样本表达凉感", "supporting_claim_ids": ["cc_1"]},
        workflow_run_id="run_1", research_plan_id="rp_1", track="need",
    )
    assert store.save_marketing_conclusion_candidate(candidate) == candidate
    assert store.list_marketing_conclusion_candidates("run_1", "rp_1") == [candidate]

    decisions = [
        MarketingConclusionDecisionRecord(
            f"md_{state}", "marketing_conclusion_decision", {"reason_codes": []},
            workflow_run_id="run_1", research_plan_id="rp_1", candidate_id="mc_1" if state in {"selected", "qualified", "directional"} else None,
            track="need", state=state,
        )
        for state in (
            "selected", "qualified", "directional", "insufficient_evidence",
            "no_single_primary_conclusion", "analysis_unavailable",
        )
    ]
    for decision in decisions:
        assert store.save_marketing_conclusion_decision(decision) == decision
    assert store.list_marketing_conclusion_decisions("run_1", "rp_1") == decisions


def test_unknown_provider_result_is_not_committed_and_retry_is_business_idempotent(
    tmp_path,
):
    store = SQLiteContentResearchStore(str(tmp_path / "marketing-replay.db"))
    # An unknown provider outcome reaches no business persistence boundary.
    assert store.list_typed_records(MarketingConclusionCandidateRecord) == []
    assert store.list_typed_records(MarketingConclusionDecisionRecord) == []
    candidate = MarketingConclusionCandidateRecord(
        "mc_replay",
        "marketing_conclusion_candidate_v1",
        {"statement": "通勤场景体感凉爽", "supporting_claim_ids": ["claim-1"]},
        workflow_run_id="run-replay",
        research_plan_id="plan-replay",
        track="need",
    )
    decision = MarketingConclusionDecisionRecord(
        "mcd_replay",
        "marketing_conclusion_decision_v2",
        {
            "input_fingerprint": "contract-1",
            "execution": "completed",
            "decision": "selected",
            "publication_role": "verified",
        },
        workflow_run_id="run-replay",
        research_plan_id="plan-replay",
        candidate_id=candidate.id,
        track="need",
        state="selected",
    )

    assert store.save_marketing_conclusion_candidate(candidate) == candidate
    assert store.save_marketing_conclusion_candidate(candidate) == candidate
    assert store.save_marketing_conclusion_decision(decision) == decision
    assert store.save_marketing_conclusion_decision(decision) == decision

    with pytest.raises(ValueError, match="candidate identity conflict"):
        store.save_marketing_conclusion_candidate(
            replace(candidate, payload={**candidate.payload, "statement": "漂移的结论"})
        )
    with pytest.raises(ValueError, match="decision identity conflict"):
        store.save_marketing_conclusion_decision(
            replace(decision, payload={**decision.payload, "decision": "directional"})
        )


def test_marketing_conclusion_migration_removes_superseded_lite_report_rows(tmp_path):
    db_path = tmp_path / "marketing-conclusion-migration.db"
    SQLiteContentResearchStore(str(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE content_research_marketing_conclusion_decisions")
        conn.execute("DROP TABLE content_research_marketing_conclusion_candidates")
        conn.execute("DELETE FROM content_research_schema_migrations WHERE version = '0016'")
        conn.execute(
            "INSERT INTO content_research_report_drafts "
            "(id, schema_version, workflow_run_id, research_plan_id, governed_snapshot_id, "
            "governed_snapshot_version, input_fingerprint, policy_version, algorithm_version, "
            "payload_json, metadata_json, created_at) "
            "VALUES ('old_draft', 'old', 'run_1', 'rp_1', 'snapshot', '1', 'fingerprint', "
            "'policy', 'algorithm', '{}', '{}', '2026-08-05T00:00:00+00:00')"
        )

    SQLiteContentResearchStore(str(db_path))
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM content_research_report_drafts").fetchone() == (0,)
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'content_research_marketing_conclusion_candidates'"
        ).fetchone() == (1,)


def _snapshot() -> ResearchResultSnapshotRecord:
    return ResearchResultSnapshotRecord(
        id="rrs_1", workflow_run_id="run_1", research_brief_id="rb_1", research_plan_id="rp_1",
        schema_version="content_research_governed_snapshot_v2", snapshot_version="1", result_type="governed_research",
        status="evidence_only_report", title="Governed input", executive_summary="",
        metadata={"governed_snapshot": {"schema_version": "governed_v1"}},
    )


def _draft() -> ReportDraft:
    return ReportDraft(
        workflow_run_id="run_1", research_plan_id="rp_1", governed_snapshot_id="rrs_1", governed_snapshot_version="1",
        input_fingerprint="snapshot_hash", policy_version="policy_v1", algorithm_version="report_v1",
        sections=(
            ReportSection("sec_core", "core_conclusions", claim_candidate_ids=("cc_1",)),
            ReportSection("sec_findings", "main_findings", claim_candidate_ids=("cc_2",)),
            ReportSection("sec_limits", "limitations_scope", limitation_ids=("lim_1",)),
        ),
    )


def _decision(draft: ReportDraft) -> ReportFaithfulnessDecision:
    return ReportFaithfulnessDecision(
        workflow_run_id=draft.workflow_run_id, research_plan_id=draft.research_plan_id,
        governed_snapshot_id=draft.governed_snapshot_id, governed_snapshot_version=draft.governed_snapshot_version,
        input_fingerprint=draft.input_fingerprint, policy_version=draft.policy_version, algorithm_version=draft.algorithm_version,
        report_draft_id=draft.id, audit_state="passed",
    )


def _publication(
    draft: ReportDraft,
    decision: ReportFaithfulnessDecision,
    *,
    compose_mode: str = "prose",
) -> ReportPublication:
    publication = ReportPublication(
        workflow_run_id=draft.workflow_run_id, research_plan_id=draft.research_plan_id,
        governed_snapshot_id=draft.governed_snapshot_id, governed_snapshot_version=draft.governed_snapshot_version,
        input_fingerprint=draft.input_fingerprint, policy_version=draft.policy_version, algorithm_version=draft.algorithm_version,
        report_draft_id=draft.id, faithfulness_decision_id=decision.id, publication_state="complete_verified_report",
        verified_section_ids=tuple(section.section_id for section in draft.sections),
        verified_section_kinds=tuple(section.section_kind for section in draft.sections),
        structured_card_section_ids=tuple(section.section_id for section in draft.sections), audit_recovery_state="all_required_sections_passed", has_free_prose=compose_mode == "prose",
        compose_mode=compose_mode,
    )
    return publication


async def _submit_lifecycle_run(db_path: str, run_id: str) -> ContentResearchPersistenceCoordinator:
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(title="publication race")
    coordinator = ContentResearchPersistenceCoordinator(db_path)
    await coordinator.apply(
        LifecycleCommand(
            command_id=f"submit:{run_id}",
            run_id=run_id,
            expected_state=None,
            expected_revision=0,
            kind="submit_research_subject",
            payload={
                "thread_id": thread["id"],
                "user_id": "user-race",
                "seed_text": "凉感衬衫",
            },
        )
    )
    return coordinator


def _save_publication_lineage(store: SQLiteContentResearchStore, run_id: str) -> ReportPublicationRecord:
    snapshot = replace(_snapshot(), workflow_run_id=run_id)
    draft = replace(_draft(), workflow_run_id=run_id)
    decision = replace(_decision(draft), workflow_run_id=run_id)
    publication = replace(_publication(draft, decision), workflow_run_id=run_id)
    store.save_result_snapshot(snapshot)
    store.save_report_draft(draft.to_record())
    store.save_report_faithfulness_decision(decision.to_record())
    return store.save_report_publication(publication.to_record())


@pytest.mark.asyncio
async def test_cancel_and_publication_first_commit_wins(tmp_path):
    publication_first_db = str(tmp_path / "publication-first.db")
    coordinator = await _submit_lifecycle_run(publication_first_db, "run_1")
    store = SQLiteContentResearchStore(publication_first_db)
    publication = _save_publication_lineage(store, "run_1")
    with pytest.raises(LifecycleCommandConflict, match="publication already committed"):
        await coordinator.apply(
            LifecycleCommand(
                command_id="cancel-after-publication",
                run_id="run_1",
                expected_state=ContentResearchState.PRESEARCH_RUNNING,
                expected_revision=1,
                kind="cancel",
                payload={},
            )
        )
    assert store.get_typed_record(ReportPublicationRecord, publication.id) == publication

    cancel_first_db = str(tmp_path / "cancel-first.db")
    coordinator = await _submit_lifecycle_run(cancel_first_db, "run_2")
    cancelled = await coordinator.apply(
        LifecycleCommand(
            command_id="cancel-before-publication",
            run_id="run_2",
            expected_state=ContentResearchState.PRESEARCH_RUNNING,
            expected_revision=1,
            kind="cancel",
            payload={},
        )
    )
    assert cancelled.state is ContentResearchState.CANCELLED_OR_FAILED
    store = SQLiteContentResearchStore(cancel_first_db)
    with pytest.raises(ValueError, match="after cancellation"):
        _save_publication_lineage(store, "run_2")
    assert store.list_typed_records(ReportPublicationRecord) == []


def test_report_versions_round_trip_append_only_and_keep_previous_version_readable(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "reports.db"))
    store.save_result_snapshot(_snapshot())
    draft = _draft()
    decision = _decision(draft)
    draft_record = draft.to_record()
    decision_record = decision.to_record()
    publication_record = _publication(draft, decision).to_record()

    assert store.save_report_draft(draft_record) == draft_record
    assert store.save_report_faithfulness_decision(decision_record) == decision_record
    assert store.save_report_publication(publication_record) == publication_record
    assert store.get_typed_record(ReportDraftRecord, draft_record.id) == draft_record
    assert store.get_typed_record(ReportPublicationRecord, publication_record.id) == publication_record

    with pytest.raises(Exception):
        store.save_report_draft(draft_record)
    assert store.get_typed_record(ReportDraftRecord, draft_record.id) == draft_record

    next_draft = replace(draft, policy_version="policy_v2", previous_version_id=draft.id)
    next_decision = _decision(next_draft)
    next_publication = replace(_publication(next_draft, next_decision), previous_version_id=publication_record.id)
    store.save_report_draft(next_draft.to_record())
    store.save_report_faithfulness_decision(next_decision.to_record())
    store.save_report_publication(next_publication.to_record())
    assert store.get_typed_record(ReportPublicationRecord, publication_record.id) == publication_record
    assert store.get_typed_record(ReportPublicationRecord, next_publication.id).previous_version_id == publication_record.id


def test_report_publication_rejects_missing_report_parents_and_snapshot_identity(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "missing-report-parent.db"))
    draft = _draft()
    decision = _decision(draft)

    with pytest.raises(ValueError, match="missing governed snapshot"):
        store.save_report_draft(draft.to_record())
    store.save_result_snapshot(_snapshot())
    with pytest.raises(ValueError, match="missing report draft"):
        store.save_report_faithfulness_decision(decision.to_record())


def test_composed_draft_round_trip_preserves_persisted_citation_anchors(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "composed-report.db"))
    snapshot = _snapshot()
    snapshot = replace(
        snapshot,
        metadata={
            "governed_input_fingerprint": "composed_hash",
            "governed_snapshot": {
                "policy_scope": {"effective_policy_hash": "policy_v1"},
                "claim_cards": [{"claim_candidate_id": "cc_1", "statement": "样本提到通勤场景。"}],
                "citation_groups": [{"citation_group_id": "citation_4", "display_index": 4, "claim_candidate_id": "cc_1"}],
                "weak_signals": [],
                "cross_direction_records": [],
                "aggregate_claims": [],
                "limitations_recovery": [],
            },
        },
    )
    store.save_result_snapshot(snapshot)

    record = ResearchReportComposer().compose(snapshot).to_record()
    store.save_report_draft(record)
    saved = store.get_typed_record(ReportDraftRecord, record.id)

    assert saved is not None
    assert saved.payload["sections"][0]["citation_anchors"][0]["citation_group_id"] == "citation_4"
