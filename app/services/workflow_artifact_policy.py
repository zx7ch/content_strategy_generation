"""Artifact versioning, materialization, and publishability policy."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Optional

import aiosqlite

from app.config import settings
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowArtifactPayloadMode


class WorkflowArtifactMaterializationError(ValueError):
    """Raised when an artifact patch chain cannot be safely materialized."""

    def __init__(self, code: str, message: str, *, artifact_id: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.artifact_id = artifact_id


class WorkflowArtifactVersionPolicy:
    """Central policy for workflow artifact lineage and materialized reads."""

    NON_PUBLISHABLE_STATUSES = {"superseded", "rejected", "archived"}

    def __init__(self, db_path: Optional[str] = None, *, max_materialization_depth: int = 20) -> None:
        self.db_path = db_path or settings.SQLITE_DB_PATH
        self.max_materialization_depth = max_materialization_depth

    @staticmethod
    async def allocate_artifact_version(
        conn: aiosqlite.Connection,
        *,
        run_id: str,
        artifact_type: str,
        parent_artifact_id: Optional[str],
        requested_version: Optional[int],
        fallback_version: int,
    ) -> int:
        if parent_artifact_id is None:
            return requested_version or fallback_version

        async with conn.execute(
            "SELECT artifact_version FROM workflow_artifacts WHERE artifact_id = ?",
            (parent_artifact_id,),
        ) as cursor:
            parent = await cursor.fetchone()
        parent_version = int(parent["artifact_version"]) if parent is not None else 0

        async with conn.execute(
            """
            SELECT MAX(artifact_version) AS max_version
            FROM workflow_artifacts
            WHERE run_id = ? AND artifact_type = ? AND parent_artifact_id = ?
            """,
            (run_id, artifact_type, parent_artifact_id),
        ) as cursor:
            sibling = await cursor.fetchone()
        max_sibling_version = int(sibling["max_version"]) if sibling and sibling["max_version"] is not None else parent_version
        minimum_next = max(parent_version + 1, max_sibling_version + 1)
        if requested_version is not None and requested_version > max_sibling_version:
            return requested_version
        return minimum_next

    async def materialize_run_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        async with WorkflowStore(self.db_path) as store:
            artifacts = [artifact.model_dump(mode="json") for artifact in await store.list_artifacts(run_id)]
        return self.materialize_artifact_dicts(artifacts)

    async def safe_materialize_run_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        async with WorkflowStore(self.db_path) as store:
            artifacts = [artifact.model_dump(mode="json") for artifact in await store.list_artifacts(run_id)]
        return self.safe_materialize_artifact_dicts(artifacts)

    def materialize_artifact_dicts(self, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id = {artifact["artifact_id"]: artifact for artifact in artifacts}
        return [self._materialize(artifact, by_id, stack=[]) for artifact in artifacts]

    def _materialize(
        self,
        artifact: dict[str, Any],
        by_id: dict[str, dict[str, Any]],
        *,
        stack: list[str],
    ) -> dict[str, Any]:
        artifact_id = artifact["artifact_id"]
        if artifact_id in stack:
            raise WorkflowArtifactMaterializationError(
                "ARTIFACT_PATCH_CYCLE",
                f"Artifact patch lineage has a cycle at {artifact_id}",
                artifact_id=artifact_id,
            )
        if len(stack) >= self.max_materialization_depth:
            raise WorkflowArtifactMaterializationError(
                "ARTIFACT_PATCH_MAX_DEPTH",
                f"Artifact patch lineage exceeds max depth {self.max_materialization_depth}",
                artifact_id=artifact_id,
            )

        result = deepcopy(artifact)
        if (artifact.get("payload_mode") or "snapshot") == WorkflowArtifactPayloadMode.SNAPSHOT.value:
            result["materialized_payload_json"] = deepcopy(artifact.get("payload_json"))
            result["materialized"] = True
            return result

        parent_id = artifact.get("parent_artifact_id")
        if not parent_id or parent_id not in by_id:
            raise WorkflowArtifactMaterializationError(
                "ARTIFACT_PARENT_MISSING",
                f"Parent artifact is missing for patch artifact {artifact_id}",
                artifact_id=artifact_id,
            )

        patch = artifact.get("payload_json") or {}
        parent = by_id[parent_id]
        if patch.get("base_artifact_id") and patch["base_artifact_id"] != parent_id:
            raise WorkflowArtifactMaterializationError(
                "ARTIFACT_BASE_MISMATCH",
                f"Patch {artifact_id} base_artifact_id does not match parent_artifact_id",
                artifact_id=artifact_id,
            )
        if patch.get("base_artifact_version") is not None and int(patch["base_artifact_version"]) != int(parent["artifact_version"]):
            raise WorkflowArtifactMaterializationError(
                "ARTIFACT_BASE_MISMATCH",
                f"Patch {artifact_id} base_artifact_version does not match parent artifact version",
                artifact_id=artifact_id,
            )

        materialized_parent = self._materialize(parent, by_id, stack=[*stack, artifact_id])
        base_payload = deepcopy(materialized_parent.get("materialized_payload_json") or materialized_parent.get("payload_json") or {})
        changed_fields = patch.get("changed_fields")
        if changed_fields is None:
            changed_fields = {
                key: value
                for key, value in patch.items()
                if key not in {"patch_type", "base_artifact_id", "base_artifact_version", "operations"}
            }
        if not isinstance(base_payload, dict) or not isinstance(changed_fields, dict):
            raise WorkflowArtifactMaterializationError(
                "ARTIFACT_PATCH_UNSUPPORTED",
                f"Patch {artifact_id} must merge dict payloads",
                artifact_id=artifact_id,
            )
        base_payload.update(deepcopy(changed_fields))
        result["materialized_payload_json"] = base_payload
        result["payload_json"] = base_payload
        result["materialized"] = True
        result["materialized_from_patch"] = True
        return result

    def safe_materialize_artifact_dicts(self, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id = {artifact["artifact_id"]: artifact for artifact in artifacts}
        materialized: list[dict[str, Any]] = []
        for artifact in artifacts:
            try:
                materialized.append(self._materialize(artifact, by_id, stack=[]))
            except WorkflowArtifactMaterializationError as exc:
                diagnostic = deepcopy(artifact)
                diagnostic["materialized"] = False
                diagnostic["materialization_error"] = {
                    "code": exc.code,
                    "message": str(exc),
                    "artifact_id": exc.artifact_id,
                }
                materialized.append(diagnostic)
        return materialized

    def select_publishable_notes(self, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        proposal_by_id = self._proposal_by_id(artifacts)
        final_results = [
            artifact
            for artifact in artifacts
            if artifact.get("artifact_type") == "final_result"
            and artifact.get("payload_mode", "snapshot") == "snapshot"
            and artifact.get("status") not in self.NON_PUBLISHABLE_STATUSES
            and artifact.get("materialized") is not False
        ]
        if final_results:
            return self._notes_from_final_result(
                final_results[-1].get("materialized_payload_json") or final_results[-1].get("payload_json"),
                proposal_by_id=proposal_by_id,
            )

        notes: list[dict[str, Any]] = []
        for artifact in artifacts:
            if artifact.get("artifact_type") != "generated_note" or artifact.get("status") != "accepted":
                continue
            if artifact.get("payload_mode", "snapshot") != WorkflowArtifactPayloadMode.SNAPSHOT.value:
                continue
            if artifact.get("materialized") is False:
                continue
            note = self.note_from_payload(
                artifact.get("materialized_payload_json") or artifact.get("payload_json") or {},
                fallback_id=artifact["artifact_id"],
                proposal_by_id=proposal_by_id,
            )
            if note is not None:
                notes.append(note)
        return notes

    @staticmethod
    def note_from_payload(
        payload: dict[str, Any],
        *,
        fallback_id: str,
        proposal_by_id: Optional[dict[str, dict[str, Any]]] = None,
    ) -> Optional[dict[str, Any]]:
        note = payload.get("note") if isinstance(payload.get("note"), dict) else payload
        title = note.get("title") or note.get("hook") or note.get("summary")
        content = note.get("content") or note.get("body") or note.get("outline")
        if not title and not content:
            return None
        generation_params = note.get("generation_params") if isinstance(note.get("generation_params"), dict) else {}
        proposal_id = str(generation_params.get("proposal_id") or note.get("proposal_id") or "")
        proposal = (proposal_by_id or {}).get(proposal_id, {})
        score = (
            note.get("predicted_score")
            or note.get("score")
            or generation_params.get("proposal_score")
            or proposal.get("score")
        )
        try:
            normalized_score = float(score) if score is not None else 0.0
        except (TypeError, ValueError):
            normalized_score = 0.0
        topic_type = (
            note.get("topic_type")
            or generation_params.get("topic_type")
            or proposal.get("topic_type")
            or "方法"
        )
        core_hypothesis = (
            note.get("core_hypothesis")
            or note.get("hypothesis")
            or generation_params.get("proposal_angle")
            or proposal.get("angle")
            or "认可笔记可沉淀为后续创作选题"
        )
        return {
            "note_id": str(note.get("note_id") or note.get("id") or fallback_id),
            "title": str(title or "未命名笔记"),
            "content": str(content or ""),
            "tags": note.get("tags") or note.get("suggested_tags") or [],
            "topic_type": str(topic_type),
            "core_hypothesis": str(core_hypothesis),
            "score": normalized_score,
            "score_type": "predicted",
            "source": "publish_candidate",
        }

    @staticmethod
    def _proposal_by_id(artifacts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        proposals: dict[str, dict[str, Any]] = {}
        for artifact in artifacts:
            if artifact.get("artifact_type") != "proposal":
                continue
            payload = artifact.get("materialized_payload_json") or artifact.get("payload_json") or {}
            if not isinstance(payload, dict):
                continue
            proposal_id = payload.get("proposal_id")
            if proposal_id:
                proposals[str(proposal_id)] = payload
        return proposals

    def _notes_from_final_result(
        self,
        payload: Optional[dict[str, Any]],
        *,
        proposal_by_id: Optional[dict[str, dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        candidates = payload.get("notes") or payload.get("generated_notes") or []
        notes: list[dict[str, Any]] = []
        for index, item in enumerate(candidates):
            if not isinstance(item, dict):
                continue
            nested_payload = item.get("payload_json") if isinstance(item.get("payload_json"), dict) else item
            note = self.note_from_payload(
                nested_payload,
                fallback_id=str(item.get("artifact_id") or f"note-{index}"),
                proposal_by_id=proposal_by_id,
            )
            if note is not None:
                notes.append(note)
        return notes
