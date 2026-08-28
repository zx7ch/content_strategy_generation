"""Append-only report audit/publish orchestration without Artifact/UI concerns."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from app.content_research.models import ResearchResultSnapshotRecord
from app.content_research.persistence_models import ReportPublicationRecord, StageCheckpointRecord
from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.reporting.contracts import (
    ReportDraft,
    ReportFaithfulnessDecision,
    ReportPublication,
)
from app.content_research.reporting.faithfulness import (
    ReportFaithfulnessEvaluator,
    ReportSemanticAuditor,
)
from app.content_research.runtime import canonical_fingerprint
from app.content_research.stores.base import ContentResearchStore


class ReportExecutionService:
    def __init__(self, store: ContentResearchStore, composer: ResearchReportComposer | None = None, evaluator: ReportFaithfulnessEvaluator | None = None) -> None:
        self._store = store
        self._composer = composer or ResearchReportComposer()
        self._evaluator = evaluator or ReportFaithfulnessEvaluator()

    async def execute(self, snapshot: ResearchResultSnapshotRecord, semantic_auditor: ReportSemanticAuditor) -> ReportPublication:
        compose_started_at = _utcnow()
        initial_draft = self._composer.compose(snapshot)
        compose_finished_at = _utcnow()
        existing = next(
            (
                item
                for item in self._store.list_typed_records(ReportPublicationRecord)
                if item.governed_snapshot_id == snapshot.id
                and item.governed_snapshot_version == snapshot.snapshot_version
                and item.workflow_run_id == snapshot.workflow_run_id
                and item.input_fingerprint == initial_draft.input_fingerprint
                and item.policy_version == initial_draft.policy_version
                and item.algorithm_version == initial_draft.algorithm_version
            ),
            None,
        )
        if existing is not None:
            return self._publication_from_record(existing)
        policy = (snapshot.metadata["governed_snapshot"])["policy_scope"]
        if not _has_admitted_cited_evidence(snapshot):
            # A provider/auth recovery snapshot can legitimately have no
            # admitted material. It is still useful as an auditable recovery
            # artifact, but it is not a verified research report. In
            # particular, an empty template must never become a
            # complete_verified_report merely because there is no prose for a
            # semantic auditor to reject.
            omitted = tuple(section.section_id for section in initial_draft.sections if section.prose)
            evidence_only_draft = self._withdraw_prose(initial_draft, omitted)
            self._store.save_report_draft(evidence_only_draft.to_record())
            self._checkpoint(snapshot, "compose", evidence_only_draft.id, 0, compose_started_at, compose_finished_at)
            faithfulness_started_at = _utcnow()
            decision = ReportFaithfulnessDecision(
                workflow_run_id=evidence_only_draft.workflow_run_id,
                research_plan_id=evidence_only_draft.research_plan_id,
                governed_snapshot_id=evidence_only_draft.governed_snapshot_id,
                governed_snapshot_version=evidence_only_draft.governed_snapshot_version,
                input_fingerprint=evidence_only_draft.input_fingerprint,
                policy_version=evidence_only_draft.policy_version,
                algorithm_version=evidence_only_draft.algorithm_version,
                report_draft_id=evidence_only_draft.id,
                scope_contract_id=evidence_only_draft.scope_contract_id,
                execution_unit_id=evidence_only_draft.execution_unit_id,
                coverage_snapshot_id=evidence_only_draft.coverage_snapshot_id,
                attempt_no=evidence_only_draft.attempt_no,
                audit_state="failed",
                reason_codes=("insufficient_admitted_evidence",),
                omitted_section_ids=omitted,
                semantic_audit={"state": "not_applicable", "usage": {}},
            )
            self._store.save_report_faithfulness_decision(decision.to_record())
            self._checkpoint(snapshot, "faithfulness", decision.id, 0, faithfulness_started_at, _utcnow())
            publication = self._publication(
                evidence_only_draft,
                decision.id,
                "evidence_only_report",
                omitted,
                False,
                str(policy.get("report_compose_mode") or "prose"),
            )
            self._store.save_report_publication(publication.to_record())
            return publication
        max_rewrites = int(policy.get("llm_cost_policy", {}).get("max_report_rewrites", 1))
        if max_rewrites < 0:
            raise ValueError("max_report_rewrites cannot be negative")
        previous_draft: ReportDraft | None = None
        withdrawn_section_ids: tuple[str, ...] = ()
        last_evaluation = None
        draft = None
        for attempt in range(max_rewrites + 1):
            draft = initial_draft if previous_draft is None else previous_draft
            self._store.save_report_draft(draft.to_record())
            self._checkpoint(snapshot, "compose", draft.id, attempt, compose_started_at, compose_finished_at)
            faithfulness_started_at = _utcnow()
            evaluation = await self._evaluator.evaluate(snapshot, draft, semantic_auditor)
            faithfulness_finished_at = _utcnow()
            decision = ReportFaithfulnessDecision(
                workflow_run_id=draft.workflow_run_id, research_plan_id=draft.research_plan_id,
                governed_snapshot_id=draft.governed_snapshot_id, governed_snapshot_version=draft.governed_snapshot_version,
                input_fingerprint=draft.input_fingerprint, policy_version=draft.policy_version,
                algorithm_version=draft.algorithm_version, report_draft_id=draft.id,
                scope_contract_id=draft.scope_contract_id,
                execution_unit_id=draft.execution_unit_id,
                coverage_snapshot_id=draft.coverage_snapshot_id,
                attempt_no=draft.attempt_no,
                audit_state="passed" if evaluation.passed else "failed", reason_codes=evaluation.reason_codes,
                omitted_section_ids=evaluation.affected_section_ids,
                semantic_audit={
                    "state": evaluation.semantic_result.state,
                    "input_draft_id": draft.id,
                    "model_version": evaluation.semantic_result.model_version,
                    "prompt_version": evaluation.semantic_result.prompt_version,
                    "usage": evaluation.semantic_result.usage or {},
                },
            )
            self._store.save_report_faithfulness_decision(decision.to_record())
            self._checkpoint(snapshot, "faithfulness", decision.id, attempt, faithfulness_started_at, faithfulness_finished_at)
            if evaluation.passed:
                if _has_directional(draft):
                    omitted = tuple(
                        section.section_id
                        for section in draft.sections
                        if section.prose
                        and section.conclusion_state
                        not in {"directional", "selected", "contested"}
                    )
                    draft = self._withdraw_prose(draft, omitted)
                    self._store.save_report_draft(draft.to_record())
                    state = (
                        "partial_verified_report"
                        if _has_selected(draft)
                        else "directional_report"
                    )
                    withdrawn_section_ids = omitted
                else:
                    if _has_selected(draft):
                        # Task 3.1 tracks are independent analysis viewpoints,
                        # not a completeness quota.  Even three selected tracks
                        # remain a partial verified report over a bounded sample.
                        state = "partial_verified_report"
                    elif _completed_marketing_analysis_has_no_publishable_conclusion(snapshot):
                        state = "evidence_only_report"
                    else:
                        state = (
                            "partial_verified_report"
                            if withdrawn_section_ids
                            else "complete_verified_report"
                        )
                publication_decision = decision
                if draft.id != decision.report_draft_id:
                    publication_decision = replace(
                        decision,
                        report_draft_id=draft.id,
                        previous_version_id=decision.id,
                    )
                    self._store.save_report_faithfulness_decision(
                        publication_decision.to_record()
                    )
                publication = self._publication(
                    draft,
                    publication_decision.id,
                    state,
                    withdrawn_section_ids,
                    state != "evidence_only_report",
                    str(policy.get("report_compose_mode") or "prose"),
                )
                self._store.save_report_publication(publication.to_record())
                return publication
            last_evaluation = evaluation
            if attempt == max_rewrites:
                break
            # A retry must be a real directed rewrite, never an identical draft
            # carrying only a different predecessor ID.  When the semantic
            # provider cannot identify a section, withdraw all free prose.
            omitted = evaluation.affected_section_ids or tuple(
                section.section_id for section in draft.sections if section.prose
            )
            withdrawn_section_ids = tuple(dict.fromkeys((*withdrawn_section_ids, *omitted)))
            rewritten = self._withdraw_prose(draft, omitted)
            if rewritten.id == draft.id:
                break
            previous_draft = rewritten
        assert draft is not None and last_evaluation is not None
        omitted = tuple(dict.fromkeys((*withdrawn_section_ids, *last_evaluation.affected_section_ids))) or tuple(
            section.section_id for section in draft.sections if section.prose
        )
        visible_marketing_states = _visible_marketing_states(draft, omitted)
        has_marketing_sections = any(
            section.section_kind.startswith("marketing_") for section in draft.sections
        )
        if not has_marketing_sections:
            state = (
                "partial_verified_report"
                if any(
                    section.prose and section.section_id not in set(omitted)
                    for section in draft.sections
                )
                else "evidence_only_report"
            )
        elif {"selected", "contested"} & visible_marketing_states:
            state = "partial_verified_report"
        elif "directional" in visible_marketing_states:
            state = "directional_report"
        else:
            state = "evidence_only_report"
        published_draft = self._withdraw_prose(draft, omitted)
        self._store.save_report_draft(published_draft.to_record())
        publication_decision = replace(
            decision,
            report_draft_id=published_draft.id,
            previous_version_id=decision.id,
        )
        self._store.save_report_faithfulness_decision(publication_decision.to_record())
        publication = self._publication(
            published_draft,
            publication_decision.id,
            state,
            omitted,
            state != "evidence_only_report",
            str(policy.get("report_compose_mode") or "prose"),
            withheld_section_ids=omitted,
        )
        self._store.save_report_publication(publication.to_record())
        return publication

    @staticmethod
    def _withdraw_prose(draft: ReportDraft, omitted: tuple[str, ...]) -> ReportDraft:
        omitted_ids = set(omitted)
        sections = tuple(
            replace(section, prose=None, citation_anchors=())
            if section.section_id in omitted_ids
            else section
            for section in draft.sections
        )
        return replace(draft, sections=sections, previous_version_id=draft.id)

    def _publication(
        self,
        draft: Any,
        decision_id: str,
        state: str,
        omitted: tuple[str, ...],
        has_prose: bool,
        compose_mode: str = "prose",
        *,
        withheld_section_ids: tuple[str, ...] = (),
    ) -> ReportPublication:
        kinds = tuple(section.section_kind for section in draft.sections if section.section_id not in omitted)
        ids = tuple(section.section_id for section in draft.sections if section.section_id not in omitted)
        return ReportPublication(
            workflow_run_id=draft.workflow_run_id, research_plan_id=draft.research_plan_id, governed_snapshot_id=draft.governed_snapshot_id,
            governed_snapshot_version=draft.governed_snapshot_version, input_fingerprint=draft.input_fingerprint,
            policy_version=draft.policy_version, algorithm_version=draft.algorithm_version, report_draft_id=draft.id,
            faithfulness_decision_id=decision_id, publication_state=state, verified_section_ids=ids,
            verified_section_kinds=kinds, structured_card_section_ids=tuple(section.section_id for section in draft.sections),
            audit_recovery_state=(
                "audit_rewrite_exhausted"
                if withheld_section_ids
                else "all_required_sections_passed"
            ),
            has_free_prose=has_prose, omitted_section_ids=omitted,
            track_publication_dispositions=_track_publication_dispositions(
                draft,
                omitted_section_ids=omitted,
                withheld_section_ids=withheld_section_ids,
            ),
            compose_mode=compose_mode,
            scope_contract_id=draft.scope_contract_id,
            execution_unit_id=draft.execution_unit_id,
            coverage_snapshot_id=draft.coverage_snapshot_id,
            attempt_no=draft.attempt_no,
        )

    @staticmethod
    def _publication_from_record(record: ReportPublicationRecord) -> ReportPublication:
        payload = record.payload
        return ReportPublication(
            workflow_run_id=record.workflow_run_id, research_plan_id=record.research_plan_id,
            governed_snapshot_id=record.governed_snapshot_id, governed_snapshot_version=record.governed_snapshot_version,
            input_fingerprint=record.input_fingerprint, policy_version=record.policy_version,
            algorithm_version=record.algorithm_version, report_draft_id=record.report_draft_id,
            faithfulness_decision_id=record.faithfulness_decision_id, publication_state=record.publication_state,
            verified_section_ids=tuple(payload["verified_section_ids"]),
            verified_section_kinds=tuple(payload["verified_section_kinds"]),
            structured_card_section_ids=tuple(payload["structured_card_section_ids"]),
            audit_recovery_state=str(payload["audit_recovery_state"]), has_free_prose=bool(payload["has_free_prose"]),
            omitted_section_ids=tuple(payload.get("omitted_section_ids") or ()), compose_mode=str(payload.get("compose_mode") or "prose"), previous_version_id=record.previous_version_id,
            track_publication_dispositions=tuple(
                (
                    str(item["track"]),
                    str(item["state"]),
                    str(item["reason_code"])
                    if item.get("reason_code") is not None
                    else None,
                )
                for item in payload.get("track_publication_dispositions") or ()
                if isinstance(item, dict)
            ),
            scope_contract_id=record.scope_contract_id,
            execution_unit_id=record.execution_unit_id,
            coverage_snapshot_id=record.coverage_snapshot_id,
            attempt_no=record.attempt_no,
            created_at=record.created_at,
        )

    def _checkpoint(
        self,
        snapshot: ResearchResultSnapshotRecord,
        stage: str,
        output_ref: str,
        attempt: int,
        started_at: datetime,
        finished_at: datetime,
    ) -> None:
        fingerprint = canonical_fingerprint({"snapshot": snapshot.id, "version": snapshot.snapshot_version, "stage": stage, "output": output_ref, "attempt": attempt})
        record_id = f"scp_{canonical_fingerprint({'run': snapshot.workflow_run_id, 'stage': stage, 'input': fingerprint})[:24]}"
        if self._store.get_typed_record(StageCheckpointRecord, record_id) is None:
            self._store.save_stage_checkpoint(StageCheckpointRecord(record_id, "content_research_stage_checkpoint_v1", {"output_refs": [output_ref]}, workflow_run_id=snapshot.workflow_run_id, subagent_task_id=f"report:{snapshot.research_plan_id}", stage_name=stage, input_fingerprint=fingerprint, status="completed", retry_count=attempt, started_at=started_at, finished_at=finished_at))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _marketing_conclusion_states(draft: ReportDraft) -> set[str | None]:
    return {
        section.conclusion_state
        for section in draft.sections
        if section.section_kind.startswith("marketing_")
    }


def _visible_marketing_states(
    draft: ReportDraft, omitted_section_ids: tuple[str, ...]
) -> set[str | None]:
    omitted = set(omitted_section_ids)
    return {
        section.conclusion_state
        for section in draft.sections
        if section.section_kind.startswith("marketing_")
        and section.section_id not in omitted
        and section.prose
    }


def _track_publication_dispositions(
    draft: ReportDraft,
    *,
    omitted_section_ids: tuple[str, ...],
    withheld_section_ids: tuple[str, ...],
) -> tuple[tuple[str, str, str | None], ...]:
    omitted = set(omitted_section_ids)
    withheld = set(withheld_section_ids)
    sections_by_track = {
        section.section_kind.removeprefix("marketing_"): section
        for section in draft.sections
        if section.section_kind.startswith("marketing_")
    }
    result: list[tuple[str, str, str | None]] = []
    for track in ("need", "value", "message"):
        section = sections_by_track.get(track)
        if section is None:
            continue
        if section.section_id in withheld:
            result.append(
                (track, "withheld_by_faithfulness", "faithfulness_not_verified")
            )
        elif section.section_id in omitted:
            result.append((track, "omitted_by_publication_policy", None))
        else:
            result.append((track, "published", None))
    return tuple(result)


def _has_directional(draft: ReportDraft) -> bool:
    return "directional" in _marketing_conclusion_states(draft)


def _has_selected(draft: ReportDraft) -> bool:
    return bool({"selected", "contested"} & _marketing_conclusion_states(draft))


def _completed_marketing_analysis_has_no_publishable_conclusion(
    snapshot: ResearchResultSnapshotRecord,
) -> bool:
    governed = snapshot.metadata.get("governed_snapshot")
    if not isinstance(governed, dict):
        return False
    conclusions = governed.get("marketing_conclusions")
    if not isinstance(conclusions, list) or len(conclusions) != 3:
        return False
    return all(
        isinstance(item, dict)
        and item.get("state") == "insufficient_evidence"
        for item in conclusions
    )


def _has_admitted_cited_evidence(snapshot: ResearchResultSnapshotRecord) -> bool:
    governed = snapshot.metadata.get("governed_snapshot")
    if not isinstance(governed, dict):
        return False
    admitted_ids = {
        str(claim.get("claim_candidate_id"))
        for claim in governed.get("claim_cards") or []
        if isinstance(claim, dict)
        and claim.get("admission_state") == "admitted"
        and str(claim.get("claim_candidate_id") or "")
    }
    if not admitted_ids:
        return False
    return any(
        isinstance(group, dict)
        and str(group.get("claim_candidate_id") or "") in admitted_ids
        and bool(group.get("evidence_refs"))
        for group in governed.get("citation_groups") or []
    )
