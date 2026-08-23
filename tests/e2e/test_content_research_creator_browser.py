"""Deterministic local-browser E2E for the Lite-only Creator vertical slice."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
from app.content_research.scope_contract import (
    CoverageSnapshot,
    ScopeAuditEvent,
    ScopeConstraint,
    ScopeDraftAuditEvent,
    ScopeQueryGroupInput,
    build_scope_draft,
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
    scope_coverage_seed = (
        run_async_in_thread(
            seed_scope_awaiting_coverage_offline(
                str(db_path),
                title="真实范围决策",
            )
        )
        if parameters.get("scope_coverage_seed")
        else None
    )
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
                "scope_coverage_seed": scope_coverage_seed,
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


def _open_product_marketing_scope(page: Page, stack: dict, *, seed: str):
    page.goto(stack["frontend_url"] + "/creator", wait_until="domcontentloaded")
    page.get_by_role("button", name=re.compile("内容调研")).click(timeout=15000)
    research_input = page.get_by_role(
        "textbox", name="输入品类、品牌或 SKU，发送后开始内容调研"
    )
    expect(research_input).to_be_enabled(timeout=15000)
    research_input.fill(seed)
    with page.expect_response(
        lambda response: response.url.endswith("/content-research/presearch")
        and response.status == 201,
        timeout=30000,
    ):
        research_input.press("Enter")
    subject_confirm = page.get_by_role("button", name="确认调研主体", exact=True)
    brief_heading = page.get_by_role("heading", name="在开始前，请确认几个关键点")
    expect(subject_confirm.or_(brief_heading)).to_be_visible(timeout=30000)
    if subject_confirm.is_visible():
        with page.expect_response(
            lambda response: response.url.endswith("/actions")
            and '"action":"confirm_subject_structure"' in (response.request.post_data or "")
            and response.status == 200,
            timeout=15000,
        ):
            subject_confirm.click()
    brief_heading.wait_for(timeout=30000)
    page.get_by_role("button", name="准确，继续").click()
    page.get_by_role("button", name="产品营销", exact=True).click()
    with page.expect_response(
        lambda response: response.url.endswith("/actions")
        and '"action":"confirm_brief"' in (response.request.post_data or "")
        and response.status == 200,
        timeout=30000,
    ) as brief_response:
        page.get_by_role("button", name=re.compile("确认并开始调研")).click()
    scope = page.locator('section[aria-label="确认检索范围"]')
    expect(scope).to_be_visible(timeout=30000)
    return scope, brief_response.value.json()["workflow_run_id"]


@pytest.mark.parametrize(
    "real_creator_stack",
    [{"source_scenario": "success"}],
    indirect=True,
)
def test_creator_confirms_suggested_v2_portfolio_and_provider_receives_exact_queries(
    browser_page,
):
    page, stack = browser_page
    scope, workflow_run_id = _open_product_marketing_scope(
        page,
        stack,
        seed="长袖衬衫 凉感 夏季通勤",
    )
    expected_queries = ["长袖衬衫", "长袖衬衫 凉感", "长袖衬衫 夏季通勤"]
    for index, query in enumerate(expected_queries, start=1):
        expect(scope.get_by_label(f"检索组 {index}")).to_have_value(query)

    with page.expect_response(
        lambda response: response.url.endswith("/actions")
        and '"action":"confirm_scope"' in (response.request.post_data or "")
        and response.status == 200,
        timeout=30000,
    ):
        scope.get_by_role("button", name="确认并开始调研").click()

    for _ in range(160):
        if stack["source_call_log"].exists():
            calls = [
                json.loads(line)
                for line in stack["source_call_log"].read_text(encoding="utf-8").splitlines()
            ]
            discovered = [
                item["query"] for item in calls if item["operation"] == "discover_candidates"
            ]
            if len(discovered) >= 3:
                break
        page.wait_for_timeout(250)
    else:
        pytest.fail("Formal worker did not call the provider with the frozen portfolio")

    assert discovered[:3] == expected_queries
    for _ in range(160):
        with sqlite3.connect(stack["db_path"]) as connection:
            row = connection.execute(
                "SELECT constraint_counts_json "
                "FROM content_research_scope_coverage_snapshots "
                "WHERE workflow_run_id=? ORDER BY created_at DESC LIMIT 1",
                (workflow_run_id,),
            ).fetchone()
        if row is not None:
            counts = json.loads(row[0])
            break
        page.wait_for_timeout(250)
    else:
        pytest.fail("Formal worker did not persist Scope coverage")
    assert counts["core_object"]["matched_candidate_count"] >= 1
    assert counts["_summary"]["eligible_candidate_count"] >= 1


@pytest.mark.parametrize(
    "real_creator_stack",
    [{"source_scenario": "success"}],
    indirect=True,
)
def test_creator_completes_missing_aspects_and_only_latest_draft_executes(browser_page):
    page, stack = browser_page
    scope, _workflow_run_id = _open_product_marketing_scope(
        page,
        stack,
        seed="长袖衬衫",
    )
    expect(scope.get_by_label("检索组 1")).to_have_value("长袖衬衫")
    expect(scope.get_by_label("产品／体验检索词")).to_be_visible()
    expect(scope.get_by_label("场景／人群检索词")).to_be_visible()

    held: list[object] = []

    def hold_first_replacement(route) -> None:
        payload = route.request.post_data_json
        action_payload = payload.get("payload", {}) if isinstance(payload, dict) else {}
        if (
            payload.get("action") == "prepare_scope"
            and action_payload.get("product_experience_aspect") == "凉感"
            and not action_payload.get("context_audience_aspect")
            and not held
        ):
            held.append(route)
            return
        route.continue_()

    page.route("**/content-research/workflows/*/actions", hold_first_replacement)
    scope.get_by_label("产品／体验检索词").fill("凉感")
    scope.get_by_label("产品／体验检索词").press("Enter")
    for _ in range(60):
        if held:
            break
        page.wait_for_timeout(250)
    else:
        pytest.fail("First Scope replacement response was not held")

    scope.get_by_label("场景／人群检索词").fill("夏季通勤")
    with page.expect_response(
        lambda response: response.url.endswith("/actions")
        and '"action":"prepare_scope"' in (response.request.post_data or "")
        and response.status == 200,
        timeout=30000,
    ):
        scope.get_by_label("场景／人群检索词").press("Enter")
    expect(scope.get_by_label("检索组 3")).to_have_value("长袖衬衫 夏季通勤")

    held_route = held[0]
    with page.expect_response(
        lambda response: response.url.endswith("/actions")
        and '"action":"prepare_scope"' in (response.request.post_data or "")
        and response.status == 422,
        timeout=30000,
    ):
        held_route.continue_()
    page.wait_for_timeout(500)
    expect(scope.get_by_label("检索组 2")).to_have_value("长袖衬衫 凉感")
    expect(scope.get_by_label("检索组 3")).to_have_value("长袖衬衫 夏季通勤")

    with page.expect_response(
        lambda response: response.url.endswith("/actions")
        and '"action":"confirm_scope"' in (response.request.post_data or "")
        and response.status == 200,
        timeout=30000,
    ):
        scope.get_by_role("button", name="确认并开始调研").click()

    for _ in range(160):
        if stack["source_call_log"].exists():
            calls = [
                json.loads(line)
                for line in stack["source_call_log"].read_text(encoding="utf-8").splitlines()
            ]
            discovered = [
                item["query"] for item in calls if item["operation"] == "discover_candidates"
            ]
            if len(discovered) >= 3:
                break
        page.wait_for_timeout(250)
    else:
        pytest.fail("Latest Scope Draft was not dispatched")
    assert discovered[:3] == ["长袖衬衫", "长袖衬衫 凉感", "长袖衬衫 夏季通勤"]


@pytest.mark.parametrize(
    "selected_direction_ids",
    [
        ("product_marketing",),
        tuple(DIRECTION_CATALOG_V1),
    ],
    ids=["single", "all"],
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
    research_input = page.get_by_role(
        "textbox",
        name="输入品类、品牌或 SKU，发送后开始内容调研",
    )
    expect(research_input).to_be_enabled(timeout=15000)
    research_input.fill("夏季通勤短裤")
    with page.expect_response(
        lambda response: response.url.endswith("/content-research/presearch")
        and response.status == 201,
        timeout=30000,
    ):
        research_input.press("Enter")

    expect(page.get_by_text("还需要你确认调研主体", exact=True)).to_be_visible(
        timeout=30000
    )
    with page.expect_response(
        lambda response: response.url.endswith("/actions")
        and '"action":"confirm_subject_structure"' in (response.request.post_data or "")
        and response.status == 200,
        timeout=15000,
    ):
        page.get_by_role("button", name="确认调研主体", exact=True).click()
    page.get_by_role("heading", name="在开始前，请确认几个关键点").wait_for(
        timeout=30000
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
    expect(confirm_button).to_be_enabled()
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


def test_creator_restores_persisted_scope_draft_after_reload(browser_page):
    page, stack = browser_page
    page.goto(stack["frontend_url"] + "/creator", wait_until="domcontentloaded")
    page.get_by_role("button", name=re.compile("内容调研")).click(timeout=15000)
    research_input = page.get_by_role(
        "textbox", name="输入品类、品牌或 SKU，发送后开始内容调研"
    )
    expect(research_input).to_be_enabled(timeout=15000)
    research_input.fill("夏季通勤长袖")
    with page.expect_response(
        lambda response: response.url.endswith("/content-research/presearch")
        and response.status == 201,
        timeout=30000,
    ):
        research_input.press("Enter")
    expect(page.get_by_text("还需要你确认调研主体", exact=True)).to_be_visible(
        timeout=30000
    )
    page.get_by_role("button", name="确认调研主体", exact=True).click()
    page.get_by_role("heading", name="在开始前，请确认几个关键点").wait_for(
        timeout=30000
    )
    page.get_by_role("button", name="准确，继续").click()
    page.get_by_role("button", name="产品营销", exact=True).click()
    with page.expect_response(
        lambda response: response.url.endswith("/actions")
        and '"action":"confirm_brief"' in (response.request.post_data or "")
        and response.status == 200,
        timeout=15000,
    ) as brief_response:
        page.get_by_role("button", name=re.compile("确认并开始调研")).click()
    workflow_run_id = brief_response.value.json()["workflow_run_id"]

    scope = page.locator('section[aria-label="确认检索范围"]')
    expect(scope).to_be_visible(timeout=30000)
    first_query = scope.get_by_label("检索组 1")
    persisted_query = first_query.input_value()

    scope_projections = []

    def capture_scope_projection(response):
        if response.url.endswith(
            f"/content-research/workflows/{workflow_run_id}/scope"
        ) and response.status == 200:
            scope_projections.append(response.json())

    page.on("response", capture_scope_projection)
    page.reload(wait_until="domcontentloaded")

    restored = page.locator('section[aria-label="确认检索范围"]')
    expect(restored).to_be_visible(timeout=30000)
    expect(restored.get_by_label("检索组 1")).to_have_value(persisted_query)
    confirm = restored.get_by_role("button", name="确认并开始调研")
    expect(confirm).to_be_enabled()
    projected = scope_projections[-1]
    projected_command = next(
        item
        for item in projected["allowed_actions"]
        if item["action"] == "confirm_scope" and item["available"]
    )
    with page.expect_response(
        lambda response: response.url.endswith(
            f"/content-research/workflows/{workflow_run_id}/actions"
        )
        and '"action":"confirm_scope"' in (response.request.post_data or ""),
        timeout=15000,
    ) as confirm_response:
        confirm.click()
    assert confirm_response.value.status == 200
    submitted = confirm_response.value.request.post_data_json["payload"]
    assert submitted["scope_draft_id"] == projected_command["scope_draft_id"]
    assert submitted["structure_hash"] == projected_command["structure_hash"]
    assert submitted["query_groups"] == [
        {"final_query": item["final_query"]}
        for item in projected_command["query_groups"]
    ]
    with sqlite3.connect(stack["db_path"]) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM content_research_scope_contracts "
            "WHERE workflow_run_id=?",
            (workflow_run_id,),
        ).fetchone()[0] == 1


def test_creator_unknown_execution_outcome_requires_manual_recovery_without_replay(
    browser_page,
):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    seeded = run_async_in_thread(
        seed_recovery(
            stack["db_path"],
            brand_id=brand_id,
            title="执行结果未知",
        )
    )
    run_id = seeded["run_id"]
    scope_projection = {
        "schema_version": "content_research_api_v1",
        "workflow_run_id": run_id,
        "state": "confirmed",
        "draft": {
            "id": "scope_draft_browser_unknown",
            "workflow_run_id": run_id,
            "research_plan_id": f"plan_{run_id}",
            "structure_hash": "structure_browser_unknown",
            "constraints": [
                {
                    "id": "season",
                    "label": "季节",
                    "value": "夏季",
                    "mode": "required",
                    "allowed_aliases": [],
                }
            ],
            "query_groups": [
                {
                    "suggested_query": "夏季 长袖衬衫 通勤",
                    "final_query": "夏季 长袖衬衫 通勤",
                    "targeted_required_terms": ["夏季"],
                }
            ],
            "created_at": "2026-08-21T00:00:00+08:00",
        },
        "scope_contract": {
            "id": "scope_contract_browser_unknown",
            "workflow_run_id": run_id,
            "research_plan_id": f"plan_{run_id}",
            "version": 1,
            "schema_version": "content_research_scope_contract_v1",
            "constraints": [
                {
                    "id": "season",
                    "label": "季节",
                    "value": "夏季",
                    "mode": "required",
                    "allowed_aliases": [],
                }
            ],
            "query_groups": [
                {
                    "id": "group_browser_unknown",
                    "suggested_query": "夏季 长袖衬衫 通勤",
                    "final_query": "夏季 长袖衬衫 通勤",
                    "origin": "system_suggested",
                    "execution_role": "coverage",
                }
            ],
            "created_at": "2026-08-21T00:01:00+08:00",
        },
        "audit_events": [],
        "allowed_actions": [],
        "coverage_snapshot": None,
        "allowed_resolutions": [],
        "decision_recovery": None,
        "execution_unit": {
            "id": "seu_browser_unknown",
            "state": "outcome_unknown",
            "attempt_no": 1,
            "recovery_state": "outcome_unknown",
            "allowed_actions": [],
            "trace_summary": {
                "fact_count": 3,
                "attempt_count": 1,
                "last_fact_kind": "outcome_unknown",
            },
        },
    }
    page.route(
        f"**/content-research/workflows/{run_id}/scope",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(scope_projection, ensure_ascii=False),
        ),
    )

    open_creator_with_restored_run(page, stack["frontend_url"], run_id)

    manual = page.locator('div[aria-label="执行结果需要人工恢复"]')
    expect(manual).to_be_visible(timeout=20000)
    expect(manual.get_by_text("需要人工确认执行结果", exact=True)).to_be_visible()
    expect(manual.get_by_text(re.compile("不会自动重放"))).to_be_visible()
    expect(page.get_by_role("button", name="重试本次已保存决定")).to_have_count(0)


def test_creator_coverage_decision_uses_only_server_declared_actions_and_exact_expand_payload(
    browser_page,
):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    seeded = run_async_in_thread(
        seed_recovery(stack["db_path"], brand_id=brand_id, title="服务端覆盖决策")
    )
    run_id = seeded["run_id"]
    projection = browser_scope_projection(
        run_id,
        coverage=True,
        allowed_resolutions=("expand_required_constraint", "generate_limited_report"),
    )
    action_payloads: list[dict] = []

    page.route(
        f"**/content-research/workflows/{run_id}/scope",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(projection, ensure_ascii=False),
        ),
    )

    def resolve_action(route):
        request = route.request.post_data_json
        action_payloads.append(request)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                browser_action_response(run_id, projection, request["payload"]),
                ensure_ascii=False,
            ),
        )

    page.route(f"**/content-research/workflows/{run_id}/actions", resolve_action)
    open_creator_with_restored_run(page, stack["frontend_url"], run_id)

    coverage = page.locator('div[aria-label="覆盖不足决策"]')
    expect(coverage).to_be_visible(timeout=20000)
    expect(coverage.get_by_text("夏季条件的合格样本不足", exact=True)).to_be_visible()
    expect(coverage.get_by_role("button", name="继续补充夏季样本")).to_be_visible()
    expect(coverage.get_by_role("button", name="基于现有证据生成受限报告")).to_be_visible()
    expect(coverage.get_by_role("button", name=re.compile("放宽"))).to_have_count(0)
    expect(page.locator('section[aria-label="确认检索范围"]')).to_have_count(0)

    coverage.get_by_role("button", name="继续补充夏季样本").click()
    coverage.get_by_label("补充检索词 1").fill("夏季 防晒 长袖衬衫")
    with page.expect_request(
        lambda request: request.url.endswith(f"/content-research/workflows/{run_id}/actions")
        and '"action":"resolve_coverage"' in (request.post_data or ""),
        timeout=15000,
    ):
        coverage.get_by_role("button", name="提交补搜决定").click()

    assert action_payloads == [
        {
            "action": "resolve_coverage",
            "payload": {
                "scope_contract_version": 1,
                "coverage_snapshot_id": "coverage_browser",
                "resolution": "expand_required_constraint",
                "constraint_id": "season",
                "supplementary_queries": ["夏季 防晒 长袖衬衫"],
            },
        }
    ]


def test_creator_known_retry_replays_once_and_keeps_one_frozen_report(browser_page):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    seeded = run_async_in_thread(
        seed_publication(
            stack["db_path"],
            brand_id=brand_id,
            title="安全重试后报告",
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
    run_id = seeded["run_id"]
    replay_request = {
        "scope_contract_version": 1,
        "coverage_snapshot_id": "coverage_browser",
        "resolution": "expand_required_constraint",
        "constraint_id": "season",
        "supplementary_queries": ["夏季 防晒 长袖衬衫"],
    }
    projection = browser_scope_projection(run_id, replay_request=replay_request)
    action_payloads: list[dict] = []
    page.route(
        f"**/content-research/workflows/{run_id}/scope",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(projection, ensure_ascii=False),
        ),
    )

    def replay_action(route):
        request = route.request.post_data_json
        action_payloads.append(request)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                browser_action_response(run_id, projection, request["payload"]),
                ensure_ascii=False,
            ),
        )

    page.route(f"**/content-research/workflows/{run_id}/actions", replay_action)
    open_creator_with_restored_run(page, stack["frontend_url"], run_id)

    retry = page.get_by_role("button", name="重试本次已保存决定")
    expect(retry).to_be_visible(timeout=20000)
    retry.click()
    expect(published_report(page)).to_have_count(1, timeout=20000)
    page.wait_for_timeout(750)
    assert action_payloads == [{"action": "resolve_coverage", "payload": replay_request}]
    expect(published_report(page)).to_have_count(1)


def test_creator_discards_a_late_scope_response_after_switching_runs(browser_page):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    old_run = run_async_in_thread(
        seed_recovery(stack["db_path"], brand_id=brand_id, title="旧调研任务")
    )
    current_run = run_async_in_thread(
        seed_recovery(stack["db_path"], brand_id=brand_id, title="当前调研任务")
    )
    old_projection = browser_scope_projection(old_run["run_id"])
    old_projection["scope_contract"]["query_groups"][0]["final_query"] = "旧任务过期范围"
    current_projection = browser_scope_projection(current_run["run_id"])
    current_projection["scope_contract"]["query_groups"][0]["final_query"] = "当前任务服务端范围"
    held_old_scope_routes = []

    page.route(
        f"**/content-research/workflows/{old_run['run_id']}/scope",
        lambda route: held_old_scope_routes.append(route),
    )
    page.route(
        f"**/content-research/workflows/{current_run['run_id']}/scope",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(current_projection, ensure_ascii=False),
        ),
    )
    open_creator_with_restored_run(page, stack["frontend_url"], old_run["run_id"])
    for _ in range(40):
        if held_old_scope_routes:
            break
        page.wait_for_timeout(50)
    assert len(held_old_scope_routes) == 1

    page.get_by_text("当前调研任务", exact=True).click()
    current_scope = page.locator('section[aria-label="已确认检索范围"]')
    expect(current_scope.get_by_text("当前任务服务端范围", exact=True)).to_be_visible(
        timeout=20000
    )
    held_old_scope_routes[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(old_projection, ensure_ascii=False),
    )
    page.wait_for_timeout(500)

    expect(current_scope.get_by_text("当前任务服务端范围", exact=True)).to_be_visible()
    expect(page.get_by_text("旧任务过期范围", exact=True)).to_have_count(0)


def test_creator_historical_run_never_overrides_durable_active_run_after_brief_confirmation(
    browser_page,
):
    page, stack = browser_page
    historical = run_async_in_thread(
        seed_publication(
            stack["db_path"],
            brand_id=default_brand_id(stack["backend_url"]),
            title="Run A 历史报告",
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
    run_async_in_thread(
        append_historical_report_message(
            stack["db_path"],
            thread_id=historical["thread_id"],
            run_id=historical["run_id"],
        )
    )

    page.goto(stack["frontend_url"] + "/creator", wait_until="domcontentloaded")
    page.get_by_text("Run A 历史报告", exact=True).click()
    expect(published_report(page)).to_be_visible(timeout=30000)

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

    expect(page.get_by_text("还需要你确认调研主体", exact=True)).to_be_visible(
        timeout=30000
    )
    page.get_by_role("button", name="确认调研主体", exact=True).click()
    page.get_by_role("heading", name="在开始前，请确认几个关键点").wait_for(
        timeout=30000
    )
    page.get_by_role("button", name="准确，继续").click()
    page.get_by_role("button", name="产品营销", exact=True).click()
    with page.expect_response(
        lambda response: response.url.endswith("/actions")
        and '"action":"confirm_brief"' in (response.request.post_data or "")
        and response.status == 200,
        timeout=30000,
    ) as brief_response:
        page.get_by_role("button", name=re.compile("确认并开始调研")).click()
    run_b = brief_response.value.json()["workflow_run_id"]
    assert run_b != historical["run_id"]
    expect(page.locator('section[aria-label="确认检索范围"]')).to_be_visible(
        timeout=30000
    )

    requested_urls: list[str] = []
    page.on("request", lambda request: requested_urls.append(request.url))
    page.reload(wait_until="domcontentloaded")

    expect(page.locator('section[aria-label="确认检索范围"]')).to_be_visible(
        timeout=30000
    )
    expect(page.get_by_text("Run A 历史报告", exact=True)).to_be_visible()
    assert any(
        url.endswith(f"/content-research/workflows/{run_b}/scope")
        for url in requested_urls
    )
    assert any(
        url.endswith(f"/content-research/workflows/{run_b}/trace")
        for url in requested_urls
    )

    page.route(
        f"**/content-research/workflows/{run_b}",
        lambda route: route.fulfill(
            status=404,
            content_type="application/json",
            body=json.dumps({"detail": "workflow projection unavailable"}),
        ),
    )
    page.reload(wait_until="domcontentloaded")

    expect(
        page.get_by_text(re.compile("内容调研运行暂不可读取"), exact=False)
    ).to_be_visible(timeout=30000)
    expect(page.get_by_text("Run A 历史报告", exact=True)).to_be_visible()
    expect(page.locator('section[aria-label="确认检索范围"]')).to_have_count(0)


@pytest.mark.parametrize(
    "real_creator_stack",
    [{"scope_coverage_seed": True, "source_scenario": "success"}],
    indirect=True,
)
def test_creator_expand_reaches_worker_and_refreshes_real_result(browser_page):
    page, stack = browser_page
    seeded = stack["scope_coverage_seed"]
    scope_responses: list[dict] = []

    def capture_scope(response) -> None:
        if (
            response.url.endswith(
                f"/content-research/workflows/{seeded['run_id']}/scope"
            )
            and response.status == 200
        ):
            scope_responses.append(response.json())

    page.on(
        "response",
        capture_scope,
    )

    open_creator_with_restored_run(page, stack["frontend_url"], seeded["run_id"])

    coverage = page.locator('div[aria-label="覆盖不足决策"]')
    expect(coverage).to_be_visible(timeout=30000)
    expect(coverage.get_by_role("button", name="继续补充长袖衬衫样本")).to_be_visible()
    expect(coverage.get_by_role("button", name="基于现有证据生成受限报告")).to_be_visible()
    expect(coverage.get_by_role("button", name="放宽长袖衬衫约束")).to_be_visible()
    expect(page.locator('section[aria-label="确认检索范围"]')).to_have_count(0)

    initial_projection = scope_responses[-1]
    assert initial_projection["coverage_snapshot"]["unmet_constraint_ids"] == [
        "not_a_contract_constraint",
        "core_object",
    ]
    assert initial_projection["allowed_actions"] == [
        {
            "action": "prepare_scope",
            "available": False,
            "unavailable_reason": "coverage_decision_required",
            "recovery_action": "resolve_coverage",
        },
        {
            "action": "confirm_scope",
            "available": False,
            "unavailable_reason": "coverage_decision_required",
            "recovery_action": "resolve_coverage",
        },
        {
            "action": "resolve_coverage",
            "available": True,
            "scope_contract_version": 1,
            "coverage_snapshot_id": seeded["coverage_snapshot_id"],
        },
    ]
    assert initial_projection["allowed_resolutions"] == [
        {
            "action": "expand_required_constraint",
            "available": True,
            "valid_constraint_ids": ["core_object"],
            "supplementary_queries_required": True,
            "unavailable_reason": None,
        },
        {
            "action": "generate_limited_report",
            "available": True,
            "valid_constraint_ids": [],
            "supplementary_queries_required": False,
            "unavailable_reason": None,
        },
        {
            "action": "relax_constraint",
            "available": True,
            "valid_constraint_ids": ["core_object"],
            "supplementary_queries_required": False,
            "unavailable_reason": None,
        },
    ]

    coverage.get_by_role("button", name="继续补充长袖衬衫样本").click()
    supplementary_query = "夏季 防晒 长袖衬衫"
    coverage.get_by_label("补充检索词 1").fill(supplementary_query)

    action_path = f"/content-research/workflows/{seeded['run_id']}/actions"
    scope_path = f"/content-research/workflows/{seeded['run_id']}/scope"
    with page.expect_response(
        lambda response: response.url.endswith(scope_path) and response.status == 200,
        timeout=30000,
    ) as post_projection_info:
        with page.expect_response(
            lambda response: response.url.endswith(action_path)
            and '"action":"resolve_coverage"' in (response.request.post_data or ""),
            timeout=30000,
        ) as action_info:
            coverage.get_by_role("button", name="提交补搜决定").click()

    assert action_info.value.status == 200
    expected_request = {
        "scope_contract_version": 1,
        "coverage_snapshot_id": seeded["coverage_snapshot_id"],
        "resolution": "expand_required_constraint",
        "constraint_id": "core_object",
        "supplementary_queries": [supplementary_query],
    }
    assert action_info.value.request.post_data_json == {
        "action": "resolve_coverage",
        "payload": expected_request,
    }
    action_result = action_info.value.json()["result"]
    post_projection = post_projection_info.value.json()
    assert post_projection["decision_recovery"] is None
    assert not any(
        item["action"] == "resolve_coverage" and item["available"]
        for item in post_projection["allowed_actions"]
    )
    assert post_projection["execution_unit"]["id"] == action_result["execution_unit"]["id"]
    assert post_projection["scope_contract"]["version"] == 1
    expect(coverage).to_have_count(0)

    for _ in range(160):
        if stack["source_call_log"].exists():
            with sqlite3.connect(stack["db_path"]) as connection:
                continuation = connection.execute(
                    "SELECT supplementary_queries_json, state "
                    "FROM content_research_scope_execution_continuations "
                    "WHERE workflow_run_id=?",
                    (seeded["run_id"],),
                ).fetchone()
                terminal_coverage = connection.execute(
                    "SELECT id, state, source_coverage_snapshot_id, execution_revision "
                    "FROM content_research_scope_coverage_snapshots "
                    "WHERE workflow_run_id=? AND execution_revision=2",
                    (seeded["run_id"],),
                ).fetchone()
                attempt = connection.execute(
                    "SELECT state, provider_state "
                    "FROM content_research_scope_execution_attempts "
                    "WHERE execution_unit_id=? AND attempt_no=0",
                    (action_result["execution_unit"]["id"],),
                ).fetchone()
                continuation_task = connection.execute(
                    "SELECT status, metadata_json "
                    "FROM content_research_subagent_tasks "
                    "WHERE workflow_run_id=? AND json_extract(metadata_json, '$.execution_unit_id')=?",
                    (seeded["run_id"], action_result["execution_unit"]["id"]),
                ).fetchone()
                execution_facts = connection.execute(
                    "SELECT kind, payload_json FROM content_research_execution_facts "
                    "WHERE execution_unit_id=? ORDER BY sequence_no",
                    (action_result["execution_unit"]["id"],),
                ).fetchall()
            if continuation is not None and continuation[1] == "completed" and terminal_coverage:
                break
        page.wait_for_timeout(250)
    else:
        pytest.fail("Expand worker did not persist a terminal continuation/Coverage state")

    source_calls = [
        json.loads(line)
        for line in stack["source_call_log"].read_text(encoding="utf-8").splitlines()
    ]
    assert supplementary_query in [
        item["query"] for item in source_calls if item["operation"] == "discover_candidates"
    ]
    assert json.loads(continuation[0]) == [supplementary_query]
    assert tuple(attempt) == ("completed", "succeeded")
    assert continuation_task[0] == "completed"
    assert json.loads(continuation_task[1])["execution_unit_id"] == action_result[
        "execution_unit"
    ]["id"]
    assert any(
        kind == "provider_request_recorded"
        and json.loads(payload).get("operation") == "discover"
        and json.loads(payload).get("request", {}).get("query") == supplementary_query
        for kind, payload in execution_facts
    )
    assert execution_facts[-1][0] == "coverage_persisted"
    assert terminal_coverage[2] == seeded["coverage_snapshot_id"]
    assert terminal_coverage[3] == 2
    assert terminal_coverage[1] == "awaiting_scope_decision"
    with sqlite3.connect(stack["db_path"]) as connection:
        publication_count = connection.execute(
            "SELECT COUNT(*) FROM content_research_report_publications "
            "WHERE workflow_run_id=?",
            (seeded["run_id"],),
        ).fetchone()[0]
    assert publication_count == 0

    with page.expect_response(
        lambda response: response.url.endswith(scope_path) and response.status == 200,
        timeout=30000,
    ) as refreshed_scope_info:
        page.reload(wait_until="domcontentloaded")
    refreshed_scope = refreshed_scope_info.value.json()
    assert refreshed_scope["execution_unit"]["state"] == "completed"
    assert refreshed_scope["coverage_snapshot"]["id"] == terminal_coverage[0]
    assert refreshed_scope["coverage_snapshot"]["state"] == "awaiting_scope_decision"
    expect(page.locator('div[aria-label="覆盖不足决策"]')).to_be_visible(timeout=30000)
    expect(published_report(page)).to_have_count(0)


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
    expect(page.get_by_text("还需要你确认调研主体", exact=True)).to_be_visible()

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
        added_checkpoint_stages = [
            row[0]
            for row in connection.execute(
                "SELECT stage_name FROM content_research_stage_checkpoints WHERE workflow_run_id=? ORDER BY created_at",
                (first["workflow_run_id"],),
            ).fetchall()
        ]
    assert after == (before[0] + 1, before[1])
    assert added_checkpoint_stages == ["subject_structure"]


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


def test_creator_restores_published_report_after_transient_lite_network_failure(browser_page):
    page, stack = browser_page
    seeded = run_async_in_thread(seed_publication(
        stack["db_path"], brand_id=default_brand_id(stack["backend_url"]), title="恢复报告",
        publication_state="complete_verified_report", requested_directions=("product_marketing",),
        direction_results={"product_marketing": {"state": "formal_directional_result", "limitations": [], "recovery_actions": []}},
        evidence_refs=all_navigation_evidence_refs()[:1],
    ))
    attempts = {"count": 0}

    def interrupt_once(route):
        attempts["count"] += 1
        if attempts["count"] == 1:
            route.abort()
        else:
            route.continue_()

    page.route(f"**/content-research/workflows/{seeded['run_id']}/lite-report", interrupt_once)
    open_creator_with_restored_run(page, stack["frontend_url"], seeded["run_id"])

    expect(published_report(page)).to_be_visible(timeout=20000)
    expect(page.get_by_text("正式报告暂不可读取", exact=False)).to_have_count(0)
    assert attempts["count"] >= 2


def test_creator_direct_run_restore_ignores_current_brand_filter(browser_page):
    page, stack = browser_page
    seeded = run_async_in_thread(seed_publication(
        stack["db_path"], brand_id=None, title="无品牌历史调研",
        publication_state="complete_verified_report", requested_directions=("product_marketing",),
        direction_results={"product_marketing": {"state": "formal_directional_result", "limitations": [], "recovery_actions": []}},
        evidence_refs=all_navigation_evidence_refs()[:1],
    ))

    open_creator_with_restored_run(page, stack["frontend_url"], seeded["run_id"])

    expect(published_report(page)).to_be_visible(timeout=20000)
    expect(page.get_by_text("该内容调研所属对话不可访问", exact=True)).to_have_count(0)


def test_creator_renders_three_marketing_tracks_and_evidence_strength(browser_page):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    conclusions = [
        {
            "track": "need",
            "state": "selected",
            "candidate_id": "mc_need_primary",
            "statement": "高温通勤场景中的凉感需求明确",
            "supporting_claim_ids": [f"cc_need_{index}" for index in range(1, 4)],
            "supporting_note_count": 3,
            "independent_author_count": 2,
            "reason_codes": [],
        },
        {
            "track": "need",
            "state": "qualified",
            "candidate_id": "mc_need_secondary",
            "statement": "不得展开的第二条合格需求结论",
            "supporting_claim_ids": [f"cc_need_{index}" for index in range(1, 4)],
            "supporting_note_count": 3,
            "independent_author_count": 2,
            "reason_codes": [],
        },
        {
            "track": "value",
            "state": "directional",
            "candidate_id": "mc_value_directional",
            "statement": "儿童与成人均可参考轻薄凉感表达",
            "supporting_claim_ids": ["cc_need_1"],
            "supporting_note_count": 1,
            "independent_author_count": 1,
            "reason_codes": ["conclusion_note_count_unmet", "conclusion_author_count_unmet"],
        },
        {
            "track": "message",
            "state": "selected",
            "candidate_id": "mc_message_primary",
            "statement": "内容表达应聚焦高温通勤的体感描述",
            "supporting_claim_ids": [f"cc_need_{index}" for index in range(1, 4)],
            "supporting_note_count": 3,
            "independent_author_count": 2,
            "reason_codes": [],
        },
    ]
    seeded = run_async_in_thread(
        seed_publication(
            stack["db_path"],
            brand_id=brand_id,
            title="营销结论 Lite 报告",
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
            marketing_conclusions=conclusions,
        )
    )

    open_creator_with_restored_run(page, stack["frontend_url"], seeded["run_id"])
    report = published_report(page)
    expect(report).to_be_visible(timeout=20000)
    expect(report.get_by_role("heading", name="场景与需求")).to_be_visible()
    expect(report.get_by_role("heading", name="可被相信的产品卖点")).to_be_visible()
    expect(report.get_by_role("heading", name="内容表达")).to_be_visible()
    expect(report.get_by_text("3 篇笔记 · 2 位独立作者").first).to_be_visible()
    expect(report.get_by_text("待验证方向", exact=True)).to_be_visible()
    expect(report.get_by_text("儿童与成人均可参考轻薄凉感表达", exact=True)).to_be_visible()
    expect(report.get_by_text("当前 1 篇 / 1 位作者", exact=True)).to_be_visible()
    expect(report.get_by_text("还缺 2 篇独立笔记、1 位独立作者", exact=True)).to_be_visible()
    expect(report.get_by_text("该方向不可作为功效或投放定论", exact=True)).to_be_visible()
    expect(report.get_by_text("另有 1 条合格结论")).to_be_visible()
    expect(report.get_by_role("heading", name="优先行动建议")).to_be_visible()
    expect(report.get_by_text("建议", exact=True)).to_be_visible()
    expect(report.get_by_text("不得展开的第二条合格需求结论")).to_have_count(0)
    expect(report.get_by_text(re.compile("raw claim .* must not render"))).to_have_count(0)

    directional_card = report.get_by_text(
        "儿童与成人均可参考轻薄凉感表达", exact=True
    ).locator("xpath=..")
    directional_card.get_by_role("button", name="证据详情").click()
    drawer = report.locator('aside[aria-label="Content Research citation evidence"]')
    expect(drawer).to_be_visible()
    expect(drawer.get_by_text("可打开来源", exact=True)).to_be_visible()

    page.locator('aside[aria-label="内容调研上下文"]').get_by_label(
        "查看完整 workflow trace"
    ).click()
    trace = page.locator('section[aria-label="Content Research Trace"]')
    expect(trace).to_be_visible()
    expect(trace.get_by_text("可打开来源", exact=True)).to_have_count(0)


def test_creator_api_trace_keeps_newest_first_numbers_and_recorded_timing_copy(
    browser_page,
):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    seeded = run_async_in_thread(
        seed_recorded_trace_timeline(
            stack["db_path"],
            brand_id=brand_id,
            title="Recorded timing timeline",
        )
    )
    trace_payloads: list[dict] = []

    def capture_trace(response) -> None:
        if response.url.endswith(f"/content-research/workflows/{seeded['run_id']}/trace"):
            trace_payloads.append(response.json())

    page.on("response", capture_trace)
    open_creator_with_restored_run(page, stack["frontend_url"], seeded["run_id"])
    sidebar = page.locator('aside[aria-label="内容调研上下文"]')
    sidebar.get_by_label("查看完整 workflow trace").click()
    trace = page.locator('section[aria-label="Content Research Trace"]')
    expect(trace).to_be_visible()
    timeline = trace.locator('section[aria-label="Content Research Trace timeline"]')
    rows = timeline.locator("article")
    expect(rows).to_have_count(4)

    expected_rows = [
        (
            "并行执行专家调研",
            "4",
            re.compile(r"排队 (?:<0\.1|\d+\.\d)s · 等待执行"),
        ),
        ("拆解调研计划", "3", "执行 2.3s · 排队 0.1s"),
        ("确认调研 Brief", "2", "执行 1.2s · 排队 0.1s"),
        ("识别调研主体与候选方向", "1", "执行 0.8s · 排队 0.1s"),
    ]
    for index, (title, number, timing_copy) in enumerate(expected_rows):
        row = rows.nth(index)
        expect(row.get_by_text(title, exact=True)).to_be_visible()
        expect(row.get_by_text(number, exact=True)).to_be_visible()
        expect(row.get_by_text(timing_copy, exact=True)).to_be_visible()

    assert trace_payloads
    assert [step["step_name"] for step in trace_payloads[-1]["runtime_steps"]] == [
        "presearch",
        "brief_confirm",
        "plan_build",
        "formal_research",
    ]
    assert trace_payloads[-1]["runtime_steps"][0]["timing"] == {
        "active_duration_ms": 800,
        "execution_finished_at": "2026-08-03T01:00:00.900001+00:00",
        "execution_started_at": "2026-08-03T01:00:00.100001+00:00",
        "queue_duration_ms": 100,
        "queued_at": "2026-08-03T01:00:00.000001+00:00",
        "timing_source": "recorded",
    }


def test_creator_trace_renders_safe_structured_query_coverage_and_q3_decisions(
    browser_page,
):
    page, stack = browser_page
    brand_id = default_brand_id(stack["backend_url"])
    seeded = run_async_in_thread(
        seed_recorded_trace_timeline(
            stack["db_path"],
            brand_id=brand_id,
            title="Structured query decisions",
        )
    )
    store = SQLiteContentResearchStore(stack["db_path"])
    started_at = datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc)
    decisions = (
        (
            "query_plan",
            {
                "primary_group_count": 2,
                "fallback_group_count": 1,
                "merged_group_count": 1,
                "query_plan_hash": "query-plan-public-hash",
                "complete_query": "must-never-appear-in-trace",
                "api_key": "secret-must-never-appear",
            },
        ),
        (
            "coverage_decision",
            {
                "satisfied": False,
                "reason_codes": ["minimum_relevant_samples_unmet"],
                "counts": {
                    "discovered": 30,
                    "deduplicated": 24,
                    "relevant": 2,
                    "detail_eligible": 2,
                    "admitted": 1,
                },
            },
        ),
        (
            "fallback_decision",
            {
                "state": "activated",
                "reason_codes": ["minimum_relevant_samples_unmet"],
            },
        ),
    )
    for index, (stage_name, payload) in enumerate(decisions):
        record_started_at = started_at + timedelta(seconds=index)
        store.save_stage_checkpoint(
            StageCheckpointRecord(
                id=f"checkpoint_browser_{stage_name}",
                schema_version="content_research_stage_checkpoint_v1",
                payload=payload,
                workflow_run_id=seeded["run_id"],
                subagent_task_id="task_browser_structured_trace",
                stage_name=stage_name,
                input_fingerprint=f"fingerprint-{stage_name}",
                status="completed",
                started_at=record_started_at,
                finished_at=record_started_at + timedelta(milliseconds=100),
                created_at=record_started_at,
            )
        )

    open_creator_with_restored_run(page, stack["frontend_url"], seeded["run_id"])
    page.locator('aside[aria-label="内容调研上下文"]').get_by_label(
        "查看完整 workflow trace"
    ).click()
    trace = page.locator('section[aria-label="Content Research Trace"]')
    decisions_section = trace.locator('section[aria-label="结构化调研决策"]')
    rows = decisions_section.locator("article")

    expect(rows).to_have_count(3)
    expect(rows.nth(0).get_by_text("补位检索判定", exact=True)).to_be_visible()
    expect(rows.nth(0).get_by_text("Q3：activated", exact=True)).to_be_visible()
    expect(rows.nth(0).get_by_text("相关证据样本不足", exact=True)).to_be_visible()
    expect(rows.nth(1).get_by_text("证据覆盖判定", exact=True)).to_be_visible()
    expect(rows.nth(1).get_by_text("发现 30 · 去重 24 · 相关 2 · 准入 1", exact=True)).to_be_visible()
    expect(rows.nth(2).get_by_text("检索计划已冻结", exact=True)).to_be_visible()
    expect(rows.nth(2).get_by_text("主检索 2 组 · 补位 1 组 · 合并 1 组", exact=True)).to_be_visible()
    expect(trace.get_by_text("must-never-appear-in-trace", exact=True)).to_have_count(0)
    expect(trace.get_by_text("secret-must-never-appear", exact=True)).to_have_count(0)


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
            marketing_conclusions=[
                {
                    "track": track,
                    "state": "insufficient_evidence",
                    "candidate_id": None,
                    "statement": None,
                    "supporting_claim_ids": [],
                    "supporting_note_count": 0,
                    "independent_author_count": 0,
                    "reason_codes": ["conclusion_no_qualified_candidate"],
                }
                for track in ("need", "value", "message")
            ],
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
    for heading in ("场景与需求", "可被相信的产品卖点", "内容表达"):
        section = report.locator(f'section[aria-label="{heading}"]')
        expect(section).to_be_visible()
        expect(section.get_by_text("暂无可验证结论", exact=True)).to_be_visible()
        expect(
            section.get_by_text(
                "验证方向：补充至少 3 篇合格笔记，并覆盖至少 2 位独立作者后重新验证。",
                exact=True,
            )
        ).to_be_visible()
        expect(section.get_by_role("button", name="证据详情")).to_have_count(0)
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
            "UPDATE workflow_runs SET status = 'succeeded' WHERE run_id = ?",
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
    assert direct_status == 500, direct_body

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


def browser_scope_projection(
    run_id: str,
    *,
    coverage: bool = False,
    allowed_resolutions: tuple[str, ...] = (),
    replay_request: dict | None = None,
) -> dict:
    draft = {
        "id": "scope_draft_browser",
        "workflow_run_id": run_id,
        "research_plan_id": f"plan_{run_id}",
        "structure_hash": "structure_browser",
        "constraints": [
            {
                "id": "season",
                "label": "季节",
                "value": "夏季",
                "mode": "required",
                "allowed_aliases": [],
            }
        ],
        "query_groups": [
            {
                "suggested_query": "夏季 长袖衬衫 通勤",
                "final_query": "夏季 长袖衬衫 通勤",
                "targeted_required_terms": ["夏季"],
            }
        ],
        "created_at": "2026-08-21T00:00:00+08:00",
    }
    contract = {
        "id": "scope_contract_browser",
        "workflow_run_id": run_id,
        "research_plan_id": f"plan_{run_id}",
        "version": 1,
        "schema_version": "content_research_scope_contract_v1",
        "constraints": draft["constraints"],
        "query_groups": [
            {
                "id": "group_browser",
                "suggested_query": "夏季 长袖衬衫 通勤",
                "final_query": "夏季 长袖衬衫 通勤",
                "origin": "system_suggested",
                "execution_role": "coverage",
            }
        ],
        "created_at": "2026-08-21T00:01:00+08:00",
    }
    resolution_rows = [
        {
            "action": action,
            "available": action in allowed_resolutions,
            "valid_constraint_ids": (
                ["season"]
                if action in allowed_resolutions
                and action in {"expand_required_constraint", "relax_constraint"}
                else []
            ),
            "supplementary_queries_required": action == "expand_required_constraint",
            "unavailable_reason": None if action in allowed_resolutions else "not_allowed",
        }
        for action in (
            "expand_required_constraint",
            "generate_limited_report",
            "relax_constraint",
        )
    ]
    coverage_snapshot = (
        {
            "id": "coverage_browser",
            "workflow_run_id": run_id,
            "scope_contract_id": contract["id"],
            "scope_contract_version": 1,
            "execution_revision": 0,
            "execution_authorization_id": None,
            "source_coverage_snapshot_id": None,
            "state": "awaiting_scope_decision",
            "constraint_counts": {
                "season": {"required": True, "matched_candidate_count": 1},
                "_summary": {
                    "reason_codes": ["required_constraint_coverage_unmet:season"]
                },
            },
            "unmet_constraint_ids": ["season"],
            "created_at": "2026-08-21T00:02:00+08:00",
        }
        if coverage
        else None
    )
    return {
        "schema_version": "content_research_api_v1",
        "workflow_run_id": run_id,
        "state": "confirmed",
        "draft": draft,
        "scope_contract": contract,
        "audit_events": [],
        "allowed_actions": (
            [
                {
                    "action": "confirm_scope",
                    "available": False,
                    "unavailable_reason": "coverage_decision_required",
                },
                {
                    "action": "resolve_coverage",
                    "available": True,
                    "scope_contract_version": 1,
                    "coverage_snapshot_id": "coverage_browser",
                },
            ]
            if coverage
            else []
        ),
        "coverage_snapshot": coverage_snapshot,
        "allowed_resolutions": resolution_rows if coverage else [],
        "decision_recovery": (
            {
                "state": "coverage_decision_required",
                "message": "Coverage decision required",
                "required_action": "resolve_coverage",
                "allowed_resolutions": list(allowed_resolutions),
            }
            if coverage
            else None
        ),
        "execution_unit": (
            {
                "id": "seu_browser_retry",
                "state": "failed",
                "attempt_no": 1,
                "recovery_state": "replayable",
                "allowed_actions": [
                    {
                        "action": "replay_coverage_decision",
                        "available": True,
                        "request": replay_request,
                    }
                ],
                "trace_summary": {
                    "fact_count": 4,
                    "attempt_count": 1,
                    "last_fact_kind": "provider_outcome_recorded",
                },
            }
            if replay_request
            else None
        ),
    }


def browser_action_response(run_id: str, projection: dict, payload: dict) -> dict:
    return {
        "workflow_run_id": run_id,
        "action": "resolve_coverage",
        "result": {
            "report_mode": "continue_research",
            "scope_contract": projection["scope_contract"],
            "unmet_constraint_ids": ["season"],
            "audit_event": {
                "id": "audit_browser_resolution",
                "workflow_run_id": run_id,
                "scope_contract_id": projection["scope_contract"]["id"],
                "scope_contract_version": 1,
                "event_name": "coverage_resolved",
                "payload": payload,
                "created_at": "2026-08-21T00:03:00+08:00",
            },
            "execution_unit": {
                "id": "seu_browser_action",
                "state": "pending",
                "attempt_no": 0,
                "recovery_state": "replayable",
                "allowed_actions": [],
                "trace_summary": {
                    "fact_count": 1,
                    "attempt_count": 1,
                    "last_fact_kind": "authorization_consumed",
                },
            },
        },
    }


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


async def seed_scope_awaiting_coverage_offline(
    db_path: str,
    *,
    title: str,
) -> dict[str, str]:
    """Persist the 5B prerequisite before the owned server and worker start."""

    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(
            title=title,
            workspace_id=WORKSPACE_ID,
            brand_id=None,
        )
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(
            thread_id=thread["id"],
            user_id="browser-e2e",
            initial_request="夏季通勤长袖",
        )
        await manager.initialize_steps(
            run.run_id,
            [
                {"step_name": "presearch", "phase": WorkflowPhase.INTAKE, "max_attempts": 3},
                {"step_name": "brief_confirm", "phase": WorkflowPhase.INTAKE, "max_attempts": 1},
                {"step_name": "plan_build", "phase": WorkflowPhase.INTAKE, "max_attempts": 1},
                {
                    "step_name": "formal_research",
                    "phase": WorkflowPhase.RETRIEVAL,
                    "max_attempts": 1,
                },
            ],
        )
        await manager.start_step(run.run_id, "presearch")
        await manager.complete_step(
            run.run_id,
            "presearch",
            artifact_refs=[{"type": "content_research_brief_draft"}],
        )
        await manager.advance_to_next_step(run.run_id)

    structure_hash = "structure_browser_scope_decision"
    plan_id = f"plan_{run.run_id}"
    brief = ResearchBriefRecord(
        id=f"brief_{run.run_id}",
        workflow_run_id=run.run_id,
        thread_id=thread["id"],
        schema_version="content_research_brief_v1",
        status="ready",
        payload={
            "schema_version": "content_research_brief_payload_v1",
            "confirmed_subject": "夏季通勤长袖",
            "subject_structure_hash": structure_hash,
            "selected_directions": ["product_marketing"],
            "requested_direction_ids": ["product_marketing"],
            "direction_catalog": list(DIRECTION_CATALOG_V1),
        },
    )
    store = SQLiteContentResearchStore(db_path)
    store.save_brief(brief)

    async def preserve_owned_stack_prerequisite(_connection, _child_ids) -> None:
        return None

    task_payload = {
        "schema_version": "content_research_subagent_task_v1",
        "workflow_run_id": run.run_id,
        "research_brief_id": brief.id,
        "research_plan_id": plan_id,
        "research_direction_id": "product_marketing",
        "agent_name": "DirectionalExecutionPipeline",
        "agent_version": "p0_spec_v1",
        "task_type": "product_marketing_research",
        "llm_scope": {"workspace_id": WORKSPACE_ID, "user_id": "browser-e2e"},
        "input_payload": {
            "schema_version": "content_research_subagent_input_v1",
            "confirmed_subject": "夏季通勤长袖",
            "subject_structure": {
                "core_object": "长袖衬衫",
                "research_intent": "通勤",
                "context_modifiers": ["夏季"],
            },
            "subject_structure_hash": structure_hash,
            "competitors": [],
            "custom_research_question": "",
            "direction": {
                "id": "product_marketing",
                "label": "产品营销",
                "direction_type": "content_pattern",
                "questions": ["提炼小红书产品卖点表达"],
                "source_scope": ["search_result"],
            },
        },
        "expected_output_schema": {
            "schema_version": "content_research_subagent_output_schema_v1",
            "required": ["finding", "evidence_refs", "missing_evidence"],
        },
        "status": "completed",
        "sequence_no": 1,
        "output_payload": {
            "schema_version": "content_research_subagent_output_v1",
            "findings": [],
            "evidence_refs": [],
            "missing_evidence": [{"reason": "required_scope_coverage_unmet"}],
            "metadata": {},
        },
    }
    async with WorkflowRunManager(db_path) as manager:
        child_ids = await manager.complete_brief_and_plan_atomically(
            workflow_run_id=run.run_id,
            task_specs=[task_payload],
            confirmation_writer=preserve_owned_stack_prerequisite,
        )
        await manager.start_child_task(child_ids[0])
        await manager.complete_child_task(child_ids[0], artifact_refs=[])
        await manager.wait_for_user_recovery(
            run.run_id,
            step_name="formal_research",
            reason={
                "code": "awaiting_scope_decision",
                "message": "Required Scope Contract coverage is unmet.",
            },
        )
    store.save_subagent_task(
        SubagentTaskRecord(
            id=f"sat_initial_{run.run_id}",
            workflow_run_id=run.run_id,
            thread_id=thread["id"],
            schema_version="content_research_subagent_task_v1",
            status="completed",
            plan_id=plan_id,
            direction_id="product_marketing",
            payload={**task_payload, "workflow_child_task_id": child_ids[0]},
        )
    )
    policy, sample_policies, direction_contracts = build_default_snapshot(
        snapshot_id=f"policy_{run.run_id}",
        workflow_run_id=run.run_id,
        brief_id=brief.id,
        plan_id=plan_id,
        direction_set_version="direction_set_v1",
        direction_ids=("product_marketing",),
        direction_catalog=DIRECTION_CATALOG_V1,
        report_compose_mode="template_only",
        confirmed_subject="夏季通勤长袖",
        subject_structure={
            "core_object": "长袖衬衫",
            "research_intent": "通勤",
            "context_modifiers": ["夏季"],
        },
        subject_structure_hash=structure_hash,
        query_groups_by_direction={
            "product_marketing": (
                {
                    "id": f"qg_initial_{run.run_id}",
                    "direction_id": "product_marketing",
                    "normalized_query": "长袖衬衫 夏季 通勤",
                    "priority": 1,
                    "sort": "likes",
                    "time_window": {"end_at": datetime.now(timezone.utc).isoformat()},
                    "candidate_cap": 20,
                    "roles": ["primary"],
                    "activation": "primary",
                },
            )
        },
        provider_capabilities={
            "xiaohongshu": {
                "adapter_version": "creator_e2e_success_v1",
                "discover_candidates": {
                    "status": "supported",
                    "fields": ["title", "author", "metrics"],
                },
                "collect_note_detail": {
                    "status": "supported",
                    "fields": [
                        "title",
                        "content_text",
                        "tags",
                        "note_type",
                        "metrics",
                        "metrics_observed_at",
                        "source_published_at",
                        "ip_location",
                        "media",
                    ],
                },
                "collect_comments": {"status": "supported", "fields": []},
            }
        },
    )
    store.save_run_policy_snapshot(policy)
    for sample_policy in sample_policies:
        store.save_sample_policy(sample_policy)
    for direction_contract in direction_contracts:
        store.save_direction_contract(direction_contract)

    constraints = (
        ScopeConstraint("core_object", "核心对象", "长袖衬衫", "required"),
        ScopeConstraint("season", "季节", "夏季", "required"),
        ScopeConstraint("scenario", "研究场景", "通勤", "required"),
    )
    query_groups = (
        ScopeQueryGroupInput(
            "长袖衬衫 夏季 通勤",
            "长袖衬衫 夏季 通勤",
            ("长袖衬衫", "夏季", "通勤"),
        ),
    )
    draft = build_scope_draft(
        workflow_run_id=run.run_id,
        research_plan_id=plan_id,
        structure_hash=structure_hash,
        constraints=constraints,
        query_groups=query_groups,
    )
    store.save_scope_draft_with_audit_event(
        draft,
        ScopeDraftAuditEvent(
            id=f"draft_event_{run.run_id}",
            workflow_run_id=run.run_id,
            scope_draft_id=draft.id,
            event_name="scope_suggested",
            payload={
                "schema_version": "content_research_scope_audit_event_v1",
                "scope_draft_id": draft.id,
                "structure_hash": structure_hash,
            },
        ),
    )
    contract, _event, _created = store.confirm_scope_atomically(
        draft.id,
        final_queries=tuple(group.final_query for group in query_groups),
        event_id=f"confirm_event_{run.run_id}",
    )
    snapshot = CoverageSnapshot(
        id=f"coverage_browser_{run.run_id}",
        workflow_run_id=run.run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        state="awaiting_scope_decision",
        constraint_counts={
            "core_object": {
                "matched_candidate_count": 0,
                "independent_author_count": 0,
                "required": True,
            },
            "_summary": {
                "minimum_samples": 2,
                "minimum_independent_authors": 2,
                "reason_codes": ["required_constraint_coverage_unmet:core_object"],
            },
        },
        # An invalid first item catches browser inference from unmet order.
        unmet_constraint_ids=("not_a_contract_constraint", "core_object"),
    )
    store.save_coverage_snapshot_with_audit_event(
        snapshot,
        ScopeAuditEvent(
            id=f"coverage_event_{run.run_id}",
            workflow_run_id=run.run_id,
            scope_contract_id=contract.id,
            scope_contract_version=contract.version,
            event_name="coverage_evaluated",
            payload={
                "schema_version": "content_research_scope_audit_event_v1",
                "coverage_snapshot_id": snapshot.id,
                "state": snapshot.state,
                "constraint_counts": snapshot.constraint_counts,
                "unmet_constraint_ids": list(snapshot.unmet_constraint_ids),
                "reason_codes": snapshot.constraint_counts["_summary"]["reason_codes"],
            },
        ),
    )
    return {
        "run_id": run.run_id,
        "thread_id": thread["id"],
        "coverage_snapshot_id": snapshot.id,
        "constraint_id": "core_object",
    }


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
    marketing_conclusions: list[dict] | None = None,
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
        marketing_conclusions=marketing_conclusions,
    )
    return {
        "run_id": run.run_id,
        "thread_id": thread["id"],
        "brief_id": brief.id,
        "publication_id": publication_id,
    }


async def append_historical_report_message(
    db_path: str,
    *,
    thread_id: str,
    run_id: str,
) -> None:
    async with ThreadStore(db_path) as thread_store:
        await thread_store.append_message(
            thread_id=thread_id,
            role="assistant",
            text="Run A 历史报告",
            message_type="artifact_result",
            run_id=run_id,
            artifact_refs=[{"type": "content_research_lite_report", "run_id": run_id}],
        )


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


async def seed_recorded_trace_timeline(
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
        steps = await manager.initialize_steps(
            run.run_id,
            [
                {"step_name": "presearch", "phase": "intake", "max_attempts": 1},
                {"step_name": "brief_confirm", "phase": "intake", "max_attempts": 1},
                {"step_name": "plan_build", "phase": "intake", "max_attempts": 1},
                {"step_name": "formal_research", "phase": "retrieval", "max_attempts": 1},
            ],
        )
        assert manager._conn is not None
        fixed_timings = {
            "presearch": {
                "queued_at": "2026-08-03T01:00:00.000001+00:00",
                "queue_spans": [
                    {
                        "started_at": "2026-08-03T01:00:00.000001+00:00",
                        "finished_at": "2026-08-03T01:00:00.100001+00:00",
                    }
                ],
                "execution_spans": [
                    {
                        "started_at": "2026-08-03T01:00:00.100001+00:00",
                        "finished_at": "2026-08-03T01:00:00.900001+00:00",
                    }
                ],
            },
            "brief_confirm": {
                "queued_at": "2026-08-03T01:00:00.900001+00:00",
                "queue_spans": [
                    {
                        "started_at": "2026-08-03T01:00:00.900001+00:00",
                        "finished_at": "2026-08-03T01:00:01.000001+00:00",
                    }
                ],
                "execution_spans": [
                    {
                        "started_at": "2026-08-03T01:00:01.000001+00:00",
                        "finished_at": "2026-08-03T01:00:02.200001+00:00",
                    }
                ],
            },
            "plan_build": {
                "queued_at": "2026-08-03T01:00:02.200001+00:00",
                "queue_spans": [
                    {
                        "started_at": "2026-08-03T01:00:02.200001+00:00",
                        "finished_at": "2026-08-03T01:00:02.300001+00:00",
                    }
                ],
                "execution_spans": [
                    {
                        "started_at": "2026-08-03T01:00:02.300001+00:00",
                        "finished_at": "2026-08-03T01:00:04.600001+00:00",
                    }
                ],
            },
            "formal_research": {
                "queued_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        fixed_timings["formal_research"]["queue_spans"] = [
            {
                "started_at": fixed_timings["formal_research"]["queued_at"],
                "finished_at": None,
            }
        ]
        for step in steps:
            terminal = step.step_name != "formal_research"
            await manager._conn.execute(
                """
                UPDATE workflow_steps
                SET status=?, timing_json=?, started_at=?, completed_at=?
                WHERE step_id=?
                """,
                (
                    "succeeded" if terminal else "pending",
                    json.dumps(fixed_timings[step.step_name]),
                    "2026-08-03 01:00:00" if terminal else None,
                    "2026-08-03 01:00:05" if terminal else None,
                    step.step_id,
                ),
            )
        await manager._conn.execute(
            "UPDATE workflow_runs SET current_step='formal_research', phase='retrieval' WHERE run_id=?",
            (run.run_id,),
        )
        await manager._conn.commit()

    SQLiteContentResearchStore(db_path).save_brief(
        ResearchBriefRecord(
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
    )
    return {"run_id": run.run_id, "thread_id": thread["id"]}


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
    marketing_conclusions: list[dict] | None = None,
) -> str:
    base = governed_snapshot()
    governed = base.metadata["governed_snapshot"]
    citation_group = governed["citation_groups"][0]
    include_admitted_cards = publication_state != "partial_verified_report"
    marketing_claim_cards = [
        {
            **governed["claim_cards"][0],
            "claim_candidate_id": f"cc_need_{index}",
            "admission_decision_id": f"cad_need_{index}",
            "claim_type": "use_context",
            "scope": {"sample": "selected_packets"},
            "statement": f"raw claim {index} must not render as a conclusion",
        }
        for index in range(1, len(evidence_refs) + 1)
    ]
    marketing_citation_groups = [
        {
            **citation_group,
            "citation_group_id": f"citation_need_{index}",
            "display_index": index,
            "claim_candidate_id": f"cc_need_{index}",
            "admission_decision_id": f"cad_need_{index}",
            "evidence_refs": [{**evidence_ref, "canonical_note_id": f"note-{index}"}],
        }
        for index, evidence_ref in enumerate(evidence_refs, start=1)
    ]
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
                    **(
                        {
                            "marketing_conclusion_policy": {
                                "primary_marketing_goal": "content_seeding",
                                "tracks": ["need", "value", "message"],
                                "minimum_notes_per_conclusion": 3,
                                "minimum_independent_authors_per_conclusion": 2,
                                "require_core_and_first_intent_support": True,
                                "maximum_primary_conclusions_per_track": 1,
                            }
                        }
                        if marketing_conclusions is not None
                        else {}
                    ),
                },
                "citation_groups": [
                    {
                        **citation_group,
                        "admission_decision_id": governed["claim_cards"][0]["admission_decision_id"],
                        "evidence_refs": evidence_refs,
                    }
                ] if include_admitted_cards and marketing_conclusions is None else (
                    marketing_citation_groups if include_admitted_cards else []
                ),
                "claim_cards": [
                    {
                        **card,
                        "claim_type": "message_angle",
                        "scope": {"sample": "selected_packets"},
                    }
                    for card in governed["claim_cards"]
                ] if include_admitted_cards and marketing_conclusions is None else (
                    marketing_claim_cards if include_admitted_cards else []
                ),
                **(
                    {"marketing_conclusions": marketing_conclusions}
                    if marketing_conclusions is not None
                    else {}
                ),
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
        await manager.begin_report_finalization(run_id)
    await ReportPublicationMaterializer(store, db_path).materialize(publication.id)
    async with WorkflowRunManager(db_path) as manager:
        await manager.complete_report_finalization(run_id)
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
