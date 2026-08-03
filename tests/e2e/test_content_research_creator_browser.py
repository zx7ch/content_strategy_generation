"""Deterministic local-browser E2E for the Lite-only Creator vertical slice."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from app.content_research.contracts import (
    DIRECTION_CATALOG_V1,
    build_default_snapshot,
)
from app.content_research.models import ResearchBriefRecord, SubagentTaskRecord
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.reporting.publication_materializer import (
    ReportPublicationMaterializer,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from app.models.workflow import WorkflowPhase
from app.services.workflow_run_manager import WorkflowRunManager
from tests.browser_process import (
    chrome_executable,
    reserve_port,
    run_process,
)
from tests.integration.test_content_research_report_store import (
    _decision,
    _publication,
)
from tests.unit.test_content_research_report_composer import (
    _snapshot as governed_snapshot,
)

WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"
USER_HEADERS = {
    "X-Workspace-Id": WORKSPACE_ID,
    "X-User-Id": "operator",
}


@pytest.fixture()
def real_creator_stack(tmp_path, request):
    repo_root = Path(__file__).resolve().parents[2]
    frontend_root = repo_root / "frontend"
    db_path = tmp_path / "creator-lite.db"
    chroma_dir = tmp_path / "chroma"
    backend_port = reserve_port()
    frontend_port = reserve_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    frontend_url = f"http://127.0.0.1:{frontend_port}"
    parameters = getattr(request, "param", {})
    preview_enabled = parameters.get("preview_enabled", True)
    preview_value = "true" if preview_enabled else "false"
    backend_env = {
        **os.environ,
        "F003_LITE_PREVIEW_ENABLED": preview_value,
        "SQLITE_DB_PATH": str(db_path),
        "CREATOR_THREADS_DB_PATH": str(db_path),
        "CHROMA_PERSIST_DIR": str(chroma_dir),
        "JOB_POLL_INTERVAL_MS": "50",
        "SSE_HEARTBEAT_SECONDS": "1",
        "CORS_ALLOWED_ORIGINS": (
            f"http://localhost:3000,http://127.0.0.1:3000,{frontend_url}"
        ),
        "PYTHONPATH": str(repo_root),
        "CREATOR_E2E_FAIL_WORKFLOW_RESTORE": (
            "1"
            if parameters.get("fail_workflow_restore")
            else "0"
        ),
    }
    frontend_env = {
        **os.environ,
        "F003_LITE_PREVIEW_ENABLED": preview_value,
        "NEXT_DIST_DIR": f".next/e2e-{frontend_port}",
        "NEXT_PUBLIC_XHS_API_BASE_URL": backend_url,
        "XHS_API_BASE_URL": backend_url,
        "NEXT_TELEMETRY_DISABLED": "1",
    }
    with run_process(
        cmd=[
            "python3",
            "-m",
            "uvicorn",
            "tests.e2e.creator_browser_runtime:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(backend_port),
            "--log-level",
            "warning",
        ],
        cwd=repo_root,
        env=backend_env,
        ready_url=f"{backend_url}/health",
        ready_timeout=30,
        name="backend",
    ):
        with run_process(
            cmd=[
                "npm",
                "run",
                "dev",
                "--",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(frontend_port),
            ],
            cwd=frontend_root,
            env=frontend_env,
            ready_url=f"{frontend_url}/creator",
            ready_timeout=60,
            name="frontend",
        ):
            yield {
                "frontend_url": frontend_url,
                "backend_url": backend_url,
                "db_path": str(db_path),
            }


@pytest.fixture()
def browser_page(real_creator_stack):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=chrome_executable(),
        )
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        yield page, real_creator_stack
        browser.close()


@pytest.mark.parametrize(
    "selected_direction_ids",
    [
        ("product_marketing",),
        ("product_marketing", "content_performance"),
        tuple(DIRECTION_CATALOG_V1),
    ],
    ids=["single", "double", "all"],
)
def test_creator_brief_uses_fixed_catalog_and_submits_selected_subset(
    browser_page,
    selected_direction_ids,
):
    page, stack = browser_page
    presearch_payloads: list[dict] = []
    confirmation_responses: list[int] = []

    def record_presearch(response) -> None:
        if response.url.endswith("/content-research/presearch") and response.status == 201:
            presearch_payloads.append(response.json())
        if (
            response.url.endswith("/actions")
            and '"action":"confirm_brief"' in (response.request.post_data or "")
        ):
            confirmation_responses.append(response.status)

    page.on("response", record_presearch)
    page.goto(stack["frontend_url"] + "/creator", wait_until="domcontentloaded")
    page.get_by_role("button", name=re.compile("内容调研")).click(timeout=15000)
    page.locator("textarea").fill("夏季通勤短裤")
    page.locator("textarea").press("Enter")

    page.get_by_role("heading", name="在开始前，请确认几个关键点").wait_for(
        timeout=20000
    )
    expect(page.get_by_role("button", name="产品营销", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="竞品发现", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="内容表现", exact=True)).to_be_visible()
    for retired_label in ("UGC 社群互动", "用户评论痛点", "品牌活动", "高增长关键词"):
        expect(page.get_by_role("button", name=retired_label, exact=True)).to_have_count(0)

    page.get_by_role("button", name="准确，继续").click()
    direction_labels = {
        "product_marketing": "产品营销",
        "competitor_discovery": "竞品发现",
        "content_performance": "内容表现",
    }
    for direction_id in selected_direction_ids:
        page.get_by_role(
            "button",
            name=direction_labels[direction_id],
            exact=True,
        ).click()
    confirm_button = page.get_by_role(
        "button",
        name=re.compile("确认并开始调研"),
    )
    with page.expect_response(
        lambda response: response.url.endswith("/actions")
        and '"action":"confirm_brief"' in (response.request.post_data or ""),
        timeout=15000,
    ) as response_info:
        if selected_direction_ids == tuple(DIRECTION_CATALOG_V1):
            confirm_button.evaluate("(button) => { button.click(); button.click(); }")
        else:
            confirm_button.click()
    response = response_info.value

    assert response.status == 200
    assert presearch_payloads[-1]["direction_catalog"] == list(DIRECTION_CATALOG_V1)
    request_payload = response.request.post_data_json
    assert request_payload["payload"]["selected_directions"] == list(
        selected_direction_ids
    )
    if selected_direction_ids == tuple(DIRECTION_CATALOG_V1):
        page.wait_for_timeout(500)
        assert confirmation_responses == [200]
        with sqlite3.connect(stack["db_path"]) as connection:
            plan_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM content_research_plans
                WHERE workflow_run_id = ?
                """,
                (response.json()["workflow_run_id"],),
            ).fetchone()[0]
        assert plan_count == 1


