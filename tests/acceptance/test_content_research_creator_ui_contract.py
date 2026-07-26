from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from tests.acceptance.test_v2_phase1_console_walkthrough import (
    _chrome_executable,
    _reserve_port,
    _run_process,
)


@pytest.fixture(scope="module")
def creator_ui_server():
    repo_root = Path(__file__).resolve().parents[2]
    frontend_root = repo_root / "frontend"
    frontend_port = _reserve_port()
    backend_port = _reserve_port()
    frontend_url = f"http://127.0.0.1:{frontend_port}"
    backend_url = f"http://127.0.0.1:{backend_port}"
    frontend_env = {
        **os.environ,
        "NEXT_PUBLIC_XHS_API_BASE_URL": backend_url,
        "XHS_API_BASE_URL": backend_url,
        "NEXT_TELEMETRY_DISABLED": "1",
    }

    with _run_process(
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
        yield {"frontend_url": frontend_url, "backend_url": backend_url}


@pytest.fixture()
def page_with_runtime(creator_ui_server):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=_chrome_executable())
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        yield page, creator_ui_server["frontend_url"], creator_ui_server["backend_url"]
        browser.close()





def test_content_research_presearch_failure_surfaces_in_chat(page_with_runtime):
    page, frontend_url, backend_url = page_with_runtime
    runtime = MockRuntime(presearch_status=500)
    runtime.install(page, backend_url)

    open_content_research(page, frontend_url, "北面", wait_for_checklist=False)

    expect(page.get_by_text("内容调研预检索失败，请检查 runtime 或小红书登录态。")).to_be_visible(timeout=5000)
    expect(page.get_by_role("heading", name="在开始前，请确认几个关键点")).to_have_count(0)


def test_content_research_confirm_double_click_is_idempotent_in_ui(page_with_runtime):
    page, frontend_url, backend_url = page_with_runtime
    runtime = MockRuntime(action_delay_ms=250)
    runtime.install(page, backend_url)

    open_content_research(page, frontend_url, "北面")
    page.get_by_role("button", name="准确，继续").click()
    page.get_by_role("button", name="产品营销").click()
    page.get_by_role("button", name=re.compile("确认并开始调研")).dblclick()

    expect(page.get_by_text("已确认调研范围，正在开始内容调研。")).to_be_visible(timeout=5000)
    assert runtime.confirm_calls == 1


def test_orphaned_content_research_run_is_not_presented_as_a_retryable_spider_failure(page_with_runtime):
    page, frontend_url, backend_url = page_with_runtime
    runtime = MockRuntime(formal_action_status=409)
    runtime.install(page, backend_url)

    open_content_research(page, frontend_url, "北面")
    page.get_by_role("button", name="准确，继续").click()
    page.get_by_role("button", name="产品营销").click()
    page.get_by_role("button", name=re.compile("确认并开始调研")).click()

    expect(page.get_by_text("本轮调研所属的 Creator 对话已不存在，无法重试。请在有效对话中重新发起一轮内容调研。")).to_be_visible(timeout=5000)
    expect(page.get_by_text("所属对话已不存在", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="重试")).to_have_count(0)


def test_content_research_empty_presearch_does_not_default_select_or_submit(page_with_runtime):
    page, frontend_url, backend_url = page_with_runtime
    empty_presearch = {
        **presearch_payload(),
        "competitor_tags": [],
        "research_directions": [],
        "fallback_used": True,
        "status": "fallback",
    }
    runtime = MockRuntime(presearch=empty_presearch)
    runtime.install(page, backend_url)

    open_content_research(page, frontend_url, "冷门手帐收纳方法")
    assert_no_preselected_presearch_buttons(page)
    expect(page.get_by_role("button", name=re.compile("确认并开始调研"))).to_be_disabled()
    expect(page.get_by_text("待补充竞品")).to_be_visible()


def test_content_research_allows_user_to_correct_the_research_subject(page_with_runtime):
    page, frontend_url, backend_url = page_with_runtime
    runtime = MockRuntime()
    runtime.install(page, backend_url)

    open_content_research(page, frontend_url, "速干T恤")
    page.get_by_role("button", name="不准确，我在下方说明").click()
    subject_input = page.get_by_label("调研主体")
    subject_input.fill("夏季通勤速干上衣")
    page.get_by_role("button", name="产品营销").click()
    page.get_by_role("button", name=re.compile("确认并开始调研")).click()

    expect(page.get_by_text("已确认调研范围，正在开始内容调研。")).to_be_visible(timeout=5000)
    assert runtime.last_confirm_payload["confirmed_subject"] == "夏季通勤速干上衣"


