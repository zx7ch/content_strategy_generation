from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app.content_research.lifecycle.coordinator import (
    ContentResearchPersistenceCoordinator,
)
from app.content_research.lifecycle.models import LifecycleCommand
from app.content_research.persistence_models import ReportPublicationRecord
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.core.runtime_schema_bootstrap import bootstrap_canonical_runtime_schema
from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.core.single_writer_migration import (
    InjectedMigrationCrash,
    MigrationIncompleteRunsError,
    MigrationSourceBusyError,
    SingleWriterDataMigrator,
)
from app.core.sqlite_runtime_lock import SQLiteRuntimeProcessLock
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.runtime_write_handlers import production_runtime_write_handlers
from app.services.llm.configuration_store import SQLiteLLMConfigurationStore
from app.services.xhs_credentials import XHSCredentialStore


async def _seed_incomplete_task_3_1_run(database_path: Path) -> None:
    async with ThreadStore(str(database_path)) as threads:
        thread = await threads.create_thread(
            title="Incomplete migration fixture",
            workspace_id="workspace-migration",
        )
    await ContentResearchPersistenceCoordinator(str(database_path)).apply(
        LifecycleCommand(
            command_id="migration-incomplete-submit",
            run_id="run-incomplete-migration",
            expected_state=None,
            expected_revision=0,
            kind="submit_research_subject",
            payload={
                "thread_id": thread["id"],
                "user_id": "migration-user",
                "seed_text": "迁移中的内容调研",
            },
        )
    )
    # The current bootstrap may contain post-Task-3.1 migrations. Keep this
    # fixture pinned to the released source generation that the Migrator owns.
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute(
            "DELETE FROM content_research_schema_migrations WHERE version > '0038'"
        )
        connection.execute("DROP TABLE IF EXISTS content_research_source_observations")


async def _seed_task_3_1_user_data(database_path: Path) -> None:
    await bootstrap_canonical_runtime_schema(
        database_path,
        discovery_secret="migration-source",
    )
    now = "2026-08-30T12:00:00+00:00"
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute(
            "INSERT INTO md_workspaces VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("workspace-kept", "Kept Workspace", "kept", "Asia/Shanghai", "active", now, now),
        )
        connection.execute(
            "INSERT INTO md_brands VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "brand-kept",
                "workspace-kept",
                "Kept Brand",
                "beauty",
                "growth",
                '{}',
                '{}',
                '{}',
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO md_brand_channels VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "channel-kept",
                "workspace-kept",
                "brand-kept",
                "xiaohongshu",
                "account-kept",
                "Kept Channel",
                None,
                "active",
                '{}',
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO md_policy_configs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "policy-kept",
                "workspace-kept",
                "brand-kept",
                "default",
                "2026-08-30",
                '{}',
                '{}',
                '{}',
                '{}',
                1,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO creator_threads "
            "(id, workspace_id, brand_id, title, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'accepted', ?, ?)",
            ("thread-kept", "workspace-kept", "brand-kept", "Migrated thread", now, now),
        )
        connection.execute(
            "INSERT INTO creator_messages "
            "(id, thread_id, role, text, message_type, run_id, created_at) "
            "VALUES (?, ?, 'user', ?, 'text', ?, ?)",
            ("message-kept", "thread-kept", "Keep this message", "run-kept", now),
        )
        connection.execute(
            "INSERT INTO workflow_runs "
            "(run_id, thread_id, user_id, status, phase, current_step, artifact_version, "
            "created_at, updated_at, completed_at, content_research_state, state_revision, "
            "state_entered_at, lifecycle_schema_version) "
            "VALUES (?, ?, ?, 'succeeded', 'finalization', 'report', 1, ?, ?, ?, "
            "'report_ready', 1, ?, 'content-research-lifecycle')",
            ("run-kept", "thread-kept", "user-kept", now, now, now, now),
        )
        connection.execute(
            "INSERT INTO workflow_artifacts "
            "(artifact_id, run_id, thread_id, artifact_type, artifact_version, status, "
            "payload_mode, payload_json, summary_text) "
            "VALUES (?, ?, ?, 'final_result', 1, 'created', 'snapshot', ?, ?)",
            (
                "artifact-kept",
                "run-kept",
                "thread-kept",
                '{"report_publication_id":"publication-kept","title":"Kept report"}',
                "Kept report",
            ),
        )
        connection.execute(
            "INSERT INTO content_research_report_publications "
            "(id, schema_version, workflow_run_id, research_plan_id, governed_snapshot_id, "
            "governed_snapshot_version, input_fingerprint, policy_version, algorithm_version, "
            "report_draft_id, faithfulness_decision_id, publication_state, payload_json, "
            "metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?)",
            (
                "publication-kept",
                "content-research-report-publication",
                "run-kept",
                "plan-kept",
                "snapshot-kept",
                "1",
                "sha256:kept",
                "policy-kept",
                "algorithm-kept",
                "draft-kept",
                "decision-kept",
                "complete_verified_report",
                '{"title":"Kept report","citations":["evidence-kept"]}',
                now,
            ),
        )
        connection.execute(
            "INSERT INTO content_research_evidence_lineage "
            "(id, workflow_run_id, evidence_record_id, research_plan_id, schema_version, "
            "transformation_type, transformation_version, lineage_payload_json, "
            "metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, '{}', '{}', ?)",
            (
                "lineage-kept",
                "run-kept",
                "evidence-kept",
                "plan-kept",
                "content-research-evidence-lineage",
                "citation",
                "1",
                now,
            ),
        )
        connection.execute(
            "INSERT INTO content_research_llm_configurations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "workspace-kept",
                "user-kept",
                "https://api.openai.com/compatible",
                "gpt-kept",
                "secret-kept",
                "validated",
                now,
                None,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO xhs_local_credentials "
            "(singleton, cookie, source, status, created_at, updated_at, failure_code) "
            "VALUES (1, ?, 'manual_cookie', 'authenticated', ?, ?, NULL)",
            ("cookie-kept", now, now),
        )
        connection.execute(
            "DELETE FROM content_research_schema_migrations WHERE version > '0038'"
        )
        connection.execute("DROP TABLE IF EXISTS content_research_source_observations")


