"""Read-only narrow projection for F003's stable three-direction product subset."""

from __future__ import annotations

from typing import Any

from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.reporting.read_model import (
    PublishedReportNotFoundError,
    PublishedReportReader,
)
from app.content_research.stores.base import ContentResearchStore
from app.memory.workflow_store import WorkflowStore

_LITE_FIELDS = {"content_text", "title"}
_DIRECTION_SET_V1 = (
    "product_marketing",
    "competitor_discovery",
    "content_performance",
)
_PUBLICATION_STATES = {
    "complete_verified_report",
    "partial_verified_report",
    "evidence_only_report",
}
_RECOVERABLE_RUN_STATES = {"failed", "paused"}


class LiteReportReader:
    """Project formal report facts without composing, collecting, or writing.

    Despite its historical name this is not a second report vocabulary: all
    publication and direction-state values are shared F003 values.
    """

    def __init__(self, store: ContentResearchStore, db_path: str) -> None:
        self._store = store
        self._db_path = db_path
        self._published = PublishedReportReader(store, db_path)

    async def read(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str | None = None,
        publication_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            report = await self._published.read(
                workflow_run_id=workflow_run_id,
                research_plan_id=research_plan_id,
                publication_id=publication_id,
            )
        except PublishedReportNotFoundError:
            return await self._recoverable_projection(workflow_run_id)
        return self._published_projection(report)

    def _published_projection(self, report: dict[str, Any]) -> dict[str, Any]:
        publication_state = str(report.get("publication_state") or "")
        if publication_state not in _PUBLICATION_STATES:
            raise PublishedReportNotFoundError("published report has unsupported publication state")
        citations = _lite_citations(report.get("citation_groups"))
        citations_by_claim = _citations_by_claim(citations)
        direction_states = _direction_states(report)
        findings = [
            _finding(card, citations_by_claim)
            for card in report.get("claim_cards") or []
            if isinstance(card, dict)
            and str(card.get("admission_state") or "admitted") in {"admitted", "accepted"}
            and _allowed_claim(card, citations_by_claim)
        ]
        weak_signals = [
            item
            for signal in report.get("weak_signals") or []
            if isinstance(signal, dict)
            for item in [_weak_signal(signal, citations_by_claim)]
            if item is not None
        ]
        observations = [item for item in findings if item["card_kind"] == "observation"]
        finding_cards = [item for item in findings if item["card_kind"] == "finding"]
        is_evidence_only = publication_state == "evidence_only_report"
        return {
            "workflow_run_id": report["workflow_run_id"],
            "workflow_execution_state": report.get("workflow_terminal_state"),
            "subject": _subject(self._store, report["workflow_run_id"]),
            "frozen_scope": _frozen_scope(report),
            "collected_at": _collected_at(citations),
            "publication": {"state": publication_state, **dict(report.get("publication") or {})},
            "sections": {
                "main_findings": [] if is_evidence_only else finding_cards,
                "weak_signals": [] if is_evidence_only else weak_signals,
                "limitations_scope": list(report.get("limitations_recovery") or []),
            },
            "status_strip": (
                {"saved_evidence_count": len(citations)}
                if is_evidence_only
                else {
                    "completed_direction_count": sum(
                        item["state"] == "completed" for item in direction_states
                    ),
                    "admitted_finding_count": len(finding_cards),
                    "observation_count": len(observations),
                    "lead_count": len(weak_signals),
                }
            ),
            "citations": citations,
            "run_direction_states": direction_states,
            "recovery_projection": None,
        }

    async def _recoverable_projection(self, workflow_run_id: str) -> dict[str, Any]:
        brief = self._store.get_brief_by_workflow(workflow_run_id)
        async with WorkflowStore(self._db_path) as workflow_store:
            run = await workflow_store.get_run(workflow_run_id)
        state = getattr(getattr(run, "status", None), "value", None) or str(
            getattr(run, "status", "unknown")
        )
        checkpoints = self._checkpoints(workflow_run_id)
        if (
            brief is None
            or state not in _RECOVERABLE_RUN_STATES
            or not _has_persisted_failure(checkpoints)
        ):
            raise PublishedReportNotFoundError("published report not found")
        completed_stages = sorted(
            {item.stage_name for item in checkpoints if item.status == "completed"}
        )
        policy = self._store.get_run_policy_snapshot_for_workflow(workflow_run_id)
        return {
            "workflow_run_id": workflow_run_id,
            "workflow_execution_state": state,
            "subject": _subject(self._store, workflow_run_id),
            "frozen_scope": _policy_scope(policy.effective_policy if policy else {}),
            "collected_at": None,
            "publication": {"state": None},
            "sections": {"main_findings": [], "weak_signals": [], "limitations_scope": []},
            "status_strip": {},
            "citations": [],
            "run_direction_states": _unavailable_direction_states(
                policy.effective_policy if policy else {}
            ),
            "recovery_projection": {
                "reason_code": _recovery_reason(self._store, workflow_run_id),
                "completed_stages": completed_stages,
                "next_action": "resume_run",
                "actionability": "available",
            },
        }

    def _checkpoints(self, workflow_run_id: str) -> list[StageCheckpointRecord]:
        return [
            item
            for item in self._store.list_typed_records(StageCheckpointRecord)
            if item.workflow_run_id == workflow_run_id
        ]


def _frozen_scope(report: dict[str, Any]) -> dict[str, Any]:
    release = report.get("release") if isinstance(report.get("release"), dict) else {}
    publication = report.get("publication") if isinstance(report.get("publication"), dict) else {}
    return {
        "direction_set_version": release.get("direction_set_version"),
        "direction_ids": list(release.get("direction_ids") or []),
        "report_compose_mode": publication.get("compose_mode") or "prose",
    }


def _policy_scope(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "direction_set_version": policy.get("direction_set_version"),
        "direction_ids": list(policy.get("direction_ids") or []),
        "report_compose_mode": policy.get("report_compose_mode") or "prose",
    }


def _direction_states(report: dict[str, Any]) -> list[dict[str, Any]]:
    states = (
        report.get("run_direction_states")
        if isinstance(report.get("run_direction_states"), list)
        else []
    )
    scope = _frozen_scope(report)
    by_direction = {item.get("direction"): item for item in states if isinstance(item, dict)}
    return [
        {
            "direction": direction_id,
            "state": (by_direction.get(direction_id) or {}).get("state", "unavailable"),
            "reason_code": _single_reason(
                (by_direction.get(direction_id) or {}).get("reason_codes")
            ),
            "recovery_action": _single_reason(
                (by_direction.get(direction_id) or {}).get("recovery_actions")
            ),
        }
        for direction_id in scope["direction_ids"]
    ]


def _unavailable_direction_states(policy: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "direction": direction_id,
            "state": "unavailable",
            "reason_code": None,
            "recovery_action": None,
        }
        for direction_id in _policy_scope(policy)["direction_ids"]
    ]


