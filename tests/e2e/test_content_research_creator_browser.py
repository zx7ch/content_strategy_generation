"""Deterministic local-browser E2E for the Lite-only Creator vertical slice."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
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
        "CONTENT_RESEARCH_MAX_CONCURRENT_RUNS": str(
            parameters.get("max_concurrent_runs", 1)
        ),
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
        "CREATOR_E2E_REQUIRE_TWO_ACTIVE_RUNS": (
            "1" if parameters.get("require_two_active_runs") else "0"
        ),
        "CREATOR_E2E_INVALID_ANALYSIS_TRACKS": str(
            parameters.get("invalid_analysis_tracks") or ""
        ),
        "CREATOR_E2E_EMPTY_ANALYSIS_TRACKS": str(
            parameters.get("empty_analysis_tracks") or ""
        ),
        "CREATOR_E2E_WITHHOLD_MARKETING_TRACK": str(
            parameters.get("withhold_marketing_track") or ""
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
        ready_timeout=60,
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
            ready_timeout=90,
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


def _goto_creator_after_brand_hydration(page, frontend_url: str) -> None:
    """Wait until Creator's selected brand is effective before user actions."""
    with page.expect_response(
        lambda response: "/threads?brand_id=" in response.url and response.status == 200,
        timeout=15000,
    ):
        page.goto(frontend_url + "/creator", wait_until="domcontentloaded")


def _start_product_marketing_run(page: Page, frontend_url: str, subject: str) -> str:
    _goto_creator_after_brand_hydration(page, frontend_url)
    with page.expect_response(
        lambda response: response.url.endswith("/threads")
        and response.request.method == "POST"
        and response.status == 201,
        timeout=30000,
    ):
        page.get_by_role("button", name=re.compile("新建对话")).click()
    page.get_by_role("button", name=re.compile("内容调研")).click(timeout=15000)
    research_input = page.get_by_role(
        "textbox", name="输入品类、品牌或 SKU，发送后开始内容调研"
    )
    expect(research_input).to_be_enabled(timeout=15000)
    research_input.fill(subject)
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
    expect(scope.get_by_test_id("scope-final-query")).not_to_have_count(0, timeout=30000)
    with page.expect_response(
        lambda response: response.url.endswith("/actions")
        and '"action":"confirm_scope"' in (response.request.post_data or ""),
        timeout=30000,
    ) as confirmation:
        scope.get_by_role("button", name="确认并开始调研").click()
    assert confirmation.value.status == 200, confirmation.value.text()
    return run_id


