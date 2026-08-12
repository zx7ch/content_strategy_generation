from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.routes.router import app
from app.config import settings
from app.services.llm.pricing import UsageCost
from app.services.llm.types import LLMCallContext, TokenUsage
from app.services.llm.usage_tracker import LLMUsageEventInput, LLMUsageTracker


@pytest.mark.asyncio
async def test_session_usage_api_returns_summary(tmp_path, monkeypatch):
    db_path = str(tmp_path / "usage-api.db")
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", db_path)
    async with LLMUsageTracker(db_path) as tracker:
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(session_id="session-1", job_id="job-1"),
                provider="openai",
                model="gpt-test",
                model_policy="balanced",
                usage=TokenUsage(prompt_tokens=100, completion_tokens=40, total_tokens=140),
                cost=UsageCost(input_cost=0.01, output_cost=0.02, total_cost=0.03),
                latency_ms=120,
                status="success",
            )
        )

    response = TestClient(app).get("/sessions/session-1/usage")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "session-1",
        "job_id": None,
        "total_calls": 1,
        "prompt_tokens": 100,
        "completion_tokens": 40,
        "total_tokens": 140,
        "total_cost": 0.03,
        "currency": "USD",
        "latency_ms": 120,
    }


@pytest.mark.asyncio
async def test_job_usage_api_returns_summary(tmp_path, monkeypatch):
    db_path = str(tmp_path / "usage-api.db")
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", db_path)
    async with LLMUsageTracker(db_path) as tracker:
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(session_id="session-1", job_id="job-2"),
                provider="openai",
                model="gpt-test",
                model_policy="quality",
                usage=TokenUsage(prompt_tokens=30, completion_tokens=20, total_tokens=50),
                cost=UsageCost(input_cost=0.03, output_cost=0.04, total_cost=0.07),
                latency_ms=75,
                status="success",
            )
        )

    response = TestClient(app).get("/jobs/job-2/usage")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": None,
        "job_id": "job-2",
        "total_calls": 1,
        "prompt_tokens": 30,
        "completion_tokens": 20,
        "total_tokens": 50,
        "total_cost": 0.07,
        "currency": "USD",
        "latency_ms": 75,
    }


@pytest.mark.asyncio
async def test_session_usage_steps_api_returns_step_breakdown(tmp_path, monkeypatch):
    db_path = str(tmp_path / "usage-api.db")
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", db_path)
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
                usage=TokenUsage(prompt_tokens=100, completion_tokens=40, total_tokens=140),
                cost=UsageCost(total_cost=0.03),
                latency_ms=120,
                status="success",
            )
        )
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(
                    session_id="session-1",
                    job_id="job-2",
                    step_id="strategy",
                    step_name="策略生成",
                    agent_name="ContentStrategyAgent",
                ),
                provider="openai",
                model="gpt-test",
                model_policy="balanced",
                usage=TokenUsage(prompt_tokens=10, completion_tokens=0, total_tokens=10),
                cost=UsageCost(total_cost=0.01),
                latency_ms=30,
                status="failed",
                error_message="bad",
            )
        )
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(
                    session_id="session-1",
                    job_id="job-2",
                    step_id="generation",
                    step_name="笔记生成",
                    agent_name="ContentGenerationAgent",
                ),
                provider="openai",
                model="gpt-test",
                model_policy="quality",
                usage=TokenUsage(prompt_tokens=50, completion_tokens=25, total_tokens=75),
                cost=UsageCost(total_cost=0.08),
                latency_ms=70,
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
                cost=UsageCost(total_cost=9.99),
                latency_ms=999,
                status="success",
            )
        )

    response = TestClient(app).get("/sessions/session-1/usage/steps")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "session-1",
        "job_id": None,
        "steps": [
            {
                "step_id": "strategy",
                "step_name": "策略生成",
                "agent_name": "ContentStrategyAgent",
                "total_calls": 2,
                "failed_calls": 1,
                "prompt_tokens": 110,
                "completion_tokens": 40,
                "total_tokens": 150,
                "total_cost": 0.04,
                "currency": "USD",
                "latency_ms": 150,
            },
            {
                "step_id": "generation",
                "step_name": "笔记生成",
                "agent_name": "ContentGenerationAgent",
                "total_calls": 1,
                "failed_calls": 0,
                "prompt_tokens": 50,
                "completion_tokens": 25,
                "total_tokens": 75,
                "total_cost": 0.08,
                "currency": "USD",
                "latency_ms": 70,
            },
        ],
    }