def test_content_research_trace_recovers_after_reload(page_with_runtime):
    page, frontend_url, backend_url = page_with_runtime
    runtime = MockRuntime(timeline=True)
    runtime.install(page, backend_url)

    open_content_research(page, frontend_url, "北面")
    page.get_by_role("button", name="准确，继续").click()
    page.get_by_role("button", name="产品营销").click()
    page.get_by_role("button", name=re.compile("确认并开始调研")).click()
    inspector = page.locator('section[aria-label="Content Research Trace inspector"]')
    expect(inspector).to_be_visible(timeout=10000)
    inspector.get_by_label("查看 Trace").click()
    trace = page.locator('section[aria-label="Content Research Trace"]')
    expect(trace).to_be_visible()
    page.keyboard.press("Escape")
    expect(trace).to_be_hidden()
    inspector.get_by_label("查看 Trace").click()
    expect(trace).to_be_visible()
    trace.click(position={"x": 4, "y": 4})
    expect(trace).to_be_hidden()
    page.reload(wait_until="networkidle")
    expect(page.locator('section[aria-label="Content Research Trace inspector"]')).to_be_visible(timeout=10000)


def test_content_research_restores_published_report_from_report_contract(page_with_runtime):
    page, frontend_url, backend_url = page_with_runtime
    runtime = MockRuntime(timeline=True)
    runtime.install(page, backend_url)

    page.goto(f"{frontend_url}/creator?contentResearchRunId=run-ui", wait_until="networkidle")

    report = page.locator('article[aria-label="Content Research published report"]')
    expect(report).to_be_visible(timeout=5000)
    expect(report.get_by_text("部分内容已核验")).to_be_visible()
    header = report.locator('header[aria-label="Content Research report header"]')
    expect(header.get_by_role("heading", name="尺寸说明应先于促销表达。")).to_be_visible()
    expect(header.get_by_text("研究范围：1 条冻结引用 · 1 条受治理结论 · 方向覆盖未公开", exact=True)).to_be_visible()
    expect(header.get_by_text("发布日期未公开", exact=True)).to_be_visible()
    expect(header.get_by_text("冻结报告版本")).to_have_count(0)
    expect(report.get_by_role("heading", name="核心结论")).to_be_visible()
    expect(report.get_by_role("heading", name="主要发现").first).to_be_visible()
    expect(report.get_by_role("heading", name="下一步建议").first).to_be_visible()
    expect(report.get_by_text("跨方向张力")).to_be_visible()
    expect(report.get_by_text("初步信号")).to_be_visible()
    report.get_by_role("button", name="打开引用 7").click()
    evidence = page.locator('aside[aria-label="Content Research citation evidence"]')
    expect(evidence.get_by_text("原笔记链接不可用").first).to_be_visible()
    expect(evidence.get_by_text("完整冻结依据")).to_be_visible()
    expect(evidence.get_by_role("link", name="打开原笔记")).to_have_count(1)
    assert runtime.report_calls == 1
    page.locator('section[aria-label="Content Research Trace inspector"]').get_by_label("查看 Trace").click()
    trace = page.locator('section[aria-label="Content Research Trace"]')
    expect(trace.get_by_text("报告生成与忠实度审计").first).to_be_visible()
    expect(trace.get_by_text("耗时未知")).to_be_visible()
    page.reload(wait_until="networkidle")
    expect(page.locator('article[aria-label="Content Research published report"]')).to_have_count(1)


def test_content_research_sidebar_uses_safe_run_and_report_summary_hierarchy(page_with_runtime):
    page, frontend_url, backend_url = page_with_runtime
    runtime = MockRuntime(timeline=True)
    runtime.install(page, backend_url)

    page.goto(f"{frontend_url}/creator?contentResearchRunId=run-ui", wait_until="networkidle")

    sidebar = page.locator('aside[aria-label="内容调研上下文"]')
    expect(sidebar).to_be_visible()
    sidebar_text = sidebar.inner_text()
    assert sidebar_text.index("研究运行 / Trace") < sidebar_text.index("本次研究摘要")
    expect(sidebar.get_by_text("部分内容已核验", exact=True)).to_be_visible()
    expect(sidebar.get_by_text("范围与检索冻结", exact=True)).to_be_visible()
    expect(sidebar.get_by_text("证据准入与归纳", exact=True)).to_be_visible()
    expect(sidebar.get_by_text("报告发布与范围", exact=True)).to_be_visible()
    expect(sidebar.get_by_text("1 条冻结引用", exact=True)).to_be_visible()
    expect(sidebar.get_by_text("1 条受治理结论", exact=True)).to_be_visible()
    expect(sidebar.get_by_text("1 组跨方向证据张力", exact=True)).to_be_visible()
    expect(sidebar.get_by_text("1 条初步信号", exact=True)).to_be_visible()
    assert "LLM calls" not in sidebar_text
    assert "已知成本" not in sidebar_text

    sidebar.get_by_label("查看完整 workflow trace").click()
    expect(page.locator('section[aria-label="Content Research Trace"]')).to_be_visible()


