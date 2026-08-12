from pathlib import Path


def test_runtime_launcher_directs_users_to_the_creator_sidebar_for_credentials() -> None:
    launcher = Path("start.command").read_text(encoding="utf-8")

    assert "填写 API Key" not in launcher
    assert "填写 config.env" not in launcher
    assert "Creator" in launcher
    assert "内容调研" in launcher
    assert "右侧栏" in launcher
