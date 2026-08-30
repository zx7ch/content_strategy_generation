from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from tests.acceptance.test_runtime_release_artifact import (
    RELEASE_GATE_HEADERS,
    _seed_persisted_content_research_fixture,
)

BASELINE_SHA256 = "bdceb4dd687de687aa17a947549d4e47b57907d9e1ba073c682171f071783140"
PARITY_ENDPOINTS = (
    "/health",
    "/workspaces/default",
    "/content-research/workflows/{run_id}",
    "/content-research/workflows/{run_id}/policy-snapshot",
    "/content-research/workflows/{run_id}/events",
    "/content-research/workflows/{run_id}/scope",
    "/content-research/workflows/{run_id}/trace",
    "/content-research/workflows/{run_id}/lite-report",
    "/content-research/llm-config",
    "/content-research/providers/xiaohongshu/login",
)


def _archive_from_env(name: str) -> Path | None:
    raw = os.getenv(name, "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request_json(url: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310 -- loopback only
            return {
                "status": int(response.status),
                "body": json.loads(response.read().decode("utf-8")),
            }
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body: Any = json.loads(raw)
        except json.JSONDecodeError:
            body = raw
        return {"status": int(exc.code), "body": body}


def _wait_for_health(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise AssertionError(
                "frozen Runtime exited before health became available:\n" + output[-4000:]
            )
        try:
            response = _request_json(f"http://127.0.0.1:{port}/health")
            if response["status"] == 200:
                return
        except OSError:
            pass
        time.sleep(0.2)
    raise AssertionError("frozen Runtime did not become healthy within 30 seconds")


def _stop(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _normalize(value: Any) -> Any:
    timestamp_keys = {
        "built_at",
        "completed_at",
        "created_at",
        "db_modified_at",
        "entered_at",
        "execution_finished_at",
        "last_synced_at",
        "runtime_started_at",
        "started_at",
        "timestamp",
        "updated_at",
    }
    duration_keys = {
        "active_duration_ms",
        "duration_ms",
        "latency_ms",
        "queue_duration_ms",
    }
    path_keys = {"sqlite_db_path"}
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in sorted(value.items()):
            if key in timestamp_keys and item is not None:
                normalized[key] = "<timestamp>" if isinstance(item, str) else _normalize(item)
            elif key in duration_keys and item is not None:
                normalized[key] = "<duration>" if isinstance(item, (int, float)) else _normalize(item)
            elif key in path_keys and item is not None:
                normalized[key] = "<runtime-user-path>" if isinstance(item, str) else _normalize(item)
            else:
                normalized[key] = _normalize(item)
        return normalized
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _differences(actual: Any, expected: Any, path: str = "$") -> list[str]:
    if type(actual) is not type(expected):
        return [f"{path}: type {type(actual).__name__} != {type(expected).__name__}"]
    if isinstance(actual, dict):
        differences: list[str] = []
        actual_keys = set(actual)
        expected_keys = set(expected)
        for key in sorted(actual_keys - expected_keys):
            differences.append(f"{path}.{key}: unexpected key")
        for key in sorted(expected_keys - actual_keys):
            differences.append(f"{path}.{key}: missing key")
        for key in sorted(actual_keys & expected_keys):
            differences.extend(_differences(actual[key], expected[key], f"{path}.{key}"))
        return differences
    if isinstance(actual, list):
        if len(actual) != len(expected):
            return [f"{path}: length {len(actual)} != {len(expected)}"]
        differences: list[str] = []
        for index, (actual_item, expected_item) in enumerate(zip(actual, expected, strict=True)):
            differences.extend(_differences(actual_item, expected_item, f"{path}[{index}]"))
        return differences
    return [] if actual == expected else [f"{path}: {actual!r} != {expected!r}"]


def _capture(
    *,
    archive: Path,
    label: str,
    root: Path,
    template_home: Path,
    workflow_run_id: str,
) -> dict[str, Any]:
    extracted = root / f"runtime-{label}"
    subprocess.run(
        ["/usr/bin/unzip", "-q", str(archive), "-d", str(extracted)],
        check=True,
    )
    runtime_dir = extracted / "xhs-runtime"
    executable = runtime_dir / "xhs-runtime"
    executable.chmod(executable.stat().st_mode | 0o111)
    home = root / f"home-{label}"
    shutil.copytree(template_home, home)

    snapshots: dict[str, Any] = {}
    for phase in ("first_start", "restart"):
        port = _free_port()
        environment = {
            **os.environ,
            "HOME": str(home),
            "RUNTIME_PORT": str(port),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false",
        }
        for inherited in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX"):
            environment.pop(inherited, None)
        process = subprocess.Popen(
            [str(executable)],
            cwd=runtime_dir,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_for_health(port, process)
            phase_snapshot: dict[str, Any] = {}
            for template in PARITY_ENDPOINTS:
                endpoint = template.format(run_id=workflow_run_id)
                headers = (
                    RELEASE_GATE_HEADERS
                    if endpoint.startswith("/content-research/")
                    and not endpoint.endswith("/providers/xiaohongshu/login")
                    else None
                )
                phase_snapshot[endpoint] = _request_json(
                    f"http://127.0.0.1:{port}{endpoint}",
                    headers=headers,
                )
            snapshots[phase] = _normalize(phase_snapshot)
        finally:
            _stop(process)
    return snapshots


@pytest.mark.acceptance
def test_frozen_runtime_candidate_matches_task_3_1_baseline(tmp_path: Path) -> None:
    required = os.getenv("REFACTOR_PARITY_REQUIRED") == "1"
    baseline = _archive_from_env("REFACTOR_BASELINE_ARCHIVE")
    candidate = _archive_from_env("REFACTOR_CANDIDATE_ARCHIVE")
    if not required and (baseline is None or candidate is None):
        pytest.skip("set both refactor parity archive paths to run the differential gate")

    assert baseline is not None and baseline.is_file(), (
        "REFACTOR_BASELINE_ARCHIVE must point to the preserved Task 3.1 ZIP"
    )
    assert candidate is not None and candidate.is_file(), (
        "REFACTOR_CANDIDATE_ARCHIVE must point to the candidate ZIP"
    )
    assert _sha256(baseline) == BASELINE_SHA256, "the preserved baseline ZIP changed"

    template_home = tmp_path / "template-home"
    data_home = template_home / "Library" / "Application Support" / "xhs-growth-agent"
    data_home.mkdir(parents=True)
    workflow_run_id = _seed_persisted_content_research_fixture(
        str(data_home / "xhs_agent.db")
    )

    baseline_snapshot = _capture(
        archive=baseline,
        label="baseline",
        root=tmp_path,
        template_home=template_home,
        workflow_run_id=workflow_run_id,
    )
    candidate_snapshot = _capture(
        archive=candidate,
        label="candidate",
        root=tmp_path,
        template_home=template_home,
        workflow_run_id=workflow_run_id,
    )

    if candidate_snapshot != baseline_snapshot:
        (tmp_path / "baseline-snapshot.json").write_text(
            json.dumps(baseline_snapshot, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (tmp_path / "candidate-snapshot.json").write_text(
            json.dumps(candidate_snapshot, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        pytest.fail(
            "frozen Runtime parity mismatch:\n"
            + "\n".join(_differences(candidate_snapshot, baseline_snapshot)[:100])
        )
