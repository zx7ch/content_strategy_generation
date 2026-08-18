"""Read-only narrow projection for F003's stable three-direction product subset."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.content_research.contracts import DIRECTION_CATALOG_V1
from app.content_research.persistence_models import (
    ReportPublicationRecord,
    StageCheckpointRecord,
)
from app.content_research.reporting.contracts import _stable_id
from app.content_research.reporting.read_model import (
    PublishedReportNotFoundError,
    PublishedReportReader,
)
from app.content_research.scope_contract import ScopeAuditEvent
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
    "directional_report",
    "evidence_only_report",
}
_RECOVERABLE_RUN_STATES = {"failed", "paused", "waiting_user", "running"}
_RECOVERABLE_FAILURE_CODES = {
    "auth_required",
    "auth_expired",
    "timeout",
    "transient_error",
    "rate_limited",
    "unavailable",
}
_FAILURE_CHECKPOINT_STATUSES = {
    "failed",
    "failed_recoverable",
    "outcome_unknown",
    "auth_required",
    "rate_limited",
    "timed_out",
}


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
        scope_projection = self._scope_projection(workflow_run_id)
        try:
            report = await self._published.read(
                workflow_run_id=workflow_run_id,
                research_plan_id=research_plan_id,
                publication_id=publication_id,
            )
            if report["citation_total"] > len(report["citation_groups"]):
                report = await self._published.read(
                    workflow_run_id=workflow_run_id,
                    research_plan_id=research_plan_id,
                    publication_id=publication_id,
                    citation_limit=report["citation_total"],
                )
            if citation_group_ids:
                await self._published.citation_groups(
                    workflow_run_id=workflow_run_id,
                    research_plan_id=research_plan_id,
                    publication_id=publication_id,
                    citation_group_ids=set(citation_group_ids),
                )
        except PublishedReportNotFoundError:
            if citation_group_ids is not None:
                raise
            if self._has_publication(workflow_run_id=workflow_run_id):
                # A malformed persisted publication is not a recoverable run;
                # preserve the current contract's ordinary not-found result.
                raise
            return await self._recoverable_projection(workflow_run_id)
        report.update(self._governed_marketing_fields(report))
        projection = self._published_projection(
            report, citation_group_ids=citation_group_ids
        )
        if scope_projection is not None:
            projection = self._apply_scope_projection(projection, scope_projection)
            self._record_scope_projection(workflow_run_id, scope_projection)
        return projection

    def _scope_projection(self, workflow_run_id: str) -> dict[str, Any] | None:
        contracts = self._store.list_scope_contracts(workflow_run_id)
        if not contracts:
            return None
        contract = contracts[-1]
        snapshot = self._store.get_coverage_snapshot(
            workflow_run_id, version=contract.version
        )
        if snapshot is None:
            if any(
                self._store.get_coverage_snapshot(
                    workflow_run_id, version=previous.version
                )
                is not None
                for previous in contracts[:-1]
            ):
                raise PublishedReportNotFoundError(
                    "report scope decision is pending collection for the latest contract"
                )
            return None
        authorization = next(
            (
                item
                for item in reversed(
                    self._store.list_scope_execution_authorizations(workflow_run_id)
                )
                if item.coverage_snapshot_id == snapshot.id
                and item.scope_contract_id == contract.id
                and item.scope_contract_version == contract.version
                and item.resolution == "generate_limited_report"
                and item.state == "authorized_limited_report"
                and item.execution_revision == snapshot.execution_revision + 1
            ),
            None,
        )
        if snapshot.state == "awaiting_scope_decision" and authorization is None:
            raise PublishedReportNotFoundError("report scope decision is pending")
        report_mode = "limited" if authorization is not None else "normal"
        limitations = (
            _scope_limitations(contract, snapshot)
            if report_mode == "limited"
            else []
        )
        return {
            "contract": contract,
            "coverage": snapshot,
            "report_mode": report_mode,
            "limitations": limitations,
        }

    @staticmethod
    def _apply_scope_projection(
        projection: dict[str, Any], scope: dict[str, Any]
    ) -> dict[str, Any]:
        contract = scope["contract"]
        snapshot = scope["coverage"]
        projection["frozen_scope"].update(
            {
                "scope_contract_version": contract.version,
                "query_groups": [
                    {
                        "id": group.id,
                        "suggested_query": group.suggested_query,
                        "final_query": group.final_query,
                        "origin": group.origin,
                        "execution_role": group.execution_role,
                    }
                    for group in contract.query_groups
                ],
                "constraint_counts": snapshot.constraint_counts,
                "unmet_constraint_ids": list(snapshot.unmet_constraint_ids),
            }
        )
        projection["status_strip"]["report_mode"] = scope["report_mode"]
        projection["sections"]["limitations_scope"] = [
            *projection["sections"]["limitations_scope"],
            *scope["limitations"],
        ]
        return projection

    def _record_scope_projection(
        self, workflow_run_id: str, scope: dict[str, Any]
    ) -> None:
        contract = scope["contract"]
        snapshot = scope["coverage"]
        payload = {
            "schema_version": "content_research_scope_audit_event_v1",
            "coverage_snapshot_id": snapshot.id,
            "resolution": (
                "generate_limited_report"
                if scope["report_mode"] == "limited"
                else "coverage_satisfied"
            ),
            "report_mode": scope["report_mode"],
            "scope_contract_version": contract.version,
            "query_groups": [
                {
                    "query_group_id": group.id,
                    "final_query": group.final_query,
                    "execution_role": group.execution_role,
                }
                for group in contract.query_groups
            ],
            "constraint_counts": snapshot.constraint_counts,
            "unmet_constraint_ids": list(snapshot.unmet_constraint_ids),
            "limitations": scope["limitations"],
        }
        event = ScopeAuditEvent(
            id=_stable_id(
                "sae",
                {
                    "scope_contract_id": contract.id,
                    "coverage_snapshot_id": snapshot.id,
                    "event_name": "report_scope_projected",
                    "report_mode": scope["report_mode"],
                },
            ),
            workflow_run_id=workflow_run_id,
            scope_contract_id=contract.id,
            scope_contract_version=contract.version,
            event_name="report_scope_projected",
            payload=payload,
        )
        existing = self._store.list_scope_audit_events(
            workflow_run_id, version=contract.version
        )
        if not any(item.id == event.id for item in existing):
            try:
                self._store.append_scope_audit_event(event)
            except ValueError:
                concurrent = self._store.list_scope_audit_events(
                    workflow_run_id, version=contract.version
                )
                if not any(item.id == event.id for item in concurrent):
                    raise

    def _governed_marketing_fields(self, report: dict[str, Any]) -> dict[str, Any]:
        publication = report.get("publication")
        snapshot_id = (
            publication.get("governed_snapshot_id")
            if isinstance(publication, dict)
            else None
        )
        snapshot = next(
            (
                item
                for item in self._store.list_result_snapshots_for_workflow(
                    str(report.get("workflow_run_id") or "")
                )
                if item.id == snapshot_id
            ),
            None,
        )
        governed = (
            snapshot.metadata.get("governed_snapshot")
            if snapshot is not None and isinstance(snapshot.metadata, dict)
            else None
        )
        if not isinstance(governed, dict):
            raise PublishedReportNotFoundError(
                "published report governed marketing snapshot is missing"
            )
        conclusions = governed.get("marketing_conclusions") or []
        if not isinstance(conclusions, list) or any(
            not isinstance(item, dict) for item in conclusions
        ):
            raise PublishedReportNotFoundError(
                "published report marketing conclusions are malformed"
            )
        policy_scope = governed.get("policy_scope")
        marketing_policy = (
            policy_scope.get("marketing_conclusion_policy")
            if isinstance(policy_scope, dict)
            else None
        )
        if not isinstance(marketing_policy, dict):
            policy = self._store.get_run_policy_snapshot_for_workflow(
                str(report.get("workflow_run_id") or "")
            )
            effective = policy.effective_policy if policy is not None else {}
            marketing_policy = effective.get("marketing_conclusion_policy")
        goal = (
            marketing_policy.get("primary_marketing_goal")
            if isinstance(marketing_policy, dict)
            else None
        )
        return {
            "marketing_conclusions": [dict(item) for item in conclusions],
            "primary_marketing_goal": goal,
        }

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
        raw_claim_cards = report.get("claim_cards")
        claim_cards = _records_in_directions(raw_claim_cards, requested_directions)
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
        all_citations = (
            [
                citation
                for citation in _lite_citations(report.get("citation_groups"))
                if citation.get("claim_candidate_id") not in excluded_claim_ids
            ]
            if requested_directions
            else []
        )
        citations_by_claim = _citations_by_claim(all_citations)
        direction_states = _direction_states(report)
        cards = [
            _validated_card(card, citations_by_claim)
            for card in claim_cards
        ]
        if publication_state != "evidence_only_report" and (
            not isinstance(raw_claim_cards, list)
            or any(not isinstance(card, dict) for card in raw_claim_cards)
            or len(claim_cards) != len(raw_claim_cards)
            or any(card is None for card in cards)
        ):
            raise PublishedReportNotFoundError(
                "published report governed card identity is invalid"
            )
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
        citations = _select_citations(all_citations, citation_group_ids)
        marketing_conclusions, priority_action = _marketing_conclusion_projection(
            report,
            claim_cards=claim_cards,
            citations_by_claim=citations_by_claim,
        )
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
                "marketing_conclusions": marketing_conclusions,
                "priority_action": priority_action,
            },
            "status_strip": (
                {"saved_evidence_count": len(citations)}
                if is_evidence_only
                else {
                    "completed_direction_count": sum(
                        item["state"] in {"formal_directional_result", "completed"}
                        for item in direction_states
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
        recovery_reason = _recovery_reason(self._store, workflow_run_id)
        if (
            brief is None
            or state not in _RECOVERABLE_RUN_STATES
            or not _has_persisted_failure(checkpoints)
            or recovery_reason not in _RECOVERABLE_FAILURE_CODES
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
            "sections": {
                "main_findings": [],
                "weak_signals": [],
                "limitations_scope": [],
                "marketing_conclusions": {},
                "priority_action": None,
            },
            "status_strip": {},
            "citations": [],
            "run_direction_states": _unavailable_direction_states(
                policy.effective_policy if policy else {}
            ),
            "recovery_projection": {
                "reason_code": recovery_reason,
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


def _scope_limitations(contract: Any, snapshot: Any) -> list[dict[str, Any]]:
    summary = snapshot.constraint_counts.get("_summary", {})
    by_id = {constraint.id: constraint for constraint in contract.constraints}
    limitations: list[dict[str, Any]] = []
    for constraint_id in snapshot.unmet_constraint_ids:
        constraint = by_id.get(constraint_id)
        if constraint is None:
            raise PublishedReportNotFoundError(
                "coverage snapshot references an unknown scope constraint"
            )
        counts = snapshot.constraint_counts.get(constraint_id, {})
        matched = int(counts.get("matched_candidate_count") or 0)
        authors = int(counts.get("independent_author_count") or 0)
        minimum_samples = int(summary.get("minimum_samples") or 0)
        minimum_authors = int(summary.get("minimum_independent_authors") or 0)
        limitations.append(
            {
                "constraint_id": constraint.id,
                "constraint_label": constraint.label,
                "constraint_value": constraint.value,
                "reason_code": f"required_constraint_coverage_unmet:{constraint.id}",
                "matched_candidate_count": matched,
                "independent_author_count": authors,
                "minimum_samples": minimum_samples,
                "minimum_independent_authors": minimum_authors,
                "message": (
                    f"Required constraint {constraint.label} ({constraint.value}) has "
                    f"{matched} matching candidates and {authors} independent authors; "
                    f"minimums are {minimum_samples} and {minimum_authors}."
                ),
            }
        )
    return limitations


def _marketing_conclusion_projection(
    report: dict[str, Any],
    *,
    claim_cards: list[dict[str, Any]],
    citations_by_claim: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if "product_marketing" not in set(_frozen_scope(report)["direction_ids"]):
        return {}, {
            "label": "建议",
            "statement": "本轮未请求产品营销方向，不形成营销策略判断。",
            "primary_marketing_goal": report.get("primary_marketing_goal"),
            "supporting_conclusion_ids": [],
        }
    raw = report.get("marketing_conclusions") or []
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise PublishedReportNotFoundError(
            "published report marketing conclusions are malformed"
        )
    goal = report.get("primary_marketing_goal") or "content_seeding"
    if goal != "content_seeding":
        raise PublishedReportNotFoundError(
            "published report primary marketing goal is invalid"
        )
    cards_by_id = {
        str(card.get("claim_candidate_id")): card
        for card in claim_cards
        if isinstance(card.get("claim_candidate_id"), str)
    }
    result: dict[str, dict[str, Any]] = {}
    selected_ids: list[str] = []
    for track in ("need", "value", "message"):
        records = [item for item in raw if item.get("track") == track]
        selected = [item for item in records if item.get("state") == "selected"]
        directional = [item for item in records if item.get("state") == "directional"]
        qualified = [item for item in records if item.get("state") == "qualified"]
        terminal = [
            item
            for item in records
            if item.get("state")
            in {
                "insufficient_evidence",
                "no_single_primary_conclusion",
                "analysis_unavailable",
            }
        ]
        if len(selected) > 1 or len(directional) > 1 or (selected and (directional or terminal)) or (directional and terminal) or len(terminal) > 1:
            raise PublishedReportNotFoundError(
                f"published report {track} marketing decision is ambiguous"
            )
        if selected:
            declared_additional_count = selected[0].get(
                "additional_qualified_count"
            )
            if declared_additional_count is None:
                additional_qualified_count = len(qualified)
            elif (
                isinstance(declared_additional_count, bool)
                or not isinstance(declared_additional_count, int)
                or declared_additional_count < 0
            ):
                raise PublishedReportNotFoundError(
                    "published report additional qualified count is malformed"
                )
            else:
                additional_qualified_count = declared_additional_count
            projected = _selected_marketing_conclusion(
                report,
                selected[0],
                cards_by_id=cards_by_id,
                citations_by_claim=citations_by_claim,
                additional_qualified_count=additional_qualified_count,
            )
            if not _marketing_section_verified(
                report, track=track, decision=selected[0], projected=projected
            ):
                result[track] = {
                    "state": "analysis_unavailable",
                    "reason_codes": ["marketing_conclusion_not_verified"],
                    "verification_direction": _marketing_verification_direction(
                        "analysis_unavailable"
                    ),
                }
                continue
            result[track] = projected
            selected_ids.append(str(projected["conclusion_id"]))
            continue
        if directional:
            projected = _selected_marketing_conclusion(
                report, directional[0], cards_by_id=cards_by_id,
                citations_by_claim=citations_by_claim, additional_qualified_count=0,
                directional=True,
            )
            if not _marketing_section_verified(report, track=track, decision=directional[0], projected=projected):
                raise PublishedReportNotFoundError("published report directional marketing conclusion is not verified")
            result[track] = projected
            continue
        decision = terminal[0] if terminal else {
            "track": track,
            "state": "analysis_unavailable",
            "reason_codes": ["marketing_conclusion_unavailable"],
        }
        state = str(decision.get("state") or "")
        reason_codes = decision.get("reason_codes") or []
        if not isinstance(reason_codes, list) or any(
            not isinstance(item, str) or not item for item in reason_codes
        ):
            raise PublishedReportNotFoundError(
                f"published report {track} marketing reasons are malformed"
            )
        result[track] = {
            "state": state,
            "reason_codes": list(reason_codes),
            "verification_direction": _marketing_verification_direction(state),
        }
    action_statement = (
        "优先用已选结论组织首轮种草内容，并通过对应证据入口复核表达边界。"
        if selected_ids
        else "先补足三条轨道的合格笔记与独立作者，再形成种草策略判断。"
    )
    return result, {
        "label": "建议",
        "statement": action_statement,
        "primary_marketing_goal": goal,
        "supporting_conclusion_ids": selected_ids,
    }


def _marketing_section_verified(
    report: dict[str, Any],
    *,
    track: str,
    decision: dict[str, Any],
    projected: dict[str, Any],
) -> bool:
    sections = report.get("sections")
    if not isinstance(sections, list):
        return False
    matches = [
        item
        for item in sections
        if isinstance(item, dict) and item.get("section_kind") == f"marketing_{track}"
    ]
    if len(matches) != 1:
        return False
    section = matches[0]
    publication = report.get("publication")
    omitted = set(
        publication.get("omitted_section_ids") or []
        if isinstance(publication, dict)
        else []
    )
    return (
        section.get("section_id") not in omitted
        and section.get("conclusion_state") == decision.get("state")
        and section.get("prose") == decision.get("statement")
        and section.get("claim_candidate_ids") == decision.get("supporting_claim_ids")
        and section.get("citation_group_ids") == projected.get("citation_group_ids")
        and section.get("marketing_conclusion_ids")
        == [projected.get("conclusion_id")]
    )


def _selected_marketing_conclusion(
    report: dict[str, Any],
    decision: dict[str, Any],
    *,
    cards_by_id: dict[str, dict[str, Any]],
    citations_by_claim: dict[str, list[dict[str, Any]]],
    additional_qualified_count: int,
    directional: bool = False,
) -> dict[str, Any]:
    statement = decision.get("statement")
    claim_ids = decision.get("supporting_claim_ids")
    note_count = decision.get("supporting_note_count")
    author_count = decision.get("independent_author_count")
    if (
        not isinstance(statement, str)
        or not statement.strip()
        or not isinstance(claim_ids, list)
        or not claim_ids
        or any(not isinstance(item, str) or not item for item in claim_ids)
        or len(claim_ids) != len(set(claim_ids))
        or isinstance(note_count, bool)
        or not isinstance(note_count, int)
        or isinstance(author_count, bool)
        or not isinstance(author_count, int)
        or note_count < (1 if directional else 3)
        or author_count < (1 if directional else 2)
        or author_count > note_count
        or (not directional and decision.get("reason_codes") not in ([], ()))
    ):
        raise PublishedReportNotFoundError(
            "published report selected marketing conclusion is malformed"
        )
    citations: list[dict[str, Any]] = []
    for claim_id in claim_ids:
        card = cards_by_id.get(claim_id)
        claim_citations = citations_by_claim.get(claim_id) or []
        if (
            card is None
            or card.get("admission_state") != "admitted"
            or card.get("direction_id") != "product_marketing"
            or not claim_citations
        ):
            raise PublishedReportNotFoundError(
                "published report selected marketing support is invalid"
            )
        citations.extend(claim_citations)
    citation_ids = list(
        dict.fromkeys(str(item["citation_group_id"]) for item in citations)
    )
    note_ids = {
        item.get("canonical_note_id")
        for item in citations
        if isinstance(item.get("canonical_note_id"), str)
        and item.get("canonical_note_id")
    }
    if len(note_ids) != note_count:
        raise PublishedReportNotFoundError(
            "published report selected marketing note count is invalid"
        )
    conclusion_id = decision.get("candidate_id")
    if not isinstance(conclusion_id, str) or not conclusion_id:
        publication = report.get("publication")
        snapshot_id = (
            publication.get("governed_snapshot_id")
            if isinstance(publication, dict)
            else None
        )
        conclusion_id = _stable_id(
            "rmc",
            {
                "snapshot": snapshot_id,
                "track": decision.get("track"),
                "state": decision.get("state"),
                "reason_codes": decision.get("reason_codes") or [],
            },
        )
    return {
        "state": "directional" if directional else "selected",
        "conclusion_id": conclusion_id,
        "statement": statement,
        "citation_group_ids": citation_ids,
        "supporting_note_count": note_count,
        "independent_author_count": author_count,
        "additional_qualified_count": additional_qualified_count,
        **({
            "note_gap": max(0, 3 - note_count),
            "author_gap": max(0, 2 - author_count),
            "reason_codes": list(decision.get("reason_codes") or []),
            "verification_direction": _marketing_verification_direction("directional"),
        } if directional else {}),
    }


def _marketing_verification_direction(state: str) -> str:
    directions = {
        "directional": "该方向不可作为功效或投放定论；补足独立证据后重新验证。",
        "insufficient_evidence": "补充至少 3 篇合格笔记，并覆盖至少 2 位独立作者后重新验证。",
        "no_single_primary_conclusion": "增加能够区分候选结论的合格笔记后重新评估主结论。",
        "analysis_unavailable": "恢复结论分析能力并继续本轮分析，不新增未经治理的判断。",
    }
    if state not in directions:
        raise PublishedReportNotFoundError(
            "published report marketing conclusion state is invalid"
        )
    return directions[state]


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
    if state == "formal_directional_result":
        return {
            "direction": direction_id,
            "state": state,
            "reason_code": None,
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
    projectable_refs = [ref for ref in raw_refs if ref.get("field_path") in _LITE_FIELDS]
    if not projectable_refs or any(
        not _valid_frozen_quote_ref(ref) for ref in projectable_refs
    ):
        return None
    refs = [
        _citation_ref(ref, navigation_state=navigation_state)
        for ref in projectable_refs
    ]
    return {
        "citation_group_id": group.get("citation_group_id"),
        "display_index": group.get("display_index"),
        "claim_candidate_id": group.get("claim_candidate_id"),
        "admission_decision_id": group.get("admission_decision_id"),
        "canonical_note_id": next(iter(note_ids)),
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
        for key in (
            "quote",
            "field_path",
            "text_start",
            "text_end",
            "source_url",
            "source_collected_at",
            "source_text_hash",
        )
    } | {"navigation_state": state, "navigation_reason": ref.get("navigation_reason")}


def _valid_frozen_quote_ref(
    ref: dict[str, Any], *, eligible_fields: set[str] = _LITE_FIELDS
) -> bool:
    quote = ref.get("quote")
    start = ref.get("text_start")
    end = ref.get("text_end")
    return (
        ref.get("field_path") in eligible_fields
        and isinstance(quote, str)
        and bool(quote)
        and isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
        and start >= 0
        and end > start
        and end - start == len(quote)
        and isinstance(ref.get("source_text_hash"), str)
        and bool(ref["source_text_hash"])
    )


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
            _valid_frozen_quote_ref(ref, eligible_fields=eligible_fields)
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
    return any(item.status in _FAILURE_CHECKPOINT_STATUSES for item in checkpoints)


def _recovery_reason(store: ContentResearchStore, workflow_run_id: str) -> str:
    failures = [
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.workflow_run_id == workflow_run_id
        and item.status in _FAILURE_CHECKPOINT_STATUSES
    ]
    if not failures:
        return "temporary_error"
    payload = failures[-1].payload
    return str(
        payload.get("reason_code")
        or (payload.get("completion") or {}).get("failure_code")
        or payload.get("failure_reason")
        or "temporary_error"
    )
