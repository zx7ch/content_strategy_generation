import sqlite3
from dataclasses import replace

import pytest

from app.content_research.bootstrap import _bootstrap_legacy_content_research_schema
from app.content_research.contracts import build_default_snapshot, policy_hash
from app.content_research.migrations import apply_content_research_migrations
from app.content_research.persistence_models import (
    AggregateClaimRecord,
    BudgetLedgerEntryRecord,
    CanonicalSourceRecord,
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
    CrossDirectionRecord,
    DirectionalEvidencePacketRecord,
    DirectionResultDecisionRecord,
    DirectionSourceProjectionRecord,
    StageCheckpointRecord,
    WeakSignalRecord,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from tests.content_research_test_constants import LEGACY_EVIDENCE_BUNDLE_FRAGMENT


def test_snapshot_round_trip_is_append_only_and_migrations_are_idempotent(tmp_path):
    db_path = str(tmp_path / "contracts.db")
    store = SQLiteContentResearchStore(db_path)
    snapshot, policies, contracts = build_default_snapshot(snapshot_id="rps_1", workflow_run_id="run_1", brief_id="rb_1", plan_id="rp_1")
    store.save_run_policy_snapshot(snapshot)
    for item in policies:
        store.save_sample_policy(item)
    for item in contracts:
        store.save_direction_contract(item)
    assert store.get_run_policy_snapshot("rps_1") == snapshot
    assert len(store.list_direction_contracts("rps_1")) == 7
    SQLiteContentResearchStore(db_path)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM content_research_schema_migrations WHERE version = '0002'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM content_research_schema_migrations WHERE version = '0003'").fetchone()[0] == 1
        checkpoint_columns = {row[1] for row in conn.execute("PRAGMA table_info(content_research_stage_checkpoints)")}
        ledger_columns = {row[1] for row in conn.execute("PRAGMA table_info(content_research_budget_ledger_entries)")}
        assert {"workflow_run_id", "subagent_task_id", "stage_name", "input_fingerprint", "retry_count"} <= checkpoint_columns
        assert {"idempotency_key", "reservation_status", "reserved_amount", "consumed_amount"} <= ledger_columns
    changed, _, _ = build_default_snapshot(snapshot_id="rps_1", workflow_run_id="run_1", brief_id="rb_1", plan_id="rp_1")
    changed = changed.__class__(**{**changed.__dict__, "effective_policy": {"changed": True}, "effective_policy_hash": policy_hash({"changed": True})})
    with pytest.raises(ValueError, match="append-only"):
        store.save_run_policy_snapshot(changed)


def test_legacy_database_upgrades_without_losing_legacy_rows(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    _bootstrap_legacy_content_research_schema(db_path)
    obsolete_suffix = LEGACY_EVIDENCE_BUNDLE_FRAGMENT
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO content_research_briefs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", ("rb_legacy", "run_legacy", "thread", "v1", "ready", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", '{"schema_version":"v1"}', "{}"))
        conn.execute(f"CREATE TABLE content_research_{obsolete_suffix}s (id TEXT PRIMARY KEY)")
        conn.execute(f"CREATE TABLE content_research_{obsolete_suffix}_items (id TEXT PRIMARY KEY)")
        conn.execute(
            f"ALTER TABLE content_research_result_snapshots ADD COLUMN {obsolete_suffix}_ids_json TEXT"
        )
    SQLiteContentResearchStore(db_path)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT id FROM content_research_briefs WHERE id = 'rb_legacy'").fetchone()[0] == "rb_legacy"
        assert conn.execute("SELECT version FROM content_research_schema_migrations WHERE version = '0002'").fetchone()[0] == "0002"
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        columns = {row[1] for row in conn.execute("PRAGMA table_info(content_research_result_snapshots)")}
        assert not any(obsolete_suffix in table for table in tables)
        assert f"{obsolete_suffix}_ids_json" not in columns


def test_interrupted_migration_rolls_back_new_schema_and_ledger(tmp_path):
    db_path = str(tmp_path / "interrupted.db")
    with pytest.raises(RuntimeError, match="injected"):
        apply_content_research_migrations(db_path, _bootstrap_legacy_content_research_schema, fail_after_statement=1)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM content_research_schema_migrations WHERE version = '0001'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM content_research_schema_migrations WHERE version IN ('0002', '0003')").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'content_research_run_policy_snapshots'").fetchone()[0] == 0
    SQLiteContentResearchStore(db_path)