@pytest.mark.parametrize(
    "real_creator_stack",
    [
        {
            "source_scenario": "concurrent",
            "max_concurrent_runs": 2,
            "require_two_active_runs": True,
        }
    ],
    indirect=True,
)
def test_two_full_runs_publish_effective_isolated_reports(browser_page):
    page_a, stack = browser_page
    browser = page_a.context.browser
    assert browser is not None
    context_b = browser.new_context(viewport={"width": 1440, "height": 960})
    page_b = context_b.new_page()
    try:
        run_a = _start_product_marketing_run(
            page_a, stack["frontend_url"], "夏季凉感T恤"
        )
        run_b = _start_product_marketing_run(
            page_b, stack["frontend_url"], "长袖衬衫 凉感 夏季通勤"
        )
        assert run_a != run_b

        for page in (page_a, page_b):
            expect(page.get_by_text("调研报告已完成", exact=True)).to_be_visible(
                timeout=120000
            )
            expect(
                page.get_by_role("article", name="Content Research published report")
            ).to_be_visible(timeout=30000)

        source_calls = [
            json.loads(line)
            for line in stack["source_call_log"].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert {call["workflow_run_id"] for call in source_calls} == {run_a, run_b}
        assert max(len(call["active_run_ids"]) for call in source_calls) == 2
        queries_by_run = {
            run_id: {
                call["query"]
                for call in source_calls
                if call["workflow_run_id"] == run_id
            }
            for run_id in (run_a, run_b)
        }
        assert queries_by_run[run_a] == {"T恤", "T恤 凉感", "T恤 夏季"}
        assert queries_by_run[run_b] == {
            "长袖衬衫",
            "长袖衬衫 凉感",
            "长袖衬衫 夏季通勤",
        }

        with sqlite3.connect(stack["db_path"]) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """SELECT run.run_id, run.thread_id,
                          run.effective_analysis_attempt_id,
                          publication.id AS publication_id,
                          publication.research_plan_id,
                          publication.governed_snapshot_id,
                          artifact.artifact_id,
                          json_extract(
                              artifact.payload_json, '$.report_publication_id'
                          ) AS artifact_publication_id,
                          unit.workflow_run_id AS analysis_run_id
                   FROM workflow_runs AS run
                   JOIN content_research_report_publications AS publication
                     ON publication.workflow_run_id=run.run_id
                   JOIN workflow_artifacts AS artifact
                     ON artifact.run_id=run.run_id
                    AND artifact.artifact_type='final_result'
                    AND json_extract(
                        artifact.payload_json, '$.report_publication_id'
                    )=publication.id
                   JOIN content_research_analysis_attempts AS attempt
                     ON attempt.id=run.effective_analysis_attempt_id
                    AND attempt.state='succeeded'
                   JOIN content_research_analysis_units AS unit
                     ON unit.id=attempt.analysis_unit_id
                    AND unit.workflow_run_id=run.run_id
                   WHERE run.run_id IN (?, ?)
                   ORDER BY run.run_id""",
                (run_a, run_b),
            ).fetchall()
            assert len(rows) == 2
            assert connection.execute(
                "SELECT COUNT(*) FROM content_research_canonical_sources "
                "WHERE platform_source_id='note-shared-summer-cooling-1'"
            ).fetchone()[0] == 1

        by_run = {str(row["run_id"]): dict(row) for row in rows}
        assert set(by_run) == {run_a, run_b}
        assert len({row["thread_id"] for row in rows}) == 2
        assert len({row["effective_analysis_attempt_id"] for row in rows}) == 2
        assert len({row["publication_id"] for row in rows}) == 2
        assert len({row["artifact_id"] for row in rows}) == 2
        for run_id, row in by_run.items():
            assert row["analysis_run_id"] == run_id
            assert row["artifact_publication_id"] == row["publication_id"]

        for run_id, row in by_run.items():
            with urlopen(
                Request(
                    f"{stack['backend_url']}/content-research/workflows/{run_id}/trace",
                    headers=USER_HEADERS,
                ),
                timeout=10,
            ) as response:
                trace = json.loads(response.read())
            marketing_trace = next(
                item
                for item in trace["logical_checkpoints"]
                if item["stage"] == "marketing_conclusion"
            )
            assert trace["workflow_run_id"] == run_id
            assert trace["state"] == "report_ready"
            assert trace["effective_attempt"] == {
                "kind": "analysis",
                "attempt_no": 1,
                "state": "succeeded",
            }
            assert (
                marketing_trace["analysis_attempt_id"]
                == row["effective_analysis_attempt_id"]
            )
    finally:
        context_b.close()


