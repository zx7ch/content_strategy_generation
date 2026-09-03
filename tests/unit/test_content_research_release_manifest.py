from __future__ import annotations

import json
from pathlib import Path

from app.content_research.api_schemas import P0_WORKFLOW_ACTIONS
from app.content_research.contracts import DIRECTION_CATALOG_V1
from app.content_research.lifecycle.models import ContentResearchState

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs/release/content_research_release_scenarios.json"


def _covered(manifest: dict, field: str) -> set[str]:
    return {
        value
        for scenario in manifest["scenarios"]
        for value in scenario.get(field, [])
    }


def test_release_manifest_covers_every_public_contract_and_gate_layer() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    required = manifest["required"]

    assert manifest["schema_version"] == "content_research_release_scenarios_v1"
    assert set(required["actions"]) == set(P0_WORKFLOW_ACTIONS)
    assert set(required["lifecycle_states"]) == {state.value for state in ContentResearchState}
    assert set(required["directions"]) == set(DIRECTION_CATALOG_V1)
    for field in ("actions", "lifecycle_states", "publication_states", "directions"):
        assert set(required[field]) <= _covered(manifest, field), field
    assert set(required["risk_classes"]) <= _covered(manifest, "risk_classes")

    all_layers = {
        layer
        for scenario in manifest["scenarios"]
        for layer in scenario["layers"]
    }
    assert {"unit", "integration", "api", "browser", "frontend", "acceptance", "artifact"} <= all_layers
    assert len({scenario["id"] for scenario in manifest["scenarios"]}) == len(
        manifest["scenarios"]
    )
    for scenario in manifest["scenarios"]:
        assert scenario["risk_classes"]
        assert scenario["tests"]
        for test_path in scenario["tests"]:
            assert (ROOT / test_path).is_file(), f"missing release test: {test_path}"
