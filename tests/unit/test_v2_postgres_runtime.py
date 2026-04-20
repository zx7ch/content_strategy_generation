from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.v2.db.runner import run_p1_1_migrations, run_p1_2_migrations, run_p1_5_migrations
from app.v2.decision.bootstrap import build_decision_runtime
from app.v2.decision.postgres_store import PostgresDecisionStore
from app.v2.decision.store import InMemoryDecisionStore
from app.v2.feedback.bootstrap import build_feedback_runtime
from app.v2.feedback.postgres_store import PostgresFeedbackStore
from app.v2.feedback.store import InMemoryFeedbackStore
from app.v2.foundation.bootstrap import build_master_data_runtime
from app.v2.foundation.models import WorkspaceRecord
from app.v2.foundation.postgres_store import PostgresMasterDataStore
from app.v2.foundation.service import MasterDataService
from app.v2.foundation.store import InMemoryMasterDataStore
from app.v2.ingestion.bootstrap import build_ingestion_runtime
from app.v2.ingestion.models import IngestionRunRecord
from app.v2.ingestion.postgres_store import PostgresIngestionStore
from app.v2.ingestion.service import IngestionService
from app.v2.ingestion.store import InMemoryIngestionStore
from app.v2.runtime import V2RuntimeConfigurationError, resolve_v2_backend
from app.v2.topic_pool.bootstrap import build_topic_pool_runtime
from app.v2.topic_pool.postgres_store import PostgresTopicPoolStore
from app.v2.topic_pool.store import InMemoryTopicPoolStore


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self._fetchone_value: dict[str, Any] | None = None
        self._fetchall_value: list[dict[str, Any]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, query: str, params: Any = None) -> None:
        self.connection.statements.append((query, params))
        normalized = " ".join(query.split()).lower()
        if "select migration_id from schema_migrations" in normalized:
            self._fetchall_value = list(self.connection.migration_rows)
        elif "returning id, name, slug, timezone, status, created_at, updated_at" in normalized:
            self._fetchone_value = {
                "id": params[0],
                "name": params[1],
                "slug": params[2],
                "timezone": params[3],
                "status": params[4],
                "created_at": params[5],
                "updated_at": params[6],
            }
        elif "returning id, workspace_id, brand_id, entry_type, source_type, source_adapter, dedupe_key" in normalized:
            row = {
                "id": params[0],
                "workspace_id": params[1],
                "brand_id": params[2],
                "entry_type": params[3],
                "source_type": params[4],
                "source_adapter": params[5],
                "dedupe_key": params[6],
                "source_config": params[7],
                "stats": params[8],
                "error_summary": params[9],
                "status": params[10],
                "started_at": params[11],
                "finished_at": params[12],
                "created_at": params[13],
            }
            self.connection.ingestion_runs[row["id"]] = row
            self._fetchone_value = row
        elif "from ingestion_runs" in normalized:
            rows = list(self.connection.ingestion_runs.values())
            if "where brand_id =" in normalized and params:
                rows = [row for row in rows if row["brand_id"] == params[0]]
            rows.sort(key=lambda item: item["created_at"], reverse=True)
            self._fetchall_value = rows[: params[1] if params and len(params) > 1 else len(rows)]
            self._fetchone_value = self._fetchall_value[0] if "limit 1" in normalized and self._fetchall_value else None
        else:
            self._fetchone_value = None
            self._fetchall_value = []

    def fetchone(self) -> dict[str, Any] | None:
        return self._fetchone_value

    def fetchall(self) -> list[dict[str, Any]]:
        return self._fetchall_value


class FakeConnection:
    def __init__(self, migration_rows: list[dict[str, Any]] | None = None) -> None:
        self.migration_rows = migration_rows or []
        self.statements: list[tuple[str, Any]] = []
        self.commits = 0
        self.ingestion_runs: dict[str, dict[str, Any]] = {}

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1


def test_run_p1_1_migrations_applies_only_pending_steps() -> None:
    connection = FakeConnection(migration_rows=[])

    applied = run_p1_1_migrations("postgresql://example", connector=lambda _dsn: connection)

    assert applied == ("v2_p1_1_foundation", "v2_p1_6_remove_account_handle")
    assert any("create table if not exists schema_migrations" in " ".join(sql.lower().split()) for sql, _ in connection.statements)
    assert any("insert into schema_migrations" in " ".join(sql.lower().split()) for sql, _ in connection.statements)
    assert connection.commits == 1


def test_run_p1_1_migrations_skips_existing_steps() -> None:
    connection = FakeConnection(migration_rows=[{"migration_id": "v2_p1_1_foundation"}])

    applied = run_p1_1_migrations("postgresql://example", connector=lambda _dsn: connection)

    assert applied == ("v2_p1_6_remove_account_handle",)
    insert_migration_statements = [
        sql for sql, _ in connection.statements if "insert into schema_migrations" in " ".join(sql.lower().split())
    ]
    assert len(insert_migration_statements) == 1


