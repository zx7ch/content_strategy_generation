from __future__ import annotations

import time
import uuid

import pytest

from app.agents.content_strategy_agent import ContentStrategyAgent
from app.memory.session_state import SessionManager
from tests.acceptance.conftest import write_acceptance_artifact


@pytest.mark.acceptance
@pytest.mark.real_dependency
@pytest.mark.asyncio
async def test_real_llm_strategy(
    spider_ready: None,
    llm_ready: None,
    acceptance_queries: dict[str, str],
    acceptance_storage,
    acceptance_artifact_dir,
):
    session_id = f"acceptance-strategy-{uuid.uuid4()}"
    async with SessionManager(acceptance_storage["db_path"]) as manager:
        await manager.create_session(session_id, "acceptance-user", acceptance_queries["primary"])

    agent = ContentStrategyAgent(session_manager=SessionManager(acceptance_storage["db_path"]))
    started = time.perf_counter()
    result = await agent.execute(session_id)
    latency_ms = int((time.perf_counter() - started) * 1000)

    assert result.success is True
    assert result.content_strategy is not None
    assert result.platform_preference is not None
    assert 0.0 <= result.quality_score <= 1.0
    assert result.content_strategy.positioning
    assert result.content_strategy.target_audience

    write_acceptance_artifact(
        acceptance_artifact_dir,
        "real_llm_strategy",
        {
            "session_id": session_id,
            "provider": agent.llm.provider.value,
            "model": agent.llm.model,
            "query": acceptance_queries["primary"],
            "latency_ms": latency_ms,
            "quality_score": result.quality_score,
            "used_fallback": result.used_fallback,
            "positioning": result.content_strategy.positioning,
        },
    )