@pytest.mark.parametrize(
    "real_creator_stack",
    [{"preview_enabled": False}],
    indirect=True,
)
def test_creator_hides_lite_entry_when_preview_is_disabled(browser_page):
    page, stack = browser_page

    page.goto(stack["frontend_url"] + "/creator", wait_until="domcontentloaded")

    expect(page.get_by_role("button", name=re.compile("内容调研"))).to_have_count(0)


def test_creator_model_service_card_masks_saved_key(browser_page):
    page, stack = browser_page
    page.goto(stack["frontend_url"] + "/creator", wait_until="networkidle")
    card = page.get_by_role("region", name="模型服务")
    expect(card).to_be_visible(timeout=15000)
    expect(card).to_have_count(1)
    summary = page.locator('section[aria-label="内容调研研究摘要"]')
    expect(summary).to_be_visible()
    assert summary.evaluate(
        "(summary) => Boolean(summary.compareDocumentPosition(document.querySelector('[aria-label=\"模型服务\"]')) & Node.DOCUMENT_POSITION_FOLLOWING)"
    )
    page.get_by_role("button", name=re.compile("内容调研")).click(timeout=15000)
    expect(page.get_by_role("region", name="模型服务")).to_have_count(1)
    card.get_by_role("button", name="配置模型").click()
    card.get_by_label("Base URL", exact=True).fill("https://proxy.example/v1")
    card.get_by_label("模型", exact=True).fill("model-x")
    card.get_by_label("API Key", exact=True).fill("secret-1234")
    page.get_by_role("button", name="测试连接").click()
    expect(page.get_by_text("连接验证成功")).to_be_visible()
    page.get_by_role("button", name="保存").click()
    expect(page.get_by_text("API Key：••••1234")).to_be_visible()
    expect(page.get_by_text("secret-1234", exact=True)).not_to_be_visible()


def test_creator_model_failure_edit_save_and_continue_same_presearch(browser_page):
    page, stack = browser_page
    retry_requests: list[dict] = []

    brand_id = default_brand_id(stack["backend_url"])
    first = run_async_in_thread(
        seed_model_presearch_recovery(
            stack["db_path"], brand_id=brand_id, title="模型失败恢复"
        )
    )

    def record_request(request) -> None:
        if request.url.endswith("/actions") and '"action":"retry_presearch"' in (request.post_data or ""):
            retry_requests.append(request.post_data_json)

    page.on("request", record_request)
    open_creator_with_restored_run(
        page, stack["frontend_url"], first["workflow_run_id"]
    )

    card = page.get_by_role("region", name="模型服务")
    expect(card.get_by_text("模型配置需要更新后才能继续调研。", exact=True)).to_be_visible(timeout=20000)
    expect(page.get_by_role("heading", name="在开始前，请确认几个关键点")).to_have_count(0)
    expect(card.get_by_role("button", name="继续调研")).to_have_count(0)
    page.reload(wait_until="networkidle")
    card = page.get_by_role("region", name="模型服务")
    expect(card.get_by_text("模型配置需要更新后才能继续调研。", exact=True)).to_be_visible(timeout=20000)
    expect(page.get_by_role("region", name="模型服务")).to_have_count(1)

    with sqlite3.connect(stack["db_path"]) as connection:
        before = (
            connection.execute(
                "SELECT COUNT(*) FROM content_research_stage_checkpoints WHERE workflow_run_id=?",
                (first["workflow_run_id"],),
            ).fetchone()[0],
            connection.execute(
                "SELECT COUNT(*) FROM content_research_directional_evidence_packets WHERE workflow_run_id=?",
                (first["workflow_run_id"],),
            ).fetchone()[0],
        )

    card.get_by_role("button", name="配置模型").click(force=True)
    card.get_by_label("Base URL", exact=True).fill("https://proxy.example/v1")
    card.get_by_label("模型", exact=True).fill("model-x")
    card.get_by_label("API Key", exact=True).fill("secret-1234")
    card.get_by_role("button", name="测试连接").click()
    expect(card.get_by_text("连接验证成功", exact=True)).to_be_visible()
    card.get_by_role("button", name="保存").click()
    continue_button = card.get_by_role("button", name="继续调研")
    expect(continue_button).to_be_enabled()

    def fail_continue(route) -> None:
        route.fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps({"error_message": "deterministic retry failure"}),
        )

    page.route("**/content-research/workflows/*/actions", fail_continue)
    continue_button.click()
    expect(card.get_by_text("继续调研失败，请重试。", exact=True)).to_be_visible()
    page.unroute("**/content-research/workflows/*/actions", fail_continue)

    with page.expect_response(
        lambda response: response.url.endswith("/actions")
        and '"action":"retry_presearch"' in (response.request.post_data or ""),
        timeout=15000,
    ) as retried_response:
        continue_button.evaluate("(button) => { button.click(); button.click(); }")
    retried = retried_response.value.json()["result"]

    assert retry_requests == [
        {"action": "retry_presearch", "payload": {}},
        {"action": "retry_presearch", "payload": {}},
    ]
    assert retried["workflow_run_id"] == first["workflow_run_id"]
    assert retried["attempt_id"] == first["attempt_id"]
    assert retried["brief_id"] == first["brief_id"]
    expect(page.get_by_role("heading", name="在开始前，请确认几个关键点")).to_be_visible()

    with sqlite3.connect(stack["db_path"]) as connection:
        after = (
            connection.execute(
                "SELECT COUNT(*) FROM content_research_stage_checkpoints WHERE workflow_run_id=?",
                (first["workflow_run_id"],),
            ).fetchone()[0],
            connection.execute(
                "SELECT COUNT(*) FROM content_research_directional_evidence_packets WHERE workflow_run_id=?",
                (first["workflow_run_id"],),
            ).fetchone()[0],
        )
    assert after == before


