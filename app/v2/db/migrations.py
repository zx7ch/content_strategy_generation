"""Migration manifest for V2 Phase 1 foundation and ingestion schema."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.v2.db.schema import (
    build_p1_1_schema_sql,
    build_p1_2_ingestion_workspace_alignment_sql,
    build_p1_2_schema_sql,
    build_p1_5_schema_sql,
    build_p1_6_remove_account_handle_sql,
    build_p1_s2_6_demo_column_sql,
)


@dataclass(frozen=True)
class MigrationStep:
    migration_id: str
    description: str
    sql: str


@lru_cache(maxsize=1)
def get_p1_1_migrations() -> tuple[MigrationStep, ...]:
    return (
        MigrationStep(
            migration_id="v2_p1_1_foundation",
            description="Create V2 Phase 1 foundation and master-data tables",
            sql=build_p1_1_schema_sql(),
        ),
        MigrationStep(
            migration_id="v2_p1_6_remove_account_handle",
            description="Remove deprecated account handle from brand channels",
            sql=build_p1_6_remove_account_handle_sql(),
        ),
    )


@lru_cache(maxsize=1)
def get_p1_2_migrations() -> tuple[MigrationStep, ...]:
    return get_p1_1_migrations() + (
        MigrationStep(
            migration_id="v2_p1_2_ingestion",
            description="Create V2 Phase 1 ingestion and evidence tables",
            sql=build_p1_2_schema_sql(),
        ),
        MigrationStep(
            migration_id="v2_p1_2_ingestion_workspace_alignment",
            description="Add persisted ingestion workspace state and channel profile URLs",
            sql=build_p1_2_ingestion_workspace_alignment_sql(),
        ),
    )


@lru_cache(maxsize=1)
def get_p1_5_migrations() -> tuple[MigrationStep, ...]:
    return get_p1_2_migrations() + (
        MigrationStep(
            migration_id="v2_p1_5_feedback_eval",
            description="Create V2 Phase 1 feedback and evaluation tables",
            sql=build_p1_5_schema_sql(),
        ),
    )


@lru_cache(maxsize=1)
def get_p1_s2_6_migrations() -> tuple[MigrationStep, ...]:
    return get_p1_5_migrations() + (
        MigrationStep(
            migration_id="v2_p1_s2_6_brands_is_demo",
            description="Add is_demo column to brands table for demo dataset provenance",
            sql=build_p1_s2_6_demo_column_sql(),
        ),
    )