def test_creator_submit_subject_reaches_only_the_approved_brief_and_restores_it(
    browser_page,
):
    page, stack = browser_page
    _goto_creator_after_brand_hydration(page, stack["frontend_url"])
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
    _goto_creator_after_brand_hydration(page, stack["frontend_url"])
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
    expect(trace_dialog.get_by_text("内容调研 · 等待用户确认", exact=True)).to_be_visible()
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
    [{"source_scenario": "complete", "withhold_marketing_track": "need"}],
    indirect=True,
)
def test_creator_generates_traceable_marketing_conclusions_without_recollecting(browser_page):
    page, stack = browser_page
    _goto_creator_after_brand_hydration(page, stack["frontend_url"])
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

    # A single confirmation click must both persist the currently focused edit
    # and freeze that newest Scope. Requiring a separate blur makes the first
    # click disappear when the blur-triggered save temporarily disables the
    # confirmation button.
    scope.get_by_label("场景或人群补充词（可选）").fill("夏季通勤")
    expected_queries = ["T恤", "T恤 凉感", "T恤 夏季通勤"]
    confirm = scope.get_by_role("button", name="确认并开始调研")
    expect(confirm).to_be_enabled()
    with page.expect_response(
        lambda response: response.url.endswith("/actions")
        and '"action":"confirm_scope"' in (response.request.post_data or ""),
        timeout=30000,
    ) as confirmation:
        confirm.click()
    assert confirmation.value.status == 200, confirmation.value.text()

    expect(page.get_by_text("调研报告已完成", exact=True)).to_be_visible(timeout=120000)
    report = page.get_by_role("article", name="Content Research published report")
    expect(report).to_be_visible(timeout=30000)
    expect(report.get_by_text("audit_rewrite_exhausted", exact=True)).to_have_count(0)
    expect(
        report.get_by_text(
            "部分叙述未通过证据核验，已自动隐藏；当前仅展示可验证内容。",
            exact=True,
        )
    ).to_be_visible()
    expect(
        report.get_by_text("分析已选定 · 本次未发布", exact=True)
    ).to_be_visible()
    expect(report.get_by_text("分析不可用", exact=True)).to_have_count(0)

    evidence_buttons = report.get_by_role("button", name="证据详情", exact=True)
    expect(evidence_buttons).not_to_have_count(0)
    first_evidence_button = evidence_buttons.nth(0)
    first_evidence_group = first_evidence_button.locator("..")
    first_evidence_button.click()
    first_inline_evidence = first_evidence_group.get_by_role(
        "region", name=re.compile(r"引用 \[\d+\] 证据详情")
    )
    expect(first_inline_evidence).to_be_visible()
    expect(first_inline_evidence).to_contain_text("content_text")

    second_evidence_button = evidence_buttons.nth(1)
    second_evidence_group = second_evidence_button.locator("..")
    second_evidence_button.click()
    expect(first_evidence_group.get_by_role("region")).to_have_count(0)
    expect(
        second_evidence_group.get_by_role(
            "region", name=re.compile(r"引用 \[\d+\] 证据详情")
        )
    ).to_be_visible()
    expect(report.get_by_role("region", name=re.compile(r"引用 \[\d+\] 证据详情"))).to_have_count(1)

    source_calls = [
        json.loads(line)
        for line in stack["source_call_log"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [call["query"] for call in source_calls] == expected_queries
    assert all(call["workflow_run_id"] == run_id for call in source_calls)

    with sqlite3.connect(stack["db_path"]) as connection:
        scope_query_groups = json.loads(
            connection.execute(
                "SELECT query_groups_json FROM content_research_scope_contracts "
                "WHERE workflow_run_id=?",
                (run_id,),
            ).fetchone()[0]
        )
        locked_plan = json.loads(
            connection.execute(
                "SELECT effective_policy_json FROM content_research_run_policy_snapshots "
                "WHERE workflow_run_id=?",
                (run_id,),
            ).fetchone()[0]
        )["locked_query_plan"]["directions"]["product_marketing"]
        assert [group["id"] for group in locked_plan["query_groups"]] == [
            group["id"] for group in scope_query_groups
        ]
        assert [group["normalized_query"] for group in locked_plan["query_groups"]] == [
            group["final_query"] for group in scope_query_groups
        ]
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
        publication_count, publication_state = connection.execute(
            "SELECT COUNT(*), MAX(publication_state) "
            "FROM content_research_report_publications WHERE workflow_run_id=?",
            (run_id,),
        ).fetchone()
        assert (publication_count, publication_state) == (1, "partial_verified_report")
        publication_payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM content_research_report_publications "
                "WHERE workflow_run_id=?",
                (run_id,),
            ).fetchone()[0]
        )
        assert publication_payload["track_publication_dispositions"] == [
            {
                "track": "need",
                "state": "withheld_by_faithfulness",
                "reason_code": "faithfulness_not_verified",
            },
            {"track": "value", "state": "published", "reason_code": None},
            {"track": "message", "state": "published", "reason_code": None},
        ]
        snapshot_id, retrieval_unit_id = connection.execute(
            "SELECT id, retrieval_execution_unit_id "
            "FROM content_research_evidence_snapshots WHERE workflow_run_id=?",
            (run_id,),
        ).fetchone()
        analysis_unit_id = connection.execute(
            "SELECT id FROM content_research_analysis_units "
            "WHERE workflow_run_id=? AND evidence_snapshot_id=?",
            (run_id, snapshot_id),
        ).fetchone()[0]
        analysis_attempt_id, analysis_attempt_state = connection.execute(
            "SELECT id, state FROM content_research_analysis_attempts "
            "WHERE analysis_unit_id=? ORDER BY attempt_no DESC LIMIT 1",
            (analysis_unit_id,),
        ).fetchone()
        assert analysis_attempt_state == "succeeded"
        assert {
            tuple(row)
            for row in connection.execute(
                "SELECT track, status FROM content_research_analysis_checkpoints "
                "WHERE analysis_unit_id=? AND stage='verifier'",
                (analysis_unit_id,),
            ).fetchall()
        } == {
            ("need", "completed"),
            ("value", "completed"),
            ("message", "completed"),
        }
        marketing_checkpoint = json.loads(
            connection.execute(
                "SELECT payload_json FROM content_research_stage_checkpoints "
                "WHERE workflow_run_id=? AND stage_name='marketing_conclusion'",
                (run_id,),
            ).fetchone()[0]
        )
        assert marketing_checkpoint["evidence_snapshot_id"] == snapshot_id
        assert marketing_checkpoint["analysis_attempt_id"] == analysis_attempt_id
        assert marketing_checkpoint["retrieval_execution_unit_id"] == retrieval_unit_id
        state, status = connection.execute(
            "SELECT content_research_state, status FROM workflow_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
    assert (state, status) == ("report_ready", "succeeded")
    trace_request = Request(
        f"{stack['backend_url']}/content-research/workflows/{run_id}/trace",
        headers=USER_HEADERS,
    )
    with urlopen(trace_request, timeout=10) as response:
        trace = json.loads(response.read())
    marketing_trace = next(
        item
        for item in trace["logical_checkpoints"]
        if item["stage"] == "marketing_conclusion"
    )
    assert trace["state"] == "report_ready"
    assert marketing_trace["analysis_attempt_id"] == analysis_attempt_id
    assert marketing_trace["evidence_snapshot_id"] == snapshot_id
    assert marketing_trace["embedding"]["document_count"] >= 3
    assert marketing_trace["embedding"]["batch_count"] == 1
    assert marketing_trace["embedding"]["success_count"] >= 3
    assert marketing_trace["embedding"]["failure_count"] == 0
    assert marketing_trace["embedding"]["duration_ms"] >= 0
    assert marketing_trace["embedding"]["dimensions"] == 3
    assert marketing_trace["embedding"]["checkpoint_id"].startswith("anc_")
    assert marketing_trace["embedding"]["input_fingerprint"]
    assert marketing_trace["embedding"]["result_checksum"]
    assert marketing_trace["embedding"]["result_refs"]
    assert "vectors" not in json.dumps(marketing_trace)
    assert {
        track: details["execution"]
        for track, details in marketing_trace["tracks"].items()
    } == {"need": "completed", "value": "completed", "message": "completed"}
    assert marketing_trace["tracks"]["need"]["state"] == "selected"
    assert marketing_trace["tracks"]["need"]["publication_disposition"] == {
        "state": "withheld_by_faithfulness",
        "reason_code": "faithfulness_not_verified",
    }
    assert "retry_analysis" not in trace.get("allowed_actions", [])


@pytest.mark.parametrize(
    "real_creator_stack",
    [{"source_scenario": "contested", "empty_analysis_tracks": "need,message"}],
    indirect=True,
)
def test_creator_reports_supported_contested_and_insufficient_tracks_with_exact_quotes(
    browser_page,
):
    page, stack = browser_page
    _goto_creator_after_brand_hydration(page, stack["frontend_url"])
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
    with page.expect_response(
        lambda response: response.url.endswith("/actions")
        and '"action":"confirm_scope"' in (response.request.post_data or ""),
        timeout=30000,
    ):
        scope.get_by_role("button", name="确认并开始调研").click()

    expect(page.get_by_text("调研报告已完成", exact=True)).to_be_visible(timeout=120000)
    report = page.get_by_role("article", name="Content Research published report")
    value = report.get_by_role("region", name="可被相信的产品卖点")
    expect(value).to_contain_text("支持 3 篇 / 3 位作者；反向 2 篇 / 2 位作者")
    expect(report.get_by_text("暂无可验证结论", exact=True)).to_have_count(2)

    evidence_buttons = value.get_by_role("button", name="证据详情", exact=True)
    expect(evidence_buttons).to_have_count(5)
    evidence_buttons.nth(0).click()
    expect(value.get_by_role("region", name=re.compile(r"引用 \[\d+\] 证据详情"))).to_contain_text(
        "穿着凉爽"
    )
    evidence_buttons.nth(4).click()
    inline_counter = value.get_by_role(
        "region", name=re.compile(r"引用 \[\d+\] 证据详情")
    )
    expect(inline_counter).to_have_count(1)
    expect(inline_counter).to_contain_text("一点也不凉爽")

    trace_request = Request(
        f"{stack['backend_url']}/content-research/workflows/{run_id}/trace",
        headers=USER_HEADERS,
    )
    with urlopen(trace_request, timeout=10) as response:
        trace = json.loads(response.read())
    marketing_trace = next(
        item
        for item in trace["logical_checkpoints"]
        if item["stage"] == "marketing_conclusion"
    )
    assert marketing_trace["tracks"]["value"] == {
        **marketing_trace["tracks"]["value"],
        "state": "contested",
        "supporting_note_count": 3,
        "independent_author_count": 3,
        "counter_note_count": 2,
        "counter_author_count": 2,
    }
    assert {
        track: marketing_trace["tracks"][track]["state"]
        for track in ("need", "message")
    } == {"need": "insufficient_evidence", "message": "insufficient_evidence"}


@pytest.mark.parametrize(
    "real_creator_stack",
    [{"source_scenario": "complete", "invalid_analysis_tracks": "value,message"}],
    indirect=True,
)
def test_creator_exposes_real_analysis_worker_failure_before_report_composition(browser_page):
    page, stack = browser_page
    _goto_creator_after_brand_hydration(page, stack["frontend_url"])
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
    page.get_by_role("button", name="确认并继续").click()
    scope = page.get_by_role("region", name="检索范围确认")
    expect(scope.get_by_test_id("scope-final-query")).to_have_count(3, timeout=30000)
    scope.get_by_role("button", name="确认并开始调研").click()

    expect(page.get_by_text("本轮调研需要恢复", exact=True)).to_be_visible(timeout=120000)
    page.get_by_role("button", name="查看 Trace").click()
    trace_dialog = page.get_by_role("dialog", name="Agent 决策日志 · Trace")
    expect(trace_dialog.get_by_text("营销结论判定", exact=True)).to_be_visible(timeout=30000)
    expect(trace_dialog.get_by_text(re.compile("需求：已选定"))).to_be_visible()
    expect(trace_dialog.get_by_text(re.compile("价值：分析不可用"))).to_be_visible()
    expect(trace_dialog.get_by_text(re.compile("表达：分析不可用"))).to_be_visible()
    expect(trace_dialog.get_by_text(re.compile("价值：模型返回的 JSON 无法解析"))).to_be_visible()
    expect(trace_dialog.get_by_text("组装并发布调研报告", exact=True)).to_be_visible()
    expect(trace_dialog.get_by_text("组装并发布调研报告", exact=True)).to_be_visible()

    trace_request = Request(
        f"{stack['backend_url']}/content-research/workflows/{run_id}/trace",
        headers=USER_HEADERS,
    )
    with urlopen(trace_request, timeout=10) as response:
        trace = json.loads(response.read())
    marketing_trace = next(
        item
        for item in trace["logical_checkpoints"]
        if item["stage"] == "marketing_conclusion"
    )
    assert trace["state"] == "recovery_required"
    assert trace["effective_attempt"] == {
        "kind": "analysis",
        "attempt_no": 1,
        "state": "failed",
    }
    assert marketing_trace["status"] == "failed"
    assert marketing_trace["tracks"]["need"]["execution"] == "completed"
    assert marketing_trace["tracks"]["value"] == {
        "state": "analysis_unavailable",
        "execution": "failed",
        "decision": "analysis_failed",
        "publication_role": "omitted",
        "reason_codes": ["marketing_analysis_unavailable"],
        "failure_code": "llm_protocol_incompatible",
        "failure_detail": "invalid_json",
        "recovery_action": "repair_model_configuration_and_resume",
    }
    assert marketing_trace["tracks"]["message"] == marketing_trace["tracks"]["value"]
    assert "runtime_steps" not in trace
    assert "runtime_child_tasks" not in trace
    assert trace["state_transitions"][-1]["to_state"] == "recovery_required"
    assert trace["external_api_summary"]["failed_count"] == 0
    assert trace["external_api_summary"]["call_count"] > 0
    source_call_count = trace["external_api_summary"]["call_count"]
    with sqlite3.connect(stack["db_path"]) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM content_research_report_publications WHERE workflow_run_id=?",
            (run_id,),
        ).fetchone() == (0,)

    trace_dialog.get_by_role("button", name="关闭 Trace 对话框").click()
    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_text("本轮调研需要恢复", exact=True)).to_be_visible(
        timeout=30000
    )
    expect(page.get_by_role("button", name="继续失败的分析")).to_be_visible()
    page.get_by_role("button", name="继续失败的分析").click()
    deadline = time.monotonic() + 30
    retry_trace = None
    while time.monotonic() < deadline:
        with urlopen(trace_request, timeout=10) as response:
            candidate = json.loads(response.read())
        if candidate.get("effective_attempt") == {
            "kind": "analysis",
            "attempt_no": 2,
            "state": "failed",
        }:
            retry_trace = candidate
            break
        time.sleep(0.2)

    assert retry_trace is not None
    assert retry_trace["state"] == "recovery_required"
    assert retry_trace["run_status"] == "waiting_user"
    assert retry_trace["current_stage"] == "marketing_analysis"
    assert retry_trace["external_api_summary"]["call_count"] == source_call_count
    retry_marketing = next(
        item
        for item in retry_trace["logical_checkpoints"]
        if item["stage"] == "marketing_conclusion"
    )
    assert retry_marketing["tracks"]["need"]["execution"] == "completed"
    assert retry_marketing["tracks"]["value"]["failure_detail"] == "invalid_json"
    assert retry_marketing["tracks"]["message"]["failure_detail"] == "invalid_json"




















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
    _goto_creator_after_brand_hydration(page, stack["frontend_url"])
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