def test_creator_complete_report_uses_lite_with_direct_source_navigation(
    browser_page,
):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    seeded = run_async_in_thread(
        seed_publication(
            stack["db_path"],
            brand_id=brand_id,
            title="完整 Lite 报告",
            publication_state="complete_verified_report",
            requested_directions=("product_marketing",),
            direction_results={
                "product_marketing": {
                    "state": "formal_directional_result",
                    "limitations": [],
                    "recovery_actions": [],
                }
            },
            evidence_refs=all_navigation_evidence_refs()[:1],
        )
    )
    requested_urls: list[str] = []
    page.on("request", lambda request: requested_urls.append(request.url))

    open_creator_with_restored_run(page, stack["frontend_url"], seeded["run_id"])
    report = published_report(page)
    expect(report).to_be_visible(timeout=20000)
    expect(report.get_by_text("已完整核验", exact=True)).to_be_visible()
    source_link = report.get_by_role("link", name="查看原笔记")
    expect(source_link).to_have_count(1)
    expect(source_link).to_have_attribute(
        "href", "https://www.xiaohongshu.com/explore/available"
    )
    expect(source_link).to_have_attribute("target", "_blank")
    expect(source_link).to_have_attribute("rel", "noopener noreferrer")
    report.get_by_role("button", name="证据详情").first.click()
    drawer = report.locator('aside[aria-label="Content Research citation evidence"]')
    expect(drawer.get_by_text("可打开来源", exact=True)).to_be_visible()
    expect(drawer.get_by_text("2026-07-21T00:00:00Z", exact=True)).to_be_visible()
    expect(drawer.get_by_role("link", name="打开原笔记")).to_have_count(1)
    sidebar = page.locator('aside[aria-label="内容调研上下文"]')
    sidebar.get_by_label("查看完整 workflow trace").click()
    trace = page.locator('section[aria-label="Content Research Trace"]')
    expect(trace).to_be_visible()
    expect(trace.get_by_role("button", name="继续本轮调研")).to_have_count(0)
    expect(trace.get_by_role("button", name="登录成功，继续本轮调研")).to_have_count(0)
    expect(trace.get_by_role("button", name="扫码登录小红书")).to_have_count(0)
    with page.expect_response(
        lambda response: response.url.endswith("/trace")
        and response.request.method == "GET",
        timeout=15000,
    ):
        trace.get_by_role("button", name="刷新", exact=True).click()
    trace.get_by_label("关闭 Trace 对话框").click()

    assert any(
        f"/content-research/workflows/{seeded['run_id']}/lite-report" in url
        for url in requested_urls
    )
    assert any(url.endswith("/trace") for url in requested_urls)
    assert not any(
        url.endswith(f"/content-research/workflows/{seeded['run_id']}/report")
        for url in requested_urls
    )


def test_creator_trace_prioritizes_auth_required_child_and_resumes_once_after_qr(
    browser_page,
):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    seeded = run_async_in_thread(
        seed_auth_required_trace(
            stack["db_path"],
            brand_id=brand_id,
            title="等待登录的 Lite 调研",
            include_masking_non_auth_failure=True,
        )
    )
    requested_urls: list[str] = []
    resume_payloads: list[dict] = []

    def record_request(request) -> None:
        requested_urls.append(request.url)
        if request.url.endswith("/actions") and request.post_data:
            payload = request.post_data_json
            if payload.get("action") == "resume_formal_research":
                resume_payloads.append(payload)

    page.on("request", record_request)
    open_creator_with_restored_run(page, stack["frontend_url"], seeded["run_id"])

    sidebar = page.locator('aside[aria-label="内容调研上下文"]')
    expect(sidebar.get_by_text("研究运行 / Trace", exact=True)).to_be_visible(
        timeout=20000
    )
    expect(sidebar.get_by_text("需要登录小红书网页端", exact=True)).to_be_visible()
    expect(sidebar.get_by_text("专家调研进行中", exact=True)).to_have_count(0)
    full_trace = sidebar.get_by_label("查看完整 workflow trace")
    expect(full_trace).to_be_visible()
    with page.expect_response(
        lambda response: response.url.endswith(
            f"/content-research/workflows/{seeded['run_id']}/trace"
        ) and response.request.method == "GET",
        timeout=15000,
    ):
        full_trace.click()

    trace = page.locator('section[aria-label="Content Research Trace"]')
    expect(trace).to_be_visible()
    expect(trace.get_by_text("当前进度：需要登录小红书网页端", exact=True)).to_be_visible()
    expect(trace.locator('section[aria-label="Content Research Trace timeline"]')).to_be_visible()
    child = trace.locator('article[aria-label="内容调研子任务"]')
    expect(child.get_by_text("产品营销专家", exact=True)).to_be_visible()
    expect(child.get_by_text("需要登录", exact=True)).to_be_visible()
    provider = trace.locator('article[aria-label="采集异常诊断"]')
    expect(provider.get_by_text("discover_candidates · auth_required", exact=True)).to_be_visible()
    expect(provider.get_by_text("更新小红书登录态后继续。", exact=True)).to_be_visible()
    expect(trace.get_by_text("RAW_PROVIDER_QUERY_MUST_NOT_RENDER", exact=True)).to_have_count(0)
    expect(trace.get_by_role("button", name="继续本轮调研")).to_have_count(0)

    with page.expect_response(
        lambda response: response.url.endswith(
            "/content-research/providers/xiaohongshu/login/qr"
        )
        and response.request.method == "POST",
        timeout=15000,
    ) as qr_response_info:
        trace.get_by_role("button", name="扫码登录小红书").click()
    assert qr_response_info.value.status == 200
    expect(trace.get_by_alt_text("小红书登录二维码")).to_be_visible()
    resume = trace.get_by_role("button", name="登录成功，继续本轮调研")
    expect(resume).to_be_visible(timeout=10000)

    with sqlite3.connect(stack["db_path"]) as connection:
        assert connection.execute(
            "SELECT status FROM workflow_runs WHERE run_id = ?",
            (seeded["run_id"],),
        ).fetchone()[0] == "running"
    with page.expect_response(
        lambda response: response.url.endswith("/actions")
        and '"action":"resume_formal_research"'
        in (response.request.post_data or ""),
        timeout=15000,
    ) as resume_response_info:
        resume.click()
    assert resume_response_info.value.status == 200
    assert resume_payloads == [{"action": "resume_formal_research", "payload": {}}]
    assert any(url.endswith("/trace") for url in requested_urls)
    assert any("/lite-report" in url for url in requested_urls)
    assert not any(url.endswith("/report") or url.endswith("/results") for url in requested_urls)


def test_creator_nonrecoverable_provider_failure_never_offers_resume(browser_page):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    seeded = run_async_in_thread(
        seed_auth_required_trace(
            stack["db_path"],
            brand_id=brand_id,
            title="不可恢复的 Lite 调研",
            child_failure_code="provider_access_rejected",
        )
    )
    resume_payloads: list[dict] = []

    def record_request(request) -> None:
        if request.url.endswith("/actions") and request.post_data:
            payload = request.post_data_json
            if payload.get("action") == "resume_formal_research":
                resume_payloads.append(payload)

    page.on("request", record_request)
    open_creator_with_restored_run(page, stack["frontend_url"], seeded["run_id"])

    sidebar = page.locator('aside[aria-label="内容调研上下文"]')
    expect(sidebar.get_by_text(re.compile("可继续"))).to_have_count(0)
    sidebar.get_by_label("查看完整 workflow trace").click()
    trace = page.locator('section[aria-label="Content Research Trace"]')
    expect(trace).to_be_visible()
    expect(trace.get_by_text(re.compile("可继续"))).to_have_count(0)
    expect(trace.get_by_role("button", name="继续本轮调研")).to_have_count(0)
    expect(trace.get_by_role("button", name="登录成功，继续本轮调研")).to_have_count(0)
    expect(trace.get_by_role("button", name="扫码登录小红书")).to_have_count(0)
    assert resume_payloads == []


