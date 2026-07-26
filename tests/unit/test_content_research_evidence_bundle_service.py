from __future__ import annotations

import pytest

from app.content_research.evidence import (
    EvidenceBundleItemRecord,
    EvidenceBundleRecord,
    EvidenceBundleService,
    EvidenceService,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore


@pytest.fixture()
def store(tmp_path):
    return SQLiteContentResearchStore(str(tmp_path / "content_research.db"))


def _source_payload(note_id: str, *, source_kind: str = "search_result") -> dict:
    return {
        "schema_version": "content_research_source_payload_v1",
        "provider": "xiaohongshu",
        "source_kind": source_kind,
        "source_url": f"https://www.xiaohongshu.com/explore/{note_id}",
        "canonical_id": note_id,
        "captured_at": "2026-07-05T00:00:00+00:00",
        "raw_payload_hash": f"hash_{note_id}",
        "cookie_status": "valid",
        "failure_reason": None,
        "query_used": "徒步短裤",
        "title": f"note {note_id}",
        "content_text": "轻量速干",
        "author": "户外作者",
        "metrics": {"liked_count": 12},
    }


def _bundle() -> EvidenceBundleRecord:
    return EvidenceBundleRecord(
        id="eb_1",
        workflow_run_id="wr_1",
        research_brief_id="rb_1",
        research_plan_id="rp_1",
        research_direction_id="rd_1",
        schema_version="content_research_evidence_bundle_v1",
        status="ready",
        bundle_type="research_direction",
        bundle_version="v1",
        summary="通勤场景内容更容易获得收藏。",
        coverage={"schema_version": "content_research_bundle_coverage_v1", "source_count": 2},
        missing_evidence=[
            {
                "schema_version": "content_research_missing_evidence_v1",
                "question": "缺少自有账号表现数据",
            }
        ],
    )


def _item(item_id: str, bundle_id: str, role: str, sort_order: int, evidence_id: str | None = None) -> EvidenceBundleItemRecord:
    return EvidenceBundleItemRecord(
        id=item_id,
        bundle_id=bundle_id,
        evidence_record_id=evidence_id,
        role=role,
        sort_order=sort_order,
        schema_version="content_research_evidence_bundle_item_v1",
        payload={
            "schema_version": "content_research_evidence_bundle_item_payload_v1",
            "note": role,
        },
    )


def test_evidence_service_ingests_source_payload_and_captured_lineage(store):
    service = EvidenceService(store)

    record = service.ingest_source_payload(
        workflow_run_id="wr_1",
        research_brief_id="rb_1",
        research_plan_id="rp_1",
        research_direction_id="rd_1",
        subagent_task_id="sat_1",
        trace_id="trc_1",
        source_payload=_source_payload("note_1"),
    )

    assert record.id.startswith("ev_")
    assert record.source_platform == "xiaohongshu"
    assert record.source_url == "https://www.xiaohongshu.com/explore/note_1"
    assert record.research_plan_id == "rp_1"
    lineage = store.list_evidence_lineage(record.id)
    assert len(lineage) == 1
    assert lineage[0].transformation_type == "captured"
    assert lineage[0].lineage_payload["source_id"] == "note_1"


def test_evidence_bundle_service_expands_roles_lineage_sources_and_missing_evidence(store):
    evidence_service = EvidenceService(store)
    bundle_service = EvidenceBundleService(store)
    supporting = evidence_service.ingest_source_payload(workflow_run_id="wr_1", source_payload=_source_payload("note_1"))
    conflicting = evidence_service.ingest_source_payload(workflow_run_id="wr_1", source_payload=_source_payload("note_2"))
    bundle = _bundle()

    bundle_service.create_bundle(
        bundle,
        [
            _item("ebi_2", bundle.id, "conflicting_fact", 2, conflicting.id),
            _item("ebi_1", bundle.id, "supporting_fact", 1, supporting.id),
            _item("ebi_3", bundle.id, "missing_evidence", 3, None),
        ],
    )

    expanded = bundle_service.expand_bundle(bundle.id)

    assert expanded is not None
    assert expanded.bundle == bundle
    assert [item.id for item in expanded.items] == ["ebi_1", "ebi_2", "ebi_3"]
    assert expanded.evidence_by_role["supporting_fact"] == [supporting]
    assert expanded.evidence_by_role["conflicting_fact"] == [conflicting]
    assert expanded.lineage_by_evidence_id[supporting.id][0].transformation_type == "captured"
    assert {
        "evidence_id": supporting.id,
        "source_url": supporting.source_url,
        "source_id": supporting.source_id,
        "source_platform": "xiaohongshu",
    } in expanded.source_links
    assert len(expanded.missing_evidence) == 2
    assert expanded.missing_evidence[0]["question"] == "缺少自有账号表现数据"
    assert expanded.missing_evidence[1]["note"] == "missing_evidence"


def test_bundle_service_rejects_unknown_non_missing_evidence(store):
    bundle_service = EvidenceBundleService(store)

    with pytest.raises(ValueError, match="Evidence record not found"):
        bundle_service.create_bundle(
            _bundle(),
            [_item("ebi_missing", "eb_1", "supporting_fact", 1, "ev_missing")],
        )


def test_bundle_service_allows_missing_evidence_without_record_id(store):
    bundle_service = EvidenceBundleService(store)
    bundle = _bundle()

    bundle_service.create_bundle(bundle, [_item("ebi_missing", bundle.id, "missing_evidence", 1)])

    expanded = bundle_service.expand_bundle(bundle.id)
    assert expanded is not None
    assert expanded.items[0].role == "missing_evidence"
