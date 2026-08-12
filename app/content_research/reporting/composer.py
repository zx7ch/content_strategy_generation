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
_COMPOSER_ALGORITHM_VERSION = "content_research_report_composer_v2"
_MARKETING_TRACKS = ("need", "value", "message")


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
        marketing_conclusions = _mapping_list(
            governed.get("marketing_conclusions") or [], "marketing_conclusions"
        )
        compose_mode = str(policy_scope.get("report_compose_mode") or "prose")
        if compose_mode not in {"prose", "template_only"}:
            raise ValueError("invalid report_compose_mode")

        claim_citations = _claim_citations(claim_cards, citation_groups)
        sections: list[ReportSection] = []
        if compose_mode == "prose":
            sections.append(self._conclusions(snapshot, claim_cards, claim_citations, policy_scope))
        sections.append(self._findings(snapshot, claim_cards, claim_citations, policy_scope))
        if compose_mode == "template_only":
            if "product_marketing" in set(policy_scope.get("direction_ids") or []):
                marketing_sections = self._marketing_sections(
                    snapshot,
                    marketing_conclusions,
                    claim_cards,
                    claim_citations,
                    policy_scope,
                )
                sections.extend(marketing_sections)
                sections.append(
                    self._priority_action(snapshot, marketing_sections, policy_scope)
                )
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

    def _marketing_sections(
        self,
        snapshot: ResearchResultSnapshotRecord,
        conclusions: list[dict[str, Any]],
        claim_cards: list[dict[str, Any]],
        claim_citations: dict[str, tuple[str, ...]],
        policy_scope: dict[str, Any],
    ) -> tuple[ReportSection, ...]:
        cards_by_id = {
            _required_string(card.get("claim_candidate_id"), "claim_candidate_id"): card
            for card in claim_cards
        }
        return tuple(
            _marketing_track_section(
                snapshot,
                track,
                [item for item in conclusions if item.get("track") == track],
                cards_by_id,
                claim_citations,
                policy_scope,
            )
            for track in _MARKETING_TRACKS
        )

    def _priority_action(
        self,
        snapshot: ResearchResultSnapshotRecord,
        marketing_sections: tuple[ReportSection, ...],
        policy_scope: dict[str, Any],
    ) -> ReportSection:
        conclusion_ids = tuple(
            conclusion_id
            for section in marketing_sections
            if section.conclusion_state == "selected"
            for conclusion_id in section.marketing_conclusion_ids
        )
        marketing_policy = policy_scope.get("marketing_conclusion_policy")
        goal = (
            str(marketing_policy.get("primary_marketing_goal") or "")
            if isinstance(marketing_policy, dict)
            else ""
        ) or "content_seeding"
        if goal != "content_seeding":
            raise ValueError("unsupported primary marketing goal")
        if conclusion_ids:
            statement = "优先用已选结论组织首轮种草内容，并通过对应证据入口复核表达边界。"
            structured_ids: tuple[str, ...] = ()
        else:
            statement = "先补足三条轨道的合格笔记与独立作者，再形成种草策略判断。"
            structured_ids = (_scope_card_id(snapshot, policy_scope),)
        return ReportSection(
            section_id=_section_id(snapshot, "priority_action"),
            section_kind="priority_action",
            structured_card_ids=structured_ids,
            action_label="建议",
            action_statement=statement,
            primary_marketing_goal=goal,
            supporting_conclusion_ids=conclusion_ids,
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


def _marketing_track_section(
    snapshot: ResearchResultSnapshotRecord,
    track: str,
    records: list[dict[str, Any]],
    cards_by_id: dict[str, dict[str, Any]],
    claim_citations: dict[str, tuple[str, ...]],
    policy_scope: dict[str, Any],
) -> ReportSection:
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
        raise ValueError(f"marketing conclusion track {track} has ambiguous decisions")
    section_id = _section_id(snapshot, f"marketing_{track}")
    if selected or directional:
        decision = (selected or directional)[0]
        conclusion_state = str(decision["state"])
        statement = _required_string(decision.get("statement"), "marketing conclusion statement")
        claim_ids_value = decision.get("supporting_claim_ids")
        if not isinstance(claim_ids_value, list) or not claim_ids_value:
            raise ValueError("selected marketing conclusion requires supporting_claim_ids")
        claim_ids = tuple(
            _required_string(claim_id, "supporting claim id")
            for claim_id in claim_ids_value
        )
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("selected marketing conclusion supporting claims must be unique")
        for claim_id in claim_ids:
            card = cards_by_id.get(claim_id)
            if (
                card is None
                or card.get("admission_state") != "admitted"
                or card.get("direction_id") != "product_marketing"
                or claim_id not in claim_citations
            ):
                raise ValueError("selected marketing conclusion has invalid governed support")
        citation_ids = tuple(
            dict.fromkeys(
                citation_id
                for claim_id in claim_ids
                for citation_id in claim_citations[claim_id]
            )
        )
        conclusion_id = _marketing_conclusion_id(snapshot, decision)
        additional_count_value = decision.get("additional_qualified_count")
        additional_count = (
            len(qualified)
            if additional_count_value is None
            else _nonnegative_int(
                additional_count_value, "additional_qualified_count"
            )
        )
        anchors = tuple(
            CitationAnchor(
                anchor_id=_stable_id(
                    "rca",
                    {
                        "section": section_id,
                        "conclusion": conclusion_id,
                        "citation": citation_id,
                    },
                ),
                section_id=section_id,
                block_id="primary_conclusion",
                text_start=0,
                text_end=len(statement),
                citation_group_id=citation_id,
            )
            for citation_id in citation_ids
        )
        return ReportSection(
            section_id=section_id,
            section_kind=f"marketing_{track}",
            prose=statement,
            claim_candidate_ids=claim_ids,
            citation_group_ids=citation_ids,
            citation_anchors=anchors,
            marketing_conclusion_ids=(conclusion_id,),
            conclusion_state=conclusion_state,
            supporting_note_count=_nonnegative_int(
                decision.get("supporting_note_count"), "supporting_note_count"
            ),
            independent_author_count=_nonnegative_int(
                decision.get("independent_author_count"),
                "independent_author_count",
            ),
            additional_qualified_count=additional_count,
            reason_codes=tuple(decision.get("reason_codes") or ()),
            verification_direction=(
                _verification_direction("directional")
                if conclusion_state == "directional"
                else None
            ),
        )

    decision = terminal[0] if terminal else {
        "track": track,
        "state": "analysis_unavailable",
        "reason_codes": ["marketing_conclusion_unavailable"],
    }
    state = _required_string(decision.get("state"), "marketing conclusion state")
    reason_codes_value = decision.get("reason_codes") or []
    if not isinstance(reason_codes_value, list) or any(
        not isinstance(item, str) or not item for item in reason_codes_value
    ):
        raise ValueError("marketing conclusion reason_codes must be a string list")
    return ReportSection(
        section_id=section_id,
        section_kind=f"marketing_{track}",
        marketing_conclusion_ids=(_marketing_conclusion_id(snapshot, decision),),
        conclusion_state=state,
        reason_codes=tuple(reason_codes_value),
        verification_direction=_verification_direction(state),
        structured_card_ids=(_scope_card_id(snapshot, policy_scope),),
    )


def _marketing_conclusion_id(
    snapshot: ResearchResultSnapshotRecord, decision: dict[str, Any]
) -> str:
    candidate_id = decision.get("candidate_id")
    if isinstance(candidate_id, str) and candidate_id:
        return candidate_id
    return _stable_id(
        "rmc",
        {
            "snapshot": snapshot.id,
            "track": decision.get("track"),
            "state": decision.get("state"),
            "reason_codes": decision.get("reason_codes") or [],
        },
    )


def _nonnegative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"marketing conclusion {field_name} must be non-negative")
    return value


def _verification_direction(state: str) -> str:
    return {
        "directional": "该方向不可作为功效或投放定论；补足独立证据后重新验证。",
        "insufficient_evidence": "补充至少 3 篇合格笔记，并覆盖至少 2 位独立作者后重新验证。",
        "no_single_primary_conclusion": "增加能够区分候选结论的合格笔记后重新评估主结论。",
        "analysis_unavailable": "恢复结论分析能力并继续本轮分析，不新增未经治理的判断。",
    }[state]


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
