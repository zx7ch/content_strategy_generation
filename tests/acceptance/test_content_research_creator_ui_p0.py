from __future__ import annotations

import os
from pathlib import Path
import re
import time

from playwright.sync_api import expect, sync_playwright
import pytest

from tests.acceptance.conftest import write_acceptance_artifact
from tests.acceptance.test_v2_phase1_console_walkthrough import (
    _chrome_executable,
    _reserve_port,
    _run_process,
)


@pytest.mark.acceptance
def test_creator_content_research_p0_browser_flow(
    acceptance_storage,
    acceptance_artifact_dir: Path,
):
    repo_root = Path(__file__).resolve().parents[2]
    frontend_root = repo_root / "frontend"
    backend_port = _reserve_port()
    frontend_port = _reserve_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    frontend_url = f"http://127.0.0.1:{frontend_port}"
    backend_env = {
        **os.environ,
        "SQLITE_DB_PATH": acceptance_storage["db_path"],
        "CHROMA_PERSIST_DIR": acceptance_storage["chroma_dir"],
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
    started = time.perf_counter()

    with _run_process(
        cmd=[
            "python3",
            "-m",
            "uvicorn",
            "app.main:app",
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
        ready_timeout=20,
        name="backend",
    ):
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
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path=_chrome_executable())
                page = browser.new_page(viewport={"width": 1440, "height": 960})
                presearch_responses: list[str] = []
                confirm_responses: list[str] = []
                report_payloads: list[dict] = []
                trace_payloads: list[dict] = []

                def record_presearch_response(response):
                    if "/content-research/presearch" in response.url:
                        presearch_responses.append(f"{response.status} {response.url}")
                    if "/content-research/workflows/" in response.url and "/actions" in response.url:
                        confirm_responses.append(f"{response.status} {response.url}")

                def record_runtime_projection(response):
                    if response.status != 200:
                        return
                    try:
                        payload = response.json()
                    except Exception:
                        return
                    if "/content-research/workflows/" in response.url and "/report" in response.url:
                        report_payloads.append(payload)
                    if "/content-research/workflows/" in response.url and "/trace" in response.url:
                        trace_payloads.append(payload)

                page.on("response", record_presearch_response)
                page.on("response", record_runtime_projection)
                # Creator keeps live polling/SSE connections open, so networkidle
                # is not a meaningful readiness signal for this user workflow.
                page.goto(f"{frontend_url}/creator", wait_until="domcontentloaded")

                try:
                    page.get_by_role("button", name=re.compile("内容调研")).click(timeout=60000)
                except Exception as exc:
                    body_text = page.locator("body").inner_text(timeout=5000)
                    raise AssertionError(f"content research entry unavailable. body:\n{body_text}") from exc
                page.locator("textarea").fill("徒步短裤")
                page.locator("textarea").press("Enter")
                try:
                    page.get_by_role("heading", name="在开始前，请确认几个关键点").wait_for(timeout=18000)
                except Exception as exc:
                    body_text = page.locator("body").inner_text(timeout=5000)
                    raise AssertionError(
                        "content research checklist did not appear within the 10-second "
                        f"presearch first-feedback contract. responses={presearch_responses}. body:\n{body_text}"
                    ) from exc
                selected_presearch_buttons = page.evaluate(
                    """
                    () => [...document.querySelectorAll('button')]
                      .filter((button) => button.className.includes('bg-[#789180]'))
                      .map((button) => button.textContent.trim())
                    """
                )
                assert selected_presearch_buttons == []
                page.get_by_role("button", name="准确，继续").click()
                confirm_button = page.get_by_role("button", name=re.compile("确认并开始调研"))
                expect(confirm_button).to_be_disabled()
                page.get_by_role("button", name=re.compile("产品营销")).click()
                expect(confirm_button).to_be_enabled()
                confirm_button.click()

                collapsed_trace = page.locator('section[aria-label="Content Research Trace inspector"]')
                try:
                    expect(collapsed_trace).to_be_visible(timeout=20000)
                except Exception as exc:
                    body_text = page.locator("body").inner_text(timeout=5000)
                    raise AssertionError(
                        "trace did not appear after confirm. "
                        f"confirm responses={confirm_responses}. body:\n{body_text}"
                    ) from exc
                expect(page.locator('section[aria-label="Content Research Trace"]')).to_have_count(0)
                if os.getenv("CONTENT_RESEARCH_CONFIRM_ONLY") == "1":
                    browser.close()
                    return

                published_report = page.locator('article[aria-label="Content Research published report"]')
                try:
                    expect(published_report).to_be_visible(timeout=60000)
                except Exception as exc:
                    body_text = page.locator("body").inner_text(timeout=5000)
                    raise AssertionError(f"published report did not appear after background completion. body:\n{body_text}") from exc

                collapsed_trace.get_by_label("展开 Content Research Trace").click()
                trace_window = page.locator('section[aria-label="Content Research Trace"]')
                expect(trace_window).to_be_visible(timeout=10000)
                try:
                    expect(trace_window).to_contain_text("报告生成与忠实度审计", timeout=10000)
                except Exception as exc:
                    body_text = page.locator("body").inner_text(timeout=5000)
                    raise AssertionError(f"trace did not show its safe published projection. body:\n{body_text}") from exc

                if os.getenv("ACCEPTANCE_RUN_REAL") == "1":
                    assert report_payloads, "real Gate 2 run did not return a published report payload"
                    assert report_payloads[-1].get("citation_total", 0) > 0, (
                        "real Gate 2 report has no citations; inspect its persisted provider outcomes "
                        "and selection checkpoints"
                    )
                    assert trace_payloads, "real Gate 2 run did not return a Trace payload"
                    assert all(
                        "request" not in operation and "cookie" not in operation and "token" not in operation
                        for operation in trace_payloads[-1].get("provider_operations", [])
                    )

                page.get_by_label("关闭 Trace 对话框").click()
                expect(page.locator('section[aria-label="Content Research Trace inspector"]')).to_be_visible(timeout=5000)
                final_text = page.locator("body").inner_text()
                browser.close()

    write_acceptance_artifact(
        acceptance_artifact_dir,
        "content_research_creator_ui_p0",
        {
            "backend_url": backend_url,
            "frontend_url": frontend_url,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "contains_trace": "Trace · 内容调研" in final_text,
            "citation_total": report_payloads[-1].get("citation_total") if report_payloads else None,
            "provider_operation_count": len(trace_payloads[-1].get("provider_operations", [])) if trace_payloads else 0,
            "db_path": acceptance_storage["db_path"],
        },
    )
