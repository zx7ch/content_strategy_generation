from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import subprocess
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError

import pytest
from playwright.sync_api import expect, sync_playwright

from app.content_research.lifecycle.coordinator import ContentResearchPersistenceCoordinator
from app.content_research.lifecycle.models import LifecycleCommand
from app.content_research.models import ResearchBriefRecord, TraceRecord, utcnow
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from tests.browser_process import chrome_executable, reserve_port, run_process

RELEASE_GATE_HEADERS = {
    "X-Workspace-Id": "00000000-0000-0000-0000-000000000001",
    "X-User-Id": "release-gate",
}
HEALTH_PROBE_TIMEOUT_SECONDS = 1
FROZEN_RUNTIME_READ_TIMEOUT_SECONDS = 5


def _release_archive() -> Path:
    return Path(os.getenv("RELEASE_ARCHIVE_PATH", "dist/xhs-runtime.zip"))


@pytest.mark.acceptance
def test_release_archive_contains_runtime_and_launcher():
    archive = _release_archive()
    if not archive.exists() and os.getenv("RELEASE_GATE_REQUIRE_ARTIFACT") != "1":
        pytest.skip("release artifact is built by the release gate")

    assert archive.is_file(), f"release gate requires {archive}"
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())

    assert "xhs-runtime/xhs-runtime" in names
    assert "xhs-runtime/start.command" in names
    assert "xhs-runtime/config.env" in names
    assert "xhs-runtime/build-info.json" in names

    forbidden_basenames = {".DS_Store", ".env", ".env.example"}
    forbidden_suffixes = {".db", ".har", ".log", ".sqlite", ".sqlite3"}
    unsafe_entries = []
    for name in names:
        path = PurePosixPath(name)
        if (
            path.name in forbidden_basenames
            or path.suffix.lower() in forbidden_suffixes
            or ".git" in path.parts
            or "__pycache__" in path.parts
        ):
            unsafe_entries.append(name)
    assert unsafe_entries == [], (
        f"release archive contains local/dev data: {sorted(unsafe_entries)}"
    )

    with zipfile.ZipFile(archive) as bundle:
        config = bundle.read("xhs-runtime/config.env").decode("utf-8")
        build_info = json.loads(bundle.read("xhs-runtime/build-info.json").decode("utf-8"))

    assert "LOG_LEVEL=INFO" in config
    assert "SQLITE_DB_PATH=" not in config
    assert "CHROMA_PERSIST_DIR=" not in config
    assert "OPENAI_API_KEY=" not in config
    assert "XHS_SPIDER_COOKIES=" not in config

    assert build_info["schema_version"] == "xhs_runtime_build_info_v1"
    assert re.fullmatch(r"[0-9a-f]{40}", build_info["git_commit"])
    assert build_info["git_dirty"] is False
    assert re.fullmatch(r"\d+\.\d+\.\d+", build_info["version"])
    assert build_info["platform"]
    assert build_info["architecture"]
    assert datetime.fromisoformat(build_info["built_at"].replace("Z", "+00:00")).tzinfo

    current_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert build_info["git_commit"] == current_commit

    source_sentinels = (
        "app/api/routes/router.py",
        "app/content_research/service.py",
        "app/memory/job_store.py",
    )
    with zipfile.ZipFile(archive) as bundle:
        for source_path in source_sentinels:
            committed = subprocess.run(
                ["git", "show", f"{build_info['git_commit']}:{source_path}"],
                check=True,
                capture_output=True,
            ).stdout
            packaged = bundle.read(f"xhs-runtime/_internal/{source_path}")
            assert packaged == committed, (
                f"release archive source does not match {build_info['git_commit']}: {source_path}"
            )


@pytest.mark.acceptance
def test_master_frontend_and_runtime_zip_share_single_writer_contract() -> None:
    archive = _release_archive()
    if os.getenv("RELEASE_GATE_REQUIRE_ARTIFACT") != "1":
        pytest.skip("release artifact is built by the release gate")
    assert archive.exists(), f"release artifact not found: {archive}"

    required_contract = "local-runtime-single-writer"
    frontend_source = Path("frontend/src/lib/api.ts").read_text(encoding="utf-8")
    assert f'REQUIRED_API_CONTRACT = "{required_contract}"' in frontend_source

    with zipfile.ZipFile(archive) as bundle:
        runtime_config = bundle.read("xhs-runtime/config.env").decode("utf-8")
        packaged_config = bundle.read("xhs-runtime/_internal/app/config.py").decode("utf-8")
    assert f"RUNTIME_API_CONTRACT={required_contract}" in runtime_config
    assert f'default="{required_contract}"' in packaged_config


