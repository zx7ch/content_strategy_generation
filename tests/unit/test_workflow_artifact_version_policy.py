"""Unit tests for T8.1 workflow artifact version policy."""

from __future__ import annotations

import pytest

from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowArtifactPayloadMode, WorkflowArtifactType
from app.services.workflow_artifact_policy import (
    WorkflowArtifactMaterializationError,
    WorkflowArtifactVersionPolicy,
)
from app.services.workflow_run_manager import WorkflowRunManager


async def _start_run(db_path: str):
    async with WorkflowRunManager(db_path) as manager:
        return await manager.start_run(thread_id="thread-1", user_id="user-1")


@pytest.mark.asyncio
async def test_same_parent_rewrites_allocate_monotonic_versions(tmp_path):
    db_path = str(tmp_path / "artifact_policy.db")
    run = await _start_run(db_path)

    async with WorkflowRunManager(db_path) as manager:
        parent = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={"title": "原版", "content": "A"},
        )
        first = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            parent_artifact_id=parent.artifact_id,
            artifact_version=parent.artifact_version + 1,
            payload={"changed_fields": {"content": "B"}, "base_artifact_id": parent.artifact_id, "base_artifact_version": 1},
        )
        second = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            parent_artifact_id=parent.artifact_id,
            artifact_version=parent.artifact_version + 1,
            payload={"changed_fields": {"content": "C"}, "base_artifact_id": parent.artifact_id, "base_artifact_version": 1},
        )

    assert parent.payload_mode == WorkflowArtifactPayloadMode.SNAPSHOT
    assert first.payload_mode == WorkflowArtifactPayloadMode.PATCH
    assert second.payload_mode == WorkflowArtifactPayloadMode.PATCH
    assert [parent.artifact_version, first.artifact_version, second.artifact_version] == [1, 2, 3]


@pytest.mark.asyncio
async def test_patch_materialization_merges_changed_fields(tmp_path):
    db_path = str(tmp_path / "materialize.db")
    run = await _start_run(db_path)

    async with WorkflowRunManager(db_path) as manager:
        parent = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={"note_id": "n1", "title": "原版", "content": "A", "tags": ["old"]},
        )
        patch = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            parent_artifact_id=parent.artifact_id,
            payload={
                "patch_type": "merge",
                "base_artifact_id": parent.artifact_id,
                "base_artifact_version": parent.artifact_version,
                "changed_fields": {"title": "新版", "tags": ["new"]},
            },
        )

    artifacts = await WorkflowArtifactVersionPolicy(db_path).materialize_run_artifacts(run.run_id)
    materialized = {artifact["artifact_id"]: artifact for artifact in artifacts}

    assert materialized[patch.artifact_id]["payload_json"] == {
        "note_id": "n1",
        "title": "新版",
        "content": "A",
        "tags": ["new"],
    }
    assert materialized[parent.artifact_id]["payload_json"]["title"] == "原版"


@pytest.mark.asyncio
async def test_materialization_rejects_missing_parent(tmp_path):
    db_path = str(tmp_path / "missing_parent.db")
    run = await _start_run(db_path)

    async with WorkflowRunManager(db_path) as manager:
        await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            parent_artifact_id="artifact_missing",
            payload={"changed_fields": {"title": "孤儿 patch"}},
        )

    with pytest.raises(WorkflowArtifactMaterializationError) as exc:
        await WorkflowArtifactVersionPolicy(db_path).materialize_run_artifacts(run.run_id)
    assert exc.value.code == "ARTIFACT_PARENT_MISSING"


@pytest.mark.asyncio
async def test_materialization_rejects_base_version_mismatch(tmp_path):
    db_path = str(tmp_path / "base_mismatch.db")
    run = await _start_run(db_path)

    async with WorkflowRunManager(db_path) as manager:
        parent = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={"title": "原版"},
        )
        await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            parent_artifact_id=parent.artifact_id,
            payload={
                "base_artifact_id": parent.artifact_id,
                "base_artifact_version": 99,
                "changed_fields": {"title": "错误基线"},
            },
        )

    with pytest.raises(WorkflowArtifactMaterializationError) as exc:
        await WorkflowArtifactVersionPolicy(db_path).materialize_run_artifacts(run.run_id)
    assert exc.value.code == "ARTIFACT_BASE_MISMATCH"


@pytest.mark.asyncio
async def test_materialization_rejects_cycle_and_max_depth(tmp_path):
    db_path = str(tmp_path / "cycle_depth.db")
    run = await _start_run(db_path)

    async with WorkflowRunManager(db_path) as manager:
        parent = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={"title": "root"},
        )
        patch = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            parent_artifact_id=parent.artifact_id,
            payload={"changed_fields": {"title": "patch"}},
        )
    async with WorkflowStore(db_path) as store:
        assert store._conn is not None
        await store._conn.execute(
            "UPDATE workflow_artifacts SET parent_artifact_id = ?, payload_mode = 'patch' WHERE artifact_id = ?",
            (patch.artifact_id, parent.artifact_id),
        )
        await store._conn.commit()

    with pytest.raises(WorkflowArtifactMaterializationError) as exc:
        await WorkflowArtifactVersionPolicy(db_path).materialize_run_artifacts(run.run_id)
    assert exc.value.code == "ARTIFACT_PATCH_CYCLE"

    db_path_depth = str(tmp_path / "depth.db")
    run_depth = await _start_run(db_path_depth)
    async with WorkflowRunManager(db_path_depth) as manager:
        root = await manager.attach_artifact(
            run_id=run_depth.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={"title": "root"},
        )
        patch_1 = await manager.attach_artifact(
            run_id=run_depth.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            parent_artifact_id=root.artifact_id,
            payload={"changed_fields": {"title": "one"}},
        )
        await manager.attach_artifact(
            run_id=run_depth.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            parent_artifact_id=patch_1.artifact_id,
            payload={"changed_fields": {"title": "two"}},
        )

    with pytest.raises(WorkflowArtifactMaterializationError) as depth_exc:
        await WorkflowArtifactVersionPolicy(db_path_depth, max_materialization_depth=1).materialize_run_artifacts(run_depth.run_id)
    assert depth_exc.value.code == "ARTIFACT_PATCH_MAX_DEPTH"


@pytest.mark.asyncio
async def test_publishable_notes_require_final_or_accepted_artifacts(tmp_path):
    db_path = str(tmp_path / "publishable.db")
    run = await _start_run(db_path)

    async with WorkflowRunManager(db_path) as manager:
        draft = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={"note_id": "draft", "title": "草稿", "content": "no"},
        )
        accepted = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={"note_id": "accepted", "title": "已接受", "content": "yes"},
        )
        accepted_patch = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            parent_artifact_id=accepted.artifact_id,
            payload={"changed_fields": {"content": "patch should not publish directly"}},
        )
    async with WorkflowStore(db_path) as store:
        await store.update_artifact_status(draft.artifact_id, "superseded")
        await store.update_artifact_status(accepted.artifact_id, "accepted")
        await store.update_artifact_status(accepted_patch.artifact_id, "accepted")

    policy = WorkflowArtifactVersionPolicy(db_path)
    artifacts = await policy.materialize_run_artifacts(run.run_id)

    assert policy.select_publishable_notes(artifacts) == [
        {
            "note_id": "accepted",
            "title": "已接受",
            "content": "yes",
            "tags": [],
            "topic_type": "方法",
            "core_hypothesis": "认可笔记可沉淀为后续创作选题",
            "score": 0.0,
            "score_type": "predicted",
            "source": "publish_candidate",
        }
    ]