def test_content_research_sidebar_truthfully_omits_report_summary_when_report_is_unavailable(page_with_runtime):
    page, frontend_url, backend_url = page_with_runtime
    runtime = MockRuntime(report_status=404, timeline=True)
    runtime.install(page, backend_url)

    page.goto(f"{frontend_url}/creator", wait_until="networkidle")

    sidebar = page.locator('aside[aria-label="内容调研上下文"]')
    expect(sidebar).to_be_visible(timeout=5000)
    expect(sidebar.get_by_text("研究摘要将在正式报告发布后显示。", exact=True)).to_be_visible()
    expect(sidebar.get_by_text("暂无已发布报告；此处不会显示未冻结的来源、结论或指标。", exact=True)).to_be_visible()
    expect(sidebar.get_by_text(re.compile("条冻结引用"))).to_have_count(0)


def test_content_research_trace_uses_nested_safe_usage_and_workflow_identity(page_with_runtime):
    page, frontend_url, backend_url = page_with_runtime
    report = published_report_payload()
    report["trace"] = {
        "checkpoint_summary": {"stages": [{
            "stage_name": "faithfulness", "status": "completed", "retry_count": 1,
            "duration_ms": 7800, "output_refs": ["audit-ui"],
        }]},
        "faithfulness": {"usage": {"total_tokens": 2486, "cost_usd": 0.18, "cost_unknown": False}},
    }
    runtime = MockRuntime(report=report, timeline=True)
    runtime.install(page, backend_url)

    page.goto(f"{frontend_url}/creator?contentResearchRunId=run-ui", wait_until="networkidle")
    page.locator('aside[aria-label="内容调研上下文"]').get_by_label("查看完整 workflow trace").click()

    trace = page.locator('section[aria-label="Content Research Trace"]')
    expect(trace.get_by_text("workflow_run · run-ui", exact=True)).to_be_visible()
    metrics = trace.locator('[aria-label="安全 Trace 指标"]')
    expect(metrics.get_by_text("2,486", exact=True)).to_be_visible()
    expect(metrics.get_by_text("LLM 调用", exact=True)).to_be_visible()
    expect(metrics.get_by_text("未公开", exact=True)).to_be_visible()
    expect(metrics.get_by_text("$0.18", exact=True)).to_be_visible()
    expect(trace.get_by_text("报告生成与忠实度审计", exact=True).first).to_be_visible()
    expect(trace.get_by_text("7.8s", exact=True)).to_be_visible()


def test_content_research_trace_marks_unknown_usage_and_empty_safe_stages_explicitly(page_with_runtime):
    page, frontend_url, backend_url = page_with_runtime
    report = published_report_payload()
    report["trace"] = {
        "checkpoint_summary": {"stages": []},
        "faithfulness": {"usage": {"cost_unknown": True}},
    }
    runtime = MockRuntime(report=report, timeline=True)
    runtime.install(page, backend_url)

    page.goto(f"{frontend_url}/creator?contentResearchRunId=run-ui", wait_until="networkidle")
    page.locator('aside[aria-label="内容调研上下文"]').get_by_label("查看完整 workflow trace").click()

    trace = page.locator('section[aria-label="Content Research Trace"]')
    metrics = trace.locator('[aria-label="安全 Trace 指标"]')
    expect(metrics.get_by_text("未记录", exact=True)).to_be_visible()
    expect(metrics.get_by_text("成本未知", exact=True)).to_be_visible()
    expect(trace.get_by_text("暂无 trace 记录。确认 brief 后会自动写入执行轨迹。", exact=True)).to_be_visible()


def test_content_research_evidence_only_report_hides_free_narrative(page_with_runtime):
    page, frontend_url, backend_url = page_with_runtime
    report = published_report_payload()
    report["publication_state"] = "evidence_only_report"
    report["sections"] = [{"section_id": "core", "section_kind": "core_conclusions", "text": "不得显示的自由叙述。"}]
    runtime = MockRuntime(report=report, timeline=True)
    runtime.install(page, backend_url)

    page.goto(f"{frontend_url}/creator?contentResearchRunId=run-ui", wait_until="networkidle")

    published = page.locator('article[aria-label="Content Research published report"]')
    header = published.locator('header[aria-label="Content Research report header"]')
    expect(header.get_by_role("heading", name="本次研究的已验证证据")).to_be_visible()
    expect(published.get_by_text("仅展示已验证证据")).to_be_visible()
    expect(published.get_by_text("不得显示的自由叙述。")).to_have_count(0)
    expect(published.get_by_text("已验证证据卡")).to_be_visible()
    expect(published.get_by_text("研究范围与限制")).to_be_visible()


