"""Materialize an already-published Content Research report for Creator."""

from __future__ import annotations

from typing import Any

from app.content_research.models import ResearchResultSnapshotRecord
from app.content_research.persistence_models import (
    ReportDraftRecord,
    ReportFaithfulnessDecisionRecord,
    ReportPublicationRecord,
)
from app.content_research.stores.base import ContentResearchStore
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowArtifact, WorkflowArtifactPayloadMode, WorkflowArtifactType
from app.services.workflow_run_manager import WorkflowRunManager


class ReportPublicationMaterializer:
    """Publish one immutable report version as Creator's final snapshot artifact.

    Composition and auditing happen before this boundary. This class only turns a
    persisted publication lineage into the single Creator-visible final result.
    """

    def __init__(self, store: ContentResearchStore, db_path: str) -> None:
        self._store = store
        self._db_path = db_path

    async def materialize(self, publication_id: str) -> WorkflowArtifact:
        publication = self._require_publication(publication_id)
        draft = self._require_parent(ReportDraftRecord, publication.report_draft_id, "report draft")
        decision = self._require_parent(
            ReportFaithfulnessDecisionRecord,
            publication.faithfulness_decision_id,
            "report faithfulness decision",
        )
        snapshot = self._require_snapshot(publication)
        self._validate_lineage(publication, draft, decision, snapshot)

        async with WorkflowStore(self._db_path) as workflow_store:
            run = await workflow_store.get_run(publication.workflow_run_id)
            if run is None:
                raise ValueError("missing Creator workflow run for report publication")
            if run.status.value != "finalizing_report":
                raise ValueError("cannot materialize a report outside finalizing_report")
            existing = await workflow_store.list_artifacts(publication.workflow_run_id)

        artifact = next(
            (
                item
                for item in existing
                if item.artifact_type == WorkflowArtifactType.FINAL_RESULT
                and (item.payload_json or {}).get("report_publication_id") == publication.id
            ),
            None,
        )
        if artifact is None:
            async with WorkflowRunManager(self._db_path) as manager:
                artifact = await manager.attach_artifact(
                    run_id=publication.workflow_run_id,
                    artifact_type=WorkflowArtifactType.FINAL_RESULT,
                    payload=self._artifact_payload(publication, draft, decision, snapshot),
                    payload_mode=WorkflowArtifactPayloadMode.SNAPSHOT,
                    summary_text="内容调研报告已发布",
                )

        async with ThreadStore(self._db_path) as thread_store:
            await thread_store.append_artifact_result_message(
                thread_id=run.thread_id,
                run_id=publication.workflow_run_id,
                artifact_refs=[
                    {
                        "artifact_id": artifact.artifact_id,
                        "artifact_type": artifact.artifact_type.value,
                        "artifact_version": artifact.artifact_version,
                        "parent_artifact_id": artifact.parent_artifact_id,
                    }
                ],
                text="内容调研报告已生成。",
                idempotent=True,
            )
        return artifact

    def _require_publication(self, publication_id: str) -> ReportPublicationRecord:
        publication = self._store.get_typed_record(ReportPublicationRecord, publication_id)
        if publication is None:
            raise ValueError("missing report publication")
        return publication

    def _require_parent(self, record_type: type[Any], record_id: str, name: str) -> Any:
        record = self._store.get_typed_record(record_type, record_id)
        if record is None:
            raise ValueError(f"missing {name}")
        return record

    def _require_snapshot(self, publication: ReportPublicationRecord) -> ResearchResultSnapshotRecord:
        snapshot = next(
            (
                item
                for item in self._store.list_result_snapshots_for_workflow(publication.workflow_run_id)
                if item.id == publication.governed_snapshot_id
            ),
            None,
        )
        if snapshot is None:
            raise ValueError("missing governed snapshot")
        return snapshot

    @staticmethod
    def _validate_lineage(
        publication: ReportPublicationRecord,
        draft: ReportDraftRecord,
        decision: ReportFaithfulnessDecisionRecord,
        snapshot: ResearchResultSnapshotRecord,
    ) -> None:
        identity = (
            "workflow_run_id",
            "research_plan_id",
            "governed_snapshot_id",
            "governed_snapshot_version",
            "input_fingerprint",
            "policy_version",
            "algorithm_version",
        )
        if any(getattr(publication, name) != getattr(draft, name) for name in identity):
            raise ValueError("report publication and draft lineage mismatch")
        if any(getattr(publication, name) != getattr(decision, name) for name in identity):
            raise ValueError("report publication and faithfulness-decision lineage mismatch")
        if decision.report_draft_id != draft.id:
            raise ValueError("faithfulness decision does not belong to report draft")
        if snapshot.snapshot_version != publication.governed_snapshot_version:
            raise ValueError("report publication and governed snapshot version mismatch")

    @staticmethod
    def _artifact_payload(
        publication: ReportPublicationRecord,
        draft: ReportDraftRecord,
        decision: ReportFaithfulnessDecisionRecord,
        snapshot: ResearchResultSnapshotRecord,
    ) -> dict[str, Any]:
        sections = _published_sections(draft.payload["sections"], publication)
        citation_groups = _artifact_citation_groups(
            sections=sections,
            verified_section_ids=publication.payload["verified_section_ids"],
            structured_card_section_ids=publication.payload["structured_card_section_ids"],
            governed_snapshot=snapshot.metadata.get("governed_snapshot"),
            preserve_all=publication.publication_state == "evidence_only_report",
        )
        return {
            "schema_version": "content_research_published_report_artifact_v1",
            "report_publication_id": publication.id,
            "report_draft_id": draft.id,
            "faithfulness_decision_id": decision.id,
            "governed_snapshot_id": snapshot.id,
            "governed_snapshot_version": snapshot.snapshot_version,
            "workflow_run_id": publication.workflow_run_id,
            "research_plan_id": publication.research_plan_id,
            "publication_state": publication.publication_state,
            "compose_mode": publication.payload.get("compose_mode") or "prose",
            "input_fingerprint": publication.input_fingerprint,
            "policy_version": publication.policy_version,
            "algorithm_version": publication.algorithm_version,
            "sections": sections,
            "citation_groups": citation_groups,
            "verified_section_ids": publication.payload["verified_section_ids"],
            "omitted_section_ids": publication.payload.get("omitted_section_ids", []),
            "audit_recovery_state": publication.payload["audit_recovery_state"],
        }


