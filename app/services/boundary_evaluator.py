"""Boundary decisions for workflow pause/cancel/constraint handling."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.models.workflow import WorkflowConstraintType, WorkflowPhase, WorkflowRunStatus
from app.services.context_builder import StepContext

BoundaryStage = Literal[
    "before_step_start",
    "after_external_call_return",
    "before_artifact_commit",
    "after_step_complete",
    "before_next_step",
]

BoundaryAction = Literal["commit", "pause", "cancel", "rerun_step", "apply_downstream"]


@dataclass(frozen=True, slots=True)
class BoundaryDecision:
    action: BoundaryAction
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


class BoundaryEvaluator:
    """Pure decision layer; WorkflowRunManager performs the actual transition."""

    def evaluate(self, context: StepContext, *, boundary: BoundaryStage) -> BoundaryDecision:
        run_status = context.run["status"]
        if boundary == "before_artifact_commit" and run_status in {
            WorkflowRunStatus.CANCELLING.value,
            WorkflowRunStatus.CANCELLED.value,
        }:
            return BoundaryDecision(
                action="cancel",
                reason="commit_guard_cancel",
                details={"run_status": run_status, "step_name": context.step["step_name"]},
            )

        if run_status == WorkflowRunStatus.CANCELLING.value:
            return BoundaryDecision(
                action="cancel",
                reason="run_cancelling",
                details={"run_status": run_status},
            )
        if run_status == WorkflowRunStatus.PAUSING.value and boundary in {
            "after_external_call_return",
            "after_step_complete",
            "before_next_step",
        }:
            return BoundaryDecision(
                action="pause",
                reason="safe_boundary_reached",
                details={"boundary": boundary},
            )

        topic_change = self._find_constraint(context, WorkflowConstraintType.TOPIC_CHANGE)
        if topic_change is not None and context.run["phase"] in {
            WorkflowPhase.STRATEGY.value,
            WorkflowPhase.GENERATION.value,
            WorkflowPhase.FINALIZATION.value,
            WorkflowPhase.REVIEW.value,
        }:
            return BoundaryDecision(
                action="rerun_step",
                reason="topic_change_requires_replan",
                details={
                    "constraint_id": topic_change["constraint_id"],
                    "current_phase": context.run["phase"],
                },
            )

        if self._has_generation_style_constraint(context):
            return BoundaryDecision(
                action="apply_downstream",
                reason="style_constraint_applies_to_generation",
                details={"step_name": context.step["step_name"]},
            )

        return BoundaryDecision(action="commit", reason="no_boundary_intervention")

    @staticmethod
    def _find_constraint(
        context: StepContext, constraint_type: WorkflowConstraintType
    ) -> dict[str, Any] | None:
        for constraint in [*context.constraints, *context.pending_constraints]:
            if constraint["constraint_type"] == constraint_type.value:
                return constraint
        return None

    @staticmethod
    def _has_generation_style_constraint(context: StepContext) -> bool:
        if not str(context.step["step_name"]).startswith("generation."):
            return False
        return any(
            constraint["constraint_type"]
            in {WorkflowConstraintType.STYLE.value, WorkflowConstraintType.FORBIDDEN_WORDS.value}
            for constraint in context.constraints
        )
