"""Writer-owned master-data mutations."""

from __future__ import annotations

import sqlite3

from app.core.runtime_write_coordinator import (
    DomainMutationRejectedError,
    MutationApplication,
    MutationIdentityConflictError,
    RuntimeMutationHandler,
    TypedMutation,
)


class _FoundationMutationHandler:
    mutation_kind = "mutate_master_data"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        payload = dict(mutation.domain_payload)
        action = payload.get("action")
        fields = payload.get("fields")
        if not isinstance(action, str) or not isinstance(fields, list):
            raise MutationIdentityConflictError()

        if action == "save_workspace" and len(fields) == 7:
            existing = connection.execute(
                "SELECT id FROM md_workspaces WHERE slug=? AND id!=?",
                (fields[2], fields[0]),
            ).fetchone()
            if existing is not None:
                raise DomainMutationRejectedError(
                    f"Workspace slug already exists: {fields[2]}"
                )
            connection.execute(
                """
                INSERT INTO md_workspaces(id, name, slug, timezone, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, slug=excluded.slug,
                    timezone=excluded.timezone, status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                fields,
            )
        elif action == "save_brand" and len(fields) == 10:
            connection.execute(
                """
                INSERT INTO md_brands(
                    id, workspace_id, name, category, stage, target_audience,
                    brand_voice, goals, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET workspace_id=excluded.workspace_id,
                    name=excluded.name, category=excluded.category, stage=excluded.stage,
                    target_audience=excluded.target_audience,
                    brand_voice=excluded.brand_voice, goals=excluded.goals,
                    updated_at=excluded.updated_at
                """,
                fields,
            )
        elif action == "save_brand_channel" and len(fields) == 11:
            connection.execute(
                """
                INSERT INTO md_brand_channels(
                    id, workspace_id, brand_id, platform, external_account_id,
                    account_name, profile_url, status, metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET workspace_id=excluded.workspace_id,
                    brand_id=excluded.brand_id, platform=excluded.platform,
                    external_account_id=excluded.external_account_id,
                    account_name=excluded.account_name, profile_url=excluded.profile_url,
                    status=excluded.status, metadata=excluded.metadata,
                    updated_at=excluded.updated_at
                """,
                fields,
            )
        elif action == "save_policy_config" and len(fields) == 12:
            if bool(fields[9]):
                connection.execute(
                    "UPDATE md_policy_configs SET is_active=0 WHERE brand_id=? AND id!=?",
                    (fields[2], fields[0]),
                )
            connection.execute(
                """
                INSERT INTO md_policy_configs(
                    id, workspace_id, brand_id, policy_name, policy_version,
                    hard_filter_rules, brand_fit_rules, exploration_preset_override,
                    topic_type_targets, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET policy_name=excluded.policy_name,
                    policy_version=excluded.policy_version,
                    hard_filter_rules=excluded.hard_filter_rules,
                    brand_fit_rules=excluded.brand_fit_rules,
                    exploration_preset_override=excluded.exploration_preset_override,
                    topic_type_targets=excluded.topic_type_targets,
                    is_active=excluded.is_active, updated_at=excluded.updated_at
                """,
                fields,
            )
        elif action == "save_state_snapshot" and len(fields) == 12:
            connection.execute(
                """
                INSERT INTO md_state_snapshots(
                    id, workspace_id, brand_id, state_version, stage, state_features,
                    source_type, source_version, computed_at, valid_from, valid_to, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET state_version=excluded.state_version,
                    stage=excluded.stage, state_features=excluded.state_features,
                    source_type=excluded.source_type, source_version=excluded.source_version,
                    computed_at=excluded.computed_at, valid_from=excluded.valid_from,
                    valid_to=excluded.valid_to
                """,
                fields,
            )
        else:
            raise MutationIdentityConflictError()

        return MutationApplication(
            result_contract="master_data_mutation_result",
            result_fields={"record_id": fields[0]},
        )


def foundation_mutation_handlers() -> tuple[RuntimeMutationHandler, ...]:
    return (_FoundationMutationHandler(),)