def _artifact_citation_groups(
    *,
    sections: object,
    verified_section_ids: object,
    structured_card_section_ids: object,
    governed_snapshot: object,
    preserve_all: bool = False,
) -> list[dict[str, Any]]:
    """Project citation groups without changing their snapshot-owned identity."""
    if not isinstance(sections, list) or any(not isinstance(section, dict) for section in sections):
        raise ValueError("report draft sections are malformed")
    if not isinstance(governed_snapshot, dict):
        raise ValueError("missing governed snapshot citation source")
    groups = governed_snapshot.get("citation_groups", [])
    if not isinstance(groups, list) or any(not isinstance(group, dict) for group in groups):
        raise ValueError("governed snapshot citation groups are malformed")
    frozen_groups: dict[str, dict[str, Any]] = {}
    for group in groups:
        group_id = group.get("citation_group_id")
        if not isinstance(group_id, str) or not group_id or group_id in frozen_groups:
            raise ValueError("governed snapshot citation group identity is invalid")
        _validate_frozen_citation_group(group)
        frozen_groups[group_id] = group

    retained_ids = set(_string_list(verified_section_ids, "verified section ids"))
    retained_ids.update(_string_list(structured_card_section_ids, "structured-card section ids"))
    used_ids: set[str] = set(frozen_groups) if preserve_all else set()
    for section in sections:
        section_id = section.get("section_id")
        if section_id not in retained_ids:
            continue
        citation_ids = _string_list(section.get("citation_group_ids", []), "section citation group ids")
        citation_id_set = set(citation_ids)
        anchors = section.get("citation_anchors", [])
        if not isinstance(anchors, list) or any(not isinstance(anchor, dict) for anchor in anchors):
            raise ValueError("report citation anchors are malformed")
        prose = section.get("prose")
        if prose is not None and not isinstance(prose, str):
            raise ValueError("report section prose is malformed")
        if prose and not anchors:
            raise ValueError("report prose has no persisted citation anchors")
        for citation_id in citation_ids:
            if citation_id not in frozen_groups:
                raise ValueError("report section citation is absent from governed snapshot")
        for anchor in anchors:
            anchor_group_id = anchor.get("citation_group_id")
            if anchor_group_id not in citation_id_set or anchor_group_id not in frozen_groups:
                raise ValueError("report citation anchor is absent from governed snapshot")
            start, end = anchor.get("text_start"), anchor.get("text_end")
            if not isinstance(start, int) or not isinstance(end, int) or not prose or start < 0 or end <= start or end > len(prose):
                raise ValueError("report citation anchor span is malformed")
        used_ids.update(citation_ids)
    return [dict(group) for group in groups if group["citation_group_id"] in used_ids]


def _published_sections(sections: object, publication: ReportPublicationRecord) -> list[dict[str, Any]]:
    """Remove prose that the publication state has explicitly withdrawn."""
    if not isinstance(sections, list) or any(not isinstance(section, dict) for section in sections):
        raise ValueError("report draft sections are malformed")
    omitted = set(publication.payload.get("omitted_section_ids") or ())
    redact_all_prose = publication.publication_state == "evidence_only_report"
    published: list[dict[str, Any]] = []
    for section in sections:
        copy = dict(section)
        if redact_all_prose or copy.get("section_id") in omitted:
            copy["prose"] = None
            copy["citation_anchors"] = []
        published.append(copy)
    return published


def _validate_frozen_citation_group(group: dict[str, Any]) -> None:
    if not isinstance(group.get("display_index"), int) or group["display_index"] < 1:
        raise ValueError("governed citation display index is invalid")
    refs = group.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise ValueError("governed citation has no evidence refs")
    for ref in refs:
        if not isinstance(ref, dict):
            raise ValueError("governed citation evidence ref is malformed")
        quote = ref.get("quote")
        start, end = ref.get("text_start"), ref.get("text_end")
        if (
            not isinstance(quote, str)
            or not quote
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end - start != len(quote)
            or not all(
                isinstance(ref.get(key), str) and ref[key]
                for key in ("field_path", "source_text_hash")
            )
            or (
                ref.get("source_url") is not None
                and not isinstance(ref.get("source_url"), str)
            )
        ):
            raise ValueError("governed citation evidence ref is incomplete")


def _string_list(value: object, field_name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{field_name} are malformed")
    return value