def _free_local_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = FROZEN_RUNTIME_READ_TIMEOUT_SECONDS,
) -> dict:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(  # noqa: S310 -- local Runtime only
            request,
            timeout=timeout_seconds,
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"{url} returned HTTP {exc.code}: {detail}") from exc


def test_release_gate_business_reads_tolerate_bounded_cold_start_jitter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_timeouts: list[float] = []

    class JSONResponse:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc_value, _traceback) -> bool:
            return False

        @staticmethod
        def read() -> bytes:
            return b'{"status":"ready"}'

    def delayed_local_response(_request, *, timeout: float):
        observed_timeouts.append(timeout)
        if timeout < FROZEN_RUNTIME_READ_TIMEOUT_SECONDS:
            raise TimeoutError("simulated frozen Runtime scheduling jitter")
        return JSONResponse()

    monkeypatch.setattr(urllib.request, "urlopen", delayed_local_response)

    assert _get_json("http://127.0.0.1:8000/trace") == {"status": "ready"}
    assert observed_timeouts == [FROZEN_RUNTIME_READ_TIMEOUT_SECONDS]


def _wait_for_health(port: int, process: subprocess.Popen) -> dict:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise AssertionError(
                "frozen Runtime exited before health became available:\n" + output[-2000:]
            )
        try:
            return _get_json(
                f"http://127.0.0.1:{port}/health",
                timeout_seconds=HEALTH_PROBE_TIMEOUT_SECONDS,
            )
        except OSError:
            time.sleep(0.2)
    raise AssertionError("frozen Runtime did not become healthy within 20 seconds")


