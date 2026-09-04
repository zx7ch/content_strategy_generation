"""SQLite storage for validated, Workspace-scoped LLM configurations."""

from __future__ import annotations

from datetime import datetime, timezone

from app.content_research.bootstrap import bootstrap_content_research_schema
from app.core.runtime_write_coordinator import RuntimeWriteCoordinator, TypedMutation
from app.core.runtime_write_registry import get_runtime_writer
from app.core.sqlite_connection_roles import (
    open_bootstrap_database,
    open_readonly_database,
)
from app.services.llm.configuration import UserLLMConfiguration


class SQLiteLLMConfigurationStore:
    def __init__(
        self,
        db_path: str,
        *,
        writer: RuntimeWriteCoordinator | None = None,
    ) -> None:
        self._db_path = db_path
        self._writer = writer or get_runtime_writer(self._db_path)
        if self._writer is None:
            bootstrap_content_research_schema(db_path)

    def get(self, workspace_id: str, user_id: str) -> UserLLMConfiguration | None:
        with open_readonly_database(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT workspace_id, user_id, base_url, model, api_key,
                       validation_status, validated_at, last_validation_error_code,
                       created_at, updated_at
                FROM content_research_llm_configurations
                WHERE workspace_id = ? AND user_id = ?
                """,
                (workspace_id, user_id),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def upsert(self, configuration: UserLLMConfiguration) -> UserLLMConfiguration:
        existing = self.get(configuration.workspace_id, configuration.user_id)
        created_at = existing.created_at if existing is not None else configuration.created_at
        saved = UserLLMConfiguration(
            workspace_id=configuration.workspace_id,
            user_id=configuration.user_id,
            base_url=configuration.base_url,
            model=configuration.model,
            api_key=configuration.api_key,
            validation_status=configuration.validation_status,
            validated_at=configuration.validated_at,
            last_validation_error_code=configuration.last_validation_error_code,
            created_at=created_at,
            updated_at=datetime.now(timezone.utc),
        )
        if self._writer is not None:
            self._writer.submit_sync(
                TypedMutation.create(
                    mutation_id=(
                        f"llm_configuration_{saved.workspace_id}_{saved.user_id}_"
                        f"{saved.updated_at.isoformat()}"
                    ),
                    mutation_kind="mutate_runtime_accounting",
                    domain_payload={
                        "action": "upsert_llm_configuration",
                        "fields": [
                            saved.workspace_id,
                            saved.user_id,
                            saved.base_url,
                            saved.model,
                            saved.api_key,
                            saved.validation_status,
                            saved.validated_at.isoformat(),
                            saved.last_validation_error_code,
                            saved.created_at.isoformat(),
                            saved.updated_at.isoformat(),
                        ],
                    },
                )
            )
            return saved
        with open_bootstrap_database(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO content_research_llm_configurations (
                    workspace_id, user_id, base_url, model, api_key, validation_status,
                    validated_at, last_validation_error_code, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, user_id) DO UPDATE SET
                    base_url=excluded.base_url, model=excluded.model, api_key=excluded.api_key,
                    validation_status=excluded.validation_status,
                    validated_at=excluded.validated_at,
                    last_validation_error_code=excluded.last_validation_error_code,
                    updated_at=excluded.updated_at
                """,
                (
                    saved.workspace_id, saved.user_id, saved.base_url, saved.model,
                    saved.api_key, saved.validation_status, saved.validated_at.isoformat(),
                    saved.last_validation_error_code, saved.created_at.isoformat(),
                    saved.updated_at.isoformat(),
                ),
            )
        return saved

    async def upsert_async(
        self,
        configuration: UserLLMConfiguration,
    ) -> UserLLMConfiguration:
        if self._writer is None:
            return self.upsert(configuration)
        existing = self.get(configuration.workspace_id, configuration.user_id)
        saved = UserLLMConfiguration(
            workspace_id=configuration.workspace_id,
            user_id=configuration.user_id,
            base_url=configuration.base_url,
            model=configuration.model,
            api_key=configuration.api_key,
            validation_status=configuration.validation_status,
            validated_at=configuration.validated_at,
            last_validation_error_code=configuration.last_validation_error_code,
            created_at=existing.created_at if existing else configuration.created_at,
            updated_at=datetime.now(timezone.utc),
        )
        await self._writer.submit(
            TypedMutation.create(
                mutation_id=(
                    f"llm_configuration_{saved.workspace_id}_{saved.user_id}_"
                    f"{saved.updated_at.isoformat()}"
                ),
                mutation_kind="mutate_runtime_accounting",
                domain_payload={
                    "action": "upsert_llm_configuration",
                    "fields": [
                        saved.workspace_id,
                        saved.user_id,
                        saved.base_url,
                        saved.model,
                        saved.api_key,
                        saved.validation_status,
                        saved.validated_at.isoformat(),
                        saved.last_validation_error_code,
                        saved.created_at.isoformat(),
                        saved.updated_at.isoformat(),
                    ],
                },
            )
        )
        return saved

    def delete(self, workspace_id: str, user_id: str) -> bool:
        if self._writer is not None:
            result = self._writer.submit_sync(
                TypedMutation.create(
                    mutation_id=f"delete_llm_configuration_{workspace_id}_{user_id}",
                    mutation_kind="mutate_runtime_accounting",
                    domain_payload={
                        "action": "delete_llm_configuration",
                        "workspace_id": workspace_id,
                        "user_id": user_id,
                    },
                )
            )
            return result.result_fields.get("deleted") is True
        with open_bootstrap_database(self._db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM content_research_llm_configurations WHERE workspace_id = ? AND user_id = ?",
                (workspace_id, user_id),
            )
        return cursor.rowcount > 0

    async def delete_async(self, workspace_id: str, user_id: str) -> bool:
        if self._writer is None:
            return self.delete(workspace_id, user_id)
        result = await self._writer.submit(
            TypedMutation.create(
                mutation_id=f"delete_llm_configuration_{workspace_id}_{user_id}",
                mutation_kind="mutate_runtime_accounting",
                domain_payload={
                    "action": "delete_llm_configuration",
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                },
            )
        )
        return result.result_fields.get("deleted") is True

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> UserLLMConfiguration:
        return UserLLMConfiguration(
            workspace_id=str(row[0]), user_id=str(row[1]), base_url=str(row[2]),
            model=str(row[3]), api_key=str(row[4]), validation_status=str(row[5]),
            validated_at=datetime.fromisoformat(str(row[6])),
            last_validation_error_code=str(row[7]) if row[7] is not None else None,
            created_at=datetime.fromisoformat(str(row[8])),
            updated_at=datetime.fromisoformat(str(row[9])),
        )
