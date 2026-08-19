from __future__ import annotations

import pytest

from app.content_research.execution_decision_identity import (
    build_execution_decision_identity,
)


@pytest.mark.parametrize(
    (
        "kwargs",
        "expected_json",
        "expected_digest",
    ),
    [
        (
            {
                "coverage_snapshot_id": "scv_1",
                "source_scope_contract_id": "rsc_1",
                "resulting_scope_contract_id": "rsc_1",
                "resolution": "generate_limited_report",
                "target_constraint_id": None,
                "supplementary_queries": (),
            },
            '{"coverage_snapshot_id":"scv_1","resolution":"generate_limited_report","resulting_scope_contract_id":"rsc_1","schema":"execution_decision_identity_v1","source_scope_contract_id":"rsc_1","supplementary_queries":[],"target_constraint_id":null}',
            "6072bdc6dec8b1e40fc2571213a7e80be823a7d5f8a07b3dc8b59fd23bc9ea92",
        ),
        (
            {
                "coverage_snapshot_id": "scv_2",
                "source_scope_contract_id": "rsc_2",
                "resulting_scope_contract_id": "rsc_2",
                "resolution": "expand_required_constraint",
                "target_constraint_id": "season",
                "supplementary_queries": ("  夏季   防晒 衬衫 ", "通勤\t衬衫"),
            },
            '{"coverage_snapshot_id":"scv_2","resolution":"expand_required_constraint","resulting_scope_contract_id":"rsc_2","schema":"execution_decision_identity_v1","source_scope_contract_id":"rsc_2","supplementary_queries":["夏季 防晒 衬衫","通勤 衬衫"],"target_constraint_id":"season"}',
            "2dcef3d6858c6010c02d115391ec959674bd6162d96a9cab19c5b9dfdd65ad63",
        ),
        (
            {
                "coverage_snapshot_id": "scv_3",
                "source_scope_contract_id": "rsc_3",
                "resulting_scope_contract_id": "rsc_4",
                "resolution": "relax_constraint",
                "target_constraint_id": "scenario",
                "supplementary_queries": (),
            },
            '{"coverage_snapshot_id":"scv_3","resolution":"relax_constraint","resulting_scope_contract_id":"rsc_4","schema":"execution_decision_identity_v1","source_scope_contract_id":"rsc_3","supplementary_queries":[],"target_constraint_id":"scenario"}',
            "96d9c17ee6f301e1ce0cf2dee1d9ced367b497e7f2146902d9e981472dd3cc17",
        ),
        (
            {
                "coverage_snapshot_id": "scv_2",
                "source_scope_contract_id": "rsc_2",
                "resulting_scope_contract_id": "rsc_2",
                "resolution": "expand_required_constraint",
                "target_constraint_id": "scenario",
                "supplementary_queries": ("夏季 防晒 衬衫", "通勤 衬衫"),
            },
            '{"coverage_snapshot_id":"scv_2","resolution":"expand_required_constraint","resulting_scope_contract_id":"rsc_2","schema":"execution_decision_identity_v1","source_scope_contract_id":"rsc_2","supplementary_queries":["夏季 防晒 衬衫","通勤 衬衫"],"target_constraint_id":"scenario"}',
            "391d5cd2aef6506a6739f6962c484c47069b386868eefe27a4ce92f84d7b88ed",
        ),
        (
            {
                "coverage_snapshot_id": "scv_2",
                "source_scope_contract_id": "rsc_2",
                "resulting_scope_contract_id": "rsc_2",
                "resolution": "expand_required_constraint",
                "target_constraint_id": "season",
                "supplementary_queries": ("夏季 透气 衬衫",),
            },
            '{"coverage_snapshot_id":"scv_2","resolution":"expand_required_constraint","resulting_scope_contract_id":"rsc_2","schema":"execution_decision_identity_v1","source_scope_contract_id":"rsc_2","supplementary_queries":["夏季 透气 衬衫"],"target_constraint_id":"season"}',
            "ea32cdd92e808edd60e01d5d571c32967377ce26b153e0b5adee76cef0510a32",
        ),
    ],
)
def test_execution_decision_identity_has_one_canonical_payload_and_digest(
    kwargs, expected_json, expected_digest
) -> None:
    """Changing serialization or normalization breaks replay parity."""
    result = build_execution_decision_identity(**kwargs)

    assert result.canonical_json == expected_json
    assert "operation" not in result.payload
    assert result.decision_fingerprint == expected_digest
    assert result.execution_unit_id == "seu_" + expected_digest[:24]


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "resolution": "expand_required_constraint",
            "target_constraint_id": "season",
            "supplementary_queries": ("夏季 衬衫", " 夏季   衬衫 "),
        },
        {
            "resolution": "relax_constraint",
            "target_constraint_id": "season",
            "supplementary_queries": ("夏季 衬衫",),
        },
        {
            "resolution": "generate_limited_report",
            "target_constraint_id": "season",
            "supplementary_queries": (),
        },
        {
            "resolution": "expand_required_constraint",
            "target_constraint_id": "season",
            "supplementary_queries": ("夏季 衬衫",),
            "resulting_scope_contract_id": "rsc_changed",
        },
        {
            "resolution": "relax_constraint",
            "target_constraint_id": "season",
            "supplementary_queries": (),
            "resulting_scope_contract_id": "rsc_invalid",
        },
    ],
)
def test_execution_decision_identity_rejects_noncanonical_field_combinations(kwargs) -> None:
    """Invalid or normalization-colliding inputs must never receive a replay identity."""
    with pytest.raises(ValueError):
        defaults = {
            "coverage_snapshot_id": "scv_invalid",
            "source_scope_contract_id": "rsc_invalid",
            "resulting_scope_contract_id": "rsc_invalid",
        }
        build_execution_decision_identity(**{**defaults, **kwargs})
