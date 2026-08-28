#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON="$PYTHON_BIN"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

PHASE="${RELEASE_GATE_PHASE:-all}"
if [[ "$PHASE" != "all" && "$PHASE" != "prebuild" && "$PHASE" != "artifact" ]]; then
  echo "Unknown RELEASE_GATE_PHASE: $PHASE" >&2
  exit 2
fi

run_prebuild_gate() {
  # Keep the Task 3.1 contract visible even if the broader test globs are reorganized.
  "$PYTHON" -m pytest -q \
    tests/unit/test_content_research_marketing_evidence_extraction.py \
    tests/unit/test_content_research_marketing_analysis_execution.py \
    tests/unit/test_content_research_marketing_quality.py \
    tests/unit/test_content_research_governed_completion.py \
    tests/unit/test_content_research_dispatch_worker.py \
    tests/unit/test_content_research_analysis_worker.py \
    tests/unit/test_content_research_lite_read_model.py \
    tests/integration/test_content_research_analysis_persistence.py \
    tests/integration/test_content_research_report_execution.py \
    tests/integration/test_content_research_packet_replay.py \
    tests/integration/test_content_research_lite_read_model.py

  "$PYTHON" -m pytest -q \
    tests/unit/test_content_research*.py \
    tests/integration/test_content_research*.py \
    tests/unit/test_llm_openai_compatible_adapter.py \
    tests/unit/test_runtime_launcher.py \
    tests/acceptance/test_task_3_1_release_gate.py

  CREATOR_BROWSER_E2E_REQUIRED=1 \
    "$PYTHON" scripts/run_creator_browser_e2e.py \
    --timeout-seconds 600 \
    --log-path .logs/release/creator-browser.log \
    --status-path .logs/release/creator-browser-status.json \
    -- "$PYTHON" scripts/run_creator_browser_e2e_suite.py

  (
    cd frontend
    npm test
    npx tsc --noEmit
  )
}

run_artifact_gate() {
  if [[ "${RELEASE_GATE_REQUIRE_ARTIFACT:-0}" != "1" ]]; then
    echo "Artifact phase requires RELEASE_GATE_REQUIRE_ARTIFACT=1" >&2
    exit 2
  fi
  RUN_FROZEN_RUNTIME_RESTART_GATE="${RUN_FROZEN_RUNTIME_RESTART_GATE:-1}" \
    "$PYTHON" -m pytest -q tests/acceptance/test_runtime_release_artifact.py
}

if [[ "$PHASE" == "all" || "$PHASE" == "prebuild" ]]; then
  run_prebuild_gate
fi
if [[ "$PHASE" == "artifact" ]] || {
  [[ "$PHASE" == "all" ]] && [[ "${RELEASE_GATE_REQUIRE_ARTIFACT:-0}" == "1" ]]
}; then
  run_artifact_gate
fi
