from __future__ import annotations

from typing import Literal, TypedDict

from app.models.session import Session, SessionStage


StageValue = Literal["init", "strategy", "generation", "completed", "failed"]
LifecycleValue = Literal["alive", "frozen", "purged"]


class AgentState(TypedDict, total=False):
    session_id: str
    stage: StageValue
    lifecycle_state: LifecycleValue
    user_query: str
    spider_note_ids: list[str]
    strategy_id: str | None
    proposal_ids: list[str]
    generated_note_ids: list[str]
    quality_score: float
    used_fallback: bool
    error_code: str | None
    error_message: str | None


class ContentStrategyState(TypedDict, total=False):
    query: str
    brand_preference: str | None
    iteration: int
    max_iterations: int
    stop_collect: bool
    sample_count: int
    quality_score: float
    degraded: bool


def project_session_to_state(session: Session) -> AgentState:
    state: AgentState = {
        "session_id": session.session_id,
        "stage": session.stage.value,
        "lifecycle_state": session.lifecycle_state.value,
        "user_query": session.user_query,
        "quality_score": float(session.quality_score or 0.0),
        "used_fallback": bool(session.used_fallback),
        "spider_note_ids": list(session.spider_note_ids or []),
        "strategy_id": session.strategy_id,
        "proposal_ids": list(session.proposal_ids or []),
        "generated_note_ids": list(session.generated_note_ids or []),
    }
    if session.error is not None:
        state["error_code"] = session.error.code
        state["error_message"] = session.error.message
    return state


def build_failure_state(
    *,
    session_id: str,
    error_code: str,
    error_message: str,
    stage: SessionStage = SessionStage.FAILED,
) -> AgentState:
    return {
        "session_id": session_id,
        "stage": stage.value,
        "error_code": error_code,
        "error_message": error_message,
    }
