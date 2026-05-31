"""Step definitions and structured context assembly for workflow steps."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from app.config import settings
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import (
    WorkflowArtifact,
    WorkflowArtifactType,
    WorkflowConstraint,
    WorkflowConstraintType,
    WorkflowPhase,
    WorkflowRun,
    WorkflowStep,
)


@dataclass(frozen=True, slots=True)
class StepDefinition:
    step_name: str
    phase: WorkflowPhase
    required_context: tuple[str, ...]
    constraint_types: tuple[WorkflowConstraintType, ...] = ()
    artifact_types: tuple[WorkflowArtifactType, ...] = ()
    interrupt_policy: str = "step_boundary"
    retry_policy: dict[str, Any] = field(default_factory=lambda: {"max_attempts": 3})
    output_requirements: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StepContext:
    run: dict[str, Any]
    step: dict[str, Any]
    definition: dict[str, Any]
    user_request: Optional[str]
    brand_context: dict[str, Any]
    constraints: list[dict[str, Any]]
    pending_constraints: list[dict[str, Any]]
    relevant_messages: list[dict[str, Any]]
    input_artifacts: list[dict[str, Any]]
    prior_artifacts: list[dict[str, Any]]
    source_context: dict[str, Any]
    rag_context: dict[str, Any]
    generation_targets: list[dict[str, Any]]
    revision_targets: list[dict[str, Any]]
    output_requirements: dict[str, Any]
    input_versions: dict[str, Any]
    input_hash: str

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


def _definition(
    step_name: str,
    phase: WorkflowPhase,
    required_context: tuple[str, ...],
    *,
    constraint_types: tuple[WorkflowConstraintType, ...] = (),
    artifact_types: tuple[WorkflowArtifactType, ...] = (),
    interrupt_policy: str = "step_boundary",
    output_requirements: Optional[dict[str, Any]] = None,
) -> StepDefinition:
    return StepDefinition(
        step_name=step_name,
        phase=phase,
        required_context=required_context,
        constraint_types=constraint_types,
        artifact_types=artifact_types,
        interrupt_policy=interrupt_policy,
        output_requirements=output_requirements or {},
    )


BROAD_CONSTRAINTS = tuple(WorkflowConstraintType)
GENERATION_CONSTRAINTS = (
    WorkflowConstraintType.STYLE,
    WorkflowConstraintType.FORBIDDEN_WORDS,
    WorkflowConstraintType.FORMAT,
    WorkflowConstraintType.TARGET_AUDIENCE,
    WorkflowConstraintType.QUANTITY_CHANGE,
)


STEP_DEFINITIONS: dict[str, StepDefinition] = {
    "intake.capture_request": _definition(
        "intake.capture_request",
        WorkflowPhase.INTAKE,
        ("user_request", "relevant_messages"),
        interrupt_policy="immediate",
    ),
    "context.build_context": _definition(
        "context.build_context",
        WorkflowPhase.CONTEXT,
        ("user_request", "constraints", "prior_artifacts"),
        constraint_types=BROAD_CONSTRAINTS,
    ),
    "context.load_constraints": _definition(
        "context.load_constraints",
        WorkflowPhase.CONTEXT,
        ("constraints",),
        constraint_types=BROAD_CONSTRAINTS,
        interrupt_policy="immediate",
    ),
    "context.load_previous_artifacts": _definition(
        "context.load_previous_artifacts",
        WorkflowPhase.CONTEXT,
        ("prior_artifacts",),
        artifact_types=tuple(WorkflowArtifactType),
        interrupt_policy="immediate",
    ),
    "discovery.plan_queries": _definition(
        "discovery.plan_queries",
        WorkflowPhase.DISCOVERY,
        ("user_request", "brand_context", "constraints"),
        constraint_types=BROAD_CONSTRAINTS,
    ),
    "discovery.spider_search": _definition(
        "discovery.spider_search",
        WorkflowPhase.DISCOVERY,
        ("source_context",),
        interrupt_policy="cooperative",
    ),
    "discovery.assess_source_quality": _definition(
        "discovery.assess_source_quality",
        WorkflowPhase.DISCOVERY,
        ("source_context",),
    ),
    "discovery.expand_queries": _definition(
        "discovery.expand_queries",
        WorkflowPhase.DISCOVERY,
        ("user_request", "constraints", "source_context"),
        constraint_types=BROAD_CONSTRAINTS,
    ),
    "discovery.persist_sources": _definition(
        "discovery.persist_sources",
        WorkflowPhase.DISCOVERY,
        ("source_context",),
        artifact_types=(WorkflowArtifactType.SOURCE_SNAPSHOT,),
    ),
    "retrieval.rag_index": _definition(
        "retrieval.rag_index",
        WorkflowPhase.RETRIEVAL,
        ("input_artifacts",),
        artifact_types=(WorkflowArtifactType.SOURCE_SNAPSHOT,),
    ),
    "retrieval.rag_retrieve": _definition(
        "retrieval.rag_retrieve",
        WorkflowPhase.RETRIEVAL,
        ("source_context", "rag_context"),
        artifact_types=(WorkflowArtifactType.RAG_INDEX, WorkflowArtifactType.RAG_RESULT),
    ),
    "strategy.prepare_prompt": _definition(
        "strategy.prepare_prompt",
        WorkflowPhase.STRATEGY,
        ("user_request", "constraints", "source_context", "rag_context"),
        constraint_types=BROAD_CONSTRAINTS,
        artifact_types=(WorkflowArtifactType.RAG_RESULT, WorkflowArtifactType.SOURCE_SNAPSHOT),
    ),
    "strategy.llm_synthesize": _definition(
        "strategy.llm_synthesize",
        WorkflowPhase.STRATEGY,
        ("user_request", "constraints", "source_context", "rag_context"),
        constraint_types=BROAD_CONSTRAINTS,
        artifact_types=(WorkflowArtifactType.RAG_RESULT, WorkflowArtifactType.SOURCE_SNAPSHOT),
        interrupt_policy="step_boundary",
    ),
    "strategy.validate_strategy": _definition(
        "strategy.validate_strategy",
        WorkflowPhase.STRATEGY,
        ("input_artifacts", "constraints"),
        constraint_types=BROAD_CONSTRAINTS,
        artifact_types=(WorkflowArtifactType.STRATEGY,),
    ),
    "strategy.persist_strategy": _definition(
        "strategy.persist_strategy",
        WorkflowPhase.STRATEGY,
        ("input_artifacts",),
        artifact_types=(WorkflowArtifactType.STRATEGY,),
    ),
    "generation.plan_proposals": _definition(
        "generation.plan_proposals",
        WorkflowPhase.GENERATION,
        ("input_artifacts", "constraints"),
        constraint_types=GENERATION_CONSTRAINTS,
        artifact_types=(WorkflowArtifactType.STRATEGY,),
    ),
    "generation.select_proposals": _definition(
        "generation.select_proposals",
        WorkflowPhase.GENERATION,
        ("input_artifacts", "constraints"),
        constraint_types=GENERATION_CONSTRAINTS,
        artifact_types=(WorkflowArtifactType.STRATEGY, WorkflowArtifactType.PROPOSAL),
    ),
    "generation.generate_notes_parallel": _definition(
        "generation.generate_notes_parallel",
        WorkflowPhase.GENERATION,
        ("input_artifacts", "constraints", "generation_targets"),
        constraint_types=GENERATION_CONSTRAINTS,
        artifact_types=(WorkflowArtifactType.STRATEGY, WorkflowArtifactType.PROPOSAL),
        interrupt_policy="cooperative",
    ),
    "generation.similarity_check": _definition(
        "generation.similarity_check",
        WorkflowPhase.GENERATION,
        ("input_artifacts", "source_context", "rag_context"),
        artifact_types=(
            WorkflowArtifactType.GENERATED_NOTE,
            WorkflowArtifactType.SOURCE_SNAPSHOT,
            WorkflowArtifactType.RAG_RESULT,
        ),
    ),
    "generation.rewrite_or_reselect": _definition(
        "generation.rewrite_or_reselect",
        WorkflowPhase.GENERATION,
        ("input_artifacts", "constraints", "revision_targets"),
        constraint_types=GENERATION_CONSTRAINTS,
        artifact_types=(WorkflowArtifactType.GENERATED_NOTE, WorkflowArtifactType.SIMILARITY_REPORT),
    ),
    "generation.aggregate_notes": _definition(
        "generation.aggregate_notes",
        WorkflowPhase.GENERATION,
        ("input_artifacts",),
        artifact_types=(WorkflowArtifactType.GENERATED_NOTE,),
    ),
    "finalization.persist_artifacts": _definition(
        "finalization.persist_artifacts",
        WorkflowPhase.FINALIZATION,
        ("input_artifacts",),
        artifact_types=(WorkflowArtifactType.GENERATED_NOTE, WorkflowArtifactType.FINAL_RESULT),
        interrupt_policy="non_interruptible",
    ),
    "finalization.emit_result_ready": _definition(
        "finalization.emit_result_ready",
        WorkflowPhase.FINALIZATION,
        ("input_artifacts",),
        artifact_types=(WorkflowArtifactType.FINAL_RESULT,),
        interrupt_policy="non_interruptible",
    ),
    "review.await_user_acceptance": _definition(
        "review.await_user_acceptance",
        WorkflowPhase.REVIEW,
        ("input_artifacts", "relevant_messages"),
        artifact_types=(WorkflowArtifactType.FINAL_RESULT, WorkflowArtifactType.GENERATED_NOTE),
        interrupt_policy="immediate",
    ),
    "review.publish_candidates": _definition(
        "review.publish_candidates",
        WorkflowPhase.REVIEW,
        ("input_artifacts",),
        artifact_types=(WorkflowArtifactType.FINAL_RESULT, WorkflowArtifactType.PUBLISH_CANDIDATE),
        interrupt_policy="immediate",
    ),
}


class ContextBuilder:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or settings.SQLITE_DB_PATH

    async def build_context(self, run_id: str, step_name: str) -> StepContext:
        definition = self._get_definition(step_name)
        async with WorkflowStore(self.db_path) as store:
            run = await store.get_run(run_id)
            if run is None:
                raise ValueError(f"Workflow run not found: {run_id}")
            step = await self._resolve_step(store, run_id, step_name)
            messages = await self._load_messages(run.thread_id)
            constraints = await store.list_constraints(run_id)
            artifacts = await store.list_artifacts(run_id)

            selected_constraints = self._filter_constraints(constraints, definition)
            input_artifacts = self._filter_artifacts(artifacts, definition)
            context = self._assemble_context(
                run=run,
                step=step,
                definition=definition,
                messages=messages,
                constraints=selected_constraints,
                all_constraints=constraints,
                artifacts=artifacts,
                input_artifacts=input_artifacts,
            )
            await self._persist_input_hash(store, step.step_id, context.input_hash)
            return context

    @staticmethod
    def _get_definition(step_name: str) -> StepDefinition:
        try:
            return STEP_DEFINITIONS[step_name]
        except KeyError as exc:
            raise ValueError(f"Unknown workflow step: {step_name}") from exc

    @staticmethod
    async def _resolve_step(store: WorkflowStore, run_id: str, step_name: str) -> WorkflowStep:
        steps = await store.list_steps(run_id)
        for step in steps:
            if step.step_name == step_name:
                return step
        raise ValueError(f"Workflow step not found: {step_name}")

    @staticmethod
    async def _load_messages(thread_id: str) -> list[dict[str, Any]]:
        async with ThreadStore() as ts:
            return await ts.get_thread_messages(thread_id)

    @staticmethod
    def _filter_constraints(
        constraints: list[WorkflowConstraint], definition: StepDefinition
    ) -> list[WorkflowConstraint]:
        if not definition.constraint_types:
            return []
        allowed = {item.value for item in definition.constraint_types}
        return [
            constraint
            for constraint in constraints
            if constraint.status == "active" and constraint.constraint_type.value in allowed
        ]

    @staticmethod
    def _filter_artifacts(
        artifacts: list[WorkflowArtifact], definition: StepDefinition
    ) -> list[WorkflowArtifact]:
        if not definition.artifact_types:
            return []
        allowed = {item.value for item in definition.artifact_types}
        return [
            artifact
            for artifact in artifacts
            if artifact.status in {"created", "active", "accepted"}
            and artifact.artifact_type.value in allowed
        ]

    def _assemble_context(
        self,
        *,
        run: WorkflowRun,
        step: WorkflowStep,
        definition: StepDefinition,
        messages: list[dict[str, Any]],
        constraints: list[WorkflowConstraint],
        all_constraints: list[WorkflowConstraint],
        artifacts: list[WorkflowArtifact],
        input_artifacts: list[WorkflowArtifact],
    ) -> StepContext:
        user_request = self._resolve_user_request(run, messages)
        relevant_messages = self._filter_messages(messages, definition)
        source_artifacts = [
            artifact for artifact in input_artifacts if artifact.artifact_type == WorkflowArtifactType.SOURCE_SNAPSHOT
        ]
        rag_artifacts = [
            artifact for artifact in input_artifacts if artifact.artifact_type == WorkflowArtifactType.RAG_RESULT
        ]
        proposal_artifacts = [
            artifact for artifact in input_artifacts if artifact.artifact_type == WorkflowArtifactType.PROPOSAL
        ]
        generated_artifacts = [
            artifact for artifact in input_artifacts if artifact.artifact_type == WorkflowArtifactType.GENERATED_NOTE
        ]
        run_payload = run.model_dump(mode="json")
        step_payload = step.model_dump(mode="json")
        # input_hash/updated_at are written by the builder itself, so excluding
        # them keeps repeated builds of identical logical input stable.
        step_payload["input_hash"] = None
        step_payload["updated_at"] = None
        selected_constraint_ids = {constraint.constraint_id for constraint in constraints}
        payload = {
            "run": run_payload,
            "step": step_payload,
            "definition": self._definition_dict(definition),
            "user_request": user_request,
            "brand_context": {},
            "constraints": [constraint.model_dump(mode="json") for constraint in constraints],
            "pending_constraints": [
                constraint.model_dump(mode="json")
                for constraint in all_constraints
                if constraint.status == "active" and constraint.constraint_id not in selected_constraint_ids
            ],
            "relevant_messages": relevant_messages,
            "input_artifacts": [artifact.model_dump(mode="json") for artifact in input_artifacts],
            "prior_artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
            "source_context": {"artifacts": [artifact.model_dump(mode="json") for artifact in source_artifacts]},
            "rag_context": {"artifacts": [artifact.model_dump(mode="json") for artifact in rag_artifacts]},
            "generation_targets": [artifact.model_dump(mode="json") for artifact in proposal_artifacts],
            "revision_targets": [artifact.model_dump(mode="json") for artifact in generated_artifacts],
            "output_requirements": definition.output_requirements,
            "input_versions": {
                "constraint_version": run.constraint_version,
                "artifact_version": run.artifact_version,
                "constraint_ids": [constraint.constraint_id for constraint in constraints],
                "artifact_ids": [artifact.artifact_id for artifact in input_artifacts],
                "message_ids": [message["id"] for message in relevant_messages],
            },
        }
        payload["input_hash"] = self._stable_hash(payload)
        return StepContext(**payload)

    @staticmethod
    def _resolve_user_request(run: WorkflowRun, messages: list[dict[str, Any]]) -> Optional[str]:
        for message in messages:
            if message["id"] == run.source_message_id:
                return message["text"]
        for message in messages:
            if message["role"] == "user":
                return message["text"]
        return None

    @staticmethod
    def _filter_messages(messages: list[dict[str, Any]], definition: StepDefinition) -> list[dict[str, Any]]:
        if "relevant_messages" not in definition.required_context and "user_request" not in definition.required_context:
            return []
        return [
            {
                "id": message["id"],
                "role": message["role"],
                "text": message["text"],
                "intent": message["intent"],
                "created_at": message["created_at"],
            }
            for message in messages
            if message["role"] == "user"
        ]

    @staticmethod
    def _definition_dict(definition: StepDefinition) -> dict[str, Any]:
        return {
            "step_name": definition.step_name,
            "phase": definition.phase.value,
            "required_context": list(definition.required_context),
            "constraint_types": [item.value for item in definition.constraint_types],
            "artifact_types": [item.value for item in definition.artifact_types],
            "interrupt_policy": definition.interrupt_policy,
            "retry_policy": definition.retry_policy,
            "output_requirements": definition.output_requirements,
        }

    @staticmethod
    def _stable_hash(payload: dict[str, Any]) -> str:
        hash_payload = {key: value for key, value in payload.items() if key != "input_hash"}
        encoded = json.dumps(hash_payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    async def _persist_input_hash(store: WorkflowStore, step_id: str, input_hash: str) -> None:
        assert store._conn is not None
        await store._conn.execute(
            "UPDATE workflow_steps SET input_hash=?, updated_at=CURRENT_TIMESTAMP WHERE step_id=?",
            (input_hash, step_id),
        )
        await store._conn.commit()
