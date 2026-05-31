"""SQLite-backed MasterDataStore for local single-user runtimes."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from app.v2.foundation.models import (
    BrandChannelRecord,
    BrandPolicyConfigRecord,
    BrandRecord,
    BrandStateSnapshotRecord,
    WorkspaceRecord,
)


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)


def _fmt_dt(dt: datetime) -> str:
    return dt.isoformat()


def _loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


class SQLiteMasterDataStore:
    """Persists master-data records (workspaces, brands, channels, policies, snapshots)
    in a SQLite database so data survives runtime restarts."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._init_tables()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_tables(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS md_workspaces (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS md_brands (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    category TEXT,
                    stage TEXT NOT NULL,
                    target_audience TEXT NOT NULL DEFAULT '{}',
                    brand_voice TEXT NOT NULL DEFAULT '{}',
                    goals TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_md_brands_workspace
                    ON md_brands(workspace_id);

                CREATE TABLE IF NOT EXISTS md_brand_channels (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    brand_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    external_account_id TEXT,
                    account_name TEXT,
                    profile_url TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_md_channels_brand
                    ON md_brand_channels(brand_id);

                CREATE TABLE IF NOT EXISTS md_policy_configs (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    brand_id TEXT NOT NULL,
                    policy_name TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    hard_filter_rules TEXT NOT NULL DEFAULT '{}',
                    brand_fit_rules TEXT NOT NULL DEFAULT '{}',
                    exploration_preset_override TEXT NOT NULL DEFAULT '{}',
                    topic_type_targets TEXT NOT NULL DEFAULT '{}',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_md_policies_brand
                    ON md_policy_configs(brand_id);

                CREATE TABLE IF NOT EXISTS md_state_snapshots (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    brand_id TEXT NOT NULL,
                    state_version TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    state_features TEXT NOT NULL DEFAULT '{}',
                    source_type TEXT NOT NULL DEFAULT 'rule_engine',
                    source_version TEXT NOT NULL DEFAULT 'v1',
                    computed_at TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_md_snapshots_brand
                    ON md_state_snapshots(brand_id);
            """)

    # ── Workspaces ─────────────────────────────────────────────────────────

    def save_workspace(self, workspace: WorkspaceRecord) -> WorkspaceRecord:
        with self._connect() as conn:
            existing_slug = conn.execute(
                "SELECT id FROM md_workspaces WHERE slug = ? AND id != ?",
                (workspace.slug, workspace.id),
            ).fetchone()
            if existing_slug is not None:
                raise ValueError(f"Workspace slug already exists: {workspace.slug}")
            conn.execute(
                """INSERT INTO md_workspaces (id, name, slug, timezone, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name, slug=excluded.slug, timezone=excluded.timezone,
                     status=excluded.status, updated_at=excluded.updated_at""",
                (
                    workspace.id, workspace.name, workspace.slug,
                    workspace.timezone, workspace.status,
                    _fmt_dt(workspace.created_at), _fmt_dt(workspace.updated_at),
                ),
            )
        return workspace

    def get_workspace(self, workspace_id: str) -> WorkspaceRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM md_workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
        return self._row_to_workspace(row) if row else None

    def get_workspace_by_slug(self, slug: str) -> WorkspaceRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM md_workspaces WHERE slug = ?", (slug,)
            ).fetchone()
        return self._row_to_workspace(row) if row else None

    @staticmethod
    def _row_to_workspace(row: sqlite3.Row) -> WorkspaceRecord:
        return WorkspaceRecord(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            timezone=row["timezone"],
            status=row["status"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    # ── Brands ─────────────────────────────────────────────────────────────

    def save_brand(self, brand: BrandRecord) -> BrandRecord:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO md_brands
                     (id, workspace_id, name, category, stage,
                      target_audience, brand_voice, goals, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     workspace_id=excluded.workspace_id, name=excluded.name,
                     category=excluded.category, stage=excluded.stage,
                     target_audience=excluded.target_audience,
                     brand_voice=excluded.brand_voice, goals=excluded.goals,
                     updated_at=excluded.updated_at""",
                (
                    brand.id, brand.workspace_id, brand.name, brand.category, brand.stage,
                    json.dumps(brand.target_audience, ensure_ascii=False),
                    json.dumps(brand.brand_voice, ensure_ascii=False),
                    json.dumps(brand.goals, ensure_ascii=False),
                    _fmt_dt(brand.created_at), _fmt_dt(brand.updated_at),
                ),
            )
        return brand

    def get_brand(self, brand_id: str) -> BrandRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM md_brands WHERE id = ?", (brand_id,)
            ).fetchone()
        return self._row_to_brand(row) if row else None

    def list_brands(self, workspace_id: str) -> list[BrandRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM md_brands WHERE workspace_id = ? ORDER BY created_at ASC",
                (workspace_id,),
            ).fetchall()
        return [self._row_to_brand(row) for row in rows]

    @staticmethod
    def _row_to_brand(row: sqlite3.Row) -> BrandRecord:
        return BrandRecord(
            id=row["id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            category=row["category"],
            stage=row["stage"],
            target_audience=_loads(row["target_audience"]),
            brand_voice=_loads(row["brand_voice"]),
            goals=_loads(row["goals"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    # ── Brand channels ─────────────────────────────────────────────────────

    def save_brand_channel(self, channel: BrandChannelRecord) -> BrandChannelRecord:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO md_brand_channels
                     (id, workspace_id, brand_id, platform, external_account_id,
                      account_name, profile_url, status, metadata, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     workspace_id=excluded.workspace_id, brand_id=excluded.brand_id,
                     platform=excluded.platform,
                     external_account_id=excluded.external_account_id,
                     account_name=excluded.account_name, profile_url=excluded.profile_url,
                     status=excluded.status, metadata=excluded.metadata,
                     updated_at=excluded.updated_at""",
                (
                    channel.id, channel.workspace_id, channel.brand_id, channel.platform,
                    channel.external_account_id, channel.account_name, channel.profile_url,
                    channel.status,
                    json.dumps(channel.metadata, ensure_ascii=False),
                    _fmt_dt(channel.created_at), _fmt_dt(channel.updated_at),
                ),
            )
        return channel

    def get_brand_channel(self, channel_id: str) -> BrandChannelRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM md_brand_channels WHERE id = ?", (channel_id,)
            ).fetchone()
        return self._row_to_channel(row) if row else None

    def list_brand_channels(self, brand_id: str) -> list[BrandChannelRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM md_brand_channels WHERE brand_id = ? ORDER BY created_at ASC",
                (brand_id,),
            ).fetchall()
        return [self._row_to_channel(row) for row in rows]

    @staticmethod
    def _row_to_channel(row: sqlite3.Row) -> BrandChannelRecord:
        return BrandChannelRecord(
            id=row["id"],
            workspace_id=row["workspace_id"],
            brand_id=row["brand_id"],
            platform=row["platform"],
            external_account_id=row["external_account_id"],
            account_name=row["account_name"],
            profile_url=row["profile_url"],
            status=row["status"],
            metadata=_loads(row["metadata"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    # ── Policy configs ──────────────────────────────────────────────────────

    def save_policy_config(self, policy: BrandPolicyConfigRecord) -> BrandPolicyConfigRecord:
        with self._connect() as conn:
            if policy.is_active:
                conn.execute(
                    "UPDATE md_policy_configs SET is_active=0 WHERE brand_id=? AND id!=?",
                    (policy.brand_id, policy.id),
                )
            conn.execute(
                """INSERT INTO md_policy_configs
                     (id, workspace_id, brand_id, policy_name, policy_version,
                      hard_filter_rules, brand_fit_rules, exploration_preset_override,
                      topic_type_targets, is_active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     policy_name=excluded.policy_name,
                     policy_version=excluded.policy_version,
                     hard_filter_rules=excluded.hard_filter_rules,
                     brand_fit_rules=excluded.brand_fit_rules,
                     exploration_preset_override=excluded.exploration_preset_override,
                     topic_type_targets=excluded.topic_type_targets,
                     is_active=excluded.is_active,
                     updated_at=excluded.updated_at""",
                (
                    policy.id, policy.workspace_id, policy.brand_id,
                    policy.policy_name, policy.policy_version,
                    json.dumps(policy.hard_filter_rules, ensure_ascii=False),
                    json.dumps(policy.brand_fit_rules, ensure_ascii=False),
                    json.dumps(policy.exploration_preset_override, ensure_ascii=False),
                    json.dumps(policy.topic_type_targets, ensure_ascii=False),
                    int(policy.is_active),
                    _fmt_dt(policy.created_at), _fmt_dt(policy.updated_at),
                ),
            )
        return policy

    def list_policy_configs(self, brand_id: str) -> list[BrandPolicyConfigRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM md_policy_configs WHERE brand_id = ?", (brand_id,)
            ).fetchall()
        return [self._row_to_policy(row) for row in rows]

    @staticmethod
    def _row_to_policy(row: sqlite3.Row) -> BrandPolicyConfigRecord:
        return BrandPolicyConfigRecord(
            id=row["id"],
            workspace_id=row["workspace_id"],
            brand_id=row["brand_id"],
            policy_name=row["policy_name"],
            policy_version=row["policy_version"],
            hard_filter_rules=_loads(row["hard_filter_rules"]),
            brand_fit_rules=_loads(row["brand_fit_rules"]),
            exploration_preset_override=_loads(row["exploration_preset_override"]),
            topic_type_targets=_loads(row["topic_type_targets"]),
            is_active=bool(row["is_active"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    # ── State snapshots ─────────────────────────────────────────────────────

    def save_state_snapshot(self, snapshot: BrandStateSnapshotRecord) -> BrandStateSnapshotRecord:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO md_state_snapshots
                     (id, workspace_id, brand_id, state_version, stage, state_features,
                      source_type, source_version, computed_at, valid_from, valid_to, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     state_version=excluded.state_version, stage=excluded.stage,
                     state_features=excluded.state_features,
                     source_type=excluded.source_type, source_version=excluded.source_version,
                     computed_at=excluded.computed_at, valid_from=excluded.valid_from,
                     valid_to=excluded.valid_to""",
                (
                    snapshot.id, snapshot.workspace_id, snapshot.brand_id,
                    snapshot.state_version, snapshot.stage,
                    json.dumps(snapshot.state_features, ensure_ascii=False),
                    snapshot.source_type, snapshot.source_version,
                    _fmt_dt(snapshot.computed_at), _fmt_dt(snapshot.valid_from),
                    _fmt_dt(snapshot.valid_to) if snapshot.valid_to else None,
                    _fmt_dt(snapshot.created_at),
                ),
            )
        return snapshot

    def list_state_snapshots(self, brand_id: str) -> list[BrandStateSnapshotRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM md_state_snapshots WHERE brand_id = ? ORDER BY valid_from ASC",
                (brand_id,),
            ).fetchall()
        return [self._row_to_snapshot(row) for row in rows]

    @staticmethod
    def _row_to_snapshot(row: sqlite3.Row) -> BrandStateSnapshotRecord:
        return BrandStateSnapshotRecord(
            id=row["id"],
            workspace_id=row["workspace_id"],
            brand_id=row["brand_id"],
            state_version=row["state_version"],
            stage=row["stage"],
            state_features=_loads(row["state_features"]),
            source_type=row["source_type"],
            source_version=row["source_version"],
            computed_at=_parse_dt(row["computed_at"]),
            valid_from=_parse_dt(row["valid_from"]),
            valid_to=_parse_dt(row["valid_to"]) if row["valid_to"] else None,
            created_at=_parse_dt(row["created_at"]),
        )
