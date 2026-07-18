from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.content_research.contracts import build_default_snapshot
from app.content_research.runtime import (
    STAGE_SEQUENCE,
    CheckpointRuntime,
    LLMCostLedger,
    canonical_fingerprint,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore


def test_actual_llm_cost_is_idempotent_even_when_completion_is_reported_concurrently(tmp_path):
    db_path = str(tmp_path / "runtime.db")
    SQLiteContentResearchStore(db_path)

    def record():
        return LLMCostLedger(db_path).record_actual(
            research_plan_id="rp_1", usage_event_id="usage_1", amount=0.0125,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        entries = list(executor.map(lambda _: record(), range(2)))
    assert entries[0] == entries[1]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*), reserved_amount, consumed_amount, reservation_status "
            "FROM content_research_budget_ledger_entries"
        ).fetchone() == (1, 0.0, 0.0125, "committed")


def test_missing_provider_usage_is_explicitly_cost_unknown_not_an_estimate(tmp_path):
    db_path = str(tmp_path / "runtime.db")
    SQLiteContentResearchStore(db_path)
    entry = LLMCostLedger(db_path).record_unknown(
        research_plan_id="rp_1", usage_event_id="usage_missing", reason="provider omitted usage",
    )
    assert entry.amount is None
    assert entry.status == "cost_unknown"
    with sqlite3.connect(db_path) as conn:
        status, payload = conn.execute(
            "SELECT reservation_status, payload_json FROM content_research_budget_ledger_entries"
        ).fetchone()
    assert status == "cost_unknown"
    assert '"cost_status": "unknown"' in payload


def test_frozen_policy_is_visibility_only_and_has_no_hard_cost_or_source_call_cap():
    snapshot, _, _ = build_default_snapshot(
        snapshot_id="rps_1", workflow_run_id="run_1", brief_id="rb_1", plan_id="rp_1",
    )
    assert snapshot.effective_policy["llm_cost_policy"] == {
        "currency": "USD", "warning_threshold_usd": 0.50, "max_report_rewrites": 1,
    }
    assert "llm_budget_policy" not in snapshot.effective_policy
    assert "hard_cap_usd" not in snapshot.effective_policy["llm_cost_policy"]
    assert "external_source_call_budget" not in snapshot.effective_policy


@pytest.mark.parametrize("stage", STAGE_SEQUENCE)
def test_every_formal_stage_persists_recoverable_interruption_and_resumes_from_it(tmp_path, stage):
    db_path = str(tmp_path / "runtime.db")
    SQLiteContentResearchStore(db_path)
    runtime = CheckpointRuntime(db_path)
    for completed_stage in STAGE_SEQUENCE[:STAGE_SEQUENCE.index(stage)]:
        runtime.checkpoint(
            subagent_task_id="task_1", stage_name=completed_stage,
            input_fingerprint=canonical_fingerprint({"stage": completed_stage}), status="completed",
            output_refs=(f"out:{completed_stage}",), usage_event_ids=(f"usage:{completed_stage}",),
        )
    runtime.checkpoint(
        subagent_task_id="task_1", stage_name=stage,
        input_fingerprint=canonical_fingerprint({"stage": stage}), status="failed_recoverable",
        failure={"code": "provider_timeout", "message": "timeout", "recoverable": True}, retry_count=1,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM content_research_observation_events")
    assert runtime.resume_stage("task_1") == stage


def test_completed_checkpoint_cannot_reopen_and_all_completed_has_no_resume_stage(tmp_path):
    db_path = str(tmp_path / "runtime.db")
    SQLiteContentResearchStore(db_path)
    runtime = CheckpointRuntime(db_path)
    for stage in STAGE_SEQUENCE:
        runtime.checkpoint(subagent_task_id="task_1", stage_name=stage, input_fingerprint=stage, status="completed")
    assert runtime.resume_stage("task_1") is None
    with pytest.raises(ValueError, match="cannot be reopened"):
        runtime.checkpoint(subagent_task_id="task_1", stage_name="collect", input_fingerprint="collect", status="running")
    with pytest.raises(ValueError, match="at most two"):
        runtime.checkpoint(subagent_task_id="task_2", stage_name="collect", input_fingerprint="collect", status="failed_recoverable", retry_count=3)
