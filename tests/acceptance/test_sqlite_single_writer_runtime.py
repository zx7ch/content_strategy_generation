from __future__ import annotations

import os
import signal
import subprocess
import time
from collections import deque
from pathlib import Path
from threading import Thread

import httpx
import pytest

from tests.browser_process import reserve_port


class _RuntimeProcess:
    def __init__(self, process: subprocess.Popen[str]) -> None:
        self.process = process
        self.output: deque[str] = deque(maxlen=400)
        self.reader = Thread(target=self._drain, daemon=True)
        self.reader.start()

    def _drain(self) -> None:
        if self.process.stdout is None:
            return
        for line in self.process.stdout:
            self.output.append(line)

    def stop(self) -> None:
        if self.process.poll() is None:
            if os.name != "nt":
                os.killpg(self.process.pid, signal.SIGTERM)
            else:  # pragma: no cover - Windows fallback
                self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - shutdown fault
                self.process.kill()
                self.process.wait(timeout=5)
        self.reader.join(timeout=1)

    def diagnostic(self) -> str:
        return "".join(self.output)


def _start_runtime(
    *,
    repo_root: Path,
    database_path: Path,
    port: int,
    process_root: Path,
) -> _RuntimeProcess:
    process_root.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PYTHONPATH": str(repo_root),
        "SQLITE_DB_PATH": str(database_path),
        "V2_DISCOVERY_SQLITE_PATH": str(process_root / "discovery.sqlite"),
        "CHROMA_PERSIST_DIR": str(process_root / "chroma"),
        "F003_LITE_PREVIEW_ENABLED": "false",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "RAG_EMBEDDING_MODEL": str(process_root / "missing-embedding-model"),
    }
    process = subprocess.Popen(
        [
            str(repo_root / ".venv/bin/python"),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "info",
        ],
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=os.name != "nt",
    )
    return _RuntimeProcess(process)


def _wait_for_health(runtime: _RuntimeProcess, port: int) -> dict:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if runtime.process.poll() is not None:
            raise AssertionError(
                "Runtime exited before health became available:\n"
                + runtime.diagnostic()
            )
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5)
            if response.status_code == 200:
                return response.json()
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise AssertionError("Runtime did not become healthy:\n" + runtime.diagnostic())


@pytest.mark.acceptance
def test_two_runtime_processes_and_path_alias_cannot_share_database(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "primary" / "runtime.sqlite"
    first_port = reserve_port()
    second_port = reserve_port()
    third_port = reserve_port()
    first = _start_runtime(
        repo_root=repo_root,
        database_path=database_path,
        port=first_port,
        process_root=tmp_path / "first",
    )
    second: _RuntimeProcess | None = None
    third: _RuntimeProcess | None = None
    try:
        first_health = _wait_for_health(first, first_port)
        assert first_health["runtime_diagnostics"]["sqlite_db_path"] == str(
            database_path.resolve()
        )
        assert database_path.is_file()

        alias_path = tmp_path / "runtime-alias.sqlite"
        alias_path.symlink_to(database_path)
        second = _start_runtime(
            repo_root=repo_root,
            database_path=alias_path,
            port=second_port,
            process_root=tmp_path / "second",
        )

        # Importing the full Runtime can exceed 12 seconds on a cold or busy
        # release runner. The lock still has to reject the alias before the
        # competing process can become healthy.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and second.process.poll() is None:
            try:
                response = httpx.get(
                    f"http://127.0.0.1:{second_port}/health",
                    timeout=0.3,
                )
                if response.status_code == 200:
                    pytest.fail("second Runtime became healthy through a database alias")
            except httpx.HTTPError:
                pass
            time.sleep(0.1)

        assert second.process.poll() is not None, (
            "second Runtime did not reject the aliased database:\n"
            + second.diagnostic()
        )
        second.reader.join(timeout=1)
        assert "LOCAL_RUNTIME_DATABASE_LOCKED" in second.diagnostic()

        hardlink_path = tmp_path / "runtime-hardlink.sqlite"
        os.link(database_path, hardlink_path)
        third = _start_runtime(
            repo_root=repo_root,
            database_path=hardlink_path,
            port=third_port,
            process_root=tmp_path / "third",
        )
        # Allow the same cold-import budget as the symlink-alias process.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and third.process.poll() is None:
            try:
                response = httpx.get(
                    f"http://127.0.0.1:{third_port}/health",
                    timeout=0.3,
                )
                if response.status_code == 200:
                    pytest.fail("second Runtime became healthy through a hard-link alias")
            except httpx.HTTPError:
                pass
            time.sleep(0.1)

        assert third.process.poll() is not None, (
            "second Runtime did not reject the hard-linked database:\n"
            + third.diagnostic()
        )
        third.reader.join(timeout=1)
        assert "LOCAL_RUNTIME_DATABASE_LOCKED" in third.diagnostic()
        assert _wait_for_health(first, first_port)["service"] == first_health["service"]
    finally:
        if third is not None:
            third.stop()
        if second is not None:
            second.stop()
        first.stop()