def test_creator_auth_required_child_beats_later_non_auth_child(browser_page):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    seeded = run_async_in_thread(
        seed_auth_required_trace(
            stack["db_path"],
            brand_id=brand_id,
            title="子任务认证优先级",
            include_masking_non_auth_child=True,
            include_provider_checkpoint=False,
        )
    )

    open_creator_with_restored_run(page, stack["frontend_url"], seeded["run_id"])
    sidebar = page.locator('aside[aria-label="内容调研上下文"]')
    expect(sidebar.get_by_text("需要登录小红书网页端", exact=True)).to_be_visible()
    sidebar.get_by_label("查看完整 workflow trace").click()
    trace = page.locator('section[aria-label="Content Research Trace"]')
    expect(trace.get_by_role("button", name="扫码登录小红书")).to_be_visible()


def test_creator_full_trace_entry_is_visible_on_narrow_screens_for_every_run_state(
    browser_page,
):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    running = run_async_in_thread(
        seed_auth_required_trace(
            stack["db_path"],
            brand_id=brand_id,
            title="窄屏运行中调研",
        )
    )
    terminal = run_async_in_thread(
        seed_publication(
            stack["db_path"],
            brand_id=brand_id,
            title="窄屏已完成调研",
            publication_state="complete_verified_report",
            requested_directions=("product_marketing",),
            direction_results={
                "product_marketing": {
                    "state": "formal_directional_result",
                    "limitations": [],
                    "recovery_actions": [],
                }
            },
            evidence_refs=all_navigation_evidence_refs()[:1],
        )
    )
    failed = run_async_in_thread(
        seed_auth_required_trace(
            stack["db_path"],
            brand_id=brand_id,
            title="窄屏失败调研",
            child_failure_code="provider_access_rejected",
            fail_parent=True,
        )
    )
    page.set_viewport_size({"width": 640, "height": 800})

    for seeded in (running, terminal, failed):
        open_creator_with_restored_run(page, stack["frontend_url"], seeded["run_id"])
        entry = page.locator('[aria-label="查看完整 workflow trace"]:visible')
        expect(entry).to_have_count(1)
        entry.click()
        trace = page.locator('section[aria-label="Content Research Trace"]')
        expect(trace).to_be_visible()
        trace.get_by_label("关闭 Trace 对话框").click()


@pytest.mark.parametrize(
    ("title", "ref_index", "explanation", "navigation_reason"),
    [
        (
            "缺失来源链接 Lite 报告",
            1,
            "未保存来源链接；可查看原文片段与采集时间",
            None,
        ),
        (
            "不可导航来源 Lite 报告",
            2,
            "来源链接当前不可打开；可查看原文片段与采集时间",
            "provider_auth_required",
        ),
    ],
)
def test_creator_complete_report_uses_lite_keeps_non_navigable_citation_details(
    browser_page,
    title,
    ref_index,
    explanation,
    navigation_reason,
):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    evidence_ref = all_navigation_evidence_refs()[ref_index]
    seeded = run_async_in_thread(
        seed_publication(
            stack["db_path"],
            brand_id=brand_id,
            title=title,
            publication_state="complete_verified_report",
            requested_directions=("product_marketing",),
            direction_results={
                "product_marketing": {
                    "state": "formal_directional_result",
                    "limitations": [],
                    "recovery_actions": [],
                }
            },
            evidence_refs=[evidence_ref],
        )
    )

    open_creator_with_restored_run(page, stack["frontend_url"], seeded["run_id"])
    report = published_report(page)
    expect(report).to_be_visible(timeout=20000)
    expect(report.get_by_role("link", name="查看原笔记")).to_have_count(0)
    report.get_by_role("button", name="证据详情").first.click()
    drawer = report.locator('aside[aria-label="Content Research citation evidence"]')
    expect(drawer.get_by_text(evidence_ref["quote"], exact=True)).to_be_visible()
    expect(
        drawer.get_by_text(evidence_ref["source_collected_at"], exact=True)
    ).to_be_visible()
    expect(drawer.get_by_text(explanation, exact=True)).to_be_visible()
    expect(drawer.get_by_role("link", name="打开原笔记")).to_have_count(0)
    if navigation_reason:
        expect(drawer.get_by_text(navigation_reason, exact=True)).to_be_visible()
    else:
        expect(drawer.get_by_text("provider_auth_required", exact=True)).to_have_count(0)


def test_creator_partial_report_shows_requested_unavailable_direction_only(
    browser_page,
):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    seeded = run_async_in_thread(
        seed_publication(
            stack["db_path"],
            brand_id=brand_id,
            title="部分 Lite 报告",
            publication_state="partial_verified_report",
            requested_directions=("competitor_discovery",),
            direction_results={
                "competitor_discovery": {
                    "state": "not_started",
                    "limitations": ["provider_capability_unavailable"],
                    "recovery_actions": [],
                },
            },
            evidence_refs=all_navigation_evidence_refs()[:1],
        )
    )

    open_creator_with_restored_run(page, stack["frontend_url"], seeded["run_id"])
    report = published_report(page)
    expect(report).to_be_visible(timeout=20000)
    direction_section = report.locator('section[aria-label="方向状态"]')
    expect(direction_section.get_by_text(re.compile("竞品发现.*unavailable"))).to_be_visible()
    expect(
        direction_section.get_by_text("collection_result_unavailable", exact=True)
    ).to_be_visible()
    expect(report.locator('section[aria-label="核心发现"]')).to_have_count(0)
    expect(report.get_by_role("button", name="打开引用 7")).to_have_count(0)
    expect(report.get_by_text(re.compile("0 条已验证发现"))).to_be_visible()
    expect(direction_section.get_by_text(re.compile("产品营销"))).to_have_count(0)
    expect(direction_section.get_by_text(re.compile("内容表现"))).to_have_count(0)


