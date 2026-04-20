from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import time
from typing import Iterator

import httpx
from playwright.sync_api import Page, sync_playwright
import pytest

from tests.acceptance.conftest import write_acceptance_artifact


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", 0))
        except PermissionError as exc:  # pragma: no cover - env specific
            pytest.skip(f"socket bind unavailable in current environment: {exc}")
        return int(sock.getsockname()[1])


def _headers(workspace_id: str) -> dict[str, str]:
    return {
        "X-Workspace-Id": workspace_id,
        "X-User-Id": "acceptance-user",
    }


def _chrome_executable() -> str:
    candidates = [
        os.getenv("PLAYWRIGHT_CHROME_EXECUTABLE", "").strip(),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    pytest.skip("Chrome executable unavailable for Playwright console walkthrough")


@contextmanager
def _run_process(
    *,
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    ready_url: str,
    ready_timeout: float,
    name: str,
) -> Iterator[None]:
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output: list[str] = []
    try:
        deadline = time.time() + ready_timeout
        while time.time() < deadline:
            if process.poll() is not None:
                if process.stdout is not None:
                    output.append(process.stdout.read())
                raise AssertionError(f"{name} exited before startup:\n{''.join(output)}")
            try:
                response = httpx.get(ready_url, timeout=0.5, follow_redirects=True)
                if response.status_code < 500:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:  # pragma: no cover - startup failure
            if process.stdout is not None:
                output.append(process.stdout.read())
            raise AssertionError(f"{name} did not start in time:\n{''.join(output)}")
        yield
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - shutdown failure
                process.kill()
                process.wait(timeout=5)


def _seed_phase1_setup(base_url: str) -> dict[str, str]:
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        workspace = client.get("/workspaces/default")
        assert workspace.status_code == 200
        workspace_payload = workspace.json()
        workspace_id = workspace_payload["workspace_id"]

        brand = client.post(
            "/brands",
            headers=_headers(workspace_id),
            json={
                "name": "Console Walkthrough Brand",
                "category": "outdoor",
                "stage": "growth",
                "target_audience": {"age_ranges": ["25-34"], "gender_skew": "female"},
            },
        )
        assert brand.status_code == 201
        brand_payload = brand.json()

        channel = client.post(
            f"/brands/{brand_payload['id']}/channels",
            headers=_headers(workspace_id),
            json={
                "platform": "xiaohongshu",
                "account_name": "Console Walkthrough 小红书",
                "profile_url": "https://www.xiaohongshu.com/user/profile/console-walkthrough-xhs",
            },
        )
        assert channel.status_code == 201
        channel_payload = channel.json()

        policy = client.put(
            f"/brands/{brand_payload['id']}/policy-configs/active",
            headers=_headers(workspace_id),
            json={
                "policy_name": "baseline_rule_v1",
                "policy_version": "v1",
                "topic_type_targets": {
                    "targets": [
                        {"topic_type": "scenario", "min_ratio": 0.34, "max_ratio": 1.0, "priority_boost": 0.12},
                        {"topic_type": "problem", "min_ratio": 0.0, "max_ratio": 0.5, "priority_boost": 0.03},
                    ]
                },
            },
        )
        assert policy.status_code == 200

        snapshot = client.post(
            f"/brands/{brand_payload['id']}/state-snapshots",
            headers=_headers(workspace_id),
            json={
                "state_version": "state_v1",
                "stage": "growth",
                "state_features": {"audience_focus": "urban commuting"},
                "source_version": "v1",
            },
        )
        assert snapshot.status_code == 201

        source_sync = client.post(
            f"/brands/{brand_payload['id']}/source-syncs",
            headers=_headers(workspace_id),
            json={
                "source_type": "xhs_extension_capture",
                "source_adapter": "extension_source_sync_adapter_v1",
                "channel_id": channel_payload["id"],
                "capture_payload": {
                    "page_type": "search_result",
                    "captured_at": "2026-04-11T10:00:00+08:00",
                    "items": [
                        {
                            "note_id": "note-1",
                            "source_url": "https://www.xiaohongshu.com/explore/note-1",
                            "title": "通勤徒步鞋怎么选",
                            "visible_text_excerpt": "解决上下班和周末轻徒步切换问题",
                            "author_handle": "competitor-a",
                            "likes": 128,
                            "comments": 22,
                            "collects": 63,
                            "shares": 11,
                            "tags": ["通勤", "徒步"],
                        },
                        {
                            "note_id": "note-2",
                            "source_url": "https://www.xiaohongshu.com/explore/note-2",
                            "title": "尺码痛点怎么避坑",
                            "visible_text_excerpt": "买鞋最怕前掌挤脚和后跟磨脚",
                            "author_handle": "competitor-b",
                            "likes": 116,
                            "comments": 25,
                            "collects": 48,
                            "shares": 6,
                            "tags": ["避坑", "尺码"],
                        },
                        {
                            "note_id": "note-3",
                            "source_url": "https://www.xiaohongshu.com/explore/note-3",
                            "title": "周末轻徒步穿搭清单",
                            "visible_text_excerpt": "从鞋包到外套的一套轻量方案",
                            "author_handle": "competitor-c",
                            "likes": 104,
                            "comments": 14,
                            "collects": 41,
                            "shares": 5,
                            "tags": ["穿搭", "徒步"],
                        },
                    ],
                },
            },
        )
        assert source_sync.status_code == 202

    return {
        "workspace_id": workspace_id,
        "brand_id": brand_payload["id"],
        "brand_name": brand_payload["name"],
        "channel_id": channel_payload["id"],
    }


def _wait_for_topic_rows(page: Page) -> int:
    page.wait_for_function(
        """
        () => {
          const rows = document.querySelectorAll('tbody tr');
          return rows.length > 0 && !document.body.innerText.includes('当前品牌还没有候选选题');
        }
        """,
        timeout=15000,
    )
    return page.locator("tbody tr").count()


@pytest.mark.acceptance
def test_v2_phase1_console_walkthrough(
    acceptance_storage,
    acceptance_artifact_dir,
) -> None:
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
        "JOB_POLL_INTERVAL_MS": "50",
        "SSE_HEARTBEAT_SECONDS": "1",
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
        ready_timeout=15,
        name="backend",
    ):
        seeded = _seed_phase1_setup(backend_url)

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
            ready_url=f"{frontend_url}/brands",
            ready_timeout=60,
            name="frontend",
        ):
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    executable_path=_chrome_executable(),
                )
                page = browser.new_page()
                page.goto(f"{frontend_url}/brands", wait_until="networkidle")

                page.get_by_role("heading", name="品牌与配置").wait_for()
                page.locator("select").select_option(label=seeded["brand_name"])
                page.goto(f"{frontend_url}/brands/{seeded['brand_id']}", wait_until="networkidle")
                page.wait_for_url(re.compile(r".*/brands/.+"))
                page.get_by_role("heading", name=seeded["brand_name"]).wait_for()
                page.get_by_role("link", name="前往数据源").click()
                page.wait_for_url(re.compile(r".*/data-sources$"))
                page.get_by_role("heading", name="数据源").wait_for()

                page.get_by_role("link", name=re.compile("选题库")).click()
                page.wait_for_url(re.compile(r".*/topic-pool$"))
                page.get_by_role("heading", name="选题候选库").wait_for()
                page.get_by_role("button", name="刷新选题").click()
                topic_row_count = _wait_for_topic_rows(page)
                page.get_by_role("button", name="展开依据").first.click()
                evidence_link = page.get_by_role("link", name="打开小红书原帖").first
                expect_href = evidence_link.get_attribute("href") or ""
                assert expect_href.startswith("https://www.xiaohongshu.com/explore/")

                page.get_by_role("button", name="执行决策").click()
                page.wait_for_function(
                    "() => window.location.pathname === '/decisions' && window.location.search.includes('batch_id=')",
                    timeout=15000,
                )
                page.get_by_role("heading", name="决策批次").wait_for()
                page.wait_for_function("() => document.body.innerText.includes('Slot 1')", timeout=15000)
                page.get_by_role("button", name="接受").first.click()
                page.wait_for_function("() => document.body.innerText.includes('review=accept')", timeout=15000)

                page.get_by_role("link", name=re.compile("发布记录")).click()
                page.wait_for_url(re.compile(r".*/publish$"))
                page.get_by_role("heading", name="发布记录").wait_for()
                page.get_by_role("button", name="登记已采纳决策").click()
                page.wait_for_function(
                    "() => document.querySelectorAll('tbody tr').length > 0 && document.body.innerText.includes('已发布')",
                    timeout=15000,
                )

                page.get_by_role("link", name=re.compile("绩效反馈")).click()
                page.wait_for_url(re.compile(r".*/performance$"))
                page.get_by_role("heading", name="绩效与反馈").wait_for()
                page.get_by_role("button", name="导入绩效快照").click()
                page.wait_for_function(
                    "() => document.querySelectorAll('tbody tr').length > 0 && document.body.innerText.includes('短期')",
                    timeout=15000,
                )

                page.get_by_role("link", name=re.compile("离线评估")).click()
                page.wait_for_url(re.compile(r".*/evaluation$"))
                page.get_by_role("heading", name="离线评估").wait_for()
                page.get_by_role("button", name="运行评估").click()
                page.wait_for_function("() => document.body.innerText.includes('样本量: 1')", timeout=15000)
                evaluation_note = page.locator("text=Replay=").first.text_content() or ""
                final_url = page.url

                browser.close()

    latency_ms = int((time.perf_counter() - started) * 1000)
    write_acceptance_artifact(
        acceptance_artifact_dir,
        "v2_phase1_console_walkthrough",
        {
            "workspace_id": seeded["workspace_id"],
            "brand_id": seeded["brand_id"],
            "channel_id": seeded["channel_id"],
            "topic_row_count": topic_row_count,
            "final_url": final_url,
            "evaluation_note": evaluation_note,
            "latency_ms": latency_ms,
            "db_path": acceptance_storage["db_path"],
            "chroma_dir": acceptance_storage["chroma_dir"],
        },
    )


