from __future__ import annotations

import pytest

from app.content_research.evidence.governance_reader import GovernanceReadModelReader
from app.content_research.persistence_models import AggregateClaimRecord, CrossDirectionRecord
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore


def _seed_governance(store: SQLiteContentResearchStore) -> None:
    for index in range(3):
        store.save_cross_direction_record(CrossDirectionRecord(
            f"cdr_{index}", "v1",
            {
                "workflow_run_id": "run_a",
                "claim_ids": [f"claim_{index}"],
                "canonical_source_ids": ["shared_source"],
                "classification": "contradiction",
                "reason": "opposite_polarity",
                "resolution_state": "unresolved",
                "raw_payload": {"cookie": "must-not-leak"},
                "system_prompt": "must-not-leak",
            },
            research_plan_id="plan_a", record_type="contradiction",
        ))
        store.save_aggregate_claim(AggregateClaimRecord(
            f"agg_{index}", "v1",
            {
                "workflow_run_id": "run_a",
                "source_claim_ids": [f"claim_{index}"],
                "canonical_source_ids": ["shared_source"],
                "derivation_method": "corroboration",
                "scope_intersection": {"topic": "comfort"},
                "inherited_limitations": ["sample_limited"],
                "request_origin": "formal_workflow",
                "token": "must-not-leak",
            },
            research_plan_id="plan_a", aggregate_type="cross_direction_corroboration",
        ))
    store.save_cross_direction_record(CrossDirectionRecord(
        "cdr_other", "v1", {"workflow_run_id": "run_b", "claim_ids": ["claim_0"]},
        research_plan_id="plan_b", record_type="overlap",
    ))
    store.save_aggregate_claim(AggregateClaimRecord(
        "agg_wrong_plan", "v1", {"workflow_run_id": "run_a", "source_claim_ids": ["claim_0"]},
        research_plan_id="plan_b", aggregate_type="cross_direction_corroboration",
    ))


def test_governance_reader_scopes_paginates_orders_and_sanitizes(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "governance-reader.db"))
    _seed_governance(store)

    reader = GovernanceReadModelReader(store)
    first = reader.read(workflow_run_id="run_a", research_plan_id="plan_a", offset=0, limit=2)
    second = reader.read(workflow_run_id="run_a", research_plan_id="plan_a", offset=2, limit=2)

    assert first.cross_direction_total == first.aggregate_total == 3
    assert [item["cross_direction_record_id"] for item in first.cross_direction_records] == ["cdr_0", "cdr_1"]
    assert [item["cross_direction_record_id"] for item in second.cross_direction_records] == ["cdr_2"]
    assert [item["aggregate_claim_id"] for item in first.aggregate_claims] == ["agg_0", "agg_1"]
    assert [item["aggregate_claim_id"] for item in second.aggregate_claims] == ["agg_2"]
    assert "raw_payload" not in first.cross_direction_records[0]
    assert "system_prompt" not in first.cross_direction_records[0]
    assert "token" not in first.aggregate_claims[0]
    assert first.aggregate_claims[0]["scope_intersection"] == {"topic": "comfort"}
    assert first.aggregate_claims[0]["inherited_limitations"] == ["sample_limited"]
    assert first.aggregate_claims[0]["request_origin"] == "formal_workflow"


@pytest.mark.parametrize("offset,limit", [(-1, 1), (0, 0), (0, 51)])
def test_governance_reader_rejects_invalid_pagination(tmp_path, offset, limit):
    reader = GovernanceReadModelReader(SQLiteContentResearchStore(str(tmp_path / "invalid-page.db")))

    with pytest.raises(ValueError, match="offset"):
        reader.read(workflow_run_id="run_a", research_plan_id="plan_a", offset=offset, limit=limit)
