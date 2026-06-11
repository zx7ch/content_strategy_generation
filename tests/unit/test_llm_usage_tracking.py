from __future__ import annotations

import aiosqlite
import pytest

from app.services.llm import (
    LLMCallContext,
    LLMUsageEventInput,
    LLMUsageTracker,
    ModelPricing,
    PricingCalculator,
    TokenUsage,
    UsageCost,
)


def test_pricing_calculator_computes_cost() -> None:
    calculator = PricingCalculator(
        {
            "openai:gpt-test": ModelPricing(
                input_per_1m_tokens=1.0,
                output_per_1m_tokens=3.0,
                currency="USD",
            )
        }
    )

    cost = calculator.calculate(
        provider="OpenAI",
        model="gpt-test",
        prompt_tokens=2_000_000,
        completion_tokens=500_000,
    )

    assert cost.input_cost == 2.0
    assert cost.output_cost == 1.5
    assert cost.total_cost == 3.5
    assert cost.currency == "USD"


def test_pricing_calculator_unknown_model_returns_zero_cost() -> None:
    cost = PricingCalculator({}).calculate(
        provider="openai",
        model="unknown",
        prompt_tokens=1000,
        completion_tokens=1000,
    )

    assert cost == UsageCost()


@pytest.mark.asyncio
async def test_usage_tracker_records_success_event(tmp_path) -> None:
    db_path = str(tmp_path / "usage.db")
    context = LLMCallContext(
        session_id="session-1",
        job_id="job-1",
        step_id="step-1",
        step_name="策略生成",
        agent_name="StrategyAgent",
        tenant_id="tenant-1",
        user_id="user-1",
    )
    async with LLMUsageTracker(db_path) as tracker:
        event_id = await tracker.record(
            LLMUsageEventInput(
                context=context,
                provider="openai",
                model="gpt-test",
                model_policy="balanced",
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
                cost=UsageCost(input_cost=0.1, output_cost=0.2, total_cost=0.3, currency="USD"),
                latency_ms=120,
                status="success",
            )
        )

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM llm_usage_events WHERE id = ?", (event_id,)) as cursor:
            row = await cursor.fetchone()

    assert row is not None
    assert row["session_id"] == "session-1"
    assert row["job_id"] == "job-1"
    assert row["step_id"] == "step-1"
    assert row["step_name"] == "策略生成"
    assert row["agent_name"] == "StrategyAgent"
    assert row["tenant_id"] == "tenant-1"
    assert row["user_id"] == "user-1"
    assert row["provider"] == "openai"
    assert row["model"] == "gpt-test"
    assert row["model_policy"] == "balanced"
    assert row["prompt_tokens"] == 10
    assert row["completion_tokens"] == 5
    assert row["total_tokens"] == 15
    assert row["total_cost"] == 0.3
    assert row["status"] == "success"
    assert row["created_at"]


@pytest.mark.asyncio
async def test_usage_tracker_records_failed_event(tmp_path) -> None:
    db_path = str(tmp_path / "usage.db")
    async with LLMUsageTracker(db_path) as tracker:
        event_id = await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(session_id="session-1", job_id="job-1"),
                provider="openai",
                model="gpt-test",
                model_policy="balanced",
                usage=TokenUsage(),
                cost=UsageCost(),
                latency_ms=50,
                status="failed",
                error_message="timeout",
            )
        )

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT status, latency_ms, error_message FROM llm_usage_events WHERE id = ?", (event_id,)) as cursor:
            row = await cursor.fetchone()

    assert row is not None
    assert row["status"] == "failed"
    assert row["latency_ms"] == 50
    assert row["error_message"] == "timeout"


