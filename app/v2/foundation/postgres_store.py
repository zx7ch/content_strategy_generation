"""Postgres-backed master-data store for V2 foundation work."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.v2.foundation.models import (
    BrandChannelRecord,
    BrandPolicyConfigRecord,
    BrandRecord,
    BrandStateSnapshotRecord,
    WorkspaceRecord,
)


def _load_psycopg_jsonb():
    try:
        from psycopg import connect  # type: ignore
        from psycopg.rows import dict_row  # type: ignore
        from psycopg.types.json import Jsonb  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional runtime package
        raise RuntimeError(
            "psycopg is required when POSTGRES_DSN is configured. "
            "Install project dependencies with psycopg[binary] support."
        ) from exc
    return connect, dict_row, Jsonb


def _default_connector(dsn: str):
    connect, dict_row, _jsonb = _load_psycopg_jsonb()
    return connect(dsn, row_factory=dict_row)


class PostgresMasterDataStore:
    def __init__(
        self,
        dsn: str,
        *,
        connector: Callable[[str], Any] | None = None,
    ) -> None:
        self._dsn = dsn
        self._connector = connector or _default_connector

    def save_workspace(self, workspace: WorkspaceRecord) -> WorkspaceRecord:
        row = self._fetchone(
            """
            INSERT INTO workspaces (id, name, slug, timezone, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id, name, slug, timezone, status, created_at, updated_at
            """,
            (
                workspace.id,
                workspace.name,
                workspace.slug,
                workspace.timezone,
                workspace.status,
                workspace.created_at,
                workspace.updated_at,
            ),
        )
        return self._workspace_from_row(row)

    def get_workspace(self, workspace_id: str) -> WorkspaceRecord | None:
        row = self._fetchone(
            """
            SELECT id, name, slug, timezone, status, created_at, updated_at
            FROM workspaces
            WHERE id = %s
            """,
            (workspace_id,),
        )
        return self._workspace_from_row(row) if row else None

    def get_workspace_by_slug(self, slug: str) -> WorkspaceRecord | None:
        row = self._fetchone(
            """
            SELECT id, name, slug, timezone, status, created_at, updated_at
            FROM workspaces
            WHERE slug = %s
            """,
            (slug,),
        )
        return self._workspace_from_row(row) if row else None

    def save_brand(self, brand: BrandRecord) -> BrandRecord:
        row = self._fetchone(
            """
            INSERT INTO brands (
                id, workspace_id, name, category, stage, target_audience, brand_voice, goals, is_demo, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                category = EXCLUDED.category,
                stage = EXCLUDED.stage,
                target_audience = EXCLUDED.target_audience,
                brand_voice = EXCLUDED.brand_voice,
                goals = EXCLUDED.goals,
                is_demo = EXCLUDED.is_demo,
                updated_at = EXCLUDED.updated_at
            RETURNING id, workspace_id, name, category, stage, target_audience, brand_voice, goals, is_demo, created_at, updated_at
            """,
            (
                brand.id,
                brand.workspace_id,
                brand.name,
                brand.category,
                brand.stage,
                self._jsonb(brand.target_audience),
                self._jsonb(brand.brand_voice),
                self._jsonb(brand.goals),
                brand.is_demo,
                brand.created_at,
                brand.updated_at,
            ),
        )
        return self._brand_from_row(row)

    def get_brand(self, brand_id: str) -> BrandRecord | None:
        row = self._fetchone(
            """
            SELECT id, workspace_id, name, category, stage, target_audience, brand_voice, goals, is_demo, created_at, updated_at
            FROM brands
            WHERE id = %s
            """,
            (brand_id,),
        )
        return self._brand_from_row(row) if row else None

    def list_brands(self, workspace_id: str) -> list[BrandRecord]:
        rows = self._fetchall(
            """
            SELECT id, workspace_id, name, category, stage, target_audience, brand_voice, goals, is_demo, created_at, updated_at
            FROM brands
            WHERE workspace_id = %s
            ORDER BY created_at ASC
            """,
            (workspace_id,),
        )
        return [self._brand_from_row(row) for row in rows]

    def delete_brand(self, brand_id: str) -> bool:
        row = self._fetchone(
            "DELETE FROM brands WHERE id = %s RETURNING id",
            (brand_id,),
        )
        return row is not None

    def save_brand_channel(self, channel: BrandChannelRecord) -> BrandChannelRecord:
        row = self._fetchone(
            """
            INSERT INTO brand_channels (
                id, workspace_id, brand_id, platform, external_account_id, account_name, profile_url, status, metadata, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                external_account_id = EXCLUDED.external_account_id,
                account_name = EXCLUDED.account_name,
                profile_url = EXCLUDED.profile_url,
                status = EXCLUDED.status,
                metadata = EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
            RETURNING id, workspace_id, brand_id, platform, external_account_id, account_name, profile_url, status, metadata, created_at, updated_at
            """,
            (
                channel.id,
                channel.workspace_id,
                channel.brand_id,
                channel.platform,
                channel.external_account_id,
                channel.account_name,
                channel.profile_url,
                channel.status,
                self._jsonb(channel.metadata),
                channel.created_at,
                channel.updated_at,
            ),
        )
        return self._channel_from_row(row)

    def get_brand_channel(self, channel_id: str) -> BrandChannelRecord | None:
        row = self._fetchone(
            """
            SELECT id, workspace_id, brand_id, platform, external_account_id, account_name, profile_url, status, metadata, created_at, updated_at
            FROM brand_channels
            WHERE id = %s
            """,
            (channel_id,),
        )
        return self._channel_from_row(row) if row else None

    def list_brand_channels(self, brand_id: str) -> list[BrandChannelRecord]:
        rows = self._fetchall(
            """
            SELECT id, workspace_id, brand_id, platform, external_account_id, account_name, profile_url, status, metadata, created_at, updated_at
            FROM brand_channels
            WHERE brand_id = %s
            ORDER BY created_at ASC
            """,
            (brand_id,),
        )
        return [self._channel_from_row(row) for row in rows]

    def delete_brand_channels(self, brand_id: str) -> int:
        row = self._fetchone(
            "WITH deleted AS (DELETE FROM brand_channels WHERE brand_id = %s RETURNING id) SELECT count(*) AS cnt FROM deleted",
            (brand_id,),
        )
        return int(row["cnt"]) if row else 0

    def save_policy_config(self, policy: BrandPolicyConfigRecord) -> BrandPolicyConfigRecord:
        with self._connector(self._dsn) as connection:
            with connection.cursor() as cursor:
                if policy.is_active:
                    cursor.execute(
                        """
                        UPDATE brand_policy_configs
                        SET is_active = FALSE, updated_at = %s
                        WHERE brand_id = %s AND is_active = TRUE AND id <> %s
                        """,
                        (policy.updated_at, policy.brand_id, policy.id),
                    )
                cursor.execute(
                    """
                    INSERT INTO brand_policy_configs (
                        id, workspace_id, brand_id, policy_name, policy_version,
                        hard_filter_rules, brand_fit_rules, exploration_preset_override, topic_type_targets,
                        is_active, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, workspace_id, brand_id, policy_name, policy_version,
                              hard_filter_rules, brand_fit_rules, exploration_preset_override, topic_type_targets,
                              is_active, created_at, updated_at
                    """,
                    (
                        policy.id,
                        policy.workspace_id,
                        policy.brand_id,
                        policy.policy_name,
                        policy.policy_version,
                        self._jsonb(policy.hard_filter_rules),
                        self._jsonb(policy.brand_fit_rules),
                        self._jsonb(policy.exploration_preset_override),
                        self._jsonb(policy.topic_type_targets),
                        policy.is_active,
                        policy.created_at,
                        policy.updated_at,
                    ),
                )
                row = cursor.fetchone()

            commit = getattr(connection, "commit", None)
            if callable(commit):
                commit()

        return self._policy_from_row(row)

    def list_policy_configs(self, brand_id: str) -> list[BrandPolicyConfigRecord]:
        rows = self._fetchall(
            """
            SELECT id, workspace_id, brand_id, policy_name, policy_version,
                   hard_filter_rules, brand_fit_rules, exploration_preset_override, topic_type_targets,
                   is_active, created_at, updated_at
            FROM brand_policy_configs
            WHERE brand_id = %s
            ORDER BY created_at ASC
            """,
            (brand_id,),
        )
        return [self._policy_from_row(row) for row in rows]

    def delete_policy_configs(self, brand_id: str) -> int:
        row = self._fetchone(
            "WITH deleted AS (DELETE FROM brand_policy_configs WHERE brand_id = %s RETURNING id) SELECT count(*) AS cnt FROM deleted",
            (brand_id,),
        )
        return int(row["cnt"]) if row else 0

    def save_state_snapshot(self, snapshot: BrandStateSnapshotRecord) -> BrandStateSnapshotRecord:
        row = self._fetchone(
            """
            INSERT INTO brand_state_snapshots (
                id, workspace_id, brand_id, state_version, stage, state_features,
                source_type, source_version, computed_at, valid_from, valid_to, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, workspace_id, brand_id, state_version, stage, state_features,
                      source_type, source_version, computed_at, valid_from, valid_to, created_at
            """,
            (
                snapshot.id,
                snapshot.workspace_id,
                snapshot.brand_id,
                snapshot.state_version,
                snapshot.stage,
                self._jsonb(snapshot.state_features),
                snapshot.source_type,
                snapshot.source_version,
                snapshot.computed_at,
                snapshot.valid_from,
                snapshot.valid_to,
                snapshot.created_at,
            ),
        )
        return self._state_snapshot_from_row(row)

    def list_state_snapshots(self, brand_id: str) -> list[BrandStateSnapshotRecord]:
        rows = self._fetchall(
            """
            SELECT id, workspace_id, brand_id, state_version, stage, state_features,
                   source_type, source_version, computed_at, valid_from, valid_to, created_at
            FROM brand_state_snapshots
            WHERE brand_id = %s
            ORDER BY valid_from DESC
            """,
            (brand_id,),
        )
        return [self._state_snapshot_from_row(row) for row in rows]

    def delete_state_snapshots(self, brand_id: str) -> int:
        row = self._fetchone(
            "WITH deleted AS (DELETE FROM brand_state_snapshots WHERE brand_id = %s RETURNING id) SELECT count(*) AS cnt FROM deleted",
            (brand_id,),
        )
        return int(row["cnt"]) if row else 0

    def _fetchone(self, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._connector(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                row = cursor.fetchone()
            commit = getattr(connection, "commit", None)
            if callable(commit):
                commit()
        return row

    def _fetchall(self, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self._connector(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                rows = cursor.fetchall() or []
        return list(rows)

    def _jsonb(self, value: dict[str, Any]) -> Any:
        _connect, _dict_row, jsonb_cls = _load_psycopg_jsonb()
        return jsonb_cls(value)

    @staticmethod
    def _workspace_from_row(row: dict[str, Any]) -> WorkspaceRecord:
        return WorkspaceRecord(**row)

    @staticmethod
    def _brand_from_row(row: dict[str, Any]) -> BrandRecord:
        return BrandRecord(**row)

    @staticmethod
    def _channel_from_row(row: dict[str, Any]) -> BrandChannelRecord:
        return BrandChannelRecord(**row)

    @staticmethod
    def _policy_from_row(row: dict[str, Any]) -> BrandPolicyConfigRecord:
        return BrandPolicyConfigRecord(**row)

    @staticmethod
    def _state_snapshot_from_row(row: dict[str, Any]) -> BrandStateSnapshotRecord:
        return BrandStateSnapshotRecord(**row)