def test_run_p1_2_migrations_applies_foundation_and_ingestion_steps() -> None:
    connection = FakeConnection(migration_rows=[])

    applied = run_p1_2_migrations("postgresql://example", connector=lambda _dsn: connection)

    assert applied == (
        "v2_p1_1_foundation",
        "v2_p1_6_remove_account_handle",
        "v2_p1_2_ingestion",
        "v2_p1_2_ingestion_workspace_alignment",
    )


def test_run_p1_2_migrations_skips_existing_foundation_step() -> None:
    connection = FakeConnection(migration_rows=[{"migration_id": "v2_p1_1_foundation"}])

    applied = run_p1_2_migrations("postgresql://example", connector=lambda _dsn: connection)

    assert applied == (
        "v2_p1_6_remove_account_handle",
        "v2_p1_2_ingestion",
        "v2_p1_2_ingestion_workspace_alignment",
    )


def test_run_p1_5_migrations_applies_feedback_eval_step() -> None:
    connection = FakeConnection(
        migration_rows=[
            {"migration_id": "v2_p1_1_foundation"},
            {"migration_id": "v2_p1_2_ingestion"},
        ]
    )

    applied = run_p1_5_migrations("postgresql://example", connector=lambda _dsn: connection)

    assert applied == (
        "v2_p1_6_remove_account_handle",
        "v2_p1_2_ingestion_workspace_alignment",
        "v2_p1_5_feedback_eval",
    )


def test_postgres_master_data_store_can_save_workspace_with_connector() -> None:
    connection = FakeConnection()
    store = PostgresMasterDataStore("postgresql://example", connector=lambda _dsn: connection)

    workspace = WorkspaceRecord(
        id="ws-1",
        name="Acme",
        slug="acme",
        timezone="Asia/Shanghai",
        status="active",
        created_at=datetime(2026, 4, 12, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 12, tzinfo=timezone.utc),
    )

    saved = store.save_workspace(workspace)

    assert saved.id == "ws-1"
    assert saved.slug == "acme"
    assert connection.commits == 1


def test_build_master_data_runtime_uses_in_memory_without_postgres_dsn() -> None:
    settings = Settings(_env_file=None)

    store, service = build_master_data_runtime(settings)

    assert store.__class__.__name__ == "InMemoryMasterDataStore"
    assert service.__class__.__name__ == "MasterDataService"


def test_resolve_v2_backend_requires_postgres_for_production_env() -> None:
    settings = Settings(_env_file=None, APP_ENV="production")

    try:
        resolve_v2_backend(settings, component="foundation")
    except V2RuntimeConfigurationError as exc:
        assert "POSTGRES_DSN is required" in str(exc)
    else:
        raise AssertionError("Expected production runtime without POSTGRES_DSN to fail closed")


def test_resolve_v2_backend_allows_in_memory_for_local_test_envs() -> None:
    settings = Settings(_env_file=None, APP_ENV="test")

    assert resolve_v2_backend(settings, component="foundation") == "in_memory"


def test_postgres_ingestion_store_can_save_and_list_runs_with_connector(monkeypatch) -> None:
    connection = FakeConnection()
    monkeypatch.setattr(
        "app.v2.ingestion.postgres_store._load_psycopg_jsonb",
        lambda: (None, None, lambda value: value),
    )
    store = PostgresIngestionStore("postgresql://example", connector=lambda _dsn: connection)

    run = IngestionRunRecord(
        id="run-1",
        workspace_id="ws-1",
        brand_id="brand-1",
        entry_type="data_import",
        source_type="manual_import",
        source_adapter="historical_note_import_v1",
        source_config={"platform": "xiaohongshu"},
        stats={"accepted_row_count": 1},
        error_summary={},
        status="completed",
        started_at=datetime(2026, 4, 12, tzinfo=timezone.utc),
        finished_at=datetime(2026, 4, 12, tzinfo=timezone.utc),
        created_at=datetime(2026, 4, 12, tzinfo=timezone.utc),
    )

    saved = store.save_ingestion_run(run)
    listed = store.list_ingestion_runs("brand-1")

    assert saved.id == "run-1"
    assert saved.source_config == {"platform": "xiaohongshu"}
    assert len(listed) == 1
    assert listed[0].id == "run-1"
    assert connection.commits >= 1


def test_build_ingestion_runtime_uses_in_memory_without_postgres_dsn() -> None:
    settings = Settings(_env_file=None)

    store, service = build_ingestion_runtime(settings)

    assert store.__class__.__name__ == "InMemoryIngestionStore"
    assert service.__class__.__name__ == "IngestionService"


def test_build_topic_pool_runtime_uses_in_memory_without_postgres_dsn() -> None:
    settings = Settings(_env_file=None)
    _, master_service = build_master_data_runtime(settings)
    _, ingestion_service = build_ingestion_runtime(settings)

    store, service = build_topic_pool_runtime(
        settings,
        master_data_service=master_service,
        ingestion_store=ingestion_service._store,  # type: ignore[attr-defined]
    )

    assert isinstance(store, InMemoryTopicPoolStore)
    assert service.__class__.__name__ == "TopicPoolService"