def test_content_research_timeline_hydrates_safe_trace_and_partial_audit_details(page_with_runtime):
    page, frontend_url, backend_url = page_with_runtime
    report = published_report_payload()
    report["publication"] |= {
        "omitted_section_ids": ["findings"],
        "reason_codes": ["semantic_audit_failed"],
        "audit_recovery_state": "targeted_rewrite_exhausted",
    }
    runtime = MockRuntime(report=report, timeline=True)
    runtime.install(page, backend_url)

    page.goto(f"{frontend_url}/creator", wait_until="networkidle")

    report_message = page.locator('article[aria-label="Content Research published report"]')
    expect(report_message).to_be_visible(timeout=5000)
    expect(report_message.get_by_text("已撤下：主要发现")).to_be_visible()
    expect(report_message.get_by_text("semantic_audit_failed")).to_be_visible()
    expect(page.locator('section[aria-label="Content Research Trace inspector"]')).to_be_visible()
    page.locator('section[aria-label="Content Research Trace inspector"]').get_by_label("查看 Trace").click()
    trace = page.locator('section[aria-label="Content Research Trace"]')
    expect(trace.get_by_text("报告生成与忠实度审计").first).to_be_visible()
    expect(trace.get_by_text("执行输入")).to_have_count(0)


def test_content_research_timeline_report_unavailable_is_explicit(page_with_runtime):
    page, frontend_url, backend_url = page_with_runtime
    runtime = MockRuntime(report_status=404, timeline=True)
    runtime.install(page, backend_url)

    page.goto(f"{frontend_url}/creator", wait_until="networkidle")

    expect(page.get_by_text(re.compile("正式报告暂不可读取"))).to_be_visible(timeout=5000)


def test_content_research_run_restore_storage_is_thread_scoped_and_has_no_legacy_key():
    source = (Path(__file__).resolve().parents[2] / "frontend/src/app/creator/page.tsx").read_text()

    assert '"xhs-growth-agent:content-research-active-runs-by-thread"' in source
    assert '"xhs-growth-agent:content-research-active-run"' not in source
    reset_conversation = source[source.index("function resetConversation()"):source.index("async function refreshContentResearchTrace")]
    assert "localStorage" not in reset_conversation
    assert source.count("removeContentResearchRunForThread(run.summary.brief.thread_id)") == 2
    assert "if (restoredThread)" in source
    assert "该内容调研所属对话不可访问，未切换到其他对话。" in source


def test_content_research_creator_has_no_legacy_flow_message_renderer():
    source = (Path(__file__).resolve().parents[2] / "frontend/src/app/creator/page.tsx").read_text()

    assert "ContentResearchFlowMessages" not in source
    assert "failedFormalResearchTasks" not in source
    assert "runtime_child_tasks" not in source


def test_content_research_complete_report_keeps_cards_and_recovery_structured(page_with_runtime):
    page, frontend_url, backend_url = page_with_runtime
    report = published_report_payload()
    report["publication_state"] = "complete_verified_report"
    report["aggregate_claims"] = [{"aggregate_claim_id": "ag-ui", "statement": "补采评论后验证尺码表达", "aggregate_type": "action_hypothesis", "hypothesis_only": True}]
    report["weak_signals"] = [{"weak_signal_id": "weak-ui", "reason": "评论样本尚不足", "recovery_action": "补采独立评论样本"}]
    report["limitations_recovery"] = [{"message": "当前样本范围有限", "recovery_action": "扩大样本窗口"}]
    runtime = MockRuntime(report=report, timeline=True)
    runtime.install(page, backend_url)

    page.goto(f"{frontend_url}/creator?contentResearchRunId=run-ui", wait_until="networkidle")

    published = page.locator('article[aria-label="Content Research published report"]')
    expect(published.get_by_text("已完整核验")).to_be_visible()
    published.get_by_text("尺寸说明是当前可验证的内容重点。").click()
    expect(published.get_by_text("已准入方向 claim")).to_be_visible()
    published.get_by_text("评论样本尚不足", exact=True).click()
    expect(published.get_by_text("补采独立评论样本")).to_be_visible()
    published.get_by_text("补采评论后验证尺码表达").click()
    expect(published.get_by_text("待验证行动假设，非已验证事实。")).to_be_visible()


