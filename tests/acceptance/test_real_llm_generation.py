from __future__ import annotations

import time
import uuid

import pytest

from app.agents.content_generation_agent import ContentGenerationAgent
from app.agents.content_strategy_agent import ContentStrategyAgent
from app.memory.session_state import SessionManager
from tests.acceptance.conftest import write_acceptance_artifact


@pytest.mark.acceptance
@pytest.mark.real_dependency
@pytest.mark.asyncio
async def test_real_llm_generation(
    spider_ready: None,
    llm_ready: None,
    acceptance_queries: dict[str, str],
    acceptance_storage,
    acceptance_artifact_dir,
):
    session_id = f"acceptance-generation-{uuid.uuid4()}"
    async with SessionManager(acceptance_storage["db_path"]) as manager:
        await manager.create_session(session_id, "acceptance-user", acceptance_queries["primary"])

    strategy_agent = ContentStrategyAgent(session_manager=SessionManager(acceptance_storage["db_path"]))
    strategy_result = await strategy_agent.execute(session_id)
    assert strategy_result.success is True

    generation_agent = ContentGenerationAgent(session_manager=SessionManager(acceptance_storage["db_path"]))
    started = time.perf_counter()
    generation_result = await generation_agent.execute(session_id)
    latency_ms = int((time.perf_counter() - started) * 1000)

    assert generation_result.success is True
    assert generation_result.notes
    assert 1 <= len(generation_result.notes) <= 5
    assert generation_result.notes[0].title
    assert generation_result.notes[0].content
    assert isinstance(generation_result.notes[0].tags, list)

    write_acceptance_artifact(
        acceptance_artifact_dir,
        "real_llm_generation",
        {
            "session_id": session_id,
            "provider": generation_agent.llm.provider.value,
            "model": generation_agent.llm.model,
            "query": acceptance_queries["primary"],
            "latency_ms": latency_ms,
            "notes_generated": len(generation_result.notes),
            "status": generation_result.status,
            "token_used": generation_result.similarity_report.get("token_used"),
            "token_budget": generation_result.similarity_report.get("token_budget"),
            "budget_remaining": generation_result.similarity_report.get("budget_remaining"),
        },
    )