@pytest.mark.acceptance
def test_migration_incomplete_run_archive_crash_and_backup_restore_matrix(
    tmp_path: Path,
) -> None:
    migrator = SingleWriterDataMigrator()
    missing_source = tmp_path / "fresh-source.sqlite"

    fresh = migrator.inspect(missing_source)

    assert fresh.source_kind == "fresh"
    assert fresh.schema_version is None
    assert fresh.incomplete_run_ids == ()
    assert not missing_source.exists()

    source = tmp_path / "task-3-1.sqlite"
    asyncio.run(_seed_incomplete_task_3_1_run(source))
    source_hash_before = hashlib.sha256(source.read_bytes()).hexdigest()
    files_before = {item.name for item in tmp_path.iterdir()}

    assessment = migrator.inspect(source)

    assert assessment.source_kind == "task_3_1"
    assert assessment.schema_version == "0038"
    assert assessment.incomplete_run_ids == ("run-incomplete-migration",)
    assert assessment.requires_incomplete_policy is True
    assert assessment.family_counts["threads"] == 1
    assert assessment.family_counts["runs"] == 1
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash_before
    assert {item.name for item in tmp_path.iterdir()} == files_before

    target = tmp_path / "single-writer.sqlite"
    manifest = tmp_path / "runtime-data-manifest.json"
    with pytest.raises(MigrationIncompleteRunsError) as insufficient_policy:
        migrator.migrate(source, target, incomplete_policy=None)
    assert insufficient_policy.value.error_code == "MIGRATION_INCOMPLETE_RUNS_PRESENT"
    assert insufficient_policy.value.run_ids == ("run-incomplete-migration",)
    assert not target.exists()
    assert not manifest.exists()
    assert not (tmp_path / "migration-backups").exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash_before

    source_lock = SQLiteRuntimeProcessLock(str(source))
    source_lock.acquire()
    try:
        with pytest.raises(MigrationSourceBusyError) as busy:
            migrator.migrate(source, target, incomplete_policy="archive_incomplete")
        assert busy.value.error_code == "MIGRATION_SOURCE_BUSY"
        assert not target.exists()
        assert not manifest.exists()
    finally:
        source_lock.release()

    for crash_stage in (
        "before_backup",
        "during_import",
        "during_validation",
        "before_manifest_activation",
    ):
        crashing = SingleWriterDataMigrator(
            fault_injector=lambda stage, expected=crash_stage: (
                (_ for _ in ()).throw(InjectedMigrationCrash(expected))
                if stage == expected
                else None
            )
        )
        with pytest.raises(InjectedMigrationCrash):
            crashing.migrate(
                source,
                target,
                incomplete_policy="archive_incomplete",
            )
        assert not manifest.exists()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash_before

    receipt = migrator.migrate(
        source,
        target,
        incomplete_policy="archive_incomplete",
    )

    assert receipt.status == "activated"
    assert receipt.archived_run_ids == ("run-incomplete-migration",)
    assert receipt.imported_family_counts["threads"] == 1
    assert receipt.imported_family_counts["runs"] == 1
    assert receipt.source_fingerprint.startswith("sha256:")
    assert receipt.backup_fingerprint.startswith("sha256:")
    assert receipt.target_fingerprint.startswith("sha256:")
    assert "migration-user" not in json.dumps(receipt.as_safe_dict())
    assert str(tmp_path) not in json.dumps(receipt.as_safe_dict())
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash_before

    backups = tuple((tmp_path / "migration-backups").glob("*.sqlite"))
    assert len(backups) == 1
    assert backups[0].stat().st_mode & 0o222 == 0
    with closing(
        sqlite3.connect(f"file:{backups[0]}?mode=ro", uri=True)
    ) as backup:
        assert backup.execute(
            "SELECT content_research_state FROM workflow_runs WHERE run_id=?",
            ("run-incomplete-migration",),
        ).fetchone() == ("presearch_running",)

    activated = json.loads(manifest.read_text(encoding="utf-8"))
    assert activated == {
        "database": target.name,
        "layout": "single_writer",
        "receipt_id": receipt.receipt_id,
        "target_fingerprint": receipt.target_fingerprint,
    }
    with closing(sqlite3.connect(f"file:{target}?mode=ro", uri=True)) as migrated:
        migrated.row_factory = sqlite3.Row
        archived = migrated.execute(
            "SELECT status, public_failure_code, read_only FROM archived_runs "
            "WHERE run_id=?",
            ("run-incomplete-migration",),
        ).fetchone()
        assert dict(archived) == {
            "status": "UPGRADE_INTERRUPTED",
            "public_failure_code": "upgrade_interrupted",
            "read_only": 1,
        }

    repeated = migrator.migrate(
        source,
        target,
        incomplete_policy="archive_incomplete",
    )
    assert repeated == receipt
    assert len(tuple((tmp_path / "migration-backups").glob("*.sqlite"))) == 1