def test_content_research_citation_metadata_and_paging_preserve_frozen_indices(page_with_runtime):
    page, frontend_url, backend_url = page_with_runtime
    report = published_report_payload()
    report["citation_total"] = 2
    report["citation_limit"] = 1
    next_page = json.loads(json.dumps(report))
    next_page["citation_offset"] = 1
    next_page["citation_groups"] = [{
        "citation_group_id": "cg-ui-2", "display_index": 8,
        "preview_ref": {"quote": "第二组冻结依据", "jump_state": "unavailable"},
        "evidence_refs": [{"quote": "第二条完整依据", "title": "第二篇笔记", "source_type": "note", "captured_at": "2026-07-21T00:00:00Z", "field_path": "note.desc", "jump_state": "unavailable"}],
    }]
    runtime = MockRuntime(report=report, report_pages={1: next_page}, timeline=True)
    runtime.install(page, backend_url)

    page.goto(f"{frontend_url}/creator?contentResearchRunId=run-ui", wait_until="networkidle")
    published = page.locator('article[aria-label="Content Research published report"]')
    published.get_by_role("button", name="打开引用 7").click()
    evidence = page.locator('aside[aria-label="Content Research citation evidence"]')
    expect(evidence.get_by_text("尺码反馈集中，链接不可用")).to_be_visible()
    expect(evidence.get_by_text("北面冲锋衣尺码笔记 · note · 2026-07-21T00:00:00Z · note.desc")).to_be_visible()
    published.get_by_role("button", name="加载其余 1 组引用").click()
    next_citation = published.get_by_role("button", name="打开引用 8")
    expect(next_citation).to_have_count(1)
    next_citation.click()
    expect(evidence.get_by_text("第二组冻结依据")).to_be_visible()
    expect(evidence.get_by_text("第二条完整依据")).to_be_visible()
    evidence.get_by_role("button", name="关闭引用依据").click()
    expect(next_citation).to_have_count(1)
    next_citation.click()
    expect(evidence.get_by_text("第二组冻结依据")).to_be_visible()
    assert runtime.report_offsets == [0, 1]


def open_content_research(page: Page, frontend_url: str, seed: str, *, wait_for_checklist: bool = True) -> None:
    page.goto(f"{frontend_url}/creator", wait_until="networkidle")
    try:
        page.get_by_role("button", name=re.compile("内容调研")).click(timeout=15000)
    except Exception as exc:
        body_text = page.locator("body").inner_text(timeout=5000)
        raise AssertionError(f"content research entry unavailable. body:\n{body_text}") from exc
    page.locator("textarea").fill(seed)
    page.locator("textarea").press("Enter")
    if wait_for_checklist:
        page.get_by_role("heading", name="在开始前，请确认几个关键点").wait_for(timeout=15000)


def assert_no_preselected_presearch_buttons(page: Page) -> None:
    selected = page.evaluate(
        """
        () => [...document.querySelectorAll('button')]
          .filter((button) => button.className.includes('bg-[#789180]'))
          .map((button) => button.textContent.trim())
        """
    )
    assert selected == []


