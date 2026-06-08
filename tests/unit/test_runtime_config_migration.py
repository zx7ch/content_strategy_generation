from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_runtime_helpers():
    source = Path("runtime_main.py").read_text(encoding="utf-8")
    start = source.index("    def _load_env_file")
    end = source.index("    # ── User data lives outside")
    helper_source = "\n".join(line[4:] for line in source[start:end].splitlines())
    namespace = {"os": os, "sys": sys, "time": __import__("time")}
    exec(helper_source, namespace)
    return namespace


def test_append_missing_template_keys_preserves_existing_values(tmp_path):
    helpers = _load_runtime_helpers()
    user_config = tmp_path / "config.env"
    template_config = tmp_path / "template.env"
    user_config.write_text("ANTHROPIC_API_KEY=real-key\nOLD_SETTING=keep\n", encoding="utf-8")
    template_config.write_text(
        "ANTHROPIC_API_KEY=\nNEW_REQUIRED_SETTING=\nNEW_DEFAULTED_SETTING=true\n",
        encoding="utf-8",
    )

    missing = helpers["_append_missing_template_keys"](str(user_config), str(template_config))

    content = user_config.read_text(encoding="utf-8")
    assert missing == ["NEW_REQUIRED_SETTING", "NEW_DEFAULTED_SETTING"]
    assert "ANTHROPIC_API_KEY=real-key" in content
    assert "OLD_SETTING=keep" in content
    assert "NEW_REQUIRED_SETTING=" in content
    assert "NEW_DEFAULTED_SETTING=true" in content
    assert list(tmp_path.glob("config.env.bak-*"))


def test_append_missing_template_keys_noops_when_config_is_current(tmp_path):
    helpers = _load_runtime_helpers()
    user_config = tmp_path / "config.env"
    template_config = tmp_path / "template.env"
    user_config.write_text("ANTHROPIC_API_KEY=real-key\nNEW_SETTING=custom\n", encoding="utf-8")
    template_config.write_text("ANTHROPIC_API_KEY=\nNEW_SETTING=default\n", encoding="utf-8")

    missing = helpers["_append_missing_template_keys"](str(user_config), str(template_config))

    assert missing == []
    assert user_config.read_text(encoding="utf-8") == "ANTHROPIC_API_KEY=real-key\nNEW_SETTING=custom\n"
    assert not list(tmp_path.glob("config.env.bak-*"))
