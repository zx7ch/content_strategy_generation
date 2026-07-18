from datetime import datetime, timezone

import pytest

from app.content_research.contracts import RunPolicySnapshot, build_default_snapshot, policy_hash


def test_default_snapshot_has_one_contract_for_each_registered_direction():
    snapshot, policies, contracts = build_default_snapshot(snapshot_id="rps_test", workflow_run_id="run_1", brief_id="rb_1", plan_id="rp_1", run_as_of_at=datetime(2026, 7, 17, tzinfo=timezone.utc))
    assert snapshot.effective_policy_hash == policy_hash(snapshot.effective_policy)
    assert len(contracts) == len(policies) == 7
    assert {item.direction_id for item in contracts} == set(snapshot.effective_policy["direction_ids"])


def test_snapshot_rejects_wrong_hash_and_naive_time():
    with pytest.raises(ValueError, match="effective_policy_hash"):
        RunPolicySnapshot(id="rps_1", workflow_run_id="run", research_brief_id="rb", research_plan_id="rp", schema_version="v1", effective_policy={"a": 1}, effective_policy_hash="wrong", run_as_of_at=datetime.now(timezone.utc))
    with pytest.raises(ValueError, match="timezone-aware"):
        build_default_snapshot(snapshot_id="rps_1", workflow_run_id="run", brief_id="rb", plan_id="rp", run_as_of_at=datetime(2026, 1, 1))