def test_recovery_plan_is_the_only_retry_authority(browser_page):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    failed = run_async_in_thread(
        seed_local_identity_conflict(
            stack["db_path"], brand_id=brand_id, title="本地身份冲突"
        )
    )

    with urlopen(
        Request(
            f"{stack['backend_url']}/content-research/workflows/{failed['workflow_run_id']}",
            headers=USER_HEADERS,
        )
    ) as response:
        run = json.load(response)["run"]

    assert run["state"] == "recovery_required"
    assert run["allowed_actions"] == ["cancel"]
    assert run.get("recovery_plan") is None

    with urlopen(
        Request(
            f"{stack['backend_url']}/content-research/workflows/"
            f"{failed['workflow_run_id']}/trace",
            headers=USER_HEADERS,
        )
    ) as response:
        trace = json.load(response)
    assert trace["recoverable"] is False
    assert trace.get("recovery_plan") is None

    forged_command = Request(
        f"{stack['backend_url']}/content-research/workflows/"
        f"{failed['workflow_run_id']}/actions",
        data=json.dumps(
            {
                "command_id": "forged-local-identity-retry",
                "expected_state": "recovery_required",
                "expected_revision": run["state_revision"],
                "action": "retry_retrieval",
                "payload": {
                    "recovery_plan_id": "forged-plan",
                    "plan_fingerprint": "sha256:forged",
                },
            }
        ).encode(),
        headers={**USER_HEADERS, "Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(HTTPError) as rejected:
        urlopen(forged_command)
    assert rejected.value.code == 409
    safe_error = rejected.value.read().decode()
    assert "STALE_CONTENT_RESEARCH_COMMAND" in safe_error
    assert "SELECT" not in safe_error
    assert "workflow_runs" not in safe_error

    with urlopen(
        Request(
            f"{stack['backend_url']}/content-research/workflows/"
            f"{failed['workflow_run_id']}",
            headers=USER_HEADERS,
        )
    ) as response:
        unchanged = json.load(response)["run"]
    assert unchanged["state_revision"] == run["state_revision"]
    assert unchanged.get("recovery_plan") is None

    open_creator_with_restored_run(
        page, stack["frontend_url"], failed["workflow_run_id"]
    )
    expect(page.get_by_role("button", name="继续失败的检索")).to_have_count(0)


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

    with urlopen(
        Request(
            f"{stack['backend_url']}/content-research/workflows/{first['workflow_run_id']}",
            headers=USER_HEADERS,
        )
    ) as response:
        recovery_plan = json.load(response)["run"]["recovery_plan"]
    with urlopen(
        Request(
            f"{stack['backend_url']}/content-research/workflows/{first['workflow_run_id']}/trace",
            headers=USER_HEADERS,
        )
    ) as response:
        assert json.load(response)["recovery_plan"] == recovery_plan

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
        assert request["payload"] == {
            "recovery_plan_id": recovery_plan["recovery_plan_id"],
            "plan_fingerprint": recovery_plan["plan_fingerprint"],
        }
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


async def seed_local_identity_conflict(
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
    run_id = f"run_local_conflict_{thread['id']}"
    brief_id = f"brief_{run_id}"
    attempt_id = f"attempt_{run_id}"
    coordinator = ContentResearchPersistenceCoordinator(db_path)
    await coordinator.apply(
        LifecycleCommand(
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
        )
    )
    await coordinator.apply(
        LifecycleCommand(
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
                "status": "failed",
                "subject_confirmation": title,
                "competitor_tags": [],
                "research_directions": [],
                "direction_catalog": list(DIRECTION_CATALOG_V1),
                "custom_competitor_input": "",
                "timeout_status": "none",
                "fallback_used": False,
                "error_code": "LOCAL_IDENTITY_CONFLICT",
                "error_message": "本地身份契约冲突",
                "recoverable": False,
                "configuration_source": "user",
                "model": "deterministic-e2e",
                "error": {
                    "code": "LOCAL_IDENTITY_CONFLICT",
                    "stage": "retrieval_running",
                    "operation": "persist_retrieval_outcome",
                    "message": "本地身份契约冲突",
                    "retryable": True,
                    "recovery_action": "retry_retrieval",
                    "attempt_id": attempt_id,
                },
            },
        )
    )
    return {
        "workflow_run_id": run_id,
        "attempt_id": attempt_id,
        "brief_id": brief_id,
        "thread_id": str(thread["id"]),
    }
















def run_async_in_thread(coroutine):
    """pytest's configured event loop owns this thread; seed SQLite elsewhere."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coroutine).result()