def test_creator_evidence_only_report_shows_saved_evidence_and_reason_only(
    browser_page,
):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    seeded = run_async_in_thread(
        seed_publication(
            stack["db_path"],
            brand_id=brand_id,
            title="仅证据 Lite 报告",
            publication_state="evidence_only_report",
            requested_directions=("product_marketing",),
            direction_results={
                "product_marketing": {
                    "state": "unavailable",
                    "limitations": ["insufficient_admitted_evidence"],
                    "recovery_actions": [],
                }
            },
            evidence_refs=all_navigation_evidence_refs()[:1],
            publication_reason="insufficient_admitted_evidence",
        )
    )

    open_creator_with_restored_run(page, stack["frontend_url"], seeded["run_id"])
    report = published_report(page)
    expect(report).to_be_visible(timeout=20000)
    expect(report.get_by_text("1 条已保存依据", exact=True)).to_be_visible()
    expect(
        report.get_by_text("insufficient_admitted_evidence", exact=True)
    ).to_be_visible()
    expect(report.get_by_role("heading", name="已保存依据", exact=True)).to_be_visible()
    report.get_by_role("button", name="证据详情").first.click()
    expect(
        report.locator('aside[aria-label="Content Research citation evidence"]').get_by_text(
            "可打开来源",
            exact=True,
        )
    ).to_be_visible()
    for hidden_section in ("核心发现", "样本观察", "线索", "方向状态", "研究限制"):
        expect(report.locator(f'section[aria-label="{hidden_section}"]')).to_have_count(0)


def test_creator_replaces_recovery_with_publication_after_resume(browser_page):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    seeded = run_async_in_thread(
        seed_recovery(
            stack["db_path"],
            brand_id=brand_id,
            title="可恢复 Lite 调研",
        )
    )

    open_creator_with_restored_run(page, stack["frontend_url"], seeded["run_id"])
    recovery = page.locator('section[aria-label="Content Research recovery status"]')
    expect(recovery).to_be_visible(timeout=20000)
    expect(published_report(page)).to_have_count(0)
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload(wait_until="domcontentloaded")
    expect(recovery).to_be_visible(timeout=20000)
    expect(published_report(page)).to_have_count(0)

    recovery.get_by_role("button", name="打开登录恢复").click()
    trace = page.locator('section[aria-label="Content Research Trace"]')
    expect(trace).to_be_visible()
    with page.expect_response(
        lambda response: response.url.endswith(
            "/content-research/providers/xiaohongshu/login/qr"
        )
        and response.request.method == "POST",
        timeout=15000,
    ):
        trace.get_by_role("button", name="扫码登录小红书").click()
    resume = trace.get_by_role("button", name="登录成功，继续本轮调研")
    expect(resume).to_be_visible(timeout=10000)
    with page.expect_response(
        lambda response: response.url.endswith("/actions"),
        timeout=15000,
    ) as response_info:
        resume.click()
    action_response = response_info.value
    assert action_response.status == 200
    assert action_response.json()["action"] == "resume_formal_research"

    run_async_in_thread(
        publish_existing_recovery_run(
            stack["db_path"],
            run_id=seeded["run_id"],
            brief_id=seeded["brief_id"],
            publication_state="complete_verified_report",
        )
    )

    report = published_report(page)
    expect(report).to_be_visible(timeout=20000)
    expect(report.get_by_text("已完整核验", exact=True)).to_be_visible()
    expect(recovery).to_have_count(0)
    expect(published_report(page)).to_have_count(1)


@pytest.mark.parametrize(
    ("reason", "expected_reason"),
    [
        ("auth_required", "需要登录小红书网页端"),
        ("rate_limited", "当前访问过于频繁，请稍后再继续"),
        ("transient_error", "采集服务暂时不可用，请检查服务状态后继续"),
        ("permanent_error", "采集服务暂时不可用，请检查服务状态后继续"),
        ("parser_error", "采集服务暂时不可用，请检查服务状态后继续"),
        ("unknown", "采集服务暂时不可用，请检查服务状态后继续"),
        ("unavailable", "采集服务暂时不可用，请检查服务状态后继续"),
    ],
)
def test_creator_removes_historical_trace_retry_copy_for_every_source_failure_reason(
    browser_page, reason, expected_reason
):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    run_async_in_thread(
        seed_historical_message(
            stack["db_path"],
            brand_id=brand_id,
            text=f"小红书采集未完成：{reason}。可在「查看调研过程」中重试。",
        )
    )

    page.goto(stack["frontend_url"] + "/creator", wait_until="domcontentloaded")

    expect(
        page.get_by_text(
            f"小红书采集未完成：{expected_reason}。",
            exact=True,
        )
    ).to_be_visible(timeout=20000)
    expect(page.get_by_text(re.compile("查看调研过程"))).to_have_count(0)
    expect(page.get_by_text(re.compile("重试"))).to_have_count(0)


@pytest.mark.parametrize(
    "real_creator_stack",
    [{"fail_workflow_restore": True}],
    indirect=True,
)
def test_creator_keeps_saved_run_and_surfaces_workflow_restore_500(browser_page):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    seeded = run_async_in_thread(
        seed_recovery(
            stack["db_path"],
            brand_id=brand_id,
            title="恢复失败仍保留入口",
        )
    )
    storage_key = "xhs-growth-agent:content-research-active-runs-by-thread"
    page.add_init_script(
        "window.localStorage.clear();"
        f"window.localStorage.setItem({json.dumps(storage_key)}, "
        f"{json.dumps(json.dumps({seeded['thread_id']: seeded['run_id']}))});"
    )
    try:
        urlopen(
            Request(
                f"{stack['backend_url']}/content-research/workflows/{seeded['run_id']}",
                headers=USER_HEADERS,
            )
        )
    except HTTPError as error:
        direct_status = error.code
        direct_body = error.read().decode("utf-8")
    else:
        raise AssertionError("corrupt workflow unexpectedly restored")
    assert direct_status == 500, direct_body
    page.goto(stack["frontend_url"] + "/creator", wait_until="domcontentloaded")

    expect(
        page.get_by_text(re.compile("deterministic workflow restore failure")).first
    ).to_be_visible(
        timeout=20000
    )
    saved = page.evaluate(
        "(key) => JSON.parse(window.localStorage.getItem(key) || '{}')",
        storage_key,
    )
    assert saved == {seeded["thread_id"]: seeded["run_id"]}


