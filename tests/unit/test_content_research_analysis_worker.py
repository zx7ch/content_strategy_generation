from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.content_research.analysis_persistence import (
    AnalysisAttempt,
    AnalysisJobClaim,
    AnalysisJobContext,
)
from app.content_research.marketing_analysis_execution import (
    MarketingAnalysisExecutionError,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.worker import ContentResearchAnalysisWorker

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class TerminalizedAttemptRepository:
    def __init__(
        self,
        claim: AnalysisJobClaim,
        *,
        effective_attempt: AnalysisAttempt | None = None,
    ) -> None:
        self.claim = claim
        self.effective_attempt = effective_attempt or claim.attempt

    def list_expired_analysis_jobs(self, **_kwargs):
        return ()

    def claim_next_analysis_job(self, **_kwargs):
        return self.claim

    def get_analysis_attempt(self, attempt_id: str):
        assert attempt_id == self.claim.attempt.id
        return self.claim.attempt

    def get_effective_attempt_for_run(self, workflow_run_id: str):
        assert workflow_run_id == self.claim.context.workflow_run_id
        return self.effective_attempt


class TerminalizedFailureService:
    def __init__(self) -> None:
        self.recorded_failures: list[tuple[str, BaseException | str]] = []

    async def execute_claimed_analysis(self, claim: AnalysisJobClaim) -> None:
        raise MarketingAnalysisExecutionError(
            claim.attempt.id, {"value": "llm_protocol_incompatible"}
        )

    async def record_analysis_failure(
        self, workflow_run_id: str, error: BaseException | str, **_kwargs
    ) -> None:
        self.recorded_failures.append((workflow_run_id, error))


class PostAnalysisReportFailureService(TerminalizedFailureService):
    def __init__(self) -> None:
        super().__init__()
        self.report_failures: list[tuple[str, BaseException | str]] = []

    async def execute_claimed_analysis(self, claim: AnalysisJobClaim) -> None:
        raise RuntimeError("report composer failed after analysis succeeded")

    async def record_report_finalization_failure(
        self, workflow_run_id: str, error: BaseException | str
    ) -> None:
        self.report_failures.append((workflow_run_id, error))


class ExpiredAttemptRepository:
    def __init__(self, claim: AnalysisJobClaim) -> None:
        self.claim = claim
        self.claim_next_called = False

    def list_expired_analysis_jobs(self, **_kwargs):
        return (self.claim.attempt,)

    def get_analysis_job_context(self, analysis_unit_id: str):
        assert analysis_unit_id == self.claim.attempt.analysis_unit_id
        return self.claim.context

    def claim_next_analysis_job(self, **_kwargs):
        self.claim_next_called = True
        raise AssertionError("expired analysis must require explicit recovery")


@pytest.mark.asyncio
async def test_expired_analysis_converges_run_without_creating_or_claiming_successor(
    tmp_path,
) -> None:
    attempt = AnalysisAttempt(
        id="analysis-attempt-expired",
        analysis_unit_id="analysis-unit-expired",
        attempt_no=2,
        state="running",
        successor_of_attempt_id="analysis-attempt-first",
        lease_owner="analysis-worker",
        lease_token="expired-token",
        lease_expires_at=NOW - timedelta(seconds=1),
        created_at=NOW - timedelta(minutes=3),
        terminal_at=None,
    )
    claim = AnalysisJobClaim(
        context=AnalysisJobContext(
            analysis_unit_id=attempt.analysis_unit_id,
            workflow_run_id="run-expired-analysis",
            research_plan_id="research-plan",
            coverage_snapshot_id="coverage-snapshot",
            execution_authorization_id=None,
            manifest={},
            created_at=NOW - timedelta(minutes=3),
        ),
        attempt=attempt,
    )
    repository = ExpiredAttemptRepository(claim)
    service = TerminalizedFailureService()
    worker = ContentResearchAnalysisWorker(
        store=SQLiteContentResearchStore(str(tmp_path / "expired-analysis-worker.db")),
        service_factory=lambda: service,
        clock=lambda: NOW,
    )
    worker._repository = repository

    assert await worker.run_once() is True
    assert repository.claim_next_called is False
    assert [(run_id, str(error)) for run_id, error in service.recorded_failures] == [
        ("run-expired-analysis", "analysis_lease_expired")
    ]


@pytest.mark.asyncio
async def test_terminalized_current_attempt_still_converges_run_after_heartbeat_fence(
    tmp_path,
) -> None:
    attempt = AnalysisAttempt(
        id="analysis-attempt-current",
        analysis_unit_id="analysis-unit-current",
        attempt_no=2,
        state="failed",
        successor_of_attempt_id="analysis-attempt-first",
        lease_owner="analysis-worker",
        lease_token="lease-token",
        lease_expires_at=NOW + timedelta(minutes=2),
        created_at=NOW,
        terminal_at=NOW,
    )
    claim = AnalysisJobClaim(
        context=AnalysisJobContext(
            analysis_unit_id=attempt.analysis_unit_id,
            workflow_run_id="run-current-analysis-failure",
            research_plan_id="research-plan",
            coverage_snapshot_id="coverage-snapshot",
            execution_authorization_id=None,
            manifest={},
            created_at=NOW,
        ),
        attempt=attempt,
    )
    service = TerminalizedFailureService()
    worker = ContentResearchAnalysisWorker(
        store=SQLiteContentResearchStore(str(tmp_path / "analysis-worker.db")),
        service_factory=lambda: service,
        clock=lambda: NOW,
    )
    worker._repository = TerminalizedAttemptRepository(claim)

    async def heartbeat_after_terminalization(
        *, attempt_id, token, stop_event, lease_lost
    ) -> None:
        await stop_event.wait()
        lease_lost.set()

    worker._heartbeat = heartbeat_after_terminalization

    assert await worker.run_once() is True
    assert [item[0] for item in service.recorded_failures] == [
        "run-current-analysis-failure"
    ]


@pytest.mark.asyncio
async def test_terminalized_stale_attempt_cannot_fail_a_successor_run(tmp_path) -> None:
    stale_attempt = AnalysisAttempt(
        id="analysis-attempt-stale",
        analysis_unit_id="analysis-unit-current",
        attempt_no=1,
        state="failed",
        successor_of_attempt_id=None,
        lease_owner="analysis-worker-old",
        lease_token="lease-token-old",
        lease_expires_at=NOW + timedelta(minutes=2),
        created_at=NOW,
        terminal_at=NOW,
    )
    successor = AnalysisAttempt(
        id="analysis-attempt-successor",
        analysis_unit_id=stale_attempt.analysis_unit_id,
        attempt_no=2,
        state="running",
        successor_of_attempt_id=stale_attempt.id,
        lease_owner="analysis-worker-new",
        lease_token="lease-token-new",
        lease_expires_at=NOW + timedelta(minutes=2),
        created_at=NOW + timedelta(seconds=1),
        terminal_at=None,
    )
    claim = AnalysisJobClaim(
        context=AnalysisJobContext(
            analysis_unit_id=stale_attempt.analysis_unit_id,
            workflow_run_id="run-stale-analysis-failure",
            research_plan_id="research-plan",
            coverage_snapshot_id="coverage-snapshot",
            execution_authorization_id=None,
            manifest={},
            created_at=NOW,
        ),
        attempt=stale_attempt,
    )
    service = TerminalizedFailureService()
    worker = ContentResearchAnalysisWorker(
        store=SQLiteContentResearchStore(str(tmp_path / "stale-analysis-worker.db")),
        service_factory=lambda: service,
        clock=lambda: NOW,
    )
    worker._repository = TerminalizedAttemptRepository(
        claim, effective_attempt=successor
    )

    async def heartbeat_after_successor(
        *, attempt_id, token, stop_event, lease_lost
    ) -> None:
        await stop_event.wait()
        lease_lost.set()

    worker._heartbeat = heartbeat_after_successor

    assert await worker.run_once() is True
    assert service.recorded_failures == []


@pytest.mark.asyncio
async def test_post_analysis_report_failure_preserves_succeeded_attempt(tmp_path) -> None:
    running_attempt = AnalysisAttempt(
        id="analysis-attempt-report-failure",
        analysis_unit_id="analysis-unit-report-failure",
        attempt_no=1,
        state="running",
        successor_of_attempt_id=None,
        lease_owner="analysis-worker",
        lease_token="lease-token",
        lease_expires_at=NOW + timedelta(minutes=2),
        created_at=NOW,
        terminal_at=None,
    )
    succeeded_attempt = replace(
        running_attempt,
        state="succeeded",
        terminal_at=NOW + timedelta(seconds=1),
    )
    claim = AnalysisJobClaim(
        context=AnalysisJobContext(
            analysis_unit_id=running_attempt.analysis_unit_id,
            workflow_run_id="run-report-failure",
            research_plan_id="research-plan",
            coverage_snapshot_id="coverage-snapshot",
            execution_authorization_id=None,
            manifest={},
            created_at=NOW,
        ),
        attempt=running_attempt,
    )
    repository = TerminalizedAttemptRepository(
        replace(claim, attempt=succeeded_attempt),
        effective_attempt=succeeded_attempt,
    )
    repository.claim = claim
    repository.get_analysis_attempt = lambda _attempt_id: succeeded_attempt
    service = PostAnalysisReportFailureService()
    worker = ContentResearchAnalysisWorker(
        store=SQLiteContentResearchStore(str(tmp_path / "post-analysis-report.db")),
        service_factory=lambda: service,
        clock=lambda: NOW,
    )
    worker._repository = repository

    assert await worker.run_once() is True
    assert service.recorded_failures == []
    assert [(run_id, str(error)) for run_id, error in service.report_failures] == [
        ("run-report-failure", "report composer failed after analysis succeeded")
    ]
