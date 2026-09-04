import os
import shutil
import subprocess
from pathlib import Path


def test_runtime_launcher_directs_users_to_the_creator_sidebar_for_credentials() -> None:
    launcher = Path("start.command").read_text(encoding="utf-8")

    assert "填写 API Key" not in launcher
    assert "填写 config.env" not in launcher
    assert "Creator" in launcher
    assert "内容调研" in launcher
    assert "右侧栏" in launcher


def test_runtime_bundle_declares_lazy_xhs_login_dependencies() -> None:
    spec = Path("runtime_main.spec").read_text(encoding="utf-8")

    assert '"qrcode"' in spec
    assert '"curl_cffi"' in spec


def test_runtime_build_reuses_installed_build_tools_offline() -> None:
    build_script = Path("scripts/build_runtime.sh").read_text(encoding="utf-8")

    assert '"$PYTHON" -m pip install --no-build-isolation -e . --quiet' in build_script


def test_runtime_launcher_reuses_a_compatible_running_runtime(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    launcher = runtime_dir / "start.command"
    shutil.copy2("start.command", launcher)

    launch_marker = tmp_path / "runtime-launched"
    executable = runtime_dir / "xhs-runtime"
    executable.write_text(
        '#!/bin/sh\nprintf "%s\\n" launched > "$RUNTIME_LAUNCH_MARKER"\n',
        encoding="utf-8",
    )
    executable.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' "
        "'{\"status\":\"healthy\",\"api_contract\":\"local-runtime-single-writer\"}'\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)

    completed = subprocess.run(
        ["/bin/bash", str(launcher)],
        check=False,
        capture_output=True,
        text=True,
        input="\n",
        timeout=5,
        env={
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "RUNTIME_LAUNCH_MARKER": str(launch_marker),
            "RUNTIME_PORT": "8123",
            "SCUTIL_BIN": str(tmp_path / "missing-scutil"),
        },
    )

    assert completed.returncode == 0
    assert not launch_marker.exists()
    assert "已有 XHS Growth Agent Runtime 正在运行" in completed.stdout
    assert "无需重复启动" in completed.stdout
    assert "Traceback" not in completed.stdout