def test_creator_surfaces_non_not_found_lite_report_error(browser_page):
    page, stack = browser_page
    lite_responses: list[tuple[int, str]] = []
    page.on(
        "response",
        lambda response: lite_responses.append((response.status, response.url))
        if "/lite-report" in response.url
        else None,
    )
    brand_id = default_brand_id(stack["backend_url"])
    seeded = run_async_in_thread(
        seed_publication(
            stack["db_path"],
            brand_id=brand_id,
            title="损坏 Lite 报告",
            publication_state="complete_verified_report",
            requested_directions=("product_marketing",),
            direction_results={
                "product_marketing": {
                    "state": "formal_directional_result",
                    "limitations": [],
                    "recovery_actions": [],
                }
            },
            evidence_refs=all_navigation_evidence_refs()[:1],
        )
    )
    with sqlite3.connect(stack["db_path"]) as connection:
        row = connection.execute(
            "SELECT artifact_id, payload_json FROM workflow_artifacts WHERE run_id = ?",
            (seeded["run_id"],),
        ).fetchone()
        assert row is not None
        artifact_payload = json.loads(row[1])
        artifact_payload.pop("sections")
        connection.execute(
            """
            UPDATE workflow_artifacts
            SET payload_json = ?
            WHERE artifact_id = ?
            """,
            (json.dumps(artifact_payload), row[0]),
        )
        connection.execute(
            "UPDATE workflow_runs SET status = 'paused' WHERE run_id = ?",
            (seeded["run_id"],),
        )
    SQLiteContentResearchStore(stack["db_path"]).save_stage_checkpoint(
        StageCheckpointRecord(
            id=f"checkpoint_corrupt_{seeded['run_id']}",
            schema_version="content_research_stage_checkpoint_v1",
            payload={"reason_code": "auth_expired"},
            workflow_run_id=seeded["run_id"],
            subagent_task_id=f"task_corrupt_{seeded['run_id']}",
            stage_name="operation",
            input_fingerprint="corrupt-existing-publication",
            status="failed",
        )
    )

    try:
        urlopen(
            Request(
                f"{stack['backend_url']}/content-research/workflows/{seeded['run_id']}/lite-report",
                headers=USER_HEADERS,
            )
        )
    except HTTPError as error:
        direct_status = error.code
        direct_body = error.read().decode("utf-8")
    else:
        raise AssertionError("corrupt publication unexpectedly returned a Lite report")
    assert direct_status != 404, direct_body

    open_creator_with_restored_run(page, stack["frontend_url"], seeded["run_id"])

    try:
        expect(page.get_by_text(re.compile("正式报告暂不可读取")).first).to_be_visible(
            timeout=20000
        )
    except AssertionError as error:
        raise AssertionError(
            f"Lite error was not visible; direct={direct_status} {direct_body}; "
            f"browser_responses={lite_responses}; body={page.locator('body').inner_text()}"
        ) from error


def open_creator_with_restored_run(
    page: Page,
    frontend_url: str,
    workflow_run_id: str,
) -> None:
    page.add_init_script("window.localStorage.clear();")
    page.goto(
        f"{frontend_url}/creator?contentResearchRunId={workflow_run_id}",
        wait_until="domcontentloaded",
    )
    page.get_by_role("button", name=re.compile("内容调研")).wait_for(timeout=15000)


def published_report(page: Page):
    return page.locator('article[aria-label="Content Research published report"]')


def default_brand_id(backend_url: str) -> str:
    with urlopen(Request(f"{backend_url}/brands", headers=USER_HEADERS)) as response:
        return str(json.load(response)["items"][0]["id"])


async def seed_publication(
    db_path: str,
    *,
    brand_id: str,
    title: str,
    publication_state: str,
    requested_directions: tuple[str, ...],
    direction_results: dict[str, dict],
    evidence_refs: list[dict],
    publication_reason: str | None = None,
) -> dict[str, str]:
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(
            title=title,
            workspace_id=WORKSPACE_ID,
            brand_id=brand_id,
        )
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id=thread["id"], user_id="browser-e2e")
    brief = ResearchBriefRecord(
        id=f"brief_{run.run_id}",
        workflow_run_id=run.run_id,
        thread_id=thread["id"],
        schema_version="content_research_brief_v1",
        status="ready",
        payload={
            "schema_version": "content_research_brief_payload_v1",
            "confirmed_subject": title,
            "selected_directions": list(requested_directions),
            "requested_direction_ids": list(requested_directions),
            "direction_catalog": list(DIRECTION_CATALOG_V1),
        },
    )
    store = SQLiteContentResearchStore(db_path)
    store.save_brief(brief)
    publication_id = await save_publication(
        store,
        db_path,
        run_id=run.run_id,
        brief_id=brief.id,
        publication_state=publication_state,
        requested_directions=requested_directions,
        direction_results=direction_results,
        evidence_refs=evidence_refs,
        publication_reason=publication_reason,
    )
    return {
        "run_id": run.run_id,
        "thread_id": thread["id"],
        "brief_id": brief.id,
        "publication_id": publication_id,
    }


async def seed_recovery(
    db_path: str,
    *,
    brand_id: str,
    title: str,
) -> dict[str, str]:
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(
            title=title,
            workspace_id=WORKSPACE_ID,
            brand_id=brand_id,
        )
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id=thread["id"], user_id="browser-e2e")
    brief = ResearchBriefRecord(
        id=f"brief_{run.run_id}",
        workflow_run_id=run.run_id,
        thread_id=thread["id"],
        schema_version="content_research_brief_v1",
        status="ready",
        payload={
            "schema_version": "content_research_brief_payload_v1",
            "confirmed_subject": title,
            "selected_directions": ["product_marketing"],
            "requested_direction_ids": ["product_marketing"],
            "direction_catalog": list(DIRECTION_CATALOG_V1),
        },
    )
    store = SQLiteContentResearchStore(db_path)
    store.save_brief(brief)
    policy, _, _ = build_default_snapshot(
        snapshot_id=f"policy_{run.run_id}",
        workflow_run_id=run.run_id,
        brief_id=brief.id,
        plan_id=f"plan_{run.run_id}",
        direction_set_version="direction_set_v1",
        direction_ids=("product_marketing",),
        direction_catalog=DIRECTION_CATALOG_V1,
        report_compose_mode="template_only",
    )
    store.save_run_policy_snapshot(policy)
    store.save_stage_checkpoint(
        StageCheckpointRecord(
            id=f"checkpoint_completed_{run.run_id}",
            schema_version="content_research_stage_checkpoint_v1",
            payload={},
            workflow_run_id=run.run_id,
            subagent_task_id=f"task_{run.run_id}",
            stage_name="collect",
            input_fingerprint="completed-collect",
            status="completed",
        )
    )
    store.save_stage_checkpoint(
        StageCheckpointRecord(
            id=f"checkpoint_failed_{run.run_id}",
            schema_version="content_research_stage_checkpoint_v1",
            payload={"reason_code": "auth_expired"},
            workflow_run_id=run.run_id,
            subagent_task_id=f"task_{run.run_id}",
            stage_name="operation",
            input_fingerprint="failed-operation",
            status="failed",
        )
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE workflow_runs SET status = 'paused' WHERE run_id = ?",
            (run.run_id,),
        )
    return {
        "run_id": run.run_id,
        "thread_id": thread["id"],
        "brief_id": brief.id,
    }