class MockRuntime:
    def __init__(
        self,
        *,
        presearch_status: int = 201,
        presearch: dict[str, Any] | None = None,
        source_results: list[str] | None = None,
        action_delay_ms: int = 0,
        formal_action_status: int = 200,
        report: dict[str, Any] | None = None,
        report_status: int = 200,
        report_pages: dict[int, dict[str, Any]] | None = None,
        timeline: bool = False,
    ) -> None:
        self.presearch_status = presearch_status
        self.presearch = presearch or presearch_payload()
        self.source_results = source_results or ["completed"]
        self.action_delay_ms = action_delay_ms
        self.formal_action_status = formal_action_status
        self.confirm_calls = 0
        self.source_calls = 0
        self.last_confirm_payload: dict[str, Any] = {}
        self.current_source = "idle"
        self.decisions: list[dict[str, Any]] = []
        self.report_calls = 0
        self.report = report or published_report_payload()
        self.report_status = report_status
        self.report_pages = report_pages or {}
        self.report_offsets: list[int] = []
        self.timeline = timeline

    def install(self, page: Page, backend_url: str) -> None:
        page.route(f"{backend_url}/**", self._handle_route)

    def _handle_route(self, route) -> None:
        request = route.request
        url = urlparse(request.url)
        path = url.path
        method = request.method.upper()
        if method == "OPTIONS":
            route.fulfill(status=204, headers=cors_headers())
            return

        if path == "/health":
            fulfill_json(route, {"service": "xhs-agent-runtime", "status": "healthy", "version": "dev", "api_contract": "local-runtime-v1"})
            return
        if path == "/runtime/prewarm":
            fulfill_json(route, {"status": "ok"})
            return
        if path == "/workspaces/default":
            fulfill_json(route, {"workspace_id": "ws-ui", "user_id": "user-ui"})
            return
        if path == "/brands":
            fulfill_json(
                route,
                {
                    "items": [
                        {
                            "id": "brand-1",
                            "workspace_id": "ws-ui",
                            "name": "轻量户外",
                            "category": "outdoor",
                            "stage": "growth",
                            "target_audience": {},
                            "brand_voice": {},
                            "goals": {},
                            "created_at": "2026-07-05T00:00:00Z",
                            "updated_at": "2026-07-05T00:00:00Z",
                        }
                    ]
                },
            )
            return
        if path == "/brands/brand-1/channels":
            fulfill_json(route, {"items": [{"id": "ch-1", "platform": "xiaohongshu", "account_name": "轻量户外"}]})
            return
        if path == "/threads" and method == "GET":
            fulfill_json(route, {"items": [{"thread_id": "thread-ui", "workspace_id": "ws-ui", "title": "北面调研报告", "brand_id": "brand-1", "status": "active", "active_job_id": None, "active_run_id": "run-ui", "created_at": "2026-07-21T00:00:00Z", "updated_at": "2026-07-21T00:00:00Z"}] if self.timeline else []})
            return
        if path == "/threads" and method == "POST":
            fulfill_json(route, {"thread_id": "thread-ui", "title": "北面", "brand_id": "brand-1"}, status=201)
            return
        if path == "/content-research/presearch":
            if self.presearch_status >= 400:
                fulfill_json(route, {"error_message": "presearch failed"}, status=self.presearch_status)
            else:
                fulfill_json(route, self.presearch, status=201)
            return
        if path == "/content-research/workflows/run-ui" and method == "GET":
            fulfill_json(route, workflow_payload(self.current_source))
            return
        if path == "/content-research/workflows/run-ui/actions":
            payload = json.loads(request.post_data or "{}")
            action = payload.get("action")
            if action == "confirm_brief":
                self.confirm_calls += 1
                self.last_confirm_payload = payload.get("payload") or {}
                if self.action_delay_ms:
                    time.sleep(self.action_delay_ms / 1000)
                fulfill_json(route, action_response(action, workflow_payload()))
                return
            if action in {"start_formal_research", "retry_formal_research"}:
                self.source_calls += 1
                if self.formal_action_status >= 400:
                    fulfill_json(route, {"error_message": "Creator thread is required to publish formal report: thread-ui"}, status=self.formal_action_status)
                    return
                status = self.source_results[min(self.source_calls - 1, len(self.source_results) - 1)]
                self.current_source = status
                fulfill_json(route, action_response(action, formal_research_payload(status)))
                return
            if action == "end_content_research":
                fulfill_json(route, action_response(action, {"ended": True, "active_run_cleared": True}))
                return
        if path == "/content-research/workflows/run-ui/trace":
            fulfill_json(route, trace_payload(self.current_source))
            return
        if path == "/content-research/workflows/run-ui/report":
            self.report_calls += 1
            offset = int(parse_qs(url.query).get("citation_offset", ["0"])[0])
            self.report_offsets.append(offset)
            fulfill_json(route, self.report_pages.get(offset, self.report) if self.report_status < 400 else {"error_message": "report unavailable"}, status=self.report_status)
            return
        if path == "/threads/thread-ui/timeline":
            fulfill_json(route, {"thread": {"thread_id": "thread-ui", "workspace_id": "ws-ui", "title": "北面调研报告", "brand_id": "brand-1", "status": "active", "active_job_id": None, "active_run_id": "run-ui", "created_at": "2026-07-21T00:00:00Z", "updated_at": "2026-07-21T00:00:00Z"}, "messages": [{"message_id": "artifact-ui", "role": "assistant", "text": "内容调研报告已生成。", "message_type": "artifact_result", "run_id": "run-ui", "artifact_refs": [], "created_at": "2026-07-21T00:00:00Z"}]})
            return
        if path == "/content-research/workflows/run-ui/decisions":
            fulfill_json(route, decisions_payload(self.decisions))
            return
        if path == "/content-research/workflows/run-ui/brand-decisions" and method == "POST":
            decision = decision_payload(json.loads(request.post_data or "{}"), target_type="brand_candidate")
            self._append_decision(decision)
            fulfill_json(route, decision)
            return
        if path == "/content-research/workflows/run-ui/content-decisions" and method == "POST":
            decision = decision_payload(json.loads(request.post_data or "{}"), target_type="recommended_content")
            self._append_decision(decision)
            fulfill_json(route, decision)
            return
        if path == "/content-research/evidence-bundles/eb-ui":
            fulfill_json(route, evidence_bundle_payload())
            return

        fulfill_json(route, {"error_message": f"Unhandled mock route: {method} {path}"}, status=404)

    def _append_decision(self, decision: dict[str, Any]) -> None:
        for existing in self.decisions:
            if existing["target_type"] == decision["target_type"] and existing["target_id"] == decision["target_id"]:
                existing["is_current"] = False
        self.decisions.append(decision)


def presearch_payload() -> dict[str, Any]:
    return {
        "schema_version": "content_research_api_v1",
        "attempt_id": "att-ui",
        "workflow_run_id": "run-ui",
        "brief_id": "rb-ui",
        "status": "completed",
        "subject_confirmation": "北面（The North Face）是户外服饰装备品牌，本次调研主体是否为该品牌？",
        "competitor_tags": ["始祖鸟", "哥伦比亚", "迪卡侬"],
        "research_directions": ["产品营销", "用户评论痛点"],
        "custom_research_question": "",
        "custom_competitor_input": "",
        "timeout_status": "none",
        "fallback_used": False,
    }