def _single_reason(value: object) -> str | None:
    return next((str(item) for item in value if item), None) if isinstance(value, list) else None


def _lite_citations(groups: object) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        refs = [
            _citation_ref(ref)
            for ref in group.get("evidence_refs", [])
            if isinstance(ref, dict) and ref.get("field_path") in _LITE_FIELDS
        ]
        if refs:
            result.append(
                {
                    "citation_group_id": group.get("citation_group_id"),
                    "display_index": group.get("display_index"),
                    "claim_candidate_id": group.get("claim_candidate_id"),
                    "evidence_refs": refs,
                }
            )
    return result


def _citation_ref(ref: dict[str, Any]) -> dict[str, Any]:
    source_url = ref.get("source_url")
    state = (
        "navigation_unavailable"
        if source_url and ref.get("navigation_state") == "navigation_unavailable"
        else "available"
        if source_url
        else "missing_source_url"
    )
    return {
        key: ref.get(key)
        for key in ("quote", "field_path", "source_url", "source_collected_at", "source_text_hash")
    } | {"navigation_state": state, "navigation_reason": ref.get("navigation_reason")}


def _citations_by_claim(citations: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for citation in citations:
        if isinstance(citation.get("claim_candidate_id"), str):
            result.setdefault(citation["claim_candidate_id"], []).append(citation)
    return result


def _allowed_claim(
    card: dict[str, Any], citations_by_claim: dict[str, list[dict[str, Any]]]
) -> bool:
    return bool(citations_by_claim.get(str(card.get("claim_candidate_id") or "")))


def _finding(
    card: dict[str, Any], citations_by_claim: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    claim_id = str(card.get("claim_candidate_id") or "")
    claim_type = str(card.get("claim_type") or "")
    card_kind = (
        "observation"
        if claim_type == "observation" and card.get("direction_id") == "content_performance"
        else "finding"
    )
    return {
        "statement": card.get("statement"),
        "claim_type": claim_type,
        "card_kind": card_kind,
        "direction": card.get("direction_id"),
        "sample_summary": card.get("scope"),
        "scope": card.get("scope"),
        "citation_group_ids": [
            item.get("citation_group_id") for item in citations_by_claim[claim_id]
        ],
    }


def _weak_signal(
    signal: dict[str, Any], citations_by_claim: dict[str, list[dict[str, Any]]]
) -> dict[str, Any] | None:
    citations = citations_by_claim.get(str(signal.get("claim_candidate_id") or ""))
    if not citations:
        return None
    return {
        "statement": signal.get("reason") or signal.get("statement"),
        "direction": signal.get("direction_id"),
        "sample_summary": signal.get("scope"),
        "qualification_reason": signal.get("reason_code") or signal.get("reason"),
        "citation_group_ids": [item.get("citation_group_id") for item in citations],
    }


def _subject(store: ContentResearchStore, workflow_run_id: str) -> str | None:
    brief = store.get_brief_by_workflow(workflow_run_id)
    return (
        str(brief.payload.get("confirmed_subject") or brief.payload.get("seed_text"))
        if brief
        else None
    )


def _collected_at(citations: list[dict[str, Any]]) -> str | None:
    return next(
        (
            ref.get("source_collected_at")
            for group in citations
            for ref in group["evidence_refs"]
            if ref.get("source_collected_at")
        ),
        None,
    )


def _has_persisted_failure(checkpoints: list[StageCheckpointRecord]) -> bool:
    return any(
        item.status in {"failed", "failed_recoverable", "outcome_unknown"} for item in checkpoints
    )


def _recovery_reason(store: ContentResearchStore, workflow_run_id: str) -> str:
    failures = [
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.workflow_run_id == workflow_run_id
        and item.status in {"failed", "failed_recoverable", "outcome_unknown"}
    ]
    if not failures:
        return "temporary_error"
    return str(failures[-1].payload.get("reason_code") or "temporary_error")
