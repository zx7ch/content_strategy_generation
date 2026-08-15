from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest

from app.content_research.models import ResearchBriefRecord, TraceRecord, utcnow
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.services.workflow_run_manager import WorkflowRunManager


def _release_archive() -> Path:
    return Path("dist/xhs-runtime.zip")


@pytest.mark.acceptance
def test_release_archive_contains_runtime_and_launcher():
    archive = _release_archive()
    if not archive.exists() and os.getenv("RELEASE_GATE_REQUIRE_ARTIFACT") != "1":
        pytest.skip("release artifact is built by the release gate")

    assert archive.is_file(), "release gate requires dist/xhs-runtime.zip"
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())

    assert "xhs-runtime/xhs-runtime" in names
    assert "xhs-runtime/start.command" in names
    assert "xhs-runtime/config.env" in names

    with zipfile.ZipFile(archive) as bundle:
        config = bundle.read("xhs-runtime/config.env").decode("utf-8")

    assert "LOG_LEVEL=INFO" in config
    assert "SQLITE_DB_PATH=" not in config
    assert "CHROMA_PERSIST_DIR=" not in config
    assert "OPENAI_API_KEY=" not in config
    assert "XHS_SPIDER_COOKIES=" not in config


def _free_local_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=1) as response:  # noqa: S310 -- local Runtime only
        return json.loads(response.read().decode("utf-8"))


def _wait_for_health(port: int, process: subprocess.Popen) -> dict:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise AssertionError(
                "frozen Runtime exited before health became available:\n" + output[-2000:]
            )
        try:
            return _get_json(f"http://127.0.0.1:{port}/health")
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
    async def create_run() -> str:
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id="thread-release-gate", user_id="release-gate")
        return run.run_id

    workflow_run_id = asyncio.run(create_run())
    store = SQLiteContentResearchStore(db_path)
    store.save_brief(ResearchBriefRecord(
        id="brief-release-gate", workflow_run_id=workflow_run_id,
        thread_id="thread-release-gate", schema_version="content_research_brief_v1",
        status="ready", payload={"schema_version": "content_research_brief_v1", "seed_text": "防晒长袖"},
    ))
    store.save_trace(TraceRecord(
        id="trace-release-gate", workflow_run_id=workflow_run_id,
        thread_id="thread-release-gate", schema_version="content_research_trace_v1",
        status="completed", started_at=utcnow(),
        payload={"schema_version": "content_research_trace_v1", "stage": "formal_research"},
    ))
    store.save_stage_checkpoint(StageCheckpointRecord(
        id="details-release-gate", workflow_run_id=workflow_run_id,
        subagent_task_id="product_marketing:release-gate", stage_name="detail",
        input_fingerprint="release-gate-details", status="completed",
        schema_version="content_research_stage_checkpoint_v1",
        payload={"candidates": [{"note_id": f"note-{index}", "detail_status": "completed"} for index in range(28)]},
    ))
    store.save_stage_checkpoint(StageCheckpointRecord(
        id="report-release-gate", workflow_run_id=workflow_run_id,
        subagent_task_id="report:release-gate", stage_name="compose",
        input_fingerprint="release-gate-report", status="failed_recoverable",
        schema_version="content_research_stage_checkpoint_v1",
        payload={"reason_code": "transient_error"},
    ))
    return workflow_run_id


@pytest.mark.acceptance
def test_frozen_runtime_restart_preserves_content_research_fixture(tmp_path):
    """Opt-in release gate: real frozen binary reads the same persisted run twice."""
    if os.getenv("RUN_FROZEN_RUNTIME_RESTART_GATE") != "1":
        pytest.skip("set RUN_FROZEN_RUNTIME_RESTART_GATE=1 after packaging")

    archive = _release_archive()
    assert archive.is_file(), "release gate requires dist/xhs-runtime.zip"
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
    assert len([
        checkpoint
        for checkpoint in SQLiteContentResearchStore(db_path).list_typed_records(StageCheckpointRecord)
        if checkpoint.workflow_run_id == workflow_run_id
    ]) == 2

    port = _free_local_port()
    environment = {**os.environ, "HOME": str(home), "RUNTIME_PORT": str(port)}
    for inherited_python_setting in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX"):
        environment.pop(inherited_python_setting, None)

    def start() -> subprocess.Popen:
        return subprocess.Popen(
            [str(executable)], cwd=runtime_dir, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )

    first = start()
    try:
        first_health = _wait_for_health(port, first)
        assert first_health["runtime_diagnostics"]["sqlite_db_path"] == db_path
        assert _get_json(f"http://127.0.0.1:{port}/content-research/workflows/{workflow_run_id}/trace")["workflow_run_id"] == workflow_run_id
        assert _get_json(f"http://127.0.0.1:{port}/content-research/workflows/{workflow_run_id}/lite-report")["workflow_run_id"] == workflow_run_id
    finally:
        _stop(first)

    second = start()
    try:
        second_health = _wait_for_health(port, second)
        assert second_health["runtime_diagnostics"]["sqlite_db_path"] == db_path
        assert _get_json(f"http://127.0.0.1:{port}/content-research/workflows/{workflow_run_id}/trace")["workflow_run_id"] == workflow_run_id
        assert _get_json(f"http://127.0.0.1:{port}/content-research/workflows/{workflow_run_id}/lite-report")["workflow_run_id"] == workflow_run_id
        checkpoints = [
            checkpoint
            for checkpoint in SQLiteContentResearchStore(db_path).list_typed_records(StageCheckpointRecord)
            if checkpoint.workflow_run_id == workflow_run_id
        ]
        assert len(checkpoints[0].payload["candidates"]) == 28
    finally:
        _stop(second)