def workflow_payload(source_status: str = "idle") -> dict[str, Any]:
    return {
        "workflow_run_id": "run-ui",
        "brief": {
            "id": "rb-ui",
            "workflow_run_id": "run-ui",
            "thread_id": "thread-ui",
            "status": "ready",
            "payload": {
                "confirmed_subject": "北面",
                "selected_competitors": ["始祖鸟"],
                "selected_directions": ["product_marketing"],
            },
        },
        "plan": {"id": "plan-ui", "brief_id": "rb-ui", "workflow_run_id": "run-ui", "status": "ready", "payload": {}},
        "directions": [],
        "subagent_tasks": [],
        "runtime_run": {
            "run_id": "run-ui",
            "current_step": "source_collect_minimal",
            "status": "completed" if source_status == "completed" else "running",
        },
        "runtime_steps": [],
        "runtime_child_tasks": [],
    }


def source_payload(status: str) -> dict[str, Any]:
    if status == "failed":
        return {
            "workflow_run_id": "run-ui",
            "provider": "xiaohongshu",
            "source_kind": "search_result_minimal",
            "status": "failed",
            "failure_reason": "auth_required",
            "cookie_status": "invalid",
            "items": [],
            "metadata": {},
        }
    return {
        "workflow_run_id": "run-ui",
        "provider": "xiaohongshu",
        "source_kind": "search_result_minimal",
        "status": "completed",
        "failure_reason": None,
        "cookie_status": "valid",
        "items": [{"canonical_id": f"note-{index}", "title": f"北面笔记 {index}"} for index in range(1, 11)],
        "metadata": {"item_count": 10},
    }


def published_report_payload() -> dict[str, Any]:
    return {
        "schema_version": "content_research_api_v1",
        "workflow_run_id": "run-ui",
        "workflow_terminal_state": "succeeded",
        "publication_state": "partial_verified_report",
        "artifact": {"artifact_id": "artifact-ui", "version": 1},
        "publication": {"report_publication_id": "pub-ui"},
        "sections": [
            {"section_id": "core", "section_kind": "core_conclusions", "text": "尺寸说明应先于促销表达。", "citation_group_ids": ["cg-ui"], "citation_anchors": [{"anchor_id": "anchor-ui", "citation_group_id": "cg-ui"}]},
            {"section_id": "findings", "section_kind": "main_findings", "text": "已验证的发现应直接服务内容决策。"},
            {"section_id": "next", "section_kind": "next_steps", "text": "下一轮先补采评论样本。"},
        ],
        "citation_groups": [{
            "citation_group_id": "cg-ui", "display_index": 7,
            "preview_ref": {"quote": "尺码反馈集中，链接不可用", "jump_state": "unavailable"},
            "evidence_refs": [
                {"quote": "完整冻结依据", "title": "北面冲锋衣尺码笔记", "source_type": "note", "captured_at": "2026-07-21T00:00:00Z", "field_path": "note.desc", "source_url": "https://example.test/note-ui", "jump_state": "available"},
                {"quote": "备用证据链接不可用", "jump_state": "unavailable"},
            ],
        }],
        "citation_total": 1,
        "citation_offset": 0,
        "citation_limit": 50,
        "claim_cards": [{"claim_candidate_id": "claim-ui", "statement": "尺寸说明是当前可验证的内容重点。"}],
        "weak_signals": [{"reason": "评论样本尚不足，需继续补采。"}],
        "cross_direction_records": [{"summary": "产品营销与评论方向存在待补采的证据张力。"}],
        "aggregate_claims": [],
        "limitations_recovery": [{"message": "当前结论仅覆盖已冻结的公开样本。"}],
        "trace": {"checkpoint_summary": {"stages": [{"stage_name": "faithfulness", "status": "completed", "retry_count": 0, "output_refs": []}]}},
    }


def formal_research_payload(source_status: str) -> dict[str, Any]:
    return {
        "workflow_run_id": "run-ui",
        "status": "failed" if source_status == "failed" else "completed",
        "task_count": 1,
        "completed_task_count": 0 if source_status == "failed" else 1,
        "partial_completed_task_count": 0,
        "failed_tasks": [{"task_id": "sat-ui", "error": "采集失败"}] if source_status == "failed" else [],
        "provider": "xiaohongshu",
        "source_kind": "search_result",
        "limit_per_specialist": 10,
    }


