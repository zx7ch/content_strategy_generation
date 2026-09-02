from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.observe.alert_evaluator import AlertEvaluator, AlertRule
from app.services.llm.configuration import UserLLMConfiguration
from app.services.llm.configuration_store import SQLiteLLMConfigurationStore
from app.services.llm.pricing import UsageCost
from app.services.llm.types import LLMCallContext, TokenUsage
from app.services.llm.usage_tracker import LLMUsageEventInput, LLMUsageTracker
from app.services.runtime_accounting_mutations import runtime_accounting_mutation_handlers
from app.services.xhs_credentials import XHSCredentialStore


def test_usage_and_alert_mutations_share_runtime_writer(tmp_path: Path) -> None:
    database = tmp_path / "accounting.sqlite"

    async def bootstrap() -> None:
        async with LLMUsageTracker(str(database)):
            pass
        async with AlertEvaluator(str(database)):
            pass

    async def exercise() -> None:
        writer = RuntimeWriteCoordinator(
            database,
            handlers=runtime_accounting_mutation_handlers(),
        )
        await writer.start()
        async with LLMUsageTracker(str(database), writer=writer) as usage:
            await usage.record(
                LLMUsageEventInput(
                    context=LLMCallContext(session_id="session-owned", job_id="job-owned"),
                    provider="openai",
                    model="gpt-owned",
                    model_policy="balanced",
                    usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
                    cost=UsageCost(total_cost=0.25),
                    latency_ms=20,
                    status="success",
                )
            )
            summary = await usage.summarize_session("session-owned")
            assert summary.total_calls == 1
            assert summary.total_tokens == 15

        async with AlertEvaluator(str(database), writer=writer) as alerts:
            alerts.rules = {
                "always_breached": AlertRule(
                    rule_name="always_breached",
                    metric_name="reindex_backlog_count",
                    severity="warning",
                    window_minutes=1,
                    threshold=-1,
                    comparison=">",
                )
            }
            evaluated = await alerts.evaluate_once()
            listed = await alerts.list_alerts(rule_name="always_breached")
            assert len(evaluated) == 1
            assert len(listed) == 1
            assert listed[0].status == "open"
        await writer.close()

    asyncio.run(bootstrap())
    asyncio.run(exercise())


def test_configuration_and_credentials_share_runtime_writer(tmp_path: Path) -> None:
    database = tmp_path / "runtime-settings.sqlite"
    SQLiteLLMConfigurationStore(str(database))
    XHSCredentialStore(str(database))

    async def exercise() -> None:
        writer = RuntimeWriteCoordinator(
            database,
            handlers=runtime_accounting_mutation_handlers(),
        )
        await writer.start()
        configurations = SQLiteLLMConfigurationStore(str(database), writer=writer)
        saved = await configurations.upsert_async(
            UserLLMConfiguration(
                workspace_id="workspace-owned",
                user_id="user-owned",
                base_url="https://proxy.example/v1",
                model="model-owned",
                api_key="secret-owned",
                validation_status="validated",
                validated_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
            )
        )
        assert configurations.get("workspace-owned", "user-owned") == saved

        credentials = XHSCredentialStore(str(database), writer=writer)
        status = await credentials.replace_async("a1=owned; web_session=owned", "manual_cookie")
        assert status.authenticated is True
        stale = await credentials.mark_stale_async("auth_required")
        assert stale.authenticated is False
        assert stale.failure_code == "auth_required"
        assert "owned" not in repr(stale)
        await writer.close()

    asyncio.run(exercise())
