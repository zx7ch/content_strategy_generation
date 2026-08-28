from __future__ import annotations

import asyncio
import threading

import pytest

from app.content_research.service import ContentResearchService


@pytest.mark.asyncio
async def test_report_snapshot_busy_wait_does_not_block_async_transaction_owner(
    monkeypatch,
) -> None:
    service = object.__new__(ContentResearchService)
    main_thread_id = threading.get_ident()
    write_wait_started = threading.Event()
    competing_writer_released = threading.Event()
    expected = object()

    def create_result_snapshot(_workflow_run_id: str, **_kwargs):
        assert threading.get_ident() != main_thread_id
        write_wait_started.set()
        if not competing_writer_released.wait(timeout=1):
            raise RuntimeError("event loop was blocked by the synchronous SQLite wait")
        return expected

    monkeypatch.setattr(service, "create_result_snapshot", create_result_snapshot)

    async def release_competing_writer() -> None:
        while not write_wait_started.is_set():
            await asyncio.sleep(0)
        competing_writer_released.set()

    release_task = asyncio.create_task(release_competing_writer())
    result = await service._create_result_snapshot_off_event_loop(
        "run-1",
        result_type="governed_research_report",
        manifest=None,
        coverage_snapshot=None,
        execution_context=None,
    )
    await release_task

    assert result is expected
