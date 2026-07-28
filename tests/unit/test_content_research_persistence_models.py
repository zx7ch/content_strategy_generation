import pytest

from app.content_research.persistence_models import (
    ReportDraftRecord,
    ReportFaithfulnessDecisionRecord,
    ReportPublicationRecord,
)


def _record(record_type: type[object], legacy_key: str) -> object:
    common = {
        "id": f"{record_type.__name__}_{legacy_key}",
        "schema_version": "content_research_report_v1",
        "payload": {"nested": {legacy_key: "legacy-reference"}},
        "workflow_run_id": "run_legacy",
        "research_plan_id": "plan_legacy",
        "governed_snapshot_id": "snapshot_legacy",
        "governed_snapshot_version": "1",
        "input_fingerprint": "fingerprint_legacy",
        "policy_version": "policy_v1",
        "algorithm_version": "report_v1",
    }
    if record_type is ReportFaithfulnessDecisionRecord:
        return ReportFaithfulnessDecisionRecord(
            **common, report_draft_id="draft_legacy"
        )
    if record_type is ReportPublicationRecord:
        return ReportPublicationRecord(
            **common,
            report_draft_id="draft_legacy",
            faithfulness_decision_id="decision_legacy",
            publication_state="complete_verified_report",
        )
    return ReportDraftRecord(**common)


@pytest.mark.parametrize(
    "record_type",
    [ReportDraftRecord, ReportFaithfulnessDecisionRecord, ReportPublicationRecord],
)
@pytest.mark.parametrize("legacy_key", ["evidence_bundle_id", "evidence_bundle_ids"])
def test_report_persistence_records_reject_legacy_bundle_references(
    record_type: type[object], legacy_key: str
) -> None:
    with pytest.raises(ValueError, match="legacy bundle/result fields"):
        _record(record_type, legacy_key)
