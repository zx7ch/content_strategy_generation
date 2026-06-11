from __future__ import annotations

import sqlite3

import pytest

from app.services.llm.pricing import ModelPricing, PricingCalculator
from app.services.llm.tracked_client import TrackedLLMChatClient
from app.services.llm.types import LLMCallContext, LLMResponse, TokenUsage
from app.services.llm.usage_tracker import LLMUsageTracker


class FakeLLMService:
    def __init__(self, response: LLMResponse | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


@pytest.mark.asyncio
async def test_tracked_chat_client_records_success_event(tmp_path):
    db_path = str(tmp_path / "usage.db")
    service = FakeLLMService(
        LLMResponse(
            content="hello",
            provider="openai",
            model="gpt-test",
            usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            latency_ms=321,
        )
    )
    client = TrackedLLMChatClient(
        llm_service=service,
        usage_tracker=LLMUsageTracker(db_path),
        pricing_calculator=PricingCalculator(
            {"openai:gpt-test": ModelPricing(input_per_1m_tokens=1.0, output_per_1m_tokens=2.0)}
        ),
        context=LLMCallContext(
            session_id="session-1",
            job_id="job-1",
            step_id="strategy",
            step_name="策略生成",
            agent_name="ContentStrategyAgent",
        ),
        model_policy="balanced",
    )

    result = await client.chat(system="system", user="user", max_tokens=128, temperature=0.2)

    assert result == "hello"
    assert service.requests[0].context == LLMCallContext(
        session_id="session-1",
        job_id="job-1",
        step_id="strategy",
        step_name="策略生成",
        agent_name="ContentStrategyAgent",
    )
    assert service.requests[0].messages[0].content == "system"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM llm_usage_events").fetchone()
    assert row is not None
    assert row["session_id"] == "session-1"
    assert row["job_id"] == "job-1"
    assert row["step_id"] == "strategy"
    assert row["step_name"] == "策略生成"
    assert row["agent_name"] == "ContentStrategyAgent"
    assert row["provider"] == "openai"
    assert row["total_tokens"] == 150
    assert row["total_cost"] == pytest.approx(0.0002)
    assert row["status"] == "success"


@pytest.mark.asyncio
async def test_tracked_chat_client_records_failed_event_and_reraises(tmp_path):
    db_path = str(tmp_path / "usage.db")
    service = FakeLLMService(error=RuntimeError("provider down"))
    client = TrackedLLMChatClient(
        llm_service=service,
        usage_tracker=LLMUsageTracker(db_path),
        pricing_calculator=PricingCalculator(),
        context=LLMCallContext(
            session_id="session-2",
            job_id="job-2",
            step_id="generation",
            step_name="笔记生成",
            agent_name="ContentGenerationAgent",
        ),
        model_policy="quality",
    )

    with pytest.raises(RuntimeError, match="provider down"):
        await client.chat(system="system", user="user")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM llm_usage_events").fetchone()
    assert row is not None
    assert row["session_id"] == "session-2"
    assert row["job_id"] == "job-2"
    assert row["step_id"] == "generation"
    assert row["step_name"] == "笔记生成"
    assert row["agent_name"] == "ContentGenerationAgent"
    assert row["provider"] == "unknown"
    assert row["total_tokens"] == 0
    assert row["status"] == "failed"
    assert row["error_message"] == "provider down"
