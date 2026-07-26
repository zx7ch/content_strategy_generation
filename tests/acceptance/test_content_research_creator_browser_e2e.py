"""Real-browser acceptance for the R4 published-report Timeline contract."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.reporting.publication_materializer import ReportPublicationMaterializer
from app.content_research.models import ResearchBriefRecord, TraceRecord, utcnow
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from app.services.workflow_run_manager import WorkflowRunManager
from tests.acceptance.conftest import write_acceptance_artifact
from tests.acceptance.test_v2_phase1_console_walkthrough import (
    _chrome_executable,
    _reserve_port,
    _run_process,
)
from tests.integration.test_content_research_report_store import _decision, _publication
from tests.unit.test_content_research_report_composer import _snapshot as governed_snapshot


@pytest.fixture(scope="module")
def real_creator_stack(tmp_path_factory):
    repo_root = Path(__file__).resolve().parents[2]
    frontend_root = repo_root / "frontend"
    root = tmp_path_factory.mktemp("content_research_browser_e2e")
    db_path = root / "browser-e2e.db"
    chroma_dir = root / "chroma"
    backend_port = _reserve_port()
    frontend_port = _reserve_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    frontend_url = f"http://127.0.0.1:{frontend_port}"
    backend_env = {
        **os.environ,
        "SQLITE_DB_PATH": str(db_path),
        "CREATOR_THREADS_DB_PATH": str(db_path),
        "CHROMA_PERSIST_DIR": str(chroma_dir),
        "JOB_POLL_INTERVAL_MS": "50",
        "SSE_HEARTBEAT_SECONDS": "1",
        "CORS_ALLOWED_ORIGINS": f"http://localhost:3000,http://127.0.0.1:3000,{frontend_url}",
        "PYTHONPATH": str(repo_root),
    }
    frontend_env = {
        **os.environ,
        "NEXT_PUBLIC_XHS_API_BASE_URL": backend_url,
        "XHS_API_BASE_URL": backend_url,
        "NEXT_TELEMETRY_DISABLED": "1",
    }
    with _run_process(
        cmd=["python3", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(backend_port), "--log-level", "warning"],
        cwd=repo_root, env=backend_env, ready_url=f"{backend_url}/health", ready_timeout=30, name="backend",
    ):
        with _run_process(
            cmd=["npm", "run", "dev", "--", "--hostname", "127.0.0.1", "--port", str(frontend_port)],
            cwd=frontend_root, env=frontend_env, ready_url=f"{frontend_url}/creator", ready_timeout=60, name="frontend",
        ):
            yield {"frontend_url": frontend_url, "backend_url": backend_url, "db_path": str(db_path)}


@pytest.fixture()
def browser_page(real_creator_stack):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=_chrome_executable())
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        yield page, real_creator_stack
        browser.close()


def test_creator_browser_restores_published_report_from_timeline_artifact(
    browser_page, acceptance_artifact_dir: Path,
):
    """The rendered report must come from the persisted Timeline artifact after reload."""
    page, stack = browser_page
    started = time.perf_counter()
    with urlopen(Request(
        f"{stack['backend_url']}/brands",
        headers={"X-Workspace-Id": "00000000-0000-0000-0000-000000000001", "X-User-Id": "operator"},
    )) as response:
        brand_id = json.load(response)["items"][0]["id"]
    workflow_run_id, thread_id, other_thread_id = run_async_in_thread(
        seed_published_report(stack["db_path"], publication_state="partial_verified_report", brand_id=brand_id)
    )
    try:
        with urlopen(Request(
            f"{stack['backend_url']}/content-research/workflows/{workflow_run_id}/report",
            headers={"X-Workspace-Id": "00000000-0000-0000-0000-000000000001", "X-User-Id": "operator"},
        )) as response:
            assert response.status == 200
    except HTTPError as exc:
        raise AssertionError(f"published report endpoint failed: {exc.read().decode('utf-8')}") from exc
    requested_urls: list[str] = []
    page.on("request", lambda request: requested_urls.append(request.url))

    open_creator_with_restored_run(page, stack["frontend_url"], workflow_run_id)
    select_published_report_thread(page)

    report = page.locator('article[aria-label="Content Research published report"]')
    try:
        expect(report).to_have_count(1, timeout=20000)
    except AssertionError as exc:
        raise AssertionError(
            f"published Timeline report was not rendered; requests={requested_urls}; body={page.locator('body').inner_text()}"
        ) from exc
    expect(report.get_by_text("部分自由叙述未通过审计")).to_be_visible()
    header = report.locator('header[aria-label="Content Research report header"]')
    expect(header.get_by_text("研究范围：", exact=False)).to_be_visible()
    expect(header.get_by_text("发布日期未公开", exact=True)).to_be_visible()
    expect(header.get_by_text("冻结报告版本")).to_have_count(0)
    expect(report.get_by_role("button", name="打开引用 7")).to_be_visible()
    report.get_by_role("button", name="打开引用 7").click()
    citation = report.locator('aside[aria-label="Content Research citation evidence"]')
    expect(citation.get_by_text("通勤")).to_be_visible()
    expect(citation.get_by_role("link", name="打开原笔记")).to_have_count(1)
    expect(report.get_by_role("heading", name="跨方向张力").last).to_be_visible()
    expect(report.get_by_role("heading", name="初步信号").last).to_be_visible()
    expect(report.get_by_role("heading", name="研究范围与限制").last).to_be_visible()
    sidebar = page.locator('aside[aria-label="内容调研上下文"]')
    expect(sidebar).to_be_visible()
    expect(sidebar.get_by_text("研究运行 / Trace", exact=True)).to_be_visible()
    expect(sidebar.get_by_text("范围与检索冻结", exact=True)).to_be_visible()
    expect(sidebar.get_by_text("本次研究摘要", exact=True)).to_be_visible()
    sidebar.get_by_label("查看完整 workflow trace").click()
    trace = page.locator('section[aria-label="Content Research Trace"]')
    expect(trace).to_be_visible()
    expect(trace.get_by_text(f"workflow_run · {workflow_run_id}", exact=True)).to_be_visible()
    trace.get_by_label("关闭 Trace 对话框").click()
    assert any(f"/threads/{thread_id}/timeline" in url for url in requested_urls)
    assert not any(f"/content-research/workflows/{workflow_run_id}/results" in url for url in requested_urls)

    page.get_by_text("其他内容调研", exact=True).click()
    expect(page.locator('article[aria-label="Content Research published report"]')).to_have_count(0)
    page.get_by_text("已发布内容调研报告", exact=True).click()
    expect(page.locator('article[aria-label="Content Research published report"]')).to_have_count(1, timeout=20000)

    page.reload(wait_until="domcontentloaded")
    expect(page.locator('article[aria-label="Content Research published report"]')).to_have_count(1, timeout=20000)
    expect(page.get_by_text("部分自由叙述未通过审计")).to_be_visible()

    write_acceptance_artifact(
        acceptance_artifact_dir,
        "content_research_creator_published_report_timeline",
        {
            "frontend_url": stack["frontend_url"], "backend_url": stack["backend_url"],
            "workflow_run_id": workflow_run_id, "thread_id": thread_id,
            "other_thread_id": other_thread_id,
            "publication_state": "partial_verified_report",
            "latency_ms": int((time.perf_counter() - started) * 1000),
        },
    )


def open_creator_with_restored_run(page: Page, frontend_url: str, workflow_run_id: str) -> None:
    page.add_init_script(
        f"""
            window.localStorage.clear();
        """
    )
    page.goto(f"{frontend_url}/creator?contentResearchRunId={workflow_run_id}", wait_until="domcontentloaded")
    page.get_by_role("button", name=re.compile("内容调研")).wait_for(timeout=15000)


def select_published_report_thread(page: Page) -> None:
    trace = page.locator('section[aria-label="Content Research Trace"]')
    if trace.is_visible():
        trace.get_by_label("关闭 Trace 对话框").click()
    page.get_by_text("已发布内容调研报告", exact=True).click(timeout=15000)


async def seed_published_report(
    db_path: str, *, publication_state: str, brand_id: str
) -> tuple[str, str, str]:
    store = SQLiteContentResearchStore(db_path)
    # ThreadStore's persisted ordering has second precision.  The runtime
    # bootstrap creates its starter thread in the same database, so cross that
    # boundary before creating the report thread that Creator should restore.
    await asyncio.sleep(1.05)
    async with ThreadStore(db_path) as thread_store:
        other_thread = await thread_store.create_thread(
            title="其他内容调研",
            workspace_id="00000000-0000-0000-0000-000000000001",
            brand_id=brand_id,
        )
        await asyncio.sleep(1.05)
        thread = await thread_store.create_thread(
            title="已发布内容调研报告",
            workspace_id="00000000-0000-0000-0000-000000000001",
            brand_id=brand_id,
        )
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id=thread["id"], user_id="browser-e2e")

    brief = ResearchBriefRecord(
        id=f"rb_{run.run_id}", workflow_run_id=run.run_id, thread_id=thread["id"],
        schema_version="content_research_brief_v1", status="ready",
        payload={
            "schema_version": "content_research_brief_v1", "attempt_id": f"attempt_{run.run_id}",
            "subject_confirmation": "已发布内容调研报告", "confirmed_subject": "已发布内容调研报告",
            "selected_directions": ["product_marketing"],
        },
    )
    store.save_brief(brief)
    store.save_trace(TraceRecord(
        id=f"trace_{run.run_id}", workflow_run_id=run.run_id, thread_id=thread["id"],
        schema_version="content_research_trace_v1", status="completed", started_at=utcnow(),
        payload={"schema_version": "content_research_trace_v1", "trace_type": "published_report_browser_e2e"},
    ))
    snapshot = replace(governed_snapshot(), id=f"rrs_{run.run_id}", workflow_run_id=run.run_id)
    draft = ResearchReportComposer().compose(snapshot)
    decision = replace(_decision(draft), workflow_run_id=run.run_id)
    publication = replace(
        _publication(draft, decision), workflow_run_id=run.run_id, publication_state=publication_state,
        has_free_prose=publication_state != "evidence_only_report",
        omitted_section_ids=(draft.sections[0].section_id,) if publication_state == "partial_verified_report" else (),
    )
    store.save_result_snapshot(snapshot)
    store.save_report_draft(draft.to_record())
    store.save_report_faithfulness_decision(decision.to_record())
    store.save_report_publication(publication.to_record())
    async with WorkflowRunManager(db_path) as manager:
        await manager.complete_run(run.run_id)
    await ReportPublicationMaterializer(store, db_path).materialize(publication.id)
    return run.run_id, thread["id"], other_thread["id"]


def run_async_in_thread(coroutine):
    """pytest's configured event loop owns this thread; seed SQLite on another one."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coroutine).result()
