import asyncio

from app.content_research.persistence_models import (
    ReportDraftRecord,
    ReportFaithfulnessDecisionRecord,
    StageCheckpointRecord,
)
from app.content_research.reporting.execution import ReportExecutionService
from app.content_research.reporting.faithfulness import SemanticAuditResult
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from tests.unit.test_content_research_report_composer import _snapshot


class PassingAudit:
    def __init__(self):
        self.calls = 0

    def audit(self, _snapshot, _draft):
        self.calls += 1
        return SemanticAuditResult("passed", model_version="fake", prompt_version="v1", usage={"total_tokens": 1})


def test_execution_persists_append_only_audit_publication_and_stage_checkpoints(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "report-execution.db"))
    snapshot = _snapshot()
    store.save_result_snapshot(snapshot)
    audit = PassingAudit()

    publication = asyncio.run(ReportExecutionService(store).execute(snapshot, audit))

    assert publication.publication_state == "complete_verified_report"
    assert audit.calls == 1
    checkpoints = store.list_typed_records(StageCheckpointRecord)
    assert {item.stage_name for item in checkpoints} == {"compose", "faithfulness"}
    assert asyncio.run(ReportExecutionService(store).execute(snapshot, audit)).id == publication.id
    assert audit.calls == 1
    assert len(store.list_typed_records(StageCheckpointRecord)) == 2


class FailingAudit:
    def __init__(self):
        self.calls = 0

    def audit(self, _snapshot, _draft):
        self.calls += 1
        return SemanticAuditResult("failed", ("semantic_scope_expansion",))


class OneSectionFailingAudit:
    def audit(self, _snapshot, draft):
        section = next(item for item in draft.sections if item.section_kind == "main_findings")
        return SemanticAuditResult("failed", ("semantic_scope_expansion",), (section.section_id,))


class OneSectionThenPassingAudit:
    def __init__(self):
        self.calls = 0

    def audit(self, _snapshot, draft):
        self.calls += 1
        if self.calls == 1:
            section = next(item for item in draft.sections if item.section_kind == "main_findings")
            return SemanticAuditResult("failed", ("semantic_scope_expansion",), (section.section_id,))
        return SemanticAuditResult("passed", model_version="fake", prompt_version="v1", usage={"total_tokens": 1})


def test_execution_exhausts_rewrites_and_publishes_evidence_only(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "report-retry.db"))
    snapshot = _snapshot()
    store.save_result_snapshot(snapshot)
    audit = FailingAudit()

    publication = asyncio.run(ReportExecutionService(store).execute(snapshot, audit))

    assert audit.calls == 2
    assert publication.publication_state == "evidence_only_report"
    assert publication.has_free_prose is False
    assert len(store.list_typed_records(StageCheckpointRecord)) == 4
    published_draft = store.get_typed_record(ReportDraftRecord, publication.report_draft_id)
    published_decision = store.get_typed_record(ReportFaithfulnessDecisionRecord, publication.faithfulness_decision_id)
    assert published_draft is not None and published_decision is not None
    assert published_draft.previous_version_id is not None
    assert all(section["prose"] is None for section in published_draft.payload["sections"])
    assert published_decision.report_draft_id == published_draft.id


def test_execution_never_marks_an_empty_or_auth_recovery_snapshot_as_complete_verified(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "report-empty-evidence.db"))
    base = _snapshot()
    governed = base.metadata["governed_snapshot"]
    snapshot = base.__class__(
        **{
            **base.__dict__,
            "metadata": {
                "governed_snapshot": {
                    **governed,
                    "claim_cards": [],
                    "citation_groups": [],
                    "weak_signals": [],
                    "cross_direction_records": [],
                    "aggregate_claims": [],
                    "limitations_recovery": [
                        {
                            "direction_id": "product_marketing",
                            "limitations": ["auth_required"],
                            "recovery_actions": ["更新小红书登录态后继续。"],
                        }
                    ],
                },
                "governed_input_fingerprint": "empty-auth-recovery",
            },
        }
    )
    store.save_result_snapshot(snapshot)
    audit = PassingAudit()

    publication = asyncio.run(ReportExecutionService(store).execute(snapshot, audit))

    assert publication.publication_state == "evidence_only_report"
    assert publication.has_free_prose is False
    assert audit.calls == 0
    decision = store.get_typed_record(ReportFaithfulnessDecisionRecord, publication.faithfulness_decision_id)
    assert decision is not None
    assert decision.payload["reason_codes"] == ["insufficient_admitted_evidence"]


def test_execution_publishes_partial_with_only_failed_prose_removed_in_a_new_draft_version(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "report-partial.db"))
    snapshot = _snapshot()
    store.save_result_snapshot(snapshot)

    publication = asyncio.run(ReportExecutionService(store).execute(snapshot, OneSectionFailingAudit()))

    assert publication.publication_state == "partial_verified_report"
    draft = store.get_typed_record(ReportDraftRecord, publication.report_draft_id)
    assert draft is not None
    sections = {item["section_kind"]: item for item in draft.payload["sections"]}
    assert sections["main_findings"]["prose"] is None
    assert sections["core_conclusions"]["prose"] is not None


def test_execution_rewrites_a_failed_section_into_a_distinct_audited_partial_draft(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "report-directed-rewrite.db"))
    snapshot = _snapshot()
    store.save_result_snapshot(snapshot)
    audit = OneSectionThenPassingAudit()

    publication = asyncio.run(ReportExecutionService(store).execute(snapshot, audit))

    assert audit.calls == 2
    assert publication.publication_state == "partial_verified_report"
    drafts = store.list_typed_records(ReportDraftRecord)
    assert len(drafts) == 2
    rewritten = next(item for item in drafts if item.previous_version_id is not None)
    first = next(item for item in drafts if item.id == rewritten.previous_version_id)
    assert rewritten.previous_version_id == first.id
    assert rewritten.payload["sections"] != first.payload["sections"]
    assert publication.omitted_section_ids == (next(
        item["section_id"] for item in first.payload["sections"] if item["section_kind"] == "main_findings"
    ),)