def _stop(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _seed_persisted_content_research_fixture(db_path: str) -> str:
    async def create_run() -> tuple[str, str]:
        async with ThreadStore(db_path) as threads:
            thread = await threads.create_thread(
                title="Release gate fixture",
                workspace_id=RELEASE_GATE_HEADERS["X-Workspace-Id"],
            )
        thread_id = str(thread["id"])
        workflow_run_id = "run-release-artifact-restart"
        coordinator = ContentResearchPersistenceCoordinator(db_path)
        await coordinator.apply(
            LifecycleCommand(
                command_id="release-artifact-submit",
                run_id=workflow_run_id,
                expected_state=None,
                expected_revision=0,
                kind="submit_research_subject",
                payload={
                    "thread_id": thread_id,
                    "user_id": RELEASE_GATE_HEADERS["X-User-Id"],
                    "seed_text": "防晒长袖",
                },
            )
        )
        return workflow_run_id, thread_id

    workflow_run_id, thread_id = asyncio.run(create_run())
    store = SQLiteContentResearchStore(db_path)
    store.save_brief(
        ResearchBriefRecord(
            id="brief-release-gate",
            workflow_run_id=workflow_run_id,
            thread_id=thread_id,
            schema_version="content_research_brief_v1",
            status="ready",
            payload={"schema_version": "content_research_brief_v1", "seed_text": "防晒长袖"},
        )
    )
    store.save_trace(
        TraceRecord(
            id="trace-release-gate",
            workflow_run_id=workflow_run_id,
            thread_id=thread_id,
            schema_version="content_research_trace_v1",
            status="completed",
            started_at=utcnow(),
            payload={"schema_version": "content_research_trace_v1", "stage": "formal_research"},
        )
    )
    store.save_stage_checkpoint(
        StageCheckpointRecord(
            id="details-release-gate",
            workflow_run_id=workflow_run_id,
            subagent_task_id="product_marketing:release-gate",
            stage_name="detail",
            input_fingerprint="release-gate-details",
            status="completed",
            schema_version="content_research_stage_checkpoint_v1",
            payload={
                "candidates": [
                    {"note_id": f"note-{index}", "detail_status": "completed"}
                    for index in range(28)
                ]
            },
        )
    )
    store.save_stage_checkpoint(
        StageCheckpointRecord(
            id="report-release-gate",
            workflow_run_id=workflow_run_id,
            subagent_task_id="report:release-gate",
            stage_name="compose",
            input_fingerprint="release-gate-report",
            status="failed_recoverable",
            schema_version="content_research_stage_checkpoint_v1",
            payload={"reason_code": "transient_error"},
        )
    )
    return workflow_run_id


@pytest.mark.acceptance
def test_frozen_runtime_restart_preserves_content_research_fixture(tmp_path):
    """Opt-in release gate: real frozen binary reads the same persisted run twice."""
    if os.getenv("RUN_FROZEN_RUNTIME_RESTART_GATE") != "1":
        pytest.skip("set RUN_FROZEN_RUNTIME_RESTART_GATE=1 after packaging")

    archive = _release_archive()
    assert archive.is_file(), f"release gate requires {archive}"
    extracted = tmp_path / "runtime"
    # Python's zipfile turns the bundle's dylib symlinks into text files.
    # Use the macOS extractor users invoke so Mach-O dependency links survive.
    subprocess.run(
        ["/usr/bin/unzip", "-q", str(archive), "-d", str(extracted)],
        check=True,
    )
    runtime_dir = extracted / "xhs-runtime"
    executable = runtime_dir / "xhs-runtime"
    executable.chmod(executable.stat().st_mode | 0o111)
    home = tmp_path / "home"
    data_home = home / "Library" / "Application Support" / "xhs-growth-agent"
    data_home.mkdir(parents=True)
    (data_home / "config.env").write_text("OPENAI_API_KEY=legacy-key\nSQLITE_DB_PATH=legacy.db\n")
    db_path = str(data_home / "xhs_agent.db")
    workflow_run_id = _seed_persisted_content_research_fixture(db_path)
    assert (
        len(
            [
                checkpoint
                for checkpoint in SQLiteContentResearchStore(db_path).list_typed_records(
                    StageCheckpointRecord
                )
                if checkpoint.workflow_run_id == workflow_run_id
            ]
        )
        == 2
    )

    port = _free_local_port()
    environment = {**os.environ, "HOME": str(home), "RUNTIME_PORT": str(port)}
    for inherited_python_setting in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX"):
        environment.pop(inherited_python_setting, None)

    def start() -> subprocess.Popen:
        return subprocess.Popen(
            [str(executable)],
            cwd=runtime_dir,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    first = start()
    try:
        first_health = _wait_for_health(port, first)
        assert first_health["runtime_diagnostics"]["sqlite_db_path"] == db_path
        assert (
            _get_json(
                f"http://127.0.0.1:{port}/content-research/workflows/{workflow_run_id}/trace",
                headers=RELEASE_GATE_HEADERS,
            )["workflow_run_id"]
            == workflow_run_id
        )
        assert (
            _get_json(
                f"http://127.0.0.1:{port}/content-research/workflows/{workflow_run_id}/lite-report",
                headers=RELEASE_GATE_HEADERS,
            )["workflow_run_id"]
            == workflow_run_id
        )
    finally:
        _stop(first)

    second = start()
    try:
        second_health = _wait_for_health(port, second)
        assert second_health["runtime_diagnostics"]["sqlite_db_path"] == db_path
        assert (
            _get_json(
                f"http://127.0.0.1:{port}/content-research/workflows/{workflow_run_id}/trace",
                headers=RELEASE_GATE_HEADERS,
            )["workflow_run_id"]
            == workflow_run_id
        )
        assert (
            _get_json(
                f"http://127.0.0.1:{port}/content-research/workflows/{workflow_run_id}/lite-report",
                headers=RELEASE_GATE_HEADERS,
            )["workflow_run_id"]
            == workflow_run_id
        )
        checkpoints = [
            checkpoint
            for checkpoint in SQLiteContentResearchStore(db_path).list_typed_records(
                StageCheckpointRecord
            )
            if checkpoint.workflow_run_id == workflow_run_id
        ]
        assert len(checkpoints[0].payload["candidates"]) == 28
    finally:
        _stop(second)


@pytest.mark.acceptance
def test_second_frozen_runtime_exits_with_a_readable_lock_message(
    tmp_path: Path,
) -> None:
    if os.getenv("RUN_FROZEN_RUNTIME_RESTART_GATE") != "1":
        pytest.skip("set RUN_FROZEN_RUNTIME_RESTART_GATE=1 after packaging")

    archive = _release_archive()
    assert archive.is_file(), f"release gate requires {archive}"
    extracted = tmp_path / "duplicate-runtime"
    subprocess.run(
        ["/usr/bin/unzip", "-q", str(archive), "-d", str(extracted)],
        check=True,
    )
    runtime_dir = extracted / "xhs-runtime"
    executable = runtime_dir / "xhs-runtime"
    executable.chmod(executable.stat().st_mode | 0o111)
    home = tmp_path / "home"
    (home / "Library" / "Application Support" / "xhs-growth-agent").mkdir(
        parents=True
    )

    first_port = _free_local_port()
    second_port = _free_local_port()

    def environment(port: int) -> dict[str, str]:
        result = {**os.environ, "HOME": str(home), "RUNTIME_PORT": str(port)}
        for inherited in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX"):
            result.pop(inherited, None)
        return result

    first = subprocess.Popen(
        [str(executable)],
        cwd=runtime_dir,
        env=environment(first_port),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        first_health = _wait_for_health(first_port, first)
        second = subprocess.run(
            [str(executable)],
            cwd=runtime_dir,
            env=environment(second_port),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        second_output = second.stdout + second.stderr

        assert second.returncode == 2
        assert "LOCAL_RUNTIME_DATABASE_LOCKED" in second_output
        assert "已有 XHS Growth Agent Runtime 正在运行" in second_output
        assert "无需重复启动" in second_output
        assert "Traceback" not in second_output
        assert "BlockingIOError" not in second_output
        assert _wait_for_health(first_port, first)["service"] == first_health["service"]
    finally:
        _stop(first)


@pytest.mark.acceptance
def test_frozen_runtime_and_same_sha_frontend_restore_run_in_real_browser(
    tmp_path: Path,
) -> None:
    if os.getenv("RUN_FROZEN_RUNTIME_RESTART_GATE") != "1":
        pytest.skip("set RUN_FROZEN_RUNTIME_RESTART_GATE=1 after packaging")

    archive = _release_archive()
    assert archive.is_file(), f"release gate requires {archive}"
    extracted = tmp_path / "browser-runtime"
    subprocess.run(
        ["/usr/bin/unzip", "-q", str(archive), "-d", str(extracted)],
        check=True,
    )
    runtime_dir = extracted / "xhs-runtime"
    executable = runtime_dir / "xhs-runtime"
    executable.chmod(executable.stat().st_mode | 0o111)

    home = tmp_path / "browser-home"
    data_home = home / "Library" / "Application Support" / "xhs-growth-agent"
    data_home.mkdir(parents=True)
    workflow_run_id = _seed_persisted_content_research_fixture(
        str(data_home / "xhs_agent.db")
    )

    repository = Path(__file__).resolve().parents[2]
    frontend_root = repository / "frontend"
    tsconfig_path = frontend_root / "tsconfig.json"
    tsconfig_before = tsconfig_path.read_bytes()
    runtime_port = reserve_port()
    frontend_port = reserve_port()
    runtime_url = f"http://127.0.0.1:{runtime_port}"
    frontend_url = f"http://127.0.0.1:{frontend_port}"
    runtime_environment = {
        **os.environ,
        "HOME": str(home),
        "RUNTIME_PORT": str(runtime_port),
        "CORS_ALLOWED_ORIGINS": frontend_url,
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false",
    }
    for inherited in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX"):
        runtime_environment.pop(inherited, None)
    frontend_environment = {
        **os.environ,
        "NEXT_DIST_DIR": f".next/artifact-{frontend_port}",
        "NEXT_PUBLIC_XHS_API_BASE_URL": runtime_url,
        "XHS_API_BASE_URL": runtime_url,
        "NEXT_TELEMETRY_DISABLED": "1",
    }

    try:
        with run_process(
            cmd=[str(executable)],
            cwd=runtime_dir,
            env=runtime_environment,
            ready_url=f"{runtime_url}/health",
            ready_timeout=60,
            name="frozen Runtime",
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
                env=frontend_environment,
                ready_url=f"{frontend_url}/creator",
                ready_timeout=90,
                name="same-SHA frontend",
            ):
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        headless=True,
                        executable_path=chrome_executable(),
                    )
                    page = browser.new_page(viewport={"width": 1440, "height": 960})
                    try:
                        with page.expect_response(
                            lambda response: response.url.endswith(
                                f"/content-research/workflows/{workflow_run_id}"
                            )
                            and response.status == 200,
                            timeout=30000,
                        ) as workflow_response:
                            page.goto(
                                f"{frontend_url}/creator?contentResearchRunId={workflow_run_id}",
                                wait_until="domcontentloaded",
                            )
                        workflow = workflow_response.value.json()
                        assert workflow["workflow_run_id"] == workflow_run_id
                        assert workflow["run"]["state"] == "recovery_required"
                        assert (
                            workflow["run"]["recovery_plan"]["action"]
                            == "retry_presearch"
                        )
                        expect(
                            page.get_by_text(
                                "模型服务配置需要更新。请在右侧保存并验证新配置后继续本次预检索。",
                                exact=True,
                            )
                        ).to_be_visible(timeout=30000)
                    finally:
                        browser.close()
    finally:
        tsconfig_path.write_bytes(tsconfig_before)
