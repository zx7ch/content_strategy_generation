"""Workflow data models for durable Creator Workbench runs."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class WorkflowRunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    PAUSING = "pausing"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class WorkflowPhase(str, Enum):
    INTAKE = "intake"
    CONTEXT = "context"
    DISCOVERY = "discovery"
    RETRIEVAL = "retrieval"
    STRATEGY = "strategy"
    GENERATION = "generation"
    FINALIZATION = "finalization"
    REVIEW = "review"


class WorkflowStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    SKIPPED = "skipped"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowArtifactType(str, Enum):
    SOURCE_SNAPSHOT = "source_snapshot"
    RAG_INDEX = "rag_index"
    RAG_RESULT = "rag_result"
    STRATEGY = "strategy"
    PROPOSAL = "proposal"
    GENERATED_NOTE = "generated_note"
    SIMILARITY_REPORT = "similarity_report"
    FINAL_RESULT = "final_result"
    PUBLISH_CANDIDATE = "publish_candidate"


class WorkflowArtifactPayloadMode(str, Enum):
    SNAPSHOT = "snapshot"
    PATCH = "patch"


class WorkflowConstraintType(str, Enum):
    STYLE = "style"
    TOPIC_CHANGE = "topic_change"
    TARGET_AUDIENCE = "target_audience"
    FORMAT = "format"
    FORBIDDEN_WORDS = "forbidden_words"
    BRAND_POLICY = "brand_policy"
    QUANTITY_CHANGE = "quantity_change"
    ARTIFACT_REVISION = "artifact_revision"


class WorkflowRun(BaseModel):
    run_id: str
    thread_id: str
    user_id: str
    status: WorkflowRunStatus = WorkflowRunStatus.CREATED
    phase: WorkflowPhase = WorkflowPhase.INTAKE
    current_step: Optional[str] = None
    active_job_id: Optional[str] = None
    active_job_type: Optional[str] = None
    constraint_version: int = 0
    artifact_version: int = 0
    interrupt_policy: str = "safe_boundary"
    source_message_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class WorkflowStep(BaseModel):
    step_id: str
    run_id: str
    step_name: str
    phase: WorkflowPhase
    status: WorkflowStepStatus = WorkflowStepStatus.PENDING
    attempt_count: int = 0
    max_attempts: int = 3
    input_hash: Optional[str] = None
    checkpoint_json: Optional[dict[str, Any]] = None
    output_artifact_refs_json: Optional[list[dict[str, Any]]] = None
    active_job_id: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    next_retry_at: Optional[datetime] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WorkflowChildTask(BaseModel):
    child_task_id: str
    run_id: str
    step_id: str
    task_type: str
    slot_index: Optional[int] = None
    proposal_id: Optional[str] = None
    status: WorkflowStepStatus = WorkflowStepStatus.PENDING
    attempt_count: int = 0
    max_attempts: int = 3
    input_hash: Optional[str] = None
    checkpoint_json: Optional[dict[str, Any]] = None
    output_artifact_refs_json: Optional[list[dict[str, Any]]] = None
    note_id: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WorkflowEvent(BaseModel):
    event_id: int
    run_id: str
    thread_id: str
    step_id: Optional[str] = None
    child_task_id: Optional[str] = None
    job_id: Optional[str] = None
    event_type: str
    event_level: str = "info"
    payload_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WorkflowArtifact(BaseModel):
    artifact_id: str
    run_id: str
    thread_id: str
    artifact_type: WorkflowArtifactType
    artifact_version: int = 1
    parent_artifact_id: Optional[str] = None
    status: str = "created"
    payload_mode: WorkflowArtifactPayloadMode = WorkflowArtifactPayloadMode.SNAPSHOT
    storage_table: Optional[str] = None
    storage_key: Optional[str] = None
    payload_json: Optional[dict[str, Any]] = None
    summary_text: Optional[str] = None
    created_by_step_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WorkflowConstraint(BaseModel):
    constraint_id: str
    run_id: str
    thread_id: str
    message_id: str
    constraint_version: int
    raw_text: str
    constraint_type: WorkflowConstraintType
    scope: str
    target_artifact_id: Optional[str] = None
    effective_from_step: Optional[str] = None
    impact_level: str = "medium"
    status: str = "active"
    confidence: float = 1.0
    normalized_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    applied_at: Optional[datetime] = None
