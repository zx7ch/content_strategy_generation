"""Services for ingesting and expanding Content Research evidence."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from app.content_research.evidence.models import (
    EvidenceLineageRecord,
    EvidenceRecord,
)
from app.content_research.models import utcnow

if TYPE_CHECKING:
    from app.content_research.stores.base import ContentResearchStore


class EvidenceService:
    def __init__(self, store: ContentResearchStore) -> None:
        self._store = store

    def ingest_source_payload(
        self,
        *,
        workflow_run_id: str,
        source_payload: dict[str, Any],
        research_brief_id: str | None = None,
        research_plan_id: str | None = None,
        research_direction_id: str | None = None,
        subagent_task_id: str | None = None,
        trace_id: str | None = None,
    ) -> EvidenceRecord:
        schema_version = str(source_payload.get("schema_version") or "")
        if not schema_version:
            raise ValueError("source payload must include schema_version")

        now = utcnow()
        evidence_id = _prefixed_id("ev", workflow_run_id, source_payload)
        source_kind = str(source_payload.get("source_kind") or "search_result")
        source_id = str(source_payload.get("canonical_id") or source_payload.get("source_url") or evidence_id)
        record = EvidenceRecord(
            id=evidence_id,
            workflow_run_id=workflow_run_id,
            research_brief_id=research_brief_id,
            research_plan_id=research_plan_id,
            research_direction_id=research_direction_id,
            subagent_task_id=subagent_task_id,
            trace_id=trace_id,
            schema_version="content_research_evidence_record_v1",
            status="candidate",
            source_type=source_kind,
            source_platform=str(source_payload.get("provider") or ""),
            source_url=str(source_payload.get("source_url") or ""),
            source_id=source_id,
            evidence_type=_evidence_type_for_source_kind(source_kind),
            normalized_payload=source_payload,
            source_author_name=str(source_payload.get("author") or ""),
            collected_at=now,
            title=str(source_payload.get("title") or ""),
            text_excerpt=str(source_payload.get("content_text") or "")[:500],
            raw_content_ref=str(source_payload.get("raw_payload_hash") or ""),
            metrics=dict(source_payload.get("metrics") or {}),
            content_hash=str(source_payload.get("raw_payload_hash") or ""),
            dedupe_key=f"{source_kind}:{source_id}",
            retrieval_query=str(source_payload.get("query_used") or ""),
            metadata={"failure_reason": source_payload.get("failure_reason")},
            created_at=now,
            updated_at=now,
        )
        saved = self._store.save_evidence_record(record)
        lineage = EvidenceLineageRecord(
            # A source record may be reused by multiple directional subagents.
            # Lineage must distinguish those independent derivations while a
            # retry of the same task remains idempotent.
            id=_prefixed_id(
                "el",
                evidence_id,
                {
                    "transformation_type": "captured",
                    "research_direction_id": research_direction_id,
                    "subagent_task_id": subagent_task_id,
                },
            ),
            workflow_run_id=workflow_run_id,
            evidence_record_id=evidence_id,
            research_brief_id=research_brief_id,
            research_plan_id=research_plan_id,
            research_direction_id=research_direction_id,
            subagent_task_id=subagent_task_id,
            trace_id=trace_id,
            schema_version="content_research_evidence_lineage_v1",
            transformation_type="captured",
            transformation_version="v1",
            lineage_payload={
                "schema_version": "content_research_evidence_lineage_payload_v1",
                "source_kind": source_kind,
                "source_url": record.source_url,
                "source_id": record.source_id,
                "raw_payload_hash": source_payload.get("raw_payload_hash"),
            },
            created_at=now,
        )
        try:
            self._store.append_evidence_lineage(lineage)
        except ValueError as exc:
            existing = self._store.list_evidence_lineage(evidence_id)
            if not any(item.id == lineage.id for item in existing):
                raise exc
        return saved

    def derive_fact_evidence(self, *, parent: EvidenceRecord, fact: dict[str, Any]) -> EvidenceRecord:
        """Persist an extracted fact without rewriting its captured source."""
        claim = str(fact.get("claim") or parent.claim or parent.title or parent.text_excerpt).strip()
        payload = {
            "schema_version": "content_research_derived_fact_payload_v1",
            "fact_id": str(fact.get("fact_id") or ""),
            "claim": claim,
            "source_evidence_id": parent.id,
            "metrics": dict(fact.get("metrics") or {}),
        }
        return self._derive_evidence(
            parent=parent,
            payload=payload,
            claim=claim,
            source_type="derived_fact",
            transformation_type="extracted_fact",
        )

    def derive_finding_evidence(
        self,
        *,
        task_id: str,
        workflow_run_id: str,
        research_plan_id: str | None,
        research_direction_id: str | None,
        finding_id: str,
        summary: str,
        supporting_facts: list[EvidenceRecord],
    ) -> EvidenceRecord:
        """Persist a directional claim and link it to precisely cited facts."""
        payload = {
            "schema_version": "content_research_derived_finding_payload_v1",
            "finding_id": finding_id,
            "claim": summary,
            "supporting_fact_evidence_ids": [fact.id for fact in supporting_facts],
        }
        evidence_id = _prefixed_id("evd", workflow_run_id, {"task_id": task_id, **payload})
        now = utcnow()
        record = EvidenceRecord(
            id=evidence_id,
            workflow_run_id=workflow_run_id,
            research_plan_id=research_plan_id,
            research_direction_id=research_direction_id,
            subagent_task_id=task_id,
            schema_version="content_research_evidence_record_v1",
            status="accepted" if supporting_facts else "candidate",
            source_type="derived_finding",
            source_platform="content_research",
            source_url="",
            source_id=finding_id,
            evidence_type="agent_observation",
            normalized_payload=payload,
            title="Directional finding",
            text_excerpt=summary[:500],
            claim=summary,
            content_hash=_hash_payload(payload),
            dedupe_key=f"derived_finding:{task_id}:{finding_id}",
            metadata={"derived_kind": "finding", "supporting_fact_count": len(supporting_facts)},
            created_at=now,
            updated_at=now,
        )
        saved = self._store.save_evidence_record(record)
        for fact in supporting_facts:
            self._append_lineage_idempotently(
                EvidenceLineageRecord(
                    id=_prefixed_id(
                        "el", evidence_id,
                        {"parent_evidence_record_id": fact.id, "transformation_type": "summarized"},
                    ),
                    workflow_run_id=workflow_run_id,
                    evidence_record_id=evidence_id,
                    research_plan_id=research_plan_id,
                    research_direction_id=research_direction_id,
                    subagent_task_id=task_id,
                    parent_evidence_record_id=fact.id,
                    schema_version="content_research_evidence_lineage_v1",
                    transformation_type="summarized",
                    transformation_version="v1",
                    lineage_payload={
                        "schema_version": "content_research_evidence_lineage_payload_v1",
                        "finding_id": finding_id,
                        "supporting_fact_evidence_id": fact.id,
                    },
                    created_at=now,
                )
            )
        return saved

    def source_independence_key(self, record: EvidenceRecord) -> str:
        """Return a conservative key: author first, canonical source as fallback."""
        platform = record.source_platform.strip().lower() or "unknown"
        author = (record.source_author_id or record.source_author_name).strip().lower()
        if author:
            return f"{platform}:author:{author}"
        source = (record.source_id or record.source_url or record.id).strip().lower()
        return f"{platform}:source:{source}"

    def _derive_evidence(
        self,
        *,
        parent: EvidenceRecord,
        payload: dict[str, Any],
        claim: str,
        source_type: str,
        transformation_type: str,
    ) -> EvidenceRecord:
        evidence_id = _prefixed_id("evd", parent.id, payload)
        now = utcnow()
        record = EvidenceRecord(
            id=evidence_id,
            workflow_run_id=parent.workflow_run_id,
            research_brief_id=parent.research_brief_id,
            research_plan_id=parent.research_plan_id,
            research_direction_id=parent.research_direction_id,
            subagent_task_id=parent.subagent_task_id,
            trace_id=parent.trace_id,
            schema_version="content_research_evidence_record_v1",
            status="accepted",
            source_type=source_type,
            source_platform=parent.source_platform,
            source_url=parent.source_url,
            source_id=parent.source_id,
            evidence_type="agent_observation",
            normalized_payload=payload,
            source_author_id=parent.source_author_id,
            source_author_name=parent.source_author_name,
            source_published_at=parent.source_published_at,
            collected_at=now,
            title=parent.title,
            text_excerpt=claim[:500],
            raw_content_ref=parent.raw_content_ref,
            claim=claim,
            metrics=dict(parent.metrics),
            language=parent.language,
            content_hash=_hash_payload(payload),
            dedupe_key=f"{source_type}:{parent.id}:{payload.get('fact_id', '')}",
            retrieval_query=parent.retrieval_query,
            metadata={
                "derived_kind": "fact",
                "source_independence_key": self.source_independence_key(parent),
                "contradiction_group_id": parent.normalized_payload.get("contradiction_group_id"),
                "claim_polarity": parent.normalized_payload.get("claim_polarity"),
            },
            created_at=now,
            updated_at=now,
        )
        saved = self._store.save_evidence_record(record)
        self._append_lineage_idempotently(
            EvidenceLineageRecord(
                id=_prefixed_id(
                    "el", evidence_id,
                    {"parent_evidence_record_id": parent.id, "transformation_type": transformation_type},
                ),
                workflow_run_id=parent.workflow_run_id,
                evidence_record_id=evidence_id,
                research_brief_id=parent.research_brief_id,
                research_plan_id=parent.research_plan_id,
                research_direction_id=parent.research_direction_id,
                subagent_task_id=parent.subagent_task_id,
                trace_id=parent.trace_id,
                parent_evidence_record_id=parent.id,
                schema_version="content_research_evidence_lineage_v1",
                transformation_type=transformation_type,
                transformation_version="v1",
                lineage_payload={
                    "schema_version": "content_research_evidence_lineage_payload_v1",
                    "source_evidence_id": parent.id,
                    "derived_evidence_id": evidence_id,
                    "derived_kind": "fact",
                },
                created_at=now,
            )
        )
        return saved

    def _append_lineage_idempotently(self, lineage: EvidenceLineageRecord) -> None:
        try:
            self._store.append_evidence_lineage(lineage)
        except ValueError as exc:
            if not any(item.id == lineage.id for item in self._store.list_evidence_lineage(lineage.evidence_record_id)):
                raise exc


def _prefixed_id(prefix: str, stable_key: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps([stable_key, payload], ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _evidence_type_for_source_kind(source_kind: str) -> str:
    if source_kind == "comment":
        return "comment"
    if source_kind in {"search_result", "search_result_minimal", "note_detail", "topic_or_keyword_page"}:
        return "search_result" if source_kind == "search_result_minimal" else source_kind
    return "manual_note"
