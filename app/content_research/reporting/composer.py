"""Deterministically compose one report draft from one governed snapshot."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.content_research.models import ResearchResultSnapshotRecord
from app.content_research.reporting.contracts import (
    CitationAnchor,
    ReportDraft,
    ReportSection,
    _stable_id,
)

_GOVERNED_SNAPSHOT_SCHEMA = "content_research_governed_snapshot_v2"
_COMPOSER_ALGORITHM_VERSION = "content_research_report_composer_v1"


class ResearchReportComposer:
    """Pure composition seam; its sole evidence input is a frozen governed snapshot."""

    def compose(self, snapshot: ResearchResultSnapshotRecord) -> ReportDraft:
        governed = _governed_snapshot(snapshot)
        plan_id = _required_string(snapshot.research_plan_id, "research_plan_id")
        input_fingerprint = _required_string(
            snapshot.metadata.get("governed_input_fingerprint"), "governed_input_fingerprint"
        )
        policy_scope = _required_mapping(governed.get("policy_scope"), "policy_scope")
        policy_version = _required_string(policy_scope.get("effective_policy_hash"), "effective_policy_hash")
        citation_groups = _citation_groups(governed.get("citation_groups"))
        claim_cards = _mapping_list(governed.get("claim_cards"), "claim_cards")
        weak_signals = _mapping_list(governed.get("weak_signals"), "weak_signals")
        cross_records = _mapping_list(governed.get("cross_direction_records"), "cross_direction_records")
        aggregates = _mapping_list(governed.get("aggregate_claims"), "aggregate_claims")
        limitations = _mapping_list(governed.get("limitations_recovery"), "limitations_recovery")
        compose_mode = str(policy_scope.get("report_compose_mode") or "prose")
        if compose_mode not in {"prose", "template_only"}:
            raise ValueError("invalid report_compose_mode")

        claim_citations = _claim_citations(claim_cards, citation_groups)
        sections: list[ReportSection] = []
        if compose_mode == "prose":
            sections.append(self._conclusions(snapshot, claim_cards, claim_citations, policy_scope))
        sections.append(self._findings(snapshot, claim_cards, claim_citations, policy_scope))
        if compose_mode == "template_only":
            weak = self._weak_signals(snapshot, weak_signals)
            if weak is not None:
                sections.append(weak)
            sections.append(self._limitations(snapshot, limitations, policy_scope))
            return ReportDraft(
                workflow_run_id=snapshot.workflow_run_id, research_plan_id=plan_id,
                governed_snapshot_id=snapshot.id, governed_snapshot_version=snapshot.snapshot_version,
                input_fingerprint=input_fingerprint, policy_version=policy_version,
                algorithm_version=_COMPOSER_ALGORITHM_VERSION, sections=tuple(sections),
            )
        tension = self._tensions(snapshot, cross_records, claim_citations)
        if tension is not None:
            sections.append(tension)
        weak = self._weak_signals(snapshot, weak_signals)
        if weak is not None:
            sections.append(weak)
        next_steps = self._next_steps(snapshot, aggregates, limitations, claim_citations)
        if next_steps is not None:
            sections.append(next_steps)
        sections.append(self._limitations(snapshot, limitations, policy_scope))
        return ReportDraft(
            workflow_run_id=snapshot.workflow_run_id,
            research_plan_id=plan_id,
            governed_snapshot_id=snapshot.id,
            governed_snapshot_version=snapshot.snapshot_version,
            input_fingerprint=input_fingerprint,
            policy_version=policy_version,
            algorithm_version=_COMPOSER_ALGORITHM_VERSION,
            sections=tuple(sections),
        )

    def _conclusions(
        self,
        snapshot: ResearchResultSnapshotRecord,
        claim_cards: list[dict[str, Any]],
        claim_citations: dict[str, tuple[str, ...]],
        policy_scope: dict[str, Any],
    ) -> ReportSection:
        section_id = _section_id(snapshot, "core_conclusions")
        if not claim_cards:
            return ReportSection(
                section_id=section_id,
                section_kind="core_conclusions",
                structured_card_ids=(_scope_card_id(snapshot, policy_scope),),
            )
        return _claim_section(section_id, "core_conclusions", claim_cards, claim_citations)

    def _findings(
        self,
        snapshot: ResearchResultSnapshotRecord,
        claim_cards: list[dict[str, Any]],
        claim_citations: dict[str, tuple[str, ...]],
        policy_scope: dict[str, Any],
    ) -> ReportSection:
        section_id = _section_id(snapshot, "main_findings")
        if not claim_cards:
            return ReportSection(
                section_id=section_id,
                section_kind="main_findings",
                structured_card_ids=(_scope_card_id(snapshot, policy_scope),),
            )
        return _claim_section(section_id, "main_findings", claim_cards, claim_citations)

    def _tensions(
        self,
        snapshot: ResearchResultSnapshotRecord,
        records: list[dict[str, Any]],
        claim_citations: dict[str, tuple[str, ...]],
    ) -> ReportSection | None:
        tensions = [record for record in records if record.get("record_type") == "contradiction"]
        if not tensions:
            return None
        refs = tuple(_required_string(item.get("cross_direction_record_id"), "cross_direction_record_id") for item in tensions)
        citation_ids = _citation_ids_for_source_claims(tensions, claim_citations)
        return ReportSection(
            section_id=_section_id(snapshot, "cross_direction_tensions"),
            section_kind="cross_direction_tensions",
            cross_direction_record_ids=refs,
            citation_group_ids=citation_ids,
        )

    def _weak_signals(
        self, snapshot: ResearchResultSnapshotRecord, weak_signals: list[dict[str, Any]]
    ) -> ReportSection | None:
        if not weak_signals:
            return None
        return ReportSection(
            section_id=_section_id(snapshot, "weak_signals"),
            section_kind="weak_signals",
            weak_signal_ids=tuple(
                _required_string(item.get("weak_signal_id"), "weak_signal_id") for item in weak_signals
            ),
        )

    def _next_steps(
        self,
        snapshot: ResearchResultSnapshotRecord,
        aggregates: list[dict[str, Any]],
        limitations: list[dict[str, Any]],
        claim_citations: dict[str, tuple[str, ...]],
    ) -> ReportSection | None:
        actions = [
            item
            for item in aggregates
            if item.get("aggregate_type") == "action_hypothesis"
            and item.get("request_origin") == "user_requested_next_steps"
        ]
        recovery_ids = _recovery_ids(snapshot, limitations)
        if not actions and not recovery_ids:
            return None
        action_ids = tuple(_required_string(item.get("aggregate_claim_id"), "aggregate_claim_id") for item in actions)
        citation_ids = _citation_ids_for_source_claims(actions, claim_citations)
        return ReportSection(
            section_id=_section_id(snapshot, "next_steps"),
            section_kind="next_steps",
            aggregate_claim_ids=action_ids,
            limitation_ids=recovery_ids,
            citation_group_ids=citation_ids,
        )

    def _limitations(
        self,
        snapshot: ResearchResultSnapshotRecord,
        limitations: list[dict[str, Any]],
        policy_scope: dict[str, Any],
    ) -> ReportSection:
        limitation_ids = _limitation_ids(snapshot, limitations)
        if not limitation_ids:
            limitation_ids = (_scope_card_id(snapshot, policy_scope),)
        return ReportSection(
            section_id=_section_id(snapshot, "limitations_scope"),
            section_kind="limitations_scope",
            limitation_ids=limitation_ids,
        )


def _governed_snapshot(snapshot: ResearchResultSnapshotRecord) -> dict[str, Any]:
    if snapshot.schema_version != _GOVERNED_SNAPSHOT_SCHEMA:
        raise ValueError("report composer requires a governed snapshot v2")
    return _required_mapping(snapshot.metadata.get("governed_snapshot"), "governed_snapshot")


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"governed snapshot requires {field_name}")
    return value


def _required_mapping(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"governed snapshot requires {field_name}")
    return value


def _mapping_list(value: object, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"governed snapshot requires {field_name} list")
    return [dict(item) for item in value]


def _citation_groups(value: object) -> dict[str, dict[str, Any]]:
    groups = _mapping_list(value, "citation_groups")
    result: dict[str, dict[str, Any]] = {}
    for group in groups:
        group_id = _required_string(group.get("citation_group_id"), "citation_group_id")
        if group_id in result:
            raise ValueError("governed snapshot cannot contain duplicate citation groups")
        if not isinstance(group.get("display_index"), int) or group["display_index"] < 1:
            raise ValueError("citation group requires a frozen positive display index")
        result[group_id] = group
    return result


def _claim_citations(
    claim_cards: list[dict[str, Any]], citation_groups: dict[str, dict[str, Any]]
) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for card in claim_cards:
        claim_id = _required_string(card.get("claim_candidate_id"), "claim_candidate_id")
        groups = tuple(
            group_id
            for group_id, group in citation_groups.items()
            if group.get("claim_candidate_id") == claim_id
        )
        if not groups:
            raise ValueError("admitted claim card has no frozen citation group")
        result[claim_id] = groups
    return result


def _claim_section(
    section_id: str,
    section_kind: str,
    claim_cards: list[dict[str, Any]],
    claim_citations: dict[str, tuple[str, ...]],
) -> ReportSection:
    blocks: list[str] = []
    anchors: list[CitationAnchor] = []
    claim_ids: list[str] = []
    group_ids: list[str] = []
    cursor = 0
    for index, card in enumerate(claim_cards, start=1):
        claim_id = _required_string(card.get("claim_candidate_id"), "claim_candidate_id")
        statement = _required_string(card.get("statement"), "claim statement")
        block = statement if index == 1 else f"\n{statement}"
        start = cursor + (1 if index > 1 else 0)
        end = start + len(statement)
        blocks.append(block)
        cursor += len(block)
        claim_ids.append(claim_id)
        for group_id in claim_citations[claim_id]:
            group_ids.append(group_id)
            anchors.append(
                CitationAnchor(
                    anchor_id=_stable_id("rca", {"section": section_id, "block": index, "start": start, "end": end, "citation": group_id}),
                    section_id=section_id,
                    block_id=f"block_{index}",
                    text_start=start,
                    text_end=end,
                    citation_group_id=group_id,
                )
            )
    return ReportSection(
        section_id=section_id,
        section_kind=section_kind,
        prose="".join(blocks),
        structured_card_ids=tuple(claim_ids),
        claim_candidate_ids=tuple(claim_ids),
        citation_group_ids=tuple(dict.fromkeys(group_ids)),
        citation_anchors=tuple(anchors),
    )


def _citation_ids_for_source_claims(
    records: Iterable[dict[str, Any]], claim_citations: dict[str, tuple[str, ...]]
) -> tuple[str, ...]:
    citation_ids: list[str] = []
    for record in records:
        source_claim_ids = record.get("source_claim_ids") or record.get("claim_ids") or []
        if not isinstance(source_claim_ids, list):
            raise ValueError("governance record source_claim_ids must be a list")
        for claim_id in source_claim_ids:
            if claim_id not in claim_citations:
                raise ValueError("governance material has no frozen claim citation")
            citation_ids.extend(claim_citations[claim_id])
    return tuple(dict.fromkeys(citation_ids))


def _section_id(snapshot: ResearchResultSnapshotRecord, kind: str) -> str:
    return _stable_id("rps", {"snapshot": snapshot.id, "version": snapshot.snapshot_version, "kind": kind})


def _scope_card_id(snapshot: ResearchResultSnapshotRecord, policy_scope: dict[str, Any]) -> str:
    return _stable_id("rsc", {"snapshot": snapshot.id, "scope": policy_scope})


def _limitation_ids(snapshot: ResearchResultSnapshotRecord, limitations: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(
        _stable_id("rlm", {"snapshot": snapshot.id, "limitation": limitation})
        for limitation in limitations
    )


def _recovery_ids(snapshot: ResearchResultSnapshotRecord, limitations: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(
        _stable_id("rrc", {"snapshot": snapshot.id, "recovery": action})
        for limitation in limitations
        for action in limitation.get("recovery_actions") or []
        if isinstance(action, str) and action
    )
