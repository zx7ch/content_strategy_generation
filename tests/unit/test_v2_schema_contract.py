from __future__ import annotations

from app.v2.db import (
    FOUNDATION_TABLES,
    P1_2_EVIDENCE_TABLES,
    build_p1_1_schema_sql,
    build_p1_2_schema_sql,
    get_p1_1_migrations,
    get_p1_2_migrations,
)


def test_p1_1_migration_manifest_is_ordered_and_non_empty() -> None:
    migrations = get_p1_1_migrations()

    assert migrations
    assert migrations[0].migration_id == "v2_p1_1_foundation"
    assert migrations[0].sql


def test_p1_1_schema_contains_required_tables() -> None:
    sql = build_p1_1_schema_sql()

    for table_name in FOUNDATION_TABLES:
        assert f"CREATE TABLE {table_name} (" in sql


def test_p1_1_schema_contains_workspace_brand_and_lineage_columns() -> None:
    sql = build_p1_1_schema_sql()

    assert "workspace_id UUID NOT NULL REFERENCES workspaces(id)" in sql
    assert "brand_id UUID NOT NULL REFERENCES brands(id)" in sql
    assert "brand_policy_config_id UUID NOT NULL REFERENCES brand_policy_configs(id)" in sql
    assert "brand_state_snapshot_id UUID NOT NULL REFERENCES brand_state_snapshots(id)" in sql
    assert "CREATE TABLE scorer_configs (" in sql
    assert "CREATE INDEX idx_topic_pool_items_status ON topic_pool_items(status);" in sql


def test_p1_1_schema_contains_publish_and_feedback_traceability_checks() -> None:
    sql = build_p1_1_schema_sql()

    assert "decision_event_id IS NULL OR decision_batch_id IS NOT NULL" in sql
    assert "observation_window_hours IS NOT NULL" in sql
    assert "reward_window_start_at IS NOT NULL" in sql
    assert "reward_window_end_at IS NOT NULL" in sql


def test_p1_2_migration_manifest_extends_foundation_order() -> None:
    migrations = get_p1_2_migrations()

    assert migrations
    assert migrations[0].migration_id == "v2_p1_1_foundation"
    assert migrations[-1].migration_id == "v2_p1_2_ingestion"


def test_p1_2_schema_contains_required_evidence_tables() -> None:
    sql = build_p1_2_schema_sql()

    for table_name in P1_2_EVIDENCE_TABLES:
        assert f"CREATE TABLE {table_name} (" in sql


def test_p1_2_schema_contains_dedupe_and_identity_constraints() -> None:
    sql = build_p1_2_schema_sql()

    assert "UNIQUE (workspace_id, platform, platform_author_id)" in sql
    assert "UNIQUE (workspace_id, platform, platform_content_id)" in sql
    assert "UNIQUE (workspace_id, content_item_id, platform_comment_id)" in sql
    assert "CREATE UNIQUE INDEX uq_topics_brand_name ON topics(brand_id, normalized_name);" in sql
