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
from app.content_research.models import ResearchBriefRecord
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.reporting.publication_materializer import (
    ReportPublicationMaterializer,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
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


def test_creator_complete_report_uses_lite_and_handles_all_navigation_states(
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
            evidence_refs=all_navigation_evidence_refs(),
        )
    )
    requested_urls: list[str] = []
    page.on("request", lambda request: requested_urls.append(request.url))

    open_creator_with_restored_run(page, stack["frontend_url"], seeded["run_id"])
    report = published_report(page)
    expect(report).to_be_visible(timeout=20000)
    expect(report.get_by_text("已完整核验", exact=True)).to_be_visible()
    report.get_by_role("button", name="打开引用 7").first.click()
    drawer = report.locator('aside[aria-label="Content Research citation evidence"]')
    expect(drawer.get_by_text("可打开来源", exact=True)).to_be_visible()
    expect(drawer.get_by_text("未保存链接来源", exact=True)).to_be_visible()
    expect(drawer.get_by_text("当前不可导航来源", exact=True)).to_be_visible()
    expect(drawer.get_by_text("2026-07-21T00:00:00Z", exact=True)).to_be_visible()
    expect(drawer.get_by_role("link", name="打开原笔记")).to_have_count(1)
    expect(
        drawer.get_by_text(
            "未保存来源链接；可查看原文片段与采集时间",
            exact=True,
        )
    ).to_be_visible()
    expect(
        drawer.get_by_text(
            "来源链接当前不可打开；可查看原文片段与采集时间",
            exact=True,
        )
    ).to_be_visible()
    expect(drawer.get_by_text("provider_auth_required", exact=True)).to_be_visible()

    assert any(
        f"/content-research/workflows/{seeded['run_id']}/lite-report" in url
        for url in requested_urls
    )
    assert not any(url.endswith("/trace") for url in requested_urls)
    assert not any(
        url.endswith(f"/content-research/workflows/{seeded['run_id']}/report")
        for url in requested_urls
    )


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
    report.get_by_role("button", name="打开引用 7").first.click()
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

    with page.expect_response(
        lambda response: response.url.endswith("/actions"),
        timeout=15000,
    ) as response_info:
        recovery.get_by_role("button", name="更新登录后继续").click()
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
                    "report_compose_mode": "prose",
                },
                "citation_groups": [
                    {
                        **citation_group,
                        "evidence_refs": evidence_refs,
                    }
                ],
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
        _publication(draft, decision),
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
            source_url="https://example.test/available",
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
            source_url="https://example.test/unavailable",
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