def test_final_entity_schema_has_only_role_specific_columns(tmp_path):
    db_path = str(tmp_path / "final-schema.db")
    SQLiteContentResearchStore(db_path)
    with sqlite3.connect(db_path) as conn:
        versions = [row[0] for row in conn.execute("SELECT version FROM content_research_schema_migrations ORDER BY version")]
        assert versions == [f"{version:04d}" for version in range(1, 41)]
        for table in (
            "content_research_canonical_sources",
            "content_research_claim_candidates",
            "content_research_stage_checkpoints",
            "content_research_budget_ledger_entries",
        ):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert not {"relation_a", "relation_b", "state"} & columns
        checkpoint_columns = {row[1] for row in conn.execute("PRAGMA table_info(content_research_stage_checkpoints)")}
        assert {"subagent_task_id", "stage_name", "input_fingerprint", "status", "retry_count", "started_at", "finished_at"} <= checkpoint_columns


def test_all_new_roles_have_distinct_typed_persistence(tmp_path):
    db_path = str(tmp_path / "roles.db")
    store = SQLiteContentResearchStore(db_path)
    snapshot, _, _ = build_default_snapshot(snapshot_id="rps_1", workflow_run_id="run_1", brief_id="rb_1", plan_id="rp_1")
    store.save_run_policy_snapshot(snapshot)
    records = [
        (store.save_canonical_source, CanonicalSourceRecord("cs_1", "v1", {}, platform="xhs", platform_source_kind="note", platform_source_id="n1")),
        (store.save_directional_evidence_packet, DirectionalEvidencePacketRecord("dep_1", "v1", {"field_projection": {"content_text": "claim", "source_url": "https://example/n1"}}, workflow_run_id="run_1", research_direction_id="product_marketing", canonical_source_id="cs_1", field_projection_hash="hash")),
        (store.save_direction_source_projection, DirectionSourceProjectionRecord("dsp_1", "v1", {}, workflow_run_id="run_1", research_direction_id="product_marketing", canonical_source_id="cs_1", evidence_packet_id="dep_1")),
        (store.save_claim_candidate, ClaimCandidateRecord("cc_1", "v2", {"quote_refs": [{"field_path": "content_text", "quote": "claim", "text_start": 0, "text_end": 5, "source_text_hash": "dd1b3c312cf7d816130354452e9629ce39355b0c534129dd26a08cd9a4502ede", "source_url": "https://example/n1"}]}, workflow_run_id="run_1", research_direction_id="product_marketing", evidence_packet_id="dep_1", statement="claim", intent_id="intent", claim_type="observation")),
        (store.save_claim_admission_decision, ClaimAdmissionDecisionRecord("cad_1", "v1", {}, research_direction_id="product_marketing", claim_candidate_id="cc_1", decision="admitted", policy_snapshot_id="rps_1")),
        (store.save_direction_result_decision, DirectionResultDecisionRecord("drd_1", "v1", {}, research_direction_id="product_marketing", policy_snapshot_id="rps_1")),
        (store.save_weak_signal, WeakSignalRecord("ws_1", "v1", {}, admission_decision_id="cad_1")),
        (store.save_cross_direction_record, CrossDirectionRecord("cdr_1", "v1", {}, research_plan_id="rp_1", record_type="overlap")),
        (store.save_aggregate_claim, AggregateClaimRecord("ac_1", "v1", {}, research_plan_id="rp_1", aggregate_type="action_hypothesis")),
        (store.save_stage_checkpoint, StageCheckpointRecord("scp_1", "v1", {}, workflow_run_id="run_1", subagent_task_id="sat_1", stage_name="collect", input_fingerprint="fp", status="completed", retry_count=2)),
        (store.save_budget_ledger_entry, BudgetLedgerEntryRecord("ble_1", "v1", {}, research_plan_id="rp_1", research_direction_id="product_marketing", idempotency_key="key", reservation_status="reserved", reserved_amount=3.0, consumed_amount=1.0, stage_checkpoint_id="scp_1")),
    ]
    for save, record in records:
        assert save(record) == record
    assert store.get_canonical_source("cs_1").platform_source_id == "n1"
    assert store.get_typed_record(ClaimCandidateRecord, "cc_1") == records[3][1]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT research_direction_id, canonical_source_id, evidence_packet_id FROM content_research_direction_source_projections WHERE id = 'dsp_1'").fetchone() == ("product_marketing", "cs_1", "dep_1")
        assert conn.execute("SELECT research_direction_id, canonical_source_id, field_projection_hash FROM content_research_directional_evidence_packets WHERE id = 'dep_1'").fetchone() == ("product_marketing", "cs_1", "hash")
        assert conn.execute("SELECT status, retry_count FROM content_research_stage_checkpoints WHERE id = 'scp_1'").fetchone() == ("completed", 2)
        assert conn.execute("SELECT research_direction_id, reserved_amount, consumed_amount FROM content_research_budget_ledger_entries WHERE id = 'ble_1'").fetchone() == ("product_marketing", 3.0, 1.0)
        assert conn.execute("SELECT research_direction_id, evidence_packet_id, statement FROM content_research_claim_candidates WHERE id = 'cc_1'").fetchone() == ("product_marketing", "dep_1", "claim")
        assert conn.execute("SELECT research_direction_id, claim_candidate_id, decision, policy_snapshot_id FROM content_research_claim_admission_decisions WHERE id = 'cad_1'").fetchone() == ("product_marketing", "cc_1", "admitted", "rps_1")
        assert conn.execute("SELECT research_direction_id, policy_snapshot_id FROM content_research_direction_result_decisions WHERE id = 'drd_1'").fetchone() == ("product_marketing", "rps_1")
        assert conn.execute("SELECT admission_decision_id FROM content_research_weak_signals WHERE id = 'ws_1'").fetchone() == ("cad_1",)
        assert conn.execute("SELECT research_plan_id, record_type FROM content_research_cross_direction_records WHERE id = 'cdr_1'").fetchone() == ("rp_1", "overlap")
        assert conn.execute("SELECT research_plan_id, aggregate_type FROM content_research_aggregate_claims WHERE id = 'ac_1'").fetchone() == ("rp_1", "action_hypothesis")