@pytest.mark.acceptance
def test_task_3_1_user_data_migrates_to_single_writer_capabilities(
    tmp_path: Path,
) -> None:
    source = tmp_path / "task-3-1-release.sqlite"
    target = tmp_path / "single-writer.sqlite"
    asyncio.run(_seed_task_3_1_user_data(source))
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    assessment = SingleWriterDataMigrator().inspect(source)
    assert assessment.schema_version == "0038"
    assert all(
        assessment.family_counts[family] == 1
        for family in (
            "workspaces",
            "brands",
            "channels",
            "policies",
            "threads",
            "messages",
            "runs",
            "artifacts",
            "reports",
            "citations",
            "configurations",
            "credentials",
        )
    )

    receipt = SingleWriterDataMigrator().migrate(
        source,
        target,
        incomplete_policy=None,
    )
    assert receipt.status == "activated"
    assert receipt.archived_run_ids == ()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash

    async def prove_canonical_capabilities() -> None:
        writer = RuntimeWriteCoordinator(
            target,
            handlers=production_runtime_write_handlers(),
        )
        await writer.start()
        try:
            async with ThreadStore(str(target)) as threads:
                thread = await threads.get_thread("thread-kept")
                messages = await threads.get_thread_messages("thread-kept")
            async with WorkflowStore(str(target)) as workflows:
                artifacts = await workflows.list_artifacts("run-kept")
            projection = ContentResearchPersistenceCoordinator(str(target)).load_now(
                "run-kept"
            )
            content = SQLiteContentResearchStore(str(target))
            publication = content.get_typed_record(
                ReportPublicationRecord,
                "publication-kept",
            )
            lineage = content.list_evidence_lineage("evidence-kept")
            configuration = SQLiteLLMConfigurationStore(str(target)).get(
                "workspace-kept",
                "user-kept",
            )
            credential = XHSCredentialStore(str(target)).get_status()

            assert thread is not None and thread["title"] == "Migrated thread"
            assert [message["text"] for message in messages] == ["Keep this message"]
            assert [artifact.artifact_id for artifact in artifacts] == ["artifact-kept"]
            assert projection.state.value == "report_ready"
            assert projection.allowed_actions == ()
            assert publication is not None and publication.payload["title"] == "Kept report"
            assert [item.id for item in lineage] == ["lineage-kept"]
            assert configuration is not None and configuration.model == "gpt-kept"
            assert credential.authenticated is True
        finally:
            await writer.close()

    asyncio.run(prove_canonical_capabilities())
