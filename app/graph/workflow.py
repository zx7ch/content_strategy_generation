from __future__ import annotations

from typing import Any, Optional

from langgraph.graph import END, START, StateGraph

from app.agents.content_generation_agent import ContentGenerationAgent
from app.agents.content_strategy_agent import ContentStrategyAgent
from app.graph.nodes import error_node, generate_node, init_node, strategy_node
from app.graph.state import AgentState
from app.memory.session_state import SessionManager


def _route_after_init(state: AgentState) -> str:
    return "error_node" if state.get("stage") == "failed" else "strategy_node"


def _route_after_strategy(state: AgentState) -> str:
    return "error_node" if state.get("stage") == "failed" else "generate_node"


def _route_after_generate(state: AgentState) -> str:
    return "error_node" if state.get("stage") == "failed" else END


def _route_after_error(_state: AgentState) -> str:
    return END


def create_workflow(
    *,
    checkpointer: Any | None = None,
    interrupt_before: list[str] | None = None,
    session_manager: Optional[SessionManager] = None,
    strategy_agent: Optional[ContentStrategyAgent] = None,
    generation_agent: Optional[ContentGenerationAgent] = None,
):
    graph = StateGraph(AgentState)

    async def _init(state: AgentState) -> AgentState:
        return await init_node(state, session_manager=session_manager)

    async def _strategy(state: AgentState) -> AgentState:
        return await strategy_node(
            state,
            session_manager=session_manager,
            strategy_agent=strategy_agent,
        )

    async def _generate(state: AgentState) -> AgentState:
        return await generate_node(
            state,
            session_manager=session_manager,
            generation_agent=generation_agent,
        )

    async def _error(state: AgentState) -> AgentState:
        return await error_node(state, session_manager=session_manager)

    graph.add_node("init_node", _init)
    graph.add_node("strategy_node", _strategy)
    graph.add_node("generate_node", _generate)
    graph.add_node("error_node", _error)

    graph.add_edge(START, "init_node")
    graph.add_conditional_edges(
        "init_node",
        _route_after_init,
        {
            "strategy_node": "strategy_node",
            "error_node": "error_node",
        },
    )
    graph.add_conditional_edges(
        "strategy_node",
        _route_after_strategy,
        {
            "generate_node": "generate_node",
            "error_node": "error_node",
        },
    )
    graph.add_conditional_edges(
        "generate_node",
        _route_after_generate,
        {
            "error_node": "error_node",
            END: END,
        },
    )
    graph.add_conditional_edges("error_node", _route_after_error, {END: END})

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before,
    )
