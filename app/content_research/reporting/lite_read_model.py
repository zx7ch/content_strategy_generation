"""Read-only narrow projection for F003's stable three-direction product subset."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.content_research.contracts import DIRECTION_CATALOG_V1
from app.content_research.persistence_models import (
    ReportPublicationRecord,
    StageCheckpointRecord,
)
from app.content_research.reporting.read_model import (
    PublishedReportNotFoundError,
    PublishedReportReader,
)
from app.content_research.stores.base import ContentResearchStore
from app.memory.workflow_store import WorkflowStore

_LITE_FIELDS = {"content_text", "title"}
_PROJECTABLE_CLAIM_TYPES = {
    "product_marketing": {
        "product_value_expression",
        "use_context",
        "target_audience_framing",
        "message_angle",
    },
    "competitor_discovery": {"named_competitor", "visible_content_expression"},
    "content_performance": {
        "observed_high_engagement_sample",
        "visible_content_format",
    },
}
_PUBLICATION_STATES = {
    "complete_verified_report",
    "partial_verified_report",
    "evidence_only_report",
}
_RECOVERABLE_RUN_STATES = {"failed", "paused"}


class ExistingPublicationUnreadableError(RuntimeError):
    """Raised when a committed publication exists but cannot be projected safely."""


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
        citation_group_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        try:
            report = await self._published.read(
                workflow_run_id=workflow_run_id,
                research_plan_id=research_plan_id,
                publication_id=publication_id,
            )
            if citation_group_ids is None and report["citation_total"] > len(
                report["citation_groups"]
            ):
                report = await self._published.read(
                    workflow_run_id=workflow_run_id,
                    research_plan_id=research_plan_id,
                    publication_id=publication_id,
                    citation_limit=report["citation_total"],
                )
            if citation_group_ids:
                report = {
                    **report,
                    "citation_groups": await self._published.citation_groups(
                        workflow_run_id=workflow_run_id,
                        research_plan_id=research_plan_id,
                        publication_id=publication_id,
                        citation_group_ids=set(citation_group_ids),
                    ),
                }
        except PublishedReportNotFoundError as exc:
            if citation_group_ids is not None:
                raise
            if self._has_publication(workflow_run_id=workflow_run_id):
                if research_plan_id is not None or publication_id is not None:
                    # A caller asked for a specific frozen publication and it
                    # did not resolve.  Keep that ordinary not-found result;
                    # it must never masquerade as a recoverable run.
                    raise
                raise ExistingPublicationUnreadableError(
                    "existing publication is unreadable"
                ) from exc
            return await self._recoverable_projection(workflow_run_id)
        return self._published_projection(report, citation_group_ids=citation_group_ids)

    def _published_projection(
        self,
        report: dict[str, Any],
        *,
        citation_group_ids: list[str] | None,
    ) -> dict[str, Any]:
        publication_state = str(report.get("publication_state") or "")
        if publication_state not in _PUBLICATION_STATES:
            raise PublishedReportNotFoundError("published report has unsupported publication state")
        if _frozen_scope(report)["report_compose_mode"] != "template_only":
            raise PublishedReportNotFoundError(
                "published report has unsupported compose mode"
            )
        requested_directions = set(_frozen_scope(report)["direction_ids"])
        claim_cards = _records_in_directions(
            report.get("claim_cards"), requested_directions
        )
        weak_signal_records = _records_in_directions(
            report.get("weak_signals"), requested_directions
        )
        excluded_claim_ids = {
            str(item["claim_candidate_id"])
            for item in [
                *(report.get("claim_cards") or []),
                *(report.get("weak_signals") or []),
            ]
            if isinstance(item, dict)
            and item.get("direction_id") not in requested_directions
            and item.get("claim_candidate_id")
        }
        citations = (
            _select_citations(
                [
                    citation
                    for citation in _lite_citations(report.get("citation_groups"))
                    if citation.get("claim_candidate_id") not in excluded_claim_ids
                ],
                citation_group_ids,
            )
            if requested_directions
            else []
        )
        citations_by_claim = _citations_by_claim(citations)
        direction_states = _direction_states(report)
        cards = [
            _validated_card(card, citations_by_claim)
            for card in claim_cards
        ]
        findings = [
            _finding(card)
            for card in cards
            if card is not None
        ]
        weak_signals = [
            item
            for signal in weak_signal_records
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
            "publication": {
                "state": publication_state,
                **dict(report.get("publication") or {}),
                "publication_reason": _publication_reason(report),
            },
            "sections": {
                "main_findings": [] if is_evidence_only else findings,
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

    def _has_publication(
        self,
        *,
        workflow_run_id: str,
    ) -> bool:
        return any(
            item.workflow_run_id == workflow_run_id
            for item in self._store.list_typed_records(ReportPublicationRecord)
        )


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
        "direction_ids": list(
            policy.get("requested_direction_ids") or policy.get("direction_ids") or []
        ),
        "report_compose_mode": policy.get("report_compose_mode") or "prose",
    }


def _records_in_directions(
    records: object, direction_ids: set[str]
) -> list[dict[str, Any]]:
    return [
        item
        for item in (records if isinstance(records, list) else [])
        if isinstance(item, dict) and item.get("direction_id") in direction_ids
    ]


def _direction_states(report: dict[str, Any]) -> list[dict[str, Any]]:
    states = (
        report.get("run_direction_states")
        if isinstance(report.get("run_direction_states"), list)
        else []
    )
    scope = _frozen_scope(report)
    by_direction = {item.get("direction"): item for item in states if isinstance(item, dict)}
    requested_direction_ids = set(scope["direction_ids"])
    return [
        _direction_state_view(
            direction_id,
            by_direction.get(direction_id),
            requested=direction_id in requested_direction_ids,
        )
        for direction_id in DIRECTION_CATALOG_V1
    ]


def _direction_state_view(
    direction_id: str, result: dict[str, Any] | None, *, requested: bool
) -> dict[str, Any]:
    if not requested:
        return {
            "direction": direction_id,
            "state": "not_requested",
            "reason_code": None,
            "recovery_action": None,
        }
    state = str((result or {}).get("state") or "")
    if state in {"", "not_started", "not_requested"}:
        return {
            "direction": direction_id,
            "state": "unavailable",
            "reason_code": "collection_result_unavailable",
            "recovery_action": None,
        }
    return {
        "direction": direction_id,
        "state": state,
        "reason_code": _single_reason((result or {}).get("reason_codes")),
        "recovery_action": _single_reason((result or {}).get("recovery_actions")),
    }


def _unavailable_direction_states(policy: dict[str, Any]) -> list[dict[str, Any]]:
    requested_direction_ids = set(_policy_scope(policy)["direction_ids"])
    return [
        _direction_state_view(
            direction_id,
            None,
            requested=direction_id in requested_direction_ids,
        )
        for direction_id in DIRECTION_CATALOG_V1
    ]


def _single_reason(value: object) -> str | None:
    return next((str(item) for item in value if item), None) if isinstance(value, list) else None


def _lite_citations(groups: object) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for group in groups if isinstance(groups, list) else []:
        citation = _lite_citation_group(group)
        if citation is not None:
            result.append(citation)
    return result


def _lite_citation_group(group: object) -> dict[str, Any] | None:
    if not isinstance(group, dict):
        return None
    raw_refs = group.get("evidence_refs")
    if not isinstance(raw_refs, list) or not raw_refs or any(
        not isinstance(ref, dict) for ref in raw_refs
    ):
        return None
    note_ids = {
        ref.get("canonical_note_id")
        for ref in raw_refs
        if isinstance(ref.get("canonical_note_id"), str)
        and ref["canonical_note_id"]
    }
    if len(note_ids) != 1 or len(note_ids) != len(
        {ref.get("canonical_note_id") for ref in raw_refs}
    ):
        return None
    source_urls = {_frozen_source_url(ref) for ref in raw_refs}
    if len(source_urls) != 1:
        return None
    source_url = source_urls.pop()
    navigation_state = _group_navigation_state(raw_refs, source_url)
    refs = [
        _citation_ref(ref, navigation_state=navigation_state)
        for ref in raw_refs
        if ref.get("field_path") in _LITE_FIELDS
    ]
    if not refs:
        return None
    return {
        "citation_group_id": group.get("citation_group_id"),
        "display_index": group.get("display_index"),
        "claim_candidate_id": group.get("claim_candidate_id"),
        "admission_decision_id": group.get("admission_decision_id"),
        "navigation_state": navigation_state,
        "source_url": source_url if navigation_state == "available" else None,
        "evidence_refs": refs,
    }


def _frozen_source_url(ref: dict[str, Any]) -> str | None:
    source_url = ref.get("source_url")
    return source_url if isinstance(source_url, str) and source_url else None


def _group_navigation_state(
    refs: list[dict[str, Any]], source_url: str | None
) -> str:
    if source_url is None:
        return "missing_source_url"
    if not _is_safe_xiaohongshu_note_url(source_url) or any(
        ref.get("navigation_state") == "navigation_unavailable" for ref in refs
    ):
        return "navigation_unavailable"
    return "available"


def _is_safe_xiaohongshu_note_url(source_url: str) -> bool:
    parsed = urlparse(source_url)
    hostname = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and (hostname == "xiaohongshu.com" or hostname.endswith(".xiaohongshu.com"))
        and bool(parsed.path)
    )


def _select_citations(
    citations: list[dict[str, Any]],
    citation_group_ids: list[str] | None,
) -> list[dict[str, Any]]:
    if citation_group_ids is None:
        return citations
    requested = set(citation_group_ids)
    return [citation for citation in citations if citation.get("citation_group_id") in requested]


def _citation_ref(
    ref: dict[str, Any], *, navigation_state: str | None = None
) -> dict[str, Any]:
    source_url = ref.get("source_url")
    state = navigation_state or (
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


def _validated_card(
    card: dict[str, Any], citations_by_claim: dict[str, list[dict[str, Any]]]
) -> dict[str, Any] | None:
    """Validate a governed card and retain only its matching frozen citations.

    This is deliberately a projection guard, not a second admission evaluator:
    frozen relevance and decision-making remain owned by the formal pipeline.
    """
    claim_id = str(card.get("claim_candidate_id") or "")
    direction_id = str(card.get("direction_id") or "")
    claim_type = str(card.get("claim_type") or "")
    admission_id = str(card.get("admission_decision_id") or "")
    scope = card.get("scope")
    if (
        str(card.get("admission_state") or "") != "admitted"
        or not claim_id
        or not admission_id
        or claim_type not in _PROJECTABLE_CLAIM_TYPES.get(direction_id, set())
        or not isinstance(scope, dict)
        or scope.get("sample") != "selected_packets"
    ):
        return None
    citations = citations_by_claim.get(claim_id) or []
    eligible_fields = (
        {"title", "content_text"}
        if direction_id == "product_marketing" and claim_type == "message_angle"
        else {"content_text"}
        if direction_id == "product_marketing"
        else _LITE_FIELDS
    )
    matching_citations = [
        citation
        for citation in citations
        if citation.get("citation_group_id")
        and citation.get("admission_decision_id") == admission_id
        and any(
            ref.get("field_path") in eligible_fields
            and isinstance(ref.get("quote"), str)
            and bool(ref["quote"])
            and isinstance(ref.get("source_text_hash"), str)
            and bool(ref["source_text_hash"])
            for ref in citation.get("evidence_refs") or []
            if isinstance(ref, dict)
        )
    ]
    if not matching_citations:
        return None
    return {
        **card,
        "_matching_citation_group_ids": [
            citation["citation_group_id"] for citation in matching_citations
        ],
    }


def _finding(card: dict[str, Any]) -> dict[str, Any]:
    claim_type = str(card.get("claim_type") or "")
    card_kind = (
        "observation"
        if card.get("direction_id") == "content_performance"
        else "finding"
    )
    return {
        "statement": card.get("statement"),
        "claim_type": claim_type,
        "card_kind": card_kind,
        "direction": card.get("direction_id"),
        "sample_summary": card.get("scope"),
        "scope": card.get("scope"),
        "citation_group_ids": list(card["_matching_citation_group_ids"]),
    }


def _weak_signal(
    signal: dict[str, Any], citations_by_claim: dict[str, list[dict[str, Any]]]
) -> dict[str, Any] | None:
    claim_id = str(signal.get("claim_candidate_id") or "")
    admission_id = str(signal.get("admission_decision_id") or "")
    if not claim_id or not admission_id:
        return None
    citations = [
        citation
        for citation in citations_by_claim.get(claim_id) or []
        if citation.get("citation_group_id")
        and citation.get("admission_decision_id") == admission_id
    ]
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


def _publication_reason(report: dict[str, Any]) -> str | None:
    publication = report.get("publication")
    if not isinstance(publication, dict):
        return None
    reason_codes = publication.get("reason_codes")
    if isinstance(reason_codes, list):
        reason = next((str(item) for item in reason_codes if item), None)
        if reason:
            return reason
    recovery_state = publication.get("audit_recovery_state")
    return str(recovery_state) if recovery_state else None


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
