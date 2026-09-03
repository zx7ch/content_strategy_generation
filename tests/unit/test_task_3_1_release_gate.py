from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_tag_release_runs_task_3_1_and_artifact_gates_before_upload() -> None:
    root = Path(__file__).parents[2]
    script = (root / "scripts" / "run_release_gate.sh").read_text(encoding="utf-8")
    browser_suite = (root / "scripts" / "run_creator_browser_e2e_suite.py").read_text(
        encoding="utf-8"
    )
    workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "tests/unit/test_content_research" in script
    assert "tests/integration/test_content_research" in script
    assert "test_content_research_marketing_evidence_extraction.py" in script
    assert "test_content_research_marketing_analysis_execution.py" in script
    assert "test_content_research_marketing_quality.py" in script
    assert "test_content_research_governed_completion.py" in script
    assert "test_content_research_dispatch_worker.py" in script
    assert "test_content_research_analysis_worker.py" in script
    assert "test_content_research_lite_read_model.py" in script
    assert "test_content_research_analysis_persistence.py" in script
    assert "test_content_research_report_execution.py" in script
    assert "test_content_research_packet_replay.py" in script
    assert "run_creator_browser_e2e_suite.py" in script
    assert "CREATOR_BROWSER_E2E_REQUIRED=1" in script
    assert "tests/e2e/test_content_research_creator_browser.py" in browser_suite
    assert '"--collect-only"' in browser_suite
    assert "tests/unit/test_runtime_launcher.py" in script
    assert "npm test" in script
    assert "tsc --noEmit" in script
    assert "RELEASE_GATE_REQUIRE_ARTIFACT" in script
    assert "RUN_FROZEN_RUNTIME_RESTART_GATE" in script
    assert "--timeout-seconds 2400" in script

    assert "actions/setup-node@v4" in workflow
    assert "npm ci" in workflow
    assert "RELEASE_GATE_PHASE=prebuild" in workflow
    assert "RELEASE_GATE_PHASE=artifact" in workflow
    assert workflow.index("RELEASE_GATE_PHASE=prebuild") < workflow.index("Build with PyInstaller")
    assert workflow.index("RELEASE_GATE_PHASE=artifact") < workflow.index("Upload release asset")