@pytest.mark.asyncio
async def test_usage_tracker_summarizes_session(tmp_path) -> None:
    db_path = str(tmp_path / "usage.db")
    async with LLMUsageTracker(db_path) as tracker:
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(session_id="session-1", job_id="job-1"),
                provider="openai",
                model="gpt-test",
                model_policy="balanced",
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
                cost=UsageCost(total_cost=0.3, currency="USD"),
                latency_ms=100,
                status="success",
            )
        )
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(session_id="session-1", job_id="job-2"),
                provider="openai",
                model="gpt-test",
                model_policy="balanced",
                usage=TokenUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30),
                cost=UsageCost(total_cost=0.6, currency="USD"),
                latency_ms=200,
                status="failed",
                error_message="bad",
            )
        )
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(session_id="other", job_id="job-3"),
                provider="openai",
                model="gpt-test",
                model_policy="balanced",
                usage=TokenUsage(prompt_tokens=99, completion_tokens=99, total_tokens=198),
                cost=UsageCost(total_cost=9.9, currency="USD"),
                latency_ms=999,
                status="success",
            )
        )

        summary = await tracker.summarize_session("session-1")

    assert summary.total_calls == 2
    assert summary.prompt_tokens == 30
    assert summary.completion_tokens == 15
    assert summary.total_tokens == 45
    assert summary.total_cost == pytest.approx(0.9)
    assert summary.latency_ms == 300
    assert summary.currency == "USD"


@pytest.mark.asyncio
async def test_usage_tracker_summarizes_job(tmp_path) -> None:
    db_path = str(tmp_path / "usage.db")
    async with LLMUsageTracker(db_path) as tracker:
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(session_id="session-1", job_id="job-1"),
                provider="deepseek",
                model="deepseek-chat",
                model_policy="cheap",
                usage=TokenUsage(prompt_tokens=8, completion_tokens=2, total_tokens=10),
                cost=UsageCost(total_cost=0.1, currency="USD"),
                latency_ms=70,
                status="success",
            )
        )
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(session_id="session-2", job_id="job-1"),
                provider="deepseek",
                model="deepseek-chat",
                model_policy="cheap",
                usage=TokenUsage(prompt_tokens=4, completion_tokens=6, total_tokens=10),
                cost=UsageCost(total_cost=0.2, currency="USD"),
                latency_ms=30,
                status="success",
            )
        )

        summary = await tracker.summarize_job("job-1")
        empty = await tracker.summarize_job("missing")

    assert summary.total_calls == 2
    assert summary.prompt_tokens == 12
    assert summary.completion_tokens == 8
    assert summary.total_tokens == 20
    assert summary.total_cost == pytest.approx(0.3)
    assert summary.latency_ms == 100
    assert empty.total_calls == 0
    assert empty.currency == "USD"