async def seed_model_presearch_recovery(
    db_path: str,
    *,
    brand_id: str,
    title: str,
) -> dict[str, str]:
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(
            title=title,
            workspace_id=WORKSPACE_ID,
            brand_id=brand_id,
        )
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(
            thread_id=thread["id"],
            user_id="operator",
            initial_request=title,
        )
        await manager.initialize_steps(
            run.run_id,
            [
                {
                    "step_name": "presearch",
                    "phase": WorkflowPhase.INTAKE,
                    "max_attempts": 3,
                },
                {
                    "step_name": "brief_confirm",
                    "phase": WorkflowPhase.INTAKE,
                    "max_attempts": 1,
                },
                {
                    "step_name": "plan_build",
                    "phase": WorkflowPhase.INTAKE,
                    "max_attempts": 1,
                },
                {
                    "step_name": "formal_research",
                    "phase": WorkflowPhase.RETRIEVAL,
                    "max_attempts": 1,
                },
            ],
        )
        await manager.start_step(run.run_id, "presearch")
        await manager.wait_for_user_recovery(
            run.run_id,
            step_name="presearch",
            reason={"code": "llm_auth_invalid", "message": "API Key 无效"},
        )
    brief = ResearchBriefRecord(
        id=f"brief_{run.run_id}",
        workflow_run_id=run.run_id,
        thread_id=thread["id"],
        schema_version="content_research_brief_v1",
        status="draft",
        payload={
            "schema_version": "content_research_brief_v1",
            "attempt_id": f"attempt_{run.run_id}",
            "seed_text": title,
            "user_note": None,
            "workspace_id": WORKSPACE_ID,
            "user_id": "operator",
            "status": "waiting_model_config",
            "subject_confirmation": title,
            "competitor_tags": [],
            "research_directions": [],
            "direction_catalog": list(DIRECTION_CATALOG_V1),
            "custom_research_question": "",
            "custom_competitor_input": "",
            "timeout_status": "none",
            "fallback_used": False,
            "error_code": "llm_auth_invalid",
            "error_message": "API Key 无效",
            "recoverable": True,
            "configuration_source": "user",
            "model": "deterministic-e2e",
        },
    )
    SQLiteContentResearchStore(db_path).save_brief(brief)
    return {
        "workflow_run_id": run.run_id,
        "attempt_id": str(brief.payload["attempt_id"]),
        "brief_id": brief.id,
        "thread_id": thread["id"],
    }


async def seed_auth_required_trace(
    db_path: str,
    *,
    brand_id: str,
    title: str,
    child_failure_code: str = "auth_required",
    include_masking_non_auth_failure: bool = False,
    include_masking_non_auth_child: bool = False,
    include_provider_checkpoint: bool = True,
    fail_parent: bool = False,
) -> dict[str, str]:
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(
            title=title,
            workspace_id=WORKSPACE_ID,
            brand_id=brand_id,
        )
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id=thread["id"], user_id="browser-e2e")
        steps = await manager.initialize_steps(
            run.run_id,
            [{"step_name": "formal_research", "phase": "retrieval", "max_attempts": 1}],
        )
        await manager.start_step(run.run_id, "formal_research")
        children = await manager.create_child_tasks(
            run_id=run.run_id,
            step_id=steps[0].step_id,
            tasks=[
                {
                    "task_type": "content_research.product_marketing",
                    "max_attempts": 2,
                }
            ],
        )
        await manager.start_child_task(children[0].child_task_id)
        await manager.fail_child_task(
            children[0].child_task_id,
            {"code": child_failure_code, "message": "provider failure"},
        )
        if include_masking_non_auth_child:
            masking_child = (await manager.create_child_tasks(
                run_id=run.run_id,
                step_id=steps[0].step_id,
                tasks=[{"task_type": "content_research.competitor_discovery", "max_attempts": 2}],
            ))[0]
            await manager.start_child_task(masking_child.child_task_id)
            await manager.fail_child_task(
                masking_child.child_task_id,
                {"code": "provider_unavailable", "message": "provider unavailable"},
            )
        if fail_parent:
            await manager.fail_run(run.run_id, {"code": child_failure_code, "message": "provider failure"})
    brief = ResearchBriefRecord(
        id=f"brief_{run.run_id}",
        workflow_run_id=run.run_id,
        thread_id=thread["id"],
        schema_version="content_research_brief_v1",
        status="ready",
        payload={
            "schema_version": "content_research_brief_payload_v1",
            "confirmed_subject": title,
            "selected_directions": ["product_marketing"],
            "requested_direction_ids": ["product_marketing"],
            "direction_catalog": list(DIRECTION_CATALOG_V1),
        },
    )
    store = SQLiteContentResearchStore(db_path)
    store.save_brief(brief)
    policy, _, _ = build_default_snapshot(
        snapshot_id=f"policy_{run.run_id}",
        workflow_run_id=run.run_id,
        brief_id=brief.id,
        plan_id=f"plan_{run.run_id}",
        direction_set_version="direction_set_v1",
        direction_ids=("product_marketing",),
        direction_catalog=DIRECTION_CATALOG_V1,
        report_compose_mode="template_only",
    )
    store.save_run_policy_snapshot(policy)
    if include_provider_checkpoint:
        store.save_stage_checkpoint(
        StageCheckpointRecord(
            id=f"checkpoint_provider_failure_{run.run_id}",
            schema_version="content_research_stage_checkpoint_v1",
            payload={
                "operation": "discover_candidates",
                "operation_fingerprint": "browser-provider-failure-operation",
                "request": {"query": "RAW_PROVIDER_QUERY_MUST_NOT_RENDER"},
                "completion": {
                    "provider": "xiaohongshu",
                    "provider_operation": "discover_candidates",
                    "source_kind": "search_result_minimal",
                    "result_status": "failed",
                    "item_count": 0,
                    "completeness": "unavailable",
                    "failure_code": child_failure_code,
                    "failure_reason": child_failure_code,
                    "retryable": child_failure_code == "auth_required",
                    "recovery_action": "更新小红书登录态后继续。" if child_failure_code == "auth_required" else None,
                },
            },
            workflow_run_id=run.run_id,
            subagent_task_id=children[0].child_task_id,
            stage_name="operation",
            input_fingerprint="browser-provider-failure-operation",
            status=child_failure_code,
        )
        )
    if include_masking_non_auth_failure:
        store.save_stage_checkpoint(
            StageCheckpointRecord(
                id=f"checkpoint_non_auth_failure_{run.run_id}",
                schema_version="content_research_stage_checkpoint_v1",
                payload={
                    "operation": "collect_detail",
                    "operation_fingerprint": "browser-non-auth-failure-operation",
                    "completion": {
                        "provider": "xiaohongshu",
                        "provider_operation": "collect_detail",
                        "source_kind": "search_result_minimal",
                        "result_status": "failed",
                        "failure_code": "provider_unavailable",
                        "failure_reason": "provider_unavailable",
                        "retryable": True,
                    },
                },
                workflow_run_id=run.run_id,
                subagent_task_id=children[0].child_task_id,
                stage_name="operation",
                input_fingerprint="browser-non-auth-failure-operation",
                status="failed",
            )
        )
    return {
        "run_id": run.run_id,
        "thread_id": thread["id"],
        "brief_id": brief.id,
        "child_task_id": children[0].child_task_id,
    }


