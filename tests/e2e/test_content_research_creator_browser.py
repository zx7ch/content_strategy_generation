"""Deterministic local-browser E2E for the Lite-only Creator vertical slice."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from app.content_research.contracts import (
    DIRECTION_CATALOG_V1,
)
from app.content_research.lifecycle.coordinator import ContentResearchPersistenceCoordinator
from app.content_research.lifecycle.models import ContentResearchState, LifecycleCommand
from app.memory.thread_store import ThreadStore
from tests.browser_process import (
    chrome_executable,
    reserve_port,
    run_process,
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
    tsconfig_path = frontend_root / "tsconfig.json"
    tsconfig_before = tsconfig_path.read_bytes()
    request.addfinalizer(lambda: tsconfig_path.write_bytes(tsconfig_before))
    db_path = tmp_path / "creator-lite.db"
    chroma_dir = tmp_path / "chroma"
    source_call_log = tmp_path / "creator-source-calls.jsonl"
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
        "CREATOR_E2E_SOURCE_SCENARIO": str(
            parameters.get("source_scenario") or "auth_required"
        ),
        "CREATOR_E2E_SOURCE_CALL_LOG": str(source_call_log),
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
                "source_call_log": source_call_log,
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


def test_creator_submit_subject_reaches_only_the_approved_brief_and_restores_it(
    browser_page,
):
    page, stack = browser_page
    page.goto(stack["frontend_url"] + "/creator", wait_until="domcontentloaded")
    page.get_by_role("button", name=re.compile("内容调研")).click(timeout=15000)
    research_input = page.get_by_role(
        "textbox", name="输入品类、品牌或 SKU，发送后开始内容调研"
    )
    expect(research_input).to_be_enabled(timeout=15000)
    research_input.fill("夏季凉感T恤")
    with page.expect_response(
        lambda response: response.url.endswith("/content-research/presearch")
        and response.status == 201,
        timeout=30000,
    ) as presearch_response:
        research_input.press("Enter")
    payload = presearch_response.value.json()
    run_id = payload["workflow_run_id"]

    expect(page.get_by_role("heading", name="在开始前，请确认几个关键点")).to_be_visible(
        timeout=30000
    )
    expect(page.get_by_text("还需要你确认调研主体", exact=True)).to_have_count(0)
    expect(page.get_by_text("已冻结检索范围", exact=True)).to_have_count(0)
    expect(page.get_by_text("专家调研进行中", exact=True)).to_have_count(0)
    assert payload["run"]["state"] == "brief_confirmation_required"
    assert payload["run"]["state_revision"] == 2

    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="在开始前，请确认几个关键点")).to_be_visible(
        timeout=30000
    )
    expect(page.get_by_text("还需要你确认调研主体", exact=True)).to_have_count(0)
    expect(page.get_by_text("已冻结检索范围", exact=True)).to_have_count(0)

    with sqlite3.connect(stack["db_path"]) as connection:
        active_run_id = connection.execute(
            "SELECT active_run_id FROM creator_threads WHERE id=(SELECT thread_id FROM workflow_runs WHERE run_id=?)",
            (run_id,),
        ).fetchone()[0]
        assert connection.execute(
            "SELECT COUNT(*) FROM content_research_scope_contracts WHERE workflow_run_id=?",
            (run_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM content_research_dispatch_jobs WHERE workflow_run_id=?",
            (run_id,),
        ).fetchone()[0] == 0
    assert active_run_id == run_id


def test_creator_corrects_search_structure_and_reads_only_the_backend_query_preview(
    browser_page,
):
    page, stack = browser_page
    page.goto(stack["frontend_url"] + "/creator", wait_until="domcontentloaded")
    page.get_by_role("button", name=re.compile("内容调研")).click(timeout=15000)
    research_input = page.get_by_role(
        "textbox", name="输入品类、品牌或 SKU，发送后开始内容调研"
    )
    research_input.fill("长袖衬衫")
    with page.expect_response(
        lambda response: response.url.endswith("/content-research/presearch")
        and response.status == 201,
        timeout=30000,
    ) as presearch_response:
        research_input.press("Enter")
    run_id = presearch_response.value.json()["workflow_run_id"]

    page.get_by_role("button", name="准确，继续").click()
    page.get_by_role("button", name="产品营销").click()
    with page.expect_response(
        lambda response: response.url.endswith("/actions")
        and '"action":"confirm_brief"' in (response.request.post_data or ""),
        timeout=30000,
    ) as confirmed:
        page.get_by_role("button", name="确认并继续").click()
    assert confirmed.value.status == 200, confirmed.value.text()

    scope = page.get_by_role("region", name="检索范围确认")
    expect(scope.get_by_role("heading", name="确认本轮实际搜索词")).to_be_visible(
        timeout=30000
    )
    expect(scope.get_by_text("重点了解什么", exact=True)).to_have_count(0)
    expect(scope.get_by_text("核心对象 A", exact=True)).to_have_count(0)
    expect(scope.get_by_text("产品／体验词 B（可选）", exact=True)).to_have_count(0)
    expect(scope.get_by_text("场景／人群词 C（可选）", exact=True)).to_have_count(0)
    expect(page.get_by_text("已冻结检索范围", exact=True)).to_have_count(0)
    expect(page.get_by_text("专家调研进行中", exact=True)).to_have_count(0)
    expect(scope.get_by_role("button", name="确认并开始调研")).to_be_enabled()
    page.get_by_role("button", name="查看 Trace").click()
    trace_dialog = page.get_by_role("dialog", name="Agent 决策日志 · Trace")
    expect(trace_dialog.get_by_text("确认检索范围", exact=True)).to_be_visible()
    expect(trace_dialog.get_by_text("等待用户操作", exact=True)).to_be_visible()
    expect(trace_dialog.get_by_text("scope_confirm", exact=False)).to_have_count(0)
    expect(trace_dialog.get_by_text("等待恢复", exact=False)).to_have_count(0)
    page.get_by_role("button", name="关闭 Trace 对话框").click()
    final_queries = scope.get_by_test_id("scope-final-query")
    expect(final_queries).to_have_count(1)
    expect(final_queries.nth(0)).to_have_text("长袖衬衫")
    expect(scope.locator("label").filter(has_text="最终搜索词").locator("input")).to_have_count(0)

    core_input = scope.get_by_label("核心搜索词")
    product_input = scope.get_by_label("产品或体验补充词（可选）")
    context_input = scope.get_by_label("场景或人群补充词（可选）")
    # Reproduce the manual rapid-focus sequence that used to dispatch three
    # overlapping writes with stale draft IDs and revisions.
    core_input.fill("T恤")
    product_input.click()
    product_input.fill("凉感")
    context_input.click()
    context_input.fill("夏季")
    context_input.press("Tab")

    scope = page.get_by_role("region", name="检索范围确认")
    final_queries = scope.get_by_test_id("scope-final-query")
    expect(final_queries).to_have_count(3)
    expect(final_queries.nth(0)).to_have_text("T恤")
    expect(final_queries.nth(1)).to_have_text("T恤 凉感")
    expect(final_queries.nth(2)).to_have_text("T恤 夏季")

    page.reload(wait_until="domcontentloaded")
    scope = page.get_by_role("region", name="检索范围确认")
    expect(scope.get_by_role("heading", name="确认本轮实际搜索词")).to_be_visible(
        timeout=30000
    )
    expect(scope.get_by_label("核心搜索词")).to_have_value("T恤")
    expect(scope.get_by_label("产品或体验补充词（可选）")).to_have_value("凉感")
    expect(scope.get_by_label("场景或人群补充词（可选）")).to_have_value("夏季")
    restored_queries = scope.get_by_test_id("scope-final-query")
    expect(restored_queries).to_have_count(3)
    expect(restored_queries.nth(0)).to_have_text("T恤")
    expect(restored_queries.nth(1)).to_have_text("T恤 凉感")
    expect(restored_queries.nth(2)).to_have_text("T恤 夏季")
    with sqlite3.connect(stack["db_path"]) as connection:
        latest_queries = json.loads(connection.execute(
            "SELECT query_groups_json FROM content_research_scope_drafts "
            "WHERE workflow_run_id=? ORDER BY created_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()[0])
        assert [group["final_query"] for group in latest_queries] == [
            "T恤",
            "T恤 凉感",
            "T恤 夏季",
        ]
        assert connection.execute(
            "SELECT COUNT(*) FROM content_research_scope_contracts WHERE workflow_run_id=?",
            (run_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM content_research_dispatch_jobs WHERE workflow_run_id=?",
            (run_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM content_research_subagent_tasks WHERE workflow_run_id=?",
            (run_id,),
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "real_creator_stack",
    [{"source_scenario": "complete"}],
    indirect=True,
)
def test_creator_confirm_scope_executes_one_complete_verified_run(browser_page):
    page, stack = browser_page
    page.goto(stack["frontend_url"] + "/creator", wait_until="domcontentloaded")
    page.get_by_role("button", name=re.compile("内容调研")).click(timeout=15000)
    research_input = page.get_by_role(
        "textbox", name="输入品类、品牌或 SKU，发送后开始内容调研"
    )
    research_input.fill("夏季凉感T恤")
    with page.expect_response(
        lambda response: response.url.endswith("/content-research/presearch")
        and response.status == 201,
        timeout=30000,
    ) as presearch_response:
        research_input.press("Enter")
    run_id = presearch_response.value.json()["workflow_run_id"]

    page.get_by_role("button", name="准确，继续").click()
    page.get_by_role("button", name="产品营销").click()
    with page.expect_response(
        lambda response: response.url.endswith("/actions")
        and '"action":"confirm_brief"' in (response.request.post_data or ""),
        timeout=30000,
    ):
        page.get_by_role("button", name="确认并继续").click()

    scope = page.get_by_role("region", name="检索范围确认")
    expect(scope.get_by_test_id("scope-final-query")).to_have_count(3, timeout=30000)
    expected_queries = ["T恤", "T恤 凉感", "T恤 夏季"]
    for index, query in enumerate(expected_queries):
        expect(scope.get_by_test_id("scope-final-query").nth(index)).to_have_text(query)

    confirm = scope.get_by_role("button", name="确认并开始调研")
    expect(confirm).to_be_enabled()
    with page.expect_response(
        lambda response: response.url.endswith("/actions")
        and '"action":"confirm_scope"' in (response.request.post_data or ""),
        timeout=30000,
    ) as confirmation:
        confirm.evaluate("(button) => { button.click(); button.click(); }")
    assert confirmation.value.status == 200, confirmation.value.text()

    expect(page.get_by_text("调研报告已完成", exact=True)).to_be_visible(timeout=120000)
    expect(page.get_by_role("article", name="Content Research published report")).to_be_visible(timeout=30000)

    source_calls = [
        json.loads(line)
        for line in stack["source_call_log"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [call["query"] for call in source_calls] == expected_queries
    assert all(call["workflow_run_id"] == run_id for call in source_calls)

    with sqlite3.connect(stack["db_path"]) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM content_research_scope_contracts WHERE workflow_run_id=?",
            (run_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM content_research_dispatch_jobs WHERE workflow_run_id=?",
            (run_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM content_research_directional_evidence_packets WHERE workflow_run_id=?",
            (run_id,),
        ).fetchone()[0] >= 3
        assert connection.execute(
            "SELECT COUNT(*) FROM content_research_report_publications WHERE workflow_run_id=?",
            (run_id,),
        ).fetchone()[0] == 1
        state, status = connection.execute(
            "SELECT content_research_state, status FROM workflow_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
    assert (state, status) == ("report_ready", "succeeded")




















def test_creator_run_b_remains_active_after_reload_and_late_run_a_history(
    browser_page,
):
    page, stack = browser_page
    historical = run_async_in_thread(
        seed_historical_conversation(
            stack["db_path"],
            brand_id=default_brand_id(stack["backend_url"]),
            title="Run A 历史报告",
        )
    )
    page.goto(stack["frontend_url"] + "/creator", wait_until="domcontentloaded")
    page.get_by_text("Run A 历史报告", exact=True).click()
    expect(page.get_by_text("这是 Run A 的历史记录。", exact=True)).to_be_visible(timeout=30000)

    page.get_by_role("button", name=re.compile("内容调研")).first.click()
    research_input = page.get_by_role(
        "textbox",
        name="输入品类、品牌或 SKU，发送后开始内容调研",
    )
    expect(research_input).to_be_enabled(timeout=30000)
    research_input.fill("Run B 夏季通勤短裤")
    with page.expect_response(
        lambda response: response.url.endswith("/content-research/presearch"),
        timeout=30000,
    ) as presearch_response:
        research_input.press("Enter")
    assert presearch_response.value.status == 201, presearch_response.value.text()

    run_b = presearch_response.value.json()["workflow_run_id"]
    page.get_by_role("heading", name="在开始前，请确认几个关键点").wait_for(
        timeout=30000
    )
    expect(page.get_by_text("还需要你确认调研主体", exact=True)).to_have_count(0)
    expect(page.locator('section[aria-label="确认检索范围"]')).to_have_count(0)
    with sqlite3.connect(stack["db_path"]) as connection:
        assert connection.execute(
            "SELECT active_run_id FROM creator_threads WHERE id=?",
            (historical["thread_id"],),
        ).fetchone()[0] == run_b

    requested_urls: list[str] = []
    page.on("request", lambda request: requested_urls.append(request.url))
    page.reload(wait_until="domcontentloaded")

    expect(page.get_by_role("heading", name="在开始前，请确认几个关键点")).to_be_visible(
        timeout=30000
    )
    expect(page.get_by_text("Run A 历史报告", exact=True)).to_be_visible()
    assert any(
        url.endswith(f"/content-research/workflows/{run_b}")
        for url in requested_urls
    )
    expect(page.locator('section[aria-label="确认检索范围"]')).to_have_count(0)
    with sqlite3.connect(stack["db_path"]) as connection:
        assert connection.execute(
            "SELECT active_run_id FROM creator_threads WHERE id=?",
            (historical["thread_id"],),
        ).fetchone()[0] == run_b








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

    assert retry_requests
    for request in retry_requests:
        assert request["action"] == "retry_presearch"
        assert request["expected_state"] == "recovery_required"
        assert request["expected_revision"] >= 1
        assert request["command_id"]
        assert request["payload"] == {}
    assert retried["workflow_run_id"] == first["workflow_run_id"]
    assert retried["attempt_id"].startswith("att_")
    assert retried["attempt_id"] != first["attempt_id"]
    assert retried["brief_id"] == first["brief_id"]
    expect(page.get_by_role("heading", name="在开始前，请确认几个关键点")).to_be_visible()
    expect(page.get_by_text("还需要你确认调研主体", exact=True)).to_have_count(0)

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
        lifecycle_events = [
            row[0]
            for row in connection.execute(
                "SELECT event FROM content_research_state_transitions WHERE run_id=? ORDER BY state_revision",
                (first["workflow_run_id"],),
            ).fetchall()
        ]
    assert after == before
    assert lifecycle_events == [
        "submit_research_subject",
        "fail",
        "retry_presearch",
        "presearch_completed",
    ]


async def seed_historical_conversation(
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
        await thread_store.append_message(
            thread_id=thread["id"],
            role="assistant",
            text="这是 Run A 的历史记录。",
        )
    return {"thread_id": str(thread["id"])}








































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




def default_brand_id(backend_url: str) -> str:
    with urlopen(Request(f"{backend_url}/brands", headers=USER_HEADERS)) as response:
        return str(json.load(response)["items"][0]["id"])












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
    run_id = f"run_model_recovery_{thread['id']}"
    brief_id = f"brief_{run_id}"
    attempt_id = f"attempt_{run_id}"
    coordinator = ContentResearchPersistenceCoordinator(db_path)
    await coordinator.apply(LifecycleCommand(
        command_id=f"seed-submit:{run_id}",
        run_id=run_id,
        expected_state=None,
        expected_revision=0,
        kind="submit_research_subject",
        payload={
            "thread_id": thread["id"],
            "user_id": "operator",
            "workspace_id": WORKSPACE_ID,
            "seed_text": title,
        },
    ))
    error = {
        "code": "llm_auth_invalid",
        "stage": "presearch",
        "operation": "llm_presearch",
        "message": "API Key 无效",
        "retryable": True,
        "recovery_action": "retry_presearch",
    }
    await coordinator.apply(LifecycleCommand(
        command_id=f"seed-failure:{run_id}",
        run_id=run_id,
        expected_state=ContentResearchState.PRESEARCH_RUNNING,
        expected_revision=1,
        kind="fail",
        payload={
            "brief_id": brief_id,
            "schema_version": "content_research_brief_v1",
            "brief_status": "failed",
            "subject": title,
            "directions": ["product_marketing"],
            "attempt_id": attempt_id,
            "seed_text": title,
            "user_note": None,
            "workspace_id": WORKSPACE_ID,
            "user_id": "operator",
            "status": "waiting_model_config",
            "subject_confirmation": title,
            "competitor_tags": [],
            "research_directions": [],
            "direction_catalog": list(DIRECTION_CATALOG_V1),
            "custom_competitor_input": "",
            "timeout_status": "none",
            "fallback_used": False,
            "error_code": "llm_auth_invalid",
            "error_message": "API Key 无效",
            "recoverable": True,
            "configuration_source": "user",
            "model": "deterministic-e2e",
            "error": error,
        },
    ))
    return {
        "workflow_run_id": run_id,
        "attempt_id": attempt_id,
        "brief_id": brief_id,
        "thread_id": thread["id"],
    }
















def run_async_in_thread(coroutine):
    """pytest's configured event loop owns this thread; seed SQLite elsewhere."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coroutine).result()