@pytest.mark.asyncio
async def test_usage_tracker_summarizes_steps_with_failed_calls(tmp_path) -> None:
    db_path = str(tmp_path / "usage.db")
    async with LLMUsageTracker(db_path) as tracker:
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(
                    session_id="session-1",
                    job_id="job-1",
                    step_id="strategy",
                    step_name="策略生成",
                    agent_name="ContentStrategyAgent",
                ),
                provider="openai",
                model="gpt-test",
                model_policy="balanced",
                usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
                cost=UsageCost(total_cost=0.3, currency="USD"),
                latency_ms=100,
                status="success",
            )
        )
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(
                    session_id="session-1",
                    job_id="job-1",
                    step_id="strategy",
                    step_name="策略生成",
                    agent_name="ContentStrategyAgent",
                ),
                provider="openai",
                model="gpt-test",
                model_policy="balanced",
                usage=TokenUsage(prompt_tokens=20, completion_tokens=0, total_tokens=20),
                cost=UsageCost(total_cost=0.04, currency="USD"),
                latency_ms=80,
                status="failed",
                error_message="timeout",
            )
        )
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(
                    session_id="session-1",
                    job_id="job-1",
                    step_id="generation",
                    step_name="笔记生成",
                    agent_name="ContentGenerationAgent",
                ),
                provider="openai",
                model="gpt-test",
                model_policy="quality",
                usage=TokenUsage(prompt_tokens=200, completion_tokens=100, total_tokens=300),
                cost=UsageCost(total_cost=0.9, currency="USD"),
                latency_ms=300,
                status="success",
            )
        )
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(session_id="session-1", job_id="job-2"),
                provider="openai",
                model="gpt-test",
                model_policy="balanced",
                usage=TokenUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
                cost=UsageCost(total_cost=0.01, currency="USD"),
                latency_ms=10,
                status="success",
            )
        )
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(
                    session_id="other",
                    job_id="job-9",
                    step_id="strategy",
                    step_name="策略生成",
                    agent_name="ContentStrategyAgent",
                ),
                provider="openai",
                model="gpt-test",
                model_policy="balanced",
                usage=TokenUsage(prompt_tokens=999, completion_tokens=999, total_tokens=1998),
                cost=UsageCost(total_cost=9.99, currency="USD"),
                latency_ms=999,
                status="success",
            )
        )

        session_steps = await tracker.summarize_session_steps("session-1")
        job_steps = await tracker.summarize_job_steps("job-1")

    assert [step.step_id for step in session_steps] == ["strategy", "generation", None]
    assert session_steps[0].step_name == "策略生成"
    assert session_steps[0].agent_name == "ContentStrategyAgent"
    assert session_steps[0].total_calls == 2
    assert session_steps[0].failed_calls == 1
    assert session_steps[0].prompt_tokens == 120
    assert session_steps[0].completion_tokens == 50
    assert session_steps[0].total_tokens == 170
    assert session_steps[0].total_cost == pytest.approx(0.34)
    assert session_steps[0].latency_ms == 180
    assert session_steps[1].step_id == "generation"
    assert session_steps[1].total_calls == 1
    assert session_steps[1].total_tokens == 300
    assert session_steps[2].step_id is None
    assert session_steps[2].total_tokens == 10

    assert [step.step_id for step in job_steps] == ["strategy", "generation"]
    assert sum(step.total_calls for step in job_steps) == 3
    assert sum(step.total_tokens for step in job_steps) == 470
    assert sum(step.total_cost for step in job_steps) == pytest.approx(1.24)


@pytest.mark.asyncio
async def test_usage_tracker_lists_events_in_created_order(tmp_path) -> None:
    db_path = str(tmp_path / "usage.db")
    async with LLMUsageTracker(db_path) as tracker:
        first_id = await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(
                    session_id="session-1",
                    job_id="job-1",
                    step_id="strategy",
                    step_name="策略生成",
                    agent_name="ContentStrategyAgent",
                ),
                provider="openai",
                model="gpt-test",
                model_policy="balanced",
                usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
                cost=UsageCost(total_cost=0.3, currency="USD"),
                latency_ms=100,
                status="success",
            )
        )
        second_id = await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(
                    session_id="session-1",
                    job_id="job-1",
                    step_id="generation",
                    step_name="笔记生成",
                    agent_name="ContentGenerationAgent",
                ),
                provider="openai",
                model="gpt-test",
                model_policy="quality",
                usage=TokenUsage(prompt_tokens=20, completion_tokens=0, total_tokens=20),
                cost=UsageCost(total_cost=0.04, currency="USD"),
                latency_ms=80,
                status="failed",
                error_message="timeout",
            )
        )
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(session_id="other", job_id="job-9"),
                provider="openai",
                model="gpt-test",
                model_policy="balanced",
                usage=TokenUsage(prompt_tokens=999, completion_tokens=999, total_tokens=1998),
                cost=UsageCost(total_cost=9.99, currency="USD"),
                latency_ms=999,
                status="success",
            )
        )

        session_events = await tracker.list_session_events("session-1")
        job_events = await tracker.list_job_events("job-1")

    assert [event.id for event in session_events] == [first_id, second_id]
    assert [event.id for event in job_events] == [first_id, second_id]
    assert session_events[0].provider == "openai"
    assert session_events[0].model == "gpt-test"
    assert session_events[0].model_policy == "balanced"
    assert session_events[0].total_tokens == 150
    assert session_events[0].total_cost == pytest.approx(0.3)
    assert session_events[0].created_at
    assert session_events[1].status == "failed"
    assert session_events[1].error_message == "timeout"
