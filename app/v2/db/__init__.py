"""V2 schema and migration contracts."""

from app.v2.db.migrations import MigrationStep, get_p1_1_migrations, get_p1_2_migrations
from app.v2.db.schema import (
    FOUNDATION_TABLES,
    P1_2_EVIDENCE_TABLES,
    build_p1_1_schema_sql,
    build_p1_2_ingestion_workspace_alignment_sql,
    build_p1_2_schema_sql,
)

__all__ = [
    "FOUNDATION_TABLES",
    "P1_2_EVIDENCE_TABLES",
    "MigrationStep",
    "build_p1_1_schema_sql",
    "build_p1_2_ingestion_workspace_alignment_sql",
    "build_p1_2_schema_sql",
    "get_p1_1_migrations",
    "get_p1_2_migrations",
]