def decisions_payload(decisions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    values = decisions or []
    return {
        "schema_version": "content_research_api_v1",
        "workflow_run_id": "run-ui",
        "decisions": values,
        "current_decisions": [decision for decision in values if decision.get("is_current", True)],
    }


def decision_payload(payload: dict[str, Any], *, target_type: str) -> dict[str, Any]:
    status = payload.get("decision_status", "selected")
    return {
        "schema_version": "content_research_api_v1",
        "decision_id": f"hd-ui-{target_type}-{len(payload.get('target_id', ''))}-{time.time_ns()}",
        "workflow_run_id": "run-ui",
        "target_type": target_type,
        "target_id": payload.get("target_id"),
        "decision_request_id": payload.get("decision_request_id"),
        "decision_status": status,
        "decision_payload": payload.get("decision_payload") or {},
        "rationale": payload.get("rationale") or "",
        "created_by_type": "user",
        "created_by_id": "user-ui",
        "research_brief_id": "rb-ui",
        "research_plan_id": "plan-ui",
        "research_result_snapshot_id": payload.get("research_result_snapshot_id"),
        "metadata": payload.get("metadata") or {},
        "advancement": {"resource_policy": "full_deep_research" if status == "selected" else "deferred"},
        "is_current": True,
        "idempotent_replay": False,
        "history_count": 1,
        "created_at": "2026-07-09T00:00:00+08:00",
    }


def evidence_bundle_payload() -> dict[str, Any]:
    return {
        "schema_version": "content_research_api_v1",
        "bundle_id": "eb-ui",
        "workflow_run_id": "run-ui",
        "research_brief_id": "rb-ui",
        "research_plan_id": "plan-ui",
        "research_direction_id": "rd-ui",
        "status": "ready",
        "bundle_type": "research_direction",
        "bundle_version": "v1",
        "summary": "始祖鸟轻量外套证据包",
        "coverage": {"source_count": 10},
        "retrieval_metrics": {},
        "faithfulness_metrics": {},
        "cross_source_metrics": {},
        "contradiction_summary": {},
        "citation_coverage": {},
        "unsupported_claim_count": 0,
        "missing_evidence": [],
        "priority_policy_id": "pp_content_research_default_v1",
        "evidence_boundary_policy_id": "ebp_content_research_default_v1",
        "decision_card": {},
        "priority": {"label": "high_priority"},
        "evidence_state": "signal",
        "evidence_grade": "C",
        "claim_scope": {"allowed": ["Use as a bounded research signal."]},
        "next_action": {"type": "content_experiment"},
        "items": [],
        "evidence_by_role": {"supporting_fact": [{"id": "ev-ui", "title": "北面笔记"}]},
        "lineage_by_evidence_id": {"ev-ui": [{"transformation_type": "captured"}]},
        "source_links": [{"evidence_id": "ev-ui", "source_url": "https://example.com/note"}],
        "metadata": {},
        "created_at": "2026-07-09T00:00:00+08:00",
        "updated_at": "2026-07-09T00:00:00+08:00",
    }


def trace_payload(source_status: str) -> dict[str, Any]:
    source = source_payload("failed" if source_status == "failed" else "completed")
    events = [
        {"id": "obs-1", "event_name": "presearch_completed", "event_type": "task_completed", "sequence_no": 1},
        {"id": "obs-2", "event_name": "source_collection_started", "event_type": "task_started", "sequence_no": 2},
    ]
    if source_status == "failed":
        events.append({
            "id": "obs-3",
            "event_name": "source_collection_failed",
            "event_type": "task_failed",
            "sequence_no": 3,
            "payload": {"source_collection": source},
        })
    elif source_status == "completed":
        events.append({
            "id": "obs-3",
            "event_name": "source_collection_completed",
            "event_type": "task_completed",
            "sequence_no": 3,
            "payload": {"source_collection": source},
        })
    return {
        "workflow_run_id": "run-ui",
        "thread_id": "thread-ui",
        "current_stage": "source_collect_minimal",
        "run_status": "completed" if source_status == "completed" else "running",
        "recoverable": True,
        "duration_ms": 100,
        "error_count": 1 if source_status == "failed" else 0,
        "retry_count": 0,
        "traces": [],
        "observation_events": events,
        "workflow_events": [],
        "runtime_steps": [],
        "runtime_child_tasks": [],
        "usage_summary": {"total_tokens": 0},
        "usage_steps": [],
        "usage_events": [],
        "source_collection": source,
    }


def action_response(action: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "content_research_workflow_action_response_v1",
        "workflow_run_id": "run-ui",
        "action": action,
        "status": result.get("status", "completed"),
        "result": result,
        "execution_mode": "local",
        "remote_run_id": None,
        "local_cache_id": "rb-ui",
        "sync_status": "local_only",
    }


def cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS",
        "Content-Type": "application/json",
    }


def fulfill_json(route, payload: dict[str, Any], *, status: int = 200) -> None:
    route.fulfill(status=status, body=json.dumps(payload), headers=cors_headers())
