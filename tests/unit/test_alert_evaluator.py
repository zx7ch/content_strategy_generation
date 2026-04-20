from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.memory.job_store import JobStore
from app.memory.session_state import SessionManager
from app.observe.alert_evaluator import AlertEvaluator


async def _create_session(db_path: str, session_id: str) -> None:
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "u1", "alert test")


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "alert_evaluator.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    return str(db_path)


async def _append_budget_event(db_path: str, session_id: str, created_at: datetime) -> None:
    async with JobStore(db_path) as store:
        record = await store.append_session_event(
            session_id=session_id,
            event_name="budget_exceeded",
            stage="generation",
            payload={"message": "budget exceeded", "progress": None, "error_code": None, "details": {}},
        )
        await store._conn.execute(
            "UPDATE session_events SET created_at = ? WHERE event_id = ?",
            (created_at.strftime("%Y-%m-%d %H:%M:%S"), record.event_id),
        )
        await store._conn.commit()


class TestAlertEvaluator:
    @pytest.mark.asyncio
    async def test_threshold_hit_opens_alert(self, isolated_db):
        session_id = str(uuid.uuid4())
        await _create_session(isolated_db, session_id)
        now = datetime(2026, 3, 21, 12, 0, 0)

        for offset in range(settings.ALERT_BUDGET_EXCEEDED_COUNT_MAX + 1):
            await _append_budget_event(isolated_db, session_id, now - timedelta(minutes=1, seconds=offset))

        async with AlertEvaluator(isolated_db) as evaluator:
            results = await evaluator.evaluate_once(now=now)
            alerts = await evaluator.list_alerts(rule_name="budget_exceeded_count")

        assert len(results) == 1
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.status == "open"
        assert alert.rule_name == "budget_exceeded_count"
        assert alert.payload["current_value"] == settings.ALERT_BUDGET_EXCEEDED_COUNT_MAX + 1
        assert alert.payload["threshold"] == settings.ALERT_BUDGET_EXCEEDED_COUNT_MAX

    @pytest.mark.asyncio
    async def test_recovery_resolves_existing_open_alert(self, isolated_db):
        session_id = str(uuid.uuid4())
        await _create_session(isolated_db, session_id)
        now = datetime(2026, 3, 21, 12, 0, 0)

        for offset in range(settings.ALERT_BUDGET_EXCEEDED_COUNT_MAX + 1):
            await _append_budget_event(isolated_db, session_id, now - timedelta(minutes=1, seconds=offset))

        async with AlertEvaluator(isolated_db) as evaluator:
            await evaluator.evaluate_once(now=now)
            await evaluator.evaluate_once(now=now + timedelta(minutes=6))
            alerts = await evaluator.list_alerts(rule_name="budget_exceeded_count")

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.status == "resolved"
        assert alert.resolved_at is not None
        assert alert.payload["resolved_value"] == 0.0

    @pytest.mark.asyncio
    async def test_repeated_breach_does_not_create_duplicate_open_alert(self, isolated_db):
        session_id = str(uuid.uuid4())
        await _create_session(isolated_db, session_id)
        now = datetime(2026, 3, 21, 12, 0, 0)

        for offset in range(settings.ALERT_BUDGET_EXCEEDED_COUNT_MAX + 1):
            await _append_budget_event(isolated_db, session_id, now - timedelta(minutes=1, seconds=offset))

        async with AlertEvaluator(isolated_db) as evaluator:
            await evaluator.evaluate_once(now=now)
            await evaluator.evaluate_once(now=now + timedelta(minutes=1))
            alerts = await evaluator.list_alerts(rule_name="budget_exceeded_count")

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.status == "open"
        assert alert.payload["consecutive_hits"] >= 2
        assert "last_seen_at" in alert.payload

    @pytest.mark.asyncio
    async def test_threshold_mapping_matches_settings(self, isolated_db):
        async with AlertEvaluator(isolated_db) as evaluator:
            budget_rule = evaluator.rules["budget_exceeded_count"]
            backlog_rule = evaluator.rules["reindex_backlog_count"]
            success_rule = evaluator.rules["job_success_rate"]

        assert budget_rule.threshold == float(settings.ALERT_BUDGET_EXCEEDED_COUNT_MAX)
        assert budget_rule.window_minutes == 5
        assert budget_rule.comparison == ">"
        assert backlog_rule.threshold == float(settings.ALERT_REINDEX_BACKLOG_COUNT_MAX)
        assert backlog_rule.consecutive_hits == 10
        assert success_rule.threshold == settings.ALERT_JOB_SUCCESS_RATE_MIN
        assert success_rule.comparison == "<"
