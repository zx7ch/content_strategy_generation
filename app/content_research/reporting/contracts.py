"""Report-version contracts independent of composition and presentation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from app.content_research.models import utcnow

if TYPE_CHECKING:
    from app.content_research.persistence_models import (
        ReportDraftRecord,
        ReportFaithfulnessDecisionRecord,
        ReportPublicationRecord,
    )

REPORT_SECTION_KINDS = frozenset(
    {
        "core_conclusions",
        "main_findings",
        "cross_direction_tensions",
        "weak_signals",
        "next_steps",
        "limitations_scope",
        "marketing_need",
        "marketing_value",
        "marketing_message",
        "priority_action",
    }
)
PUBLICATION_STATES = frozenset(
    {"complete_verified_report", "partial_verified_report", "directional_report", "evidence_only_report"}
)
_CORE_SECTION_KINDS = frozenset({"core_conclusions", "main_findings", "limitations_scope"})


def _require(*values: str) -> None:
    if not all(values):
        raise ValueError("required report identity field is missing")


def _stable_id(prefix: str, value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:24]}"


def _json_compatible(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _unique_nonempty(values: tuple[str, ...], field_name: str) -> None:
    if any(not value for value in values):
        raise ValueError(f"{field_name} cannot contain an empty id")
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must be unique")


def _validate_report_lineage(
    scope_contract_id: str | None,
    execution_unit_id: str | None,
    coverage_snapshot_id: str | None,
    attempt_no: int | None,
) -> None:
    values = (scope_contract_id, execution_unit_id, coverage_snapshot_id, attempt_no)
    if all(value is None for value in values):
        return
    if any(value is None for value in values):
        raise ValueError("report execution lineage must be complete")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (scope_contract_id, execution_unit_id, coverage_snapshot_id)
    ) or not isinstance(attempt_no, int) or isinstance(attempt_no, bool) or attempt_no < 0:
        raise ValueError("report execution lineage is invalid")


@dataclass(frozen=True)
class CitationAnchor:
    """A persisted link from one stable prose block/span to a snapshot citation."""

    anchor_id: str
    section_id: str
    block_id: str
    text_start: int
    text_end: int
    citation_group_id: str

    def __post_init__(self) -> None:
        _require(self.anchor_id, self.section_id, self.block_id, self.citation_group_id)
        if self.text_start < 0 or self.text_end <= self.text_start:
            raise ValueError("citation anchor requires a non-empty valid text span")


@dataclass(frozen=True)
class ReportSection:
    section_id: str
    section_kind: str
    prose: str | None = None
    structured_card_ids: tuple[str, ...] = ()
    claim_candidate_ids: tuple[str, ...] = ()
    aggregate_claim_ids: tuple[str, ...] = ()
    cross_direction_record_ids: tuple[str, ...] = ()
    weak_signal_ids: tuple[str, ...] = ()
    limitation_ids: tuple[str, ...] = ()
    citation_group_ids: tuple[str, ...] = ()
    citation_anchors: tuple[CitationAnchor, ...] = ()
    marketing_conclusion_ids: tuple[str, ...] = ()
    conclusion_state: str | None = None
    reason_codes: tuple[str, ...] = ()
    supporting_note_count: int = 0
    independent_author_count: int = 0
    additional_qualified_count: int = 0
    verification_direction: str | None = None
    action_label: str | None = None
    action_statement: str | None = None
    primary_marketing_goal: str | None = None
    supporting_conclusion_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(self.section_id, self.section_kind)
        if self.section_kind not in REPORT_SECTION_KINDS:
            raise ValueError(f"unsupported report section kind: {self.section_kind}")
        if self.prose is not None and not self.prose.strip():
            raise ValueError("report prose cannot be blank")
        for field_name, values in (
            ("structured_card_ids", self.structured_card_ids),
            ("claim_candidate_ids", self.claim_candidate_ids),
            ("aggregate_claim_ids", self.aggregate_claim_ids),
            ("cross_direction_record_ids", self.cross_direction_record_ids),
            ("weak_signal_ids", self.weak_signal_ids),
            ("limitation_ids", self.limitation_ids),
            ("citation_group_ids", self.citation_group_ids),
            ("marketing_conclusion_ids", self.marketing_conclusion_ids),
            ("reason_codes", self.reason_codes),
            ("supporting_conclusion_ids", self.supporting_conclusion_ids),
        ):
            _unique_nonempty(values, field_name)
        if min(
            self.supporting_note_count,
            self.independent_author_count,
            self.additional_qualified_count,
        ) < 0:
            raise ValueError("marketing conclusion counts cannot be negative")
        if not self.reference_ids:
            raise ValueError("report section requires governed-snapshot references")
        if self.prose is not None and not self.citation_group_ids:
            raise ValueError("report prose requires citation-group references")
        if self.prose is not None and not self.citation_anchors:
            raise ValueError("report prose requires persisted citation anchors")
        anchor_ids = tuple(anchor.anchor_id for anchor in self.citation_anchors)
        _unique_nonempty(anchor_ids, "citation anchor ids")
        for anchor in self.citation_anchors:
            if anchor.section_id != self.section_id:
                raise ValueError("citation anchor section does not match report section")
            if anchor.citation_group_id not in self.citation_group_ids:
                raise ValueError("citation anchor must reference a declared citation group")
            if self.prose is None or anchor.text_end > len(self.prose):
                raise ValueError("citation anchor span is outside report prose")
        spans = sorted((anchor.text_start, anchor.text_end) for anchor in self.citation_anchors)
        for previous, current in zip(spans, spans[1:]):
            if previous[1] > current[0] and previous != current:
                raise ValueError("citation anchors cannot partially overlap")
        if self.section_kind == "cross_direction_tensions" and not self.cross_direction_record_ids:
            raise ValueError("cross-direction tensions require cross-direction records")
        if self.section_kind == "weak_signals" and not self.weak_signal_ids:
            raise ValueError("weak-signals section requires weak-signal references")
        if self.section_kind == "next_steps" and not (self.aggregate_claim_ids or self.limitation_ids):
            raise ValueError("next-steps section requires action or recovery references")
        if self.section_kind == "limitations_scope" and not self.limitation_ids:
            raise ValueError("limitations section requires limitation references")
        if self.section_kind in {"core_conclusions", "main_findings"} and not (
            self.claim_candidate_ids
            or self.aggregate_claim_ids
            or (self.prose is None and self.structured_card_ids)
        ):
            raise ValueError("conclusion and finding sections require claim references")
        if self.section_kind in {"marketing_need", "marketing_value", "marketing_message"}:
            expected_track = self.section_kind.removeprefix("marketing_")
            if not self.marketing_conclusion_ids or not self.conclusion_state:
                raise ValueError("marketing conclusion section requires a governed decision")
            if self.conclusion_state in {"selected", "directional"}:
                if not self.claim_candidate_ids or not self.citation_group_ids:
                    raise ValueError("supported marketing conclusion requires governed support")
                if self.conclusion_state == "selected" and (self.verification_direction or self.reason_codes):
                    raise ValueError("selected marketing conclusion cannot carry failure guidance")
                if self.conclusion_state == "directional" and not self.verification_direction:
                    raise ValueError("directional marketing conclusion requires verification guidance")
            elif self.conclusion_state in {
                "insufficient_evidence",
                "no_single_primary_conclusion",
                "analysis_unavailable",
            }:
                if self.prose is not None or not self.verification_direction:
                    raise ValueError("non-selected marketing conclusion cannot carry prose")
            else:
                raise ValueError(
                    f"unsupported {expected_track} marketing conclusion state"
                )
        if self.section_kind == "priority_action":
            if (
                self.action_label != "建议"
                or not self.action_statement
                or not self.primary_marketing_goal
            ):
                raise ValueError("priority action requires a goal-aware 建议")
            if self.prose is not None:
                raise ValueError("priority action must remain a structured recommendation")

    @property
    def reference_ids(self) -> tuple[str, ...]:
        return (
            self.structured_card_ids
            + self.claim_candidate_ids
            + self.aggregate_claim_ids
            + self.cross_direction_record_ids
            + self.weak_signal_ids
            + self.limitation_ids
            + self.citation_group_ids
            + self.marketing_conclusion_ids
            + self.supporting_conclusion_ids
        )


@dataclass(frozen=True)
class ReportDraft:
    workflow_run_id: str
    research_plan_id: str
    governed_snapshot_id: str
    governed_snapshot_version: str
    input_fingerprint: str
    policy_version: str
    algorithm_version: str
    sections: tuple[ReportSection, ...]
    previous_version_id: str | None = None
    scope_contract_id: str | None = None
    execution_unit_id: str | None = None
    coverage_snapshot_id: str | None = None
    attempt_no: int | None = None
    created_at: datetime = field(default_factory=utcnow, compare=False)

    def __post_init__(self) -> None:
        _require(
            self.workflow_run_id,
            self.research_plan_id,
            self.governed_snapshot_id,
            self.governed_snapshot_version,
            self.input_fingerprint,
            self.policy_version,
            self.algorithm_version,
        )
        if not self.sections:
            raise ValueError("report draft requires at least one section")
        section_ids = tuple(section.section_id for section in self.sections)
        _unique_nonempty(section_ids, "report section ids")
        if self.previous_version_id == self.id:
            raise ValueError("report draft cannot point to itself as a predecessor")
        _validate_report_lineage(
            self.scope_contract_id,
            self.execution_unit_id,
            self.coverage_snapshot_id,
            self.attempt_no,
        )

    @property
    def id(self) -> str:
        return _stable_id("rpd", self.identity_payload)

    @property
    def identity_payload(self) -> dict[str, object]:
        return {
            "workflow_run_id": self.workflow_run_id,
            "research_plan_id": self.research_plan_id,
            "governed_snapshot_id": self.governed_snapshot_id,
            "governed_snapshot_version": self.governed_snapshot_version,
            "input_fingerprint": self.input_fingerprint,
            "policy_version": self.policy_version,
            "algorithm_version": self.algorithm_version,
            "scope_contract_id": self.scope_contract_id,
            "execution_unit_id": self.execution_unit_id,
            "coverage_snapshot_id": self.coverage_snapshot_id,
            "attempt_no": self.attempt_no,
            "previous_version_id": self.previous_version_id,
            "sections": [asdict(section) for section in self.sections],
        }

    def to_record(self) -> ReportDraftRecord:
        from app.content_research.persistence_models import ReportDraftRecord

        return ReportDraftRecord(
            id=self.id,
            schema_version="content_research_report_draft_v1",
            payload={"sections": _json_compatible([asdict(section) for section in self.sections])},
            workflow_run_id=self.workflow_run_id,
            research_plan_id=self.research_plan_id,
            governed_snapshot_id=self.governed_snapshot_id,
            governed_snapshot_version=self.governed_snapshot_version,
            input_fingerprint=self.input_fingerprint,
            policy_version=self.policy_version,
            algorithm_version=self.algorithm_version,
            scope_contract_id=self.scope_contract_id,
            execution_unit_id=self.execution_unit_id,
            coverage_snapshot_id=self.coverage_snapshot_id,
            attempt_no=self.attempt_no,
            previous_version_id=self.previous_version_id,
            created_at=self.created_at,
        )


@dataclass(frozen=True)
class ReportFaithfulnessDecision:
    workflow_run_id: str
    research_plan_id: str
    governed_snapshot_id: str
    governed_snapshot_version: str
    input_fingerprint: str
    policy_version: str
    algorithm_version: str
    report_draft_id: str
    audit_state: str
    reason_codes: tuple[str, ...] = ()
    omitted_section_ids: tuple[str, ...] = ()
    semantic_audit: dict[str, object] = field(default_factory=dict)
    previous_version_id: str | None = None
    scope_contract_id: str | None = None
    execution_unit_id: str | None = None
    coverage_snapshot_id: str | None = None
    attempt_no: int | None = None
    created_at: datetime = field(default_factory=utcnow, compare=False)

    def __post_init__(self) -> None:
        _require(
            self.workflow_run_id,
            self.research_plan_id,
            self.governed_snapshot_id,
            self.governed_snapshot_version,
            self.input_fingerprint,
            self.policy_version,
            self.algorithm_version,
            self.report_draft_id,
            self.audit_state,
        )
        if self.audit_state not in {"passed", "failed", "unavailable", "pending"}:
            raise ValueError("invalid report faithfulness audit state")
        _unique_nonempty(self.reason_codes, "audit reason codes")
        _unique_nonempty(self.omitted_section_ids, "omitted section ids")
        _validate_report_lineage(
            self.scope_contract_id,
            self.execution_unit_id,
            self.coverage_snapshot_id,
            self.attempt_no,
        )

    @property
    def id(self) -> str:
        return _stable_id("rfd", self.identity_payload)

    @property
    def identity_payload(self) -> dict[str, object]:
        return {
            "workflow_run_id": self.workflow_run_id,
            "research_plan_id": self.research_plan_id,
            "governed_snapshot_id": self.governed_snapshot_id,
            "governed_snapshot_version": self.governed_snapshot_version,
            "input_fingerprint": self.input_fingerprint,
            "policy_version": self.policy_version,
            "algorithm_version": self.algorithm_version,
            "report_draft_id": self.report_draft_id,
            "audit_state": self.audit_state,
            "reason_codes": self.reason_codes,
            "omitted_section_ids": self.omitted_section_ids,
            "semantic_audit": _json_compatible(self.semantic_audit),
            "scope_contract_id": self.scope_contract_id,
            "execution_unit_id": self.execution_unit_id,
            "coverage_snapshot_id": self.coverage_snapshot_id,
            "attempt_no": self.attempt_no,
            "previous_version_id": self.previous_version_id,
        }

    def to_record(self) -> ReportFaithfulnessDecisionRecord:
        from app.content_research.persistence_models import ReportFaithfulnessDecisionRecord

        return ReportFaithfulnessDecisionRecord(
            id=self.id,
            schema_version="content_research_report_faithfulness_v1",
            payload={
                "audit_state": self.audit_state,
                "reason_codes": list(self.reason_codes),
                "omitted_section_ids": list(self.omitted_section_ids),
                "semantic_audit": _json_compatible(self.semantic_audit),
            },
            workflow_run_id=self.workflow_run_id,
            research_plan_id=self.research_plan_id,
            governed_snapshot_id=self.governed_snapshot_id,
            governed_snapshot_version=self.governed_snapshot_version,
            input_fingerprint=self.input_fingerprint,
            policy_version=self.policy_version,
            algorithm_version=self.algorithm_version,
            report_draft_id=self.report_draft_id,
            scope_contract_id=self.scope_contract_id,
            execution_unit_id=self.execution_unit_id,
            coverage_snapshot_id=self.coverage_snapshot_id,
            attempt_no=self.attempt_no,
            previous_version_id=self.previous_version_id,
            created_at=self.created_at,
        )


@dataclass(frozen=True)
class ReportPublication:
    workflow_run_id: str
    research_plan_id: str
    governed_snapshot_id: str
    governed_snapshot_version: str
    input_fingerprint: str
    policy_version: str
    algorithm_version: str
    report_draft_id: str
    faithfulness_decision_id: str
    publication_state: str
    verified_section_ids: tuple[str, ...]
    verified_section_kinds: tuple[str, ...]
    structured_card_section_ids: tuple[str, ...]
    audit_recovery_state: str
    has_free_prose: bool
    compose_mode: str = "prose"
    artifact_kind: str = "snapshot"
    timeline_message_type: str = "artifact_result"
    final_message_count: int = 1
    omitted_section_ids: tuple[str, ...] = ()
    previous_version_id: str | None = None
    scope_contract_id: str | None = None
    execution_unit_id: str | None = None
    coverage_snapshot_id: str | None = None
    attempt_no: int | None = None
    created_at: datetime = field(default_factory=utcnow, compare=False)

    def __post_init__(self) -> None:
        _require(
            self.workflow_run_id,
            self.research_plan_id,
            self.governed_snapshot_id,
            self.governed_snapshot_version,
            self.input_fingerprint,
            self.policy_version,
            self.algorithm_version,
            self.report_draft_id,
            self.faithfulness_decision_id,
            self.publication_state,
            self.audit_recovery_state,
        )
        if self.publication_state not in PUBLICATION_STATES:
            raise ValueError("invalid report publication state")
        if self.compose_mode not in {"prose", "template_only"}:
            raise ValueError("invalid report compose mode")
        if self.artifact_kind != "snapshot":
            raise ValueError("published report artifact must be a materialized snapshot")
        if self.timeline_message_type != "artifact_result" or self.final_message_count != 1:
            raise ValueError("published report requires exactly one artifact-result message")
        for field_name, values in (
            ("verified section ids", self.verified_section_ids),
            ("verified section kinds", self.verified_section_kinds),
            ("structured-card section ids", self.structured_card_section_ids),
            ("omitted section ids", self.omitted_section_ids),
        ):
            _unique_nonempty(values, field_name)
        if self.publication_state == "complete_verified_report":
            required_kinds = {"main_findings", "limitations_scope"} if self.compose_mode == "template_only" else _CORE_SECTION_KINDS
            if not required_kinds <= set(self.verified_section_kinds):
                raise ValueError("complete report requires all core audited sections")
            if self.omitted_section_ids:
                raise ValueError("complete report cannot omit prose sections")
            if self.compose_mode == "prose" and not self.has_free_prose:
                raise ValueError("complete report requires audited prose")
        elif self.publication_state in {"partial_verified_report", "directional_report"}:
            if not self.structured_card_section_ids:
                raise ValueError("partial or directional report requires structured cards")
            if not self.omitted_section_ids:
                raise ValueError("partial or directional report requires omitted prose sections")
        elif self.has_free_prose:
            raise ValueError("evidence-only report cannot contain free prose")
        _validate_report_lineage(
            self.scope_contract_id,
            self.execution_unit_id,
            self.coverage_snapshot_id,
            self.attempt_no,
        )

    @property
    def id(self) -> str:
        return _stable_id("rpp", self.identity_payload)

    @property
    def identity_payload(self) -> dict[str, object]:
        return {
            "workflow_run_id": self.workflow_run_id,
            "research_plan_id": self.research_plan_id,
            "governed_snapshot_id": self.governed_snapshot_id,
            "governed_snapshot_version": self.governed_snapshot_version,
            "input_fingerprint": self.input_fingerprint,
            "policy_version": self.policy_version,
            "algorithm_version": self.algorithm_version,
            "report_draft_id": self.report_draft_id,
            "faithfulness_decision_id": self.faithfulness_decision_id,
            "publication_state": self.publication_state,
            "verified_section_ids": self.verified_section_ids,
            "verified_section_kinds": self.verified_section_kinds,
            "structured_card_section_ids": self.structured_card_section_ids,
            "audit_recovery_state": self.audit_recovery_state,
            "has_free_prose": self.has_free_prose,
            "compose_mode": self.compose_mode,
            "artifact_kind": self.artifact_kind,
            "timeline_message_type": self.timeline_message_type,
            "final_message_count": self.final_message_count,
            "omitted_section_ids": self.omitted_section_ids,
            "scope_contract_id": self.scope_contract_id,
            "execution_unit_id": self.execution_unit_id,
            "coverage_snapshot_id": self.coverage_snapshot_id,
            "attempt_no": self.attempt_no,
            "previous_version_id": self.previous_version_id,
        }

    def to_record(self) -> ReportPublicationRecord:
        from app.content_research.persistence_models import ReportPublicationRecord

        return ReportPublicationRecord(
            id=self.id,
            schema_version="content_research_report_publication_v1",
            payload={
                "verified_section_ids": list(self.verified_section_ids),
                "verified_section_kinds": list(self.verified_section_kinds),
                "structured_card_section_ids": list(self.structured_card_section_ids),
                "audit_recovery_state": self.audit_recovery_state,
                "has_free_prose": self.has_free_prose,
                "compose_mode": self.compose_mode,
                "artifact_kind": self.artifact_kind,
                "timeline_message_type": self.timeline_message_type,
                "final_message_count": self.final_message_count,
                "omitted_section_ids": list(self.omitted_section_ids),
            },
            workflow_run_id=self.workflow_run_id,
            research_plan_id=self.research_plan_id,
            governed_snapshot_id=self.governed_snapshot_id,
            governed_snapshot_version=self.governed_snapshot_version,
            input_fingerprint=self.input_fingerprint,
            policy_version=self.policy_version,
            algorithm_version=self.algorithm_version,
            report_draft_id=self.report_draft_id,
            faithfulness_decision_id=self.faithfulness_decision_id,
            publication_state=self.publication_state,
            scope_contract_id=self.scope_contract_id,
            execution_unit_id=self.execution_unit_id,
            coverage_snapshot_id=self.coverage_snapshot_id,
            attempt_no=self.attempt_no,
            previous_version_id=self.previous_version_id,
            created_at=self.created_at,
        )