@pytest.mark.asyncio
async def test_job_usage_steps_api_scopes_by_job(tmp_path, monkeypatch):
    db_path = str(tmp_path / "usage-api.db")
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", db_path)
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
                cost=UsageCost(total_cost=0.15),
                latency_ms=100,
                status="success",
            )
        )
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(
                    session_id="session-1",
                    job_id="job-2",
                    step_id="generation",
                    step_name="笔记生成",
                    agent_name="ContentGenerationAgent",
                ),
                provider="openai",
                model="gpt-test",
                model_policy="quality",
                usage=TokenUsage(prompt_tokens=200, completion_tokens=100, total_tokens=300),
                cost=UsageCost(total_cost=0.9),
                latency_ms=300,
                status="success",
            )
        )

    response = TestClient(app).get("/jobs/job-2/usage/steps")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": None,
        "job_id": "job-2",
        "steps": [
            {
                "step_id": "generation",
                "step_name": "笔记生成",
                "agent_name": "ContentGenerationAgent",
                "total_calls": 1,
                "failed_calls": 0,
                "prompt_tokens": 200,
                "completion_tokens": 100,
                "total_tokens": 300,
                "total_cost": 0.9,
                "currency": "USD",
                "latency_ms": 300,
            }
        ],
    }


@pytest.mark.asyncio
async def test_session_usage_events_api_returns_raw_events(tmp_path, monkeypatch):
    db_path = str(tmp_path / "usage-api.db")
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", db_path)
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
                cost=UsageCost(total_cost=0.3),
                latency_ms=100,
                status="success",
            )
        )
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(
                    session_id="session-1",
                    job_id="job-2",
                    step_id="generation",
                    step_name="笔记生成",
                    agent_name="ContentGenerationAgent",
                ),
                provider="openai",
                model="gpt-test",
                model_policy="quality",
                usage=TokenUsage(prompt_tokens=20, completion_tokens=0, total_tokens=20),
                cost=UsageCost(total_cost=0.04),
                latency_ms=80,
                status="failed",
                error_message="timeout",
            )
        )

    response = TestClient(app).get("/sessions/session-1/usage/events")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "session-1"
    assert payload["job_id"] is None
    assert len(payload["events"]) == 2
    assert payload["events"][0]["step_id"] == "strategy"
    assert payload["events"][0]["model_policy"] == "balanced"
    assert payload["events"][0]["total_tokens"] == 150
    assert payload["events"][0]["total_cost"] == 0.3
    assert payload["events"][0]["created_at"]
    assert payload["events"][1]["status"] == "failed"
    assert payload["events"][1]["error_message"] == "timeout"


@pytest.mark.asyncio
async def test_job_usage_events_api_scopes_by_job(tmp_path, monkeypatch):
    db_path = str(tmp_path / "usage-api.db")
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", db_path)
    async with LLMUsageTracker(db_path) as tracker:
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(session_id="session-1", job_id="job-1", step_id="strategy"),
                provider="openai",
                model="gpt-test",
                model_policy="balanced",
                usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
                cost=UsageCost(total_cost=0.3),
                latency_ms=100,
                status="success",
            )
        )
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(session_id="session-1", job_id="job-2", step_id="generation"),
                provider="openai",
                model="gpt-test",
                model_policy="quality",
                usage=TokenUsage(prompt_tokens=200, completion_tokens=100, total_tokens=300),
                cost=UsageCost(total_cost=0.9),
                latency_ms=300,
                status="success",
            )
        )

    response = TestClient(app).get("/jobs/job-2/usage/events")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] is None
    assert payload["job_id"] == "job-2"
    assert len(payload["events"]) == 1
    assert payload["events"][0]["step_id"] == "generation"
    assert payload["events"][0]["total_tokens"] == 300
