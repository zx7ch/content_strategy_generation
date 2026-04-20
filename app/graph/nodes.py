from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from app.agents.content_generation_agent import ContentGenerationAgent
from app.agents.content_strategy_agent import ContentStrategyAgent
from app.graph.state import AgentState, build_failure_state, project_session_to_state
from app.memory.session_state import SessionManager
from app.models.session import SessionError, SessionStage


@asynccontextmanager
async def _session_scope(session_manager: Optional[SessionManager]) -> AsyncIterator[SessionManager]:
    manager = session_manager or SessionManager()
    if getattr(manager, "_conn", None) is None:
        async with manager as connected:
            yield connected
    else:
        yield manager


def _resolve_session_id(state: AgentState) -> str:
    session_id = state.get("session_id")
    if not session_id:
        raise ValueError("session_id is required in AgentState")
    return session_id


def _resolve_error_stage(stage: Optional[str]) -> SessionStage:
    if stage in {
        SessionStage.INIT.value,
        SessionStage.STRATEGY.value,
        SessionStage.GENERATION.value,
    }:
        return SessionStage(stage)
    return SessionStage.FAILED


async def init_node(
    state: AgentState,
    *,
    session_manager: Optional[SessionManager] = None,
) -> AgentState:
    try:
        session_id = _resolve_session_id(state)
    except ValueError:
        return build_failure_state(
            session_id="",
            error_code="MISSING_SESSION_ID",
            error_message="session_id is required",
        )

    async with _session_scope(session_manager) as manager:
        session = await manager.get_session(session_id)
        if session is None:
            return build_failure_state(
                session_id=session_id,
                error_code="SESSION_NOT_FOUND",
                error_message="Session not found",
            )
        return project_session_to_state(session)


async def strategy_node(
    state: AgentState,
    *,
    session_manager: Optional[SessionManager] = None,
    strategy_agent: Optional[ContentStrategyAgent] = None,
) -> AgentState:
    session_id = _resolve_session_id(state)

    async with _session_scope(session_manager) as manager:
        session = await manager.get_session(session_id)
        if session is None:
            return build_failure_state(
                session_id=session_id,
                error_code="SESSION_NOT_FOUND",
                error_message="Session not found",
            )

        if session.strategy_id and session.content_strategy is not None and session.platform_preference is not None:
            return project_session_to_state(session)

        agent = strategy_agent or ContentStrategyAgent(session_manager=SessionManager(manager.db_path))
        result = await agent.execute(session_id)

        refreshed = await manager.get_session(session_id)
        if refreshed is None:
            return build_failure_state(
                session_id=session_id,
                error_code="SESSION_NOT_FOUND",
                error_message="Session disappeared during strategy execution",
            )
        if not result.success:
            projected = project_session_to_state(refreshed)
            projected["stage"] = SessionStage.FAILED.value
            projected["error_code"] = result.error_code or "STRATEGY_EXECUTION_ERROR"
            projected["error_message"] = result.message
            return projected
        return project_session_to_state(refreshed)


async def generate_node(
    state: AgentState,
    *,
    session_manager: Optional[SessionManager] = None,
    generation_agent: Optional[ContentGenerationAgent] = None,
) -> AgentState:
    session_id = _resolve_session_id(state)

    async with _session_scope(session_manager) as manager:
        session = await manager.get_session(session_id)
        if session is None:
            return build_failure_state(
                session_id=session_id,
                error_code="SESSION_NOT_FOUND",
                error_message="Session not found",
            )

        if session.generated_note_ids or session.generated_notes:
            return project_session_to_state(session)

        agent = generation_agent or ContentGenerationAgent(session_manager=SessionManager(manager.db_path))
        result = await agent.execute(session_id)

        refreshed = await manager.get_session(session_id)
        if refreshed is None:
            return build_failure_state(
                session_id=session_id,
                error_code="SESSION_NOT_FOUND",
                error_message="Session disappeared during generation execution",
            )
        if not result.success:
            projected = project_session_to_state(refreshed)
            projected["stage"] = SessionStage.FAILED.value
            projected["error_code"] = result.error_code or "GENERATION_EXECUTION_ERROR"
            projected["error_message"] = result.message
            return projected
        return project_session_to_state(refreshed)


async def error_node(
    state: AgentState,
    *,
    session_manager: Optional[SessionManager] = None,
) -> AgentState:
    try:
        session_id = _resolve_session_id(state)
    except ValueError:
        return build_failure_state(
            session_id="",
            error_code="MISSING_SESSION_ID",
            error_message="session_id is required",
        )

    error_code = state.get("error_code") or "WORKFLOW_ERROR"
    error_message = state.get("error_message") or "Workflow execution failed"
    error_stage = _resolve_error_stage(state.get("stage"))

    async with _session_scope(session_manager) as manager:
        session = await manager.get_session(session_id)
        if session is None:
            return build_failure_state(
                session_id=session_id,
                error_code="SESSION_NOT_FOUND",
                error_message="Session not found",
            )

        updated = await manager.update_session(
            session_id,
            stage=SessionStage.FAILED,
            error=SessionError(
                code=error_code,
                message=error_message,
                stage=error_stage,
            ),
        )
        if updated is None:
            return build_failure_state(
                session_id=session_id,
                error_code="SESSION_NOT_FOUND",
                error_message="Session not found",
            )
        projected = project_session_to_state(updated)
        projected["stage"] = SessionStage.FAILED.value
        projected["error_code"] = error_code
        projected["error_message"] = error_message
        return projected