def test_stage_checkpoint_status_can_be_retired_without_creating_a_second_row(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "checkpoint-update.db"))
    checkpoint = StageCheckpointRecord(
        "scp_recoverable",
        "v1",
        {"completion": {"failure_code": "auth_required"}},
        workflow_run_id="run_1",
        subagent_task_id="sat_1",
        stage_name="operation",
        input_fingerprint="discover:fp",
        status="auth_required",
    )
    store.save_stage_checkpoint(checkpoint)
    store.save_stage_checkpoint(replace(checkpoint, status="superseded"))

    with sqlite3.connect(str(tmp_path / "checkpoint-update.db")) as conn:
        assert conn.execute(
            "SELECT status FROM content_research_stage_checkpoints WHERE id = ?",
            (checkpoint.id,),
        ).fetchone() == ("superseded",)
        assert conn.execute("SELECT COUNT(*) FROM content_research_stage_checkpoints").fetchone() == (1,)


def test_stage_checkpoint_retry_cannot_change_immutable_execution_ownership(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "checkpoint-ownership.db"))
    checkpoint = StageCheckpointRecord(
        "scp_owned",
        "v1",
        {"attempt": 1},
        workflow_run_id="run_owned",
        subagent_task_id="task_owned",
        stage_name="operation",
        input_fingerprint="owned:fp",
        status="running",
        scope_contract_id="rsc_owned",
        execution_unit_id="seu_owned",
        attempt_no=1,
        execution_revision=2,
    )
    store.save_stage_checkpoint(checkpoint)

    with pytest.raises(ValueError, match="immutable execution ownership"):
        store.save_stage_checkpoint(
            replace(
                checkpoint,
                status="completed",
                execution_unit_id="seu_foreign",
                attempt_no=2,
            )
        )

    assert store.get_typed_record(StageCheckpointRecord, checkpoint.id) == checkpoint


def test_run_independent_source_count_is_distinct_and_workflow_scoped(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "run-union.db"))

    def save_projection(*, run_id: str, direction_id: str, source_id: str, suffix: str) -> None:
        canonical_id = f"cs_{source_id}"
        if store.get_canonical_source(canonical_id) is None:
            store.save_canonical_source(CanonicalSourceRecord(
                canonical_id, "v1", {}, platform="xhs", platform_source_kind="note", platform_source_id=source_id,
            ))
        store.save_directional_evidence_packet(DirectionalEvidencePacketRecord(
            f"dep_{suffix}", "v1", {}, workflow_run_id=run_id, research_direction_id=direction_id,
            canonical_source_id=canonical_id, field_projection_hash=f"hash_{suffix}",
        ))
        store.save_direction_source_projection(DirectionSourceProjectionRecord(
            f"dsp_{suffix}", "v1", {}, workflow_run_id=run_id, research_direction_id=direction_id,
            canonical_source_id=canonical_id, evidence_packet_id=f"dep_{suffix}",
        ))

    for index in range(51):
        save_projection(run_id="run_1", direction_id="product_marketing", source_id=f"note_{index}", suffix=f"product_{index}")
    save_projection(run_id="run_1", direction_id="brand_activity", source_id="note_0", suffix="brand_shared")
    save_projection(run_id="run_1", direction_id="brand_activity", source_id="note_extra", suffix="brand_extra")
    save_projection(run_id="run_2", direction_id="product_marketing", source_id="note_other_run", suffix="other_run")

    assert store.count_run_independent_sources("run_1") == 52
    assert store.count_run_independent_sources("run_2") == 1


def test_typed_record_rejects_missing_upstream_relation(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "missing-parent.db"))
    with pytest.raises(ValueError, match="missing evidence packet"):
        store.save_claim_candidate(
            ClaimCandidateRecord(
                "cc_missing", "v2", {"quote_refs": []}, workflow_run_id="run_1", research_direction_id="product_marketing", evidence_packet_id="dep_missing", statement="claim", intent_id="intent", claim_type="observation"
            )
        )
