from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from app.content_research.evidence.models import EvidenceLineageRecord, EvidenceRecord
from app.content_research.models import ResearchResultSnapshotRecord, utcnow
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore


@pytest.fixture()
def store(tmp_path):
    return SQLiteContentResearchStore(str(tmp_path / "content_research.db"))


def _normalized_payload(source_id: str = "note_1") -> dict:
    return {
        "schema_version": "content_research_source_payload_v1",
        "provider": "xiaohongshu",
        "source_kind": "search_result",
        "source_url": f"https://www.xiaohongshu.com/explore/{source_id}",
        "canonical_id": source_id,
        "raw_payload_hash": f"hash_{source_id}",
    }


def _record(record_id: str = "ev_1", **overrides) -> EvidenceRecord:
    payload = {
        "id": record_id,
        "workflow_run_id": "wr_1",
        "research_brief_id": "rb_1",
        "research_plan_id": "rp_1",
        "research_direction_id": "rd_1",
        "subagent_task_id": "sat_1",
        "trace_id": "trc_1",
        "schema_version": "content_research_evidence_record_v1",
        "status": "candidate",
        "source_type": "search_result",
        "source_platform": "xiaohongshu",
        "source_url": "https://www.xiaohongshu.com/explore/note_1",
        "source_id": "note_1",
        "evidence_type": "search_result",
        "normalized_payload": _normalized_payload(),
        "title": "夏季徒步短裤怎么选",
        "text_excerpt": "轻量速干",
        "raw_content_ref": "hash_note_1",
        "metrics": {"liked_count": 12},
        "content_hash": "hash_note_1",
        "dedupe_key": "search_result:note_1",
        "retrieval_query": "徒步短裤",
    }
    payload.update(overrides)
    return EvidenceRecord(**payload)


def _lineage(lineage_id: str = "el_1", evidence_id: str = "ev_1", created_at=None) -> EvidenceLineageRecord:
    return EvidenceLineageRecord(
        id=lineage_id,
        workflow_run_id="wr_1",
        evidence_record_id=evidence_id,
        research_brief_id="rb_1",
        research_plan_id="rp_1",
        research_direction_id="rd_1",
        subagent_task_id="sat_1",
        trace_id="trc_1",
        schema_version="content_research_evidence_lineage_v1",
        transformation_type="captured",
        transformation_version="v1",
        lineage_payload={
            "schema_version": "content_research_evidence_lineage_payload_v1",
            "source_id": "note_1",
        },
        created_at=created_at or utcnow(),
    )


def test_evidence_record_can_be_saved_and_queried_by_required_boundaries(store):
    record = _record()
    other = _record(
        "ev_2",
        workflow_run_id="wr_2",
        research_plan_id="rp_2",
        research_direction_id="rd_2",
        subagent_task_id="sat_2",
        source_id="note_2",
        source_url="https://www.xiaohongshu.com/explore/note_2",
        normalized_payload=_normalized_payload("note_2"),
        dedupe_key="search_result:note_2",
    )

    store.save_evidence_record(record)
    store.save_evidence_record(other)

    assert store.get_evidence_record(record.id) == record
    assert store.list_evidence_records(workflow_run_id="wr_1") == [record]
    assert store.list_evidence_records(research_plan_id="rp_1") == [record]
    assert store.list_evidence_records(research_direction_id="rd_1") == [record]
    assert store.list_evidence_records(subagent_task_id="sat_1") == [record]
    assert store.list_evidence_records(workflow_run_id="missing") == []


def test_evidence_record_requires_schema_version_in_normalized_payload(store):
    record = _record(normalized_payload={"source_id": "missing_schema"})

    with pytest.raises(ValueError, match="schema_version"):
        store.save_evidence_record(record)


def test_evidence_record_status_and_metadata_can_be_updated_by_id(store):
    record = _record(metadata={"stage": "captured"})
    updated = replace(record, status="accepted", metadata={"stage": "reviewed"})

    store.save_evidence_record(record)
    store.save_evidence_record(updated)

    assert store.get_evidence_record(record.id) == updated


def test_evidence_lineage_is_append_only_and_ordered(store):
    record = _record()
    first = _lineage("el_1", record.id)
    second = _lineage(
        "el_2",
        record.id,
        created_at=first.created_at + timedelta(seconds=1),
    )

    store.save_evidence_record(record)
    store.append_evidence_lineage(first)
    store.append_evidence_lineage(second)

    assert [item.id for item in store.list_evidence_lineage(record.id)] == ["el_1", "el_2"]
    with pytest.raises(ValueError, match="append-only"):
        store.append_evidence_lineage(replace(first, transformation_type="normalized"))


def test_result_snapshot_is_immutable_by_id(store):
    snapshot = ResearchResultSnapshotRecord(
        id="rrs_1",
        workflow_run_id="wr_1",
        research_brief_id="rb_1",
        research_plan_id="rp_1",
        schema_version="content_research_result_snapshot_v1",
        snapshot_version="1",
        result_type="topic_research",
        status="ready",
        title="Original snapshot",
        executive_summary="Original summary",
        findings=[
            {
                "result_item_id": "ri_1",
                "claim": "Supported claim",
                "summary": "Supported claim",
                "evidence_bundle_id": "eb_1",
                "evidence_bundle_ids": ["eb_1"],
                "support_level": "medium",
                "claim_status": "supported",
            }
        ],
        evidence_bundle_ids=["eb_1"],
        claim_count=1,
        supported_claim_count=1,
    )
    store.save_result_snapshot(snapshot)

    with pytest.raises(ValueError, match="immutable"):
        store.save_result_snapshot(replace(snapshot, executive_summary="Mutated summary"))

    assert store.get_result_snapshot("rrs_1") == snapshot
    assert store.list_result_snapshots_for_workflow("wr_1") == [snapshot]
