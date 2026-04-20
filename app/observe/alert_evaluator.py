from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

import aiosqlite

from app.config import settings
from app.logging_config import get_logger, log_event


def _utcnow() -> datetime:
    return datetime.utcnow()


def _sqlite_ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True, slots=True)
class AlertRule:
    rule_name: str
    metric_name: str
    severity: str
    window_minutes: int
    threshold: float
    comparison: str
    consecutive_hits: int = 1

    def is_breached(self, value: float) -> bool:
        if self.comparison == ">":
            return value > self.threshold
        if self.comparison == "<":
            return value < self.threshold
        raise ValueError(f"Unsupported comparison: {self.comparison}")


@dataclass(slots=True)
class AlertRecord:
    id: int
    rule_name: str
    severity: str
    status: str
    minute_bucket: str
    fired_at: str
    resolved_at: Optional[str]
    payload_json: str

    @property
    def payload(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.payload_json)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}


class AlertEvaluator:
    """Evaluate operational alert rules from SQLite fact tables."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or settings.SQLITE_DB_PATH
        self._conn: Optional[aiosqlite.Connection] = None
        self._logger = get_logger(__name__, component="observe")
        self.rules: dict[str, AlertRule] = {
            "job_success_rate": AlertRule(
                rule_name="job_success_rate",
                metric_name="job_success_rate",
                severity="critical",
                window_minutes=5,
                threshold=settings.ALERT_JOB_SUCCESS_RATE_MIN,
                comparison="<",
                consecutive_hits=15,
            ),
            "budget_exceeded_count": AlertRule(
                rule_name="budget_exceeded_count",
                metric_name="budget_exceeded_count",
                severity="warning",
                window_minutes=5,
                threshold=float(settings.ALERT_BUDGET_EXCEEDED_COUNT_MAX),
                comparison=">",
                consecutive_hits=1,
            ),
            "reindex_backlog_count": AlertRule(
                rule_name="reindex_backlog_count",
                metric_name="reindex_backlog_count",
                severity="warning",
                window_minutes=10,
                threshold=float(settings.ALERT_REINDEX_BACKLOG_COUNT_MAX),
                comparison=">",
                consecutive_hits=10,
            ),
        }

    async def __aenter__(self) -> "AlertEvaluator":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._conn is not None:
            return

        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._init_tables()

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    async def _init_tables(self) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_name TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL,
                minute_bucket TEXT NOT NULL,
                fired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP,
                payload_json TEXT NOT NULL
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alerts_rule_status ON alerts(rule_name, status, minute_bucket)"
        )
        await self._conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_alerts_open_rule_bucket
            ON alerts(rule_name, minute_bucket)
            WHERE status = 'open'
            """
        )
        await self._conn.commit()

    async def evaluate_once(self, now: Optional[datetime] = None) -> list[AlertRecord]:
        now = now or _utcnow()
        results: list[AlertRecord] = []
        for rule in self.rules.values():
            metric_value = await self._compute_metric(rule, now)
            record = await self._evaluate_rule(rule, metric_value, now)
            if record is not None:
                results.append(record)
        return results

    async def list_alerts(self, *, rule_name: Optional[str] = None) -> list[AlertRecord]:
        assert self._conn is not None
        sql = "SELECT * FROM alerts"
        params: list[Any] = []
        if rule_name is not None:
            sql += " WHERE rule_name = ?"
            params.append(rule_name)
        sql += " ORDER BY id ASC"
        async with self._conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_alert(row) for row in rows]

    @staticmethod
    def _row_to_alert(row: aiosqlite.Row) -> AlertRecord:
        return AlertRecord(
            id=int(row["id"]),
            rule_name=row["rule_name"],
            severity=row["severity"],
            status=row["status"],
            minute_bucket=row["minute_bucket"],
            fired_at=row["fired_at"],
            resolved_at=row["resolved_at"],
            payload_json=row["payload_json"],
        )

    async def _compute_metric(self, rule: AlertRule, now: datetime) -> float:
        if rule.metric_name == "budget_exceeded_count":
            return float(await self._count_session_events("budget_exceeded", now, rule.window_minutes))
        if rule.metric_name == "reindex_backlog_count":
            return float(await self._count_pending_reindex())
        if rule.metric_name == "job_success_rate":
            return await self._job_success_rate(now, rule.window_minutes)
        raise ValueError(f"Unsupported metric: {rule.metric_name}")

    async def _count_session_events(self, event_name: str, now: datetime, window_minutes: int) -> int:
        assert self._conn is not None
        if not await self._table_exists("session_events"):
            return 0
        window_start = _sqlite_ts(now - timedelta(minutes=window_minutes))
        async with self._conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM session_events
            WHERE event_name = ?
              AND DATETIME(created_at) >= DATETIME(?)
            """,
            (event_name, window_start),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["c"] or 0)

    async def _count_pending_reindex(self) -> int:
        assert self._conn is not None
        if not await self._table_exists("sessions"):
            return 0
        async with self._conn.execute(
            "SELECT COUNT(*) AS c FROM sessions WHERE reindex_state = 'pending'"
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["c"] or 0)

    async def _job_success_rate(self, now: datetime, window_minutes: int) -> float:
        assert self._conn is not None
        if not await self._table_exists("jobs"):
            return 1.0
        window_start = _sqlite_ts(now - timedelta(minutes=window_minutes))
        async with self._conn.execute(
            """
            SELECT status, last_error_code, COUNT(*) AS c
            FROM jobs
            WHERE status IN ('succeeded', 'failed')
              AND DATETIME(updated_at) >= DATETIME(?)
            GROUP BY status, last_error_code
            """,
            (window_start,),
        ) as cursor:
            rows = await cursor.fetchall()

        succeeded = 0
        failed = 0
        excluded = {"INVALID_STAGE", "SESSION_NOT_FOUND"}
        for row in rows:
            count = int(row["c"] or 0)
            if row["status"] == "succeeded":
                succeeded += count
            elif row["last_error_code"] not in excluded:
                failed += count

        total = succeeded + failed
        if total == 0:
            return 1.0
        return succeeded / total

    async def _table_exists(self, table_name: str) -> bool:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ) as cursor:
            row = await cursor.fetchone()
        return row is not None

    async def _evaluate_rule(
        self,
        rule: AlertRule,
        value: float,
        now: datetime,
    ) -> Optional[AlertRecord]:
        assert self._conn is not None
        existing = await self._get_latest_active_alert(rule.rule_name)
        if rule.is_breached(value):
            return await self._handle_breach(rule, value, now, existing)
        return await self._handle_recovery(rule, value, now, existing)

    async def _handle_breach(
        self,
        rule: AlertRule,
        value: float,
        now: datetime,
        existing: Optional[AlertRecord],
    ) -> AlertRecord:
        assert self._conn is not None
        minute_bucket = now.strftime("%Y-%m-%d %H:%M")
        window_start = (now - timedelta(minutes=rule.window_minutes)).isoformat()
        payload = {
            "window_start": window_start,
            "window_end": now.isoformat(),
            "current_value": value,
            "threshold": rule.threshold,
            "last_seen_at": now.isoformat(),
            "consecutive_hits": 1,
        }

        if existing is None:
            status = "open" if rule.consecutive_hits <= 1 else "suppressed"
            return await self._insert_alert(rule, status=status, minute_bucket=minute_bucket, payload=payload)

        current_payload = existing.payload
        previous_hits = int(current_payload.get("consecutive_hits", 1))
        payload["consecutive_hits"] = previous_hits + 1

        if existing.status == "open":
            await self._update_alert_payload(existing.id, payload)
            return await self._get_alert_by_id(existing.id)

        if payload["consecutive_hits"] >= rule.consecutive_hits:
            await self._promote_alert_to_open(existing.id, rule.severity, minute_bucket, payload)
            opened = await self._get_alert_by_id(existing.id)
            assert opened is not None
            return opened

        await self._update_alert_payload(existing.id, payload)
        suppressed = await self._get_alert_by_id(existing.id)
        assert suppressed is not None
        return suppressed

    async def _handle_recovery(
        self,
        rule: AlertRule,
        value: float,
        now: datetime,
        existing: Optional[AlertRecord],
    ) -> Optional[AlertRecord]:
        del rule
        if existing is None:
            return None

        assert self._conn is not None
        payload = existing.payload
        payload["resolved_value"] = value
        payload["resolved_at"] = now.isoformat()
        await self._conn.execute(
            """
            UPDATE alerts
            SET status = 'resolved',
                resolved_at = ?,
                payload_json = ?
            WHERE id = ?
            """,
            (_sqlite_ts(now), json.dumps(payload, ensure_ascii=False), existing.id),
        )
        await self._conn.commit()
        return await self._get_alert_by_id(existing.id)

    async def _insert_alert(
        self,
        rule: AlertRule,
        *,
        status: str,
        minute_bucket: str,
        payload: dict[str, Any],
    ) -> AlertRecord:
        assert self._conn is not None
        await self._conn.execute(
            """
            INSERT INTO alerts(rule_name, severity, status, minute_bucket, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                rule.rule_name,
                rule.severity,
                status,
                minute_bucket,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        await self._conn.commit()
        async with self._conn.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT 1") as cursor:
            row = await cursor.fetchone()
        alert = self._row_to_alert(row)
        log_event(
            self._logger,
            event_name="alert_opened",
            level="warning" if rule.severity == "warning" else "error",
            component="observe",
            stage="alerting",
            job_id=None,
            session_id=None,
            rule_name=rule.rule_name,
            alert_status=status,
            current_value=payload["current_value"],
            threshold=rule.threshold,
        )
        return alert

    async def _update_alert_payload(self, alert_id: int, payload: dict[str, Any]) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE alerts SET payload_json = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), alert_id),
        )
        await self._conn.commit()

    async def _promote_alert_to_open(
        self,
        alert_id: int,
        severity: str,
        minute_bucket: str,
        payload: dict[str, Any],
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """
            UPDATE alerts
            SET status = 'open',
                severity = ?,
                minute_bucket = ?,
                payload_json = ?
            WHERE id = ?
            """,
            (severity, minute_bucket, json.dumps(payload, ensure_ascii=False), alert_id),
        )
        await self._conn.commit()

    async def _get_latest_active_alert(self, rule_name: str) -> Optional[AlertRecord]:
        assert self._conn is not None
        async with self._conn.execute(
            """
            SELECT *
            FROM alerts
            WHERE rule_name = ?
              AND status IN ('open', 'suppressed')
            ORDER BY id DESC
            LIMIT 1
            """,
            (rule_name,),
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_alert(row) if row is not None else None

    async def _get_alert_by_id(self, alert_id: int) -> Optional[AlertRecord]:
        assert self._conn is not None
        async with self._conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)) as cursor:
            row = await cursor.fetchone()
        return self._row_to_alert(row) if row is not None else None