def test_build_decision_runtime_uses_in_memory_without_postgres_dsn() -> None:
    settings = Settings(_env_file=None)
    _, master_service = build_master_data_runtime(settings)
    _, ingestion_service = build_ingestion_runtime(settings)
    topic_pool_store, _ = build_topic_pool_runtime(
        settings,
        master_data_service=master_service,
        ingestion_store=ingestion_service._store,  # type: ignore[attr-defined]
    )

    store, service = build_decision_runtime(
        settings,
        master_data_service=master_service,
        topic_pool_store=topic_pool_store,
    )

    assert isinstance(store, InMemoryDecisionStore)
    assert service.__class__.__name__ == "DecisionService"


def test_build_feedback_runtime_uses_in_memory_without_postgres_dsn() -> None:
    settings = Settings(_env_file=None)
    _, master_service = build_master_data_runtime(settings)
    _, ingestion_service = build_ingestion_runtime(settings)
    topic_pool_store, _ = build_topic_pool_runtime(
        settings,
        master_data_service=master_service,
        ingestion_store=ingestion_service._store,  # type: ignore[attr-defined]
    )
    decision_store, _ = build_decision_runtime(
        settings,
        master_data_service=master_service,
        topic_pool_store=topic_pool_store,
    )

    store, service = build_feedback_runtime(
        settings,
        master_data_service=master_service,
        topic_pool_store=topic_pool_store,
        decision_store=decision_store,
    )

    assert isinstance(store, InMemoryFeedbackStore)
    assert service.__class__.__name__ == "FeedbackService"


def test_build_ingestion_runtime_uses_postgres_when_dsn_present(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://user:pass@localhost:5432/xhs")
    settings = Settings(_env_file=None)
    called: list[str] = []

    monkeypatch.setattr(
        "app.v2.ingestion.bootstrap.run_p1_2_migrations",
        lambda dsn: called.append(dsn),
    )
    monkeypatch.setattr(
        "app.v2.ingestion.bootstrap.PostgresIngestionStore",
        lambda dsn: ("postgres-store", dsn),
    )

    store, service = build_ingestion_runtime(settings)

    assert called == ["postgresql://user:pass@localhost:5432/xhs"]
    assert store == ("postgres-store", "postgresql://user:pass@localhost:5432/xhs")
    assert service.__class__.__name__ == "IngestionService"


def test_build_topic_pool_runtime_uses_postgres_when_dsn_present(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://user:pass@localhost:5432/xhs")
    settings = Settings(_env_file=None)
    master_service = MasterDataService(InMemoryMasterDataStore())
    ingestion_store = InMemoryIngestionStore()
    _ingestion_service = IngestionService(ingestion_store)
    called: list[str] = []

    monkeypatch.setattr("app.v2.topic_pool.bootstrap.run_p1_2_migrations", lambda dsn: called.append(dsn))
    monkeypatch.setattr("app.v2.topic_pool.bootstrap.PostgresTopicPoolStore", lambda dsn: ("postgres-topic-pool", dsn))

    store, service = build_topic_pool_runtime(
        settings,
        master_data_service=master_service,
        ingestion_store=ingestion_store,
    )

    assert called == ["postgresql://user:pass@localhost:5432/xhs"]
    assert store == ("postgres-topic-pool", "postgresql://user:pass@localhost:5432/xhs")
    assert service.__class__.__name__ == "TopicPoolService"


def test_build_decision_runtime_uses_postgres_when_dsn_present(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://user:pass@localhost:5432/xhs")
    settings = Settings(_env_file=None)
    master_service = MasterDataService(InMemoryMasterDataStore())
    topic_pool_store = object()
    called: list[str] = []

    monkeypatch.setattr("app.v2.decision.bootstrap.run_p1_2_migrations", lambda dsn: called.append(dsn))
    monkeypatch.setattr("app.v2.decision.bootstrap.PostgresDecisionStore", lambda dsn: ("postgres-decision", dsn))

    store, service = build_decision_runtime(
        settings,
        master_data_service=master_service,
        topic_pool_store=topic_pool_store,  # type: ignore[arg-type]
    )

    assert called == ["postgresql://user:pass@localhost:5432/xhs"]
    assert store == ("postgres-decision", "postgresql://user:pass@localhost:5432/xhs")
    assert service.__class__.__name__ == "DecisionService"


def test_build_feedback_runtime_uses_postgres_when_dsn_present(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://user:pass@localhost:5432/xhs")
    settings = Settings(_env_file=None)
    master_service = MasterDataService(InMemoryMasterDataStore())
    called: list[str] = []

    monkeypatch.setattr("app.v2.feedback.bootstrap.run_p1_5_migrations", lambda dsn: called.append(dsn))
    monkeypatch.setattr("app.v2.feedback.bootstrap.PostgresFeedbackStore", lambda dsn: ("postgres-feedback", dsn))

    store, service = build_feedback_runtime(
        settings,
        master_data_service=master_service,
        topic_pool_store=object(),  # type: ignore[arg-type]
        decision_store=object(),  # type: ignore[arg-type]
    )

    assert called == ["postgresql://user:pass@localhost:5432/xhs"]
    assert store == ("postgres-feedback", "postgresql://user:pass@localhost:5432/xhs")
    assert service.__class__.__name__ == "FeedbackService"
