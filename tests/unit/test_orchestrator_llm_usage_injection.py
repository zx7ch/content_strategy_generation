from __future__ import annotations

import pytest

from app.agents.orchestrator import Orchestrator
from app.agents.content_generation_agent import GenerationExecutionResult


class FakeTrackedClient:
    pass


class FakeStrategyResult:
    success = True
    quality_score = 0.9
    used_fallback = False
    error_code = None
    message = None


@pytest.mark.asyncio
async def test_orchestrator_injects_tracked_strategy_client(monkeypatch, tmp_path):
    calls = []
    fake_client = FakeTrackedClient()
    captured_clients = []

    def fake_builder(**kwargs):
        calls.append(kwargs)
        return fake_client

    async def fake_execute(self, session_id, *, progress_callback=None):
        del session_id, progress_callback
        captured_clients.append(self.llm)
        return FakeStrategyResult()

    monkeypatch.setattr("app.agents.orchestrator.build_default_tracked_chat_client", fake_builder)
    monkeypatch.setattr("app.agents.content_strategy_agent.ContentStrategyAgent.execute", fake_execute)

    orchestrator = Orchestrator(db_path=str(tmp_path / "orchestrator.db"))
    result = await orchestrator._run_strategy_job("session-1", {}, job_id="job-1")

    assert result["success"] is True
    assert captured_clients == [fake_client]
    assert calls == [
        {
            "db_path": str(tmp_path / "orchestrator.db"),
            "session_id": "session-1",
            "job_id": "job-1",
            "model_policy": "balanced",
            "step_id": "strategy",
            "step_name": "策略生成",
            "agent_name": "ContentStrategyAgent",
        }
    ]


@pytest.mark.asyncio
async def test_orchestrator_injects_tracked_generation_client(monkeypatch, tmp_path):
    calls = []
    fake_client = FakeTrackedClient()
    captured_clients = []

    def fake_builder(**kwargs):
        calls.append(kwargs)
        return fake_client

    async def fake_execute(self, session_id, *, progress_callback=None):
        del session_id, progress_callback
        captured_clients.append(self.llm)
        return GenerationExecutionResult(
            success=True,
            message="ok",
            status="completed",
            notes=[],
            similarity_report={},
        )

    monkeypatch.setattr("app.agents.orchestrator.build_default_tracked_chat_client", fake_builder)
    monkeypatch.setattr("app.agents.content_generation_agent.ContentGenerationAgent.execute", fake_execute)

    orchestrator = Orchestrator(db_path=str(tmp_path / "orchestrator.db"))
    result = await orchestrator._run_generation_job("session-1", {}, job_id="job-2")

    assert result["success"] is True
    assert captured_clients == [fake_client]
    assert calls == [
        {
            "db_path": str(tmp_path / "orchestrator.db"),
            "session_id": "session-1",
            "job_id": "job-2",
            "model_policy": "quality",
            "step_id": "generation",
            "step_name": "笔记生成",
            "agent_name": "ContentGenerationAgent",
        }
    ]