@pytest.mark.acceptance
def test_v2_brand_creation_from_console(
    acceptance_storage,
    acceptance_artifact_dir,
) -> None:
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
        "JOB_POLL_INTERVAL_MS": "50",
        "SSE_HEARTBEAT_SECONDS": "1",
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
        ready_timeout=15,
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
            ready_url=f"{frontend_url}/brands",
            ready_timeout=60,
            name="frontend",
        ):
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    executable_path=_chrome_executable(),
                )
                page = browser.new_page()
                page.goto(f"{frontend_url}/brands", wait_until="networkidle")

                page.get_by_role("button", name="+ 新建品牌").click()
                page.get_by_label("品牌名称").fill("UI Created Brand")
                page.get_by_label("品类").fill("outdoor")
                page.get_by_label("品牌阶段").select_option("growth")
                page.get_by_label("目标人群说明").fill("25-34 岁城市女性")
                page.get_by_role("button", name="创建并进入配置").click()

                page.wait_for_url(re.compile(r".*/brands/.+"))
                page.get_by_role("heading", name="UI Created Brand").wait_for()
                audience_input = page.get_by_role("textbox", name="人群摘要")
                audience_input.wait_for()
                assert audience_input.input_value() == "25-34 岁城市女性"
                final_url = page.url

                browser.close()

    latency_ms = int((time.perf_counter() - started) * 1000)
    write_acceptance_artifact(
        acceptance_artifact_dir,
        "v2_brand_creation_from_console",
        {
            "final_url": final_url,
            "latency_ms": latency_ms,
            "db_path": acceptance_storage["db_path"],
            "chroma_dir": acceptance_storage["chroma_dir"],
        },
    )