async def seed_historical_message(
    db_path: str,
    *,
    brand_id: str,
    text: str,
) -> str:
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(
            title="历史采集失败",
            workspace_id=WORKSPACE_ID,
            brand_id=brand_id,
        )
        await thread_store.append_message(
            thread_id=thread["id"],
            role="assistant",
            text=text,
        )
    return str(thread["id"])


async def publish_existing_recovery_run(
    db_path: str,
    *,
    run_id: str,
    brief_id: str,
    publication_state: str,
) -> None:
    store = SQLiteContentResearchStore(db_path)
    await save_publication(
        store,
        db_path,
        run_id=run_id,
        brief_id=brief_id,
        publication_state=publication_state,
        requested_directions=("product_marketing",),
        direction_results={
            "product_marketing": {
                "state": "formal_directional_result",
                "limitations": [],
                "recovery_actions": [],
            }
        },
        evidence_refs=all_navigation_evidence_refs()[:1],
    )


async def save_publication(
    store: SQLiteContentResearchStore,
    db_path: str,
    *,
    run_id: str,
    brief_id: str,
    publication_state: str,
    requested_directions: tuple[str, ...],
    direction_results: dict[str, dict],
    evidence_refs: list[dict],
    publication_reason: str | None = None,
) -> str:
    base = governed_snapshot()
    governed = base.metadata["governed_snapshot"]
    citation_group = governed["citation_groups"][0]
    include_admitted_cards = publication_state != "partial_verified_report"
    snapshot = replace(
        base,
        id=f"snapshot_{run_id}",
        workflow_run_id=run_id,
        research_brief_id=brief_id,
        research_plan_id=f"plan_{run_id}",
        metadata={
            "governed_input_fingerprint": f"fingerprint_{run_id}",
            "governed_snapshot": {
                **governed,
                "policy_scope": {
                    **governed["policy_scope"],
                    "direction_set_version": "direction_set_v1",
                    "direction_ids": list(requested_directions),
                    "report_compose_mode": "template_only",
                },
                "citation_groups": [
                    {
                        **citation_group,
                        "admission_decision_id": governed["claim_cards"][0]["admission_decision_id"],
                        "evidence_refs": evidence_refs,
                    }
                ] if include_admitted_cards else [],
                "claim_cards": [
                    {
                        **card,
                        "claim_type": "message_angle",
                        "scope": {"sample": "selected_packets"},
                    }
                    for card in governed["claim_cards"]
                ] if include_admitted_cards else [],
                "direction_results": [
                    {
                        "direction_id": direction_id,
                        **direction_results[direction_id],
                    }
                    for direction_id in requested_directions
                ],
                "weak_signals": [],
                "cross_direction_records": [],
                "aggregate_claims": [],
                "limitations_recovery": [
                    {
                        "direction_id": direction_id,
                        "limitations": list(
                            direction_results[direction_id].get("limitations", [])
                        ),
                        "recovery_actions": list(
                            direction_results[direction_id].get(
                                "recovery_actions",
                                [],
                            )
                        ),
                    }
                    for direction_id in requested_directions
                    if direction_results[direction_id].get("limitations")
                    or direction_results[direction_id].get("recovery_actions")
                ],
            },
        },
    )
    draft = ResearchReportComposer().compose(snapshot)
    decision = replace(_decision(draft), workflow_run_id=run_id)
    if publication_reason:
        decision = replace(
            decision,
            audit_state="failed",
            reason_codes=(publication_reason,),
        )
    publication_changes: dict[str, object] = {
        "workflow_run_id": run_id,
        "publication_state": publication_state,
    }
    if publication_state == "partial_verified_report":
        publication_changes["omitted_section_ids"] = (draft.sections[0].section_id,)
    if publication_state == "evidence_only_report":
        publication_changes |= {
            "has_free_prose": False,
            "verified_section_ids": (),
            "verified_section_kinds": (),
            "structured_card_section_ids": (),
        }
    publication = replace(
        _publication(draft, decision, compose_mode="template_only"),
        **publication_changes,
    )
    store.save_result_snapshot(snapshot)
    store.save_report_draft(draft.to_record())
    store.save_report_faithfulness_decision(decision.to_record())
    store.save_report_publication(publication.to_record())
    async with WorkflowRunManager(db_path) as manager:
        await manager.complete_run(run_id)
    await ReportPublicationMaterializer(store, db_path).materialize(publication.id)
    return publication.id


def all_navigation_evidence_refs() -> list[dict]:
    return [
        frozen_ref(
            quote="可打开来源",
            source_url="https://www.xiaohongshu.com/explore/available",
            collected_at="2026-07-21T00:00:00Z",
            hash_char="a",
        ),
        frozen_ref(
            quote="未保存链接来源",
            source_url=None,
            collected_at="2026-07-21T00:01:00Z",
            hash_char="b",
        ),
        frozen_ref(
            quote="当前不可导航来源",
            source_url="https://www.xiaohongshu.com/explore/unavailable",
            collected_at="2026-07-21T00:02:00Z",
            hash_char="c",
            navigation_state="navigation_unavailable",
            navigation_reason="provider_auth_required",
        ),
    ]


def frozen_ref(
    *,
    quote: str,
    source_url: str | None,
    collected_at: str,
    hash_char: str,
    navigation_state: str | None = None,
    navigation_reason: str | None = None,
) -> dict:
    return {
        "canonical_note_id": "note-available",
        "field_path": "content_text",
        "quote": quote,
        "text_start": 0,
        "text_end": len(quote),
        "source_text_hash": hash_char * 64,
        "source_url": source_url,
        "source_collected_at": collected_at,
        "navigation_state": navigation_state,
        "navigation_reason": navigation_reason,
    }


def run_async_in_thread(coroutine):
    """pytest's configured event loop owns this thread; seed SQLite elsewhere."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coroutine).result()
