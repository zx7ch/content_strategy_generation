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
            '{"coverage_snapshot_id":"scv_1","operation":"limited_report","resolution":"generate_limited_report","resulting_scope_contract_id":"rsc_1","schema":"execution_decision_identity_v1","source_scope_contract_id":"rsc_1","supplementary_queries":[],"target_constraint_id":null}',
            "187960462beb3fca1192b60bfb15a95d743d148ff56ef9471258c853e13eb6bf",
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
            '{"coverage_snapshot_id":"scv_2","operation":"supplementary_collection","resolution":"expand_required_constraint","resulting_scope_contract_id":"rsc_2","schema":"execution_decision_identity_v1","source_scope_contract_id":"rsc_2","supplementary_queries":["夏季 防晒 衬衫","通勤 衬衫"],"target_constraint_id":"season"}',
            "3f8afa2f9c087475dd1ba036535d7f2d66b153705ba4851040779d7b0a504c70",
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
            '{"coverage_snapshot_id":"scv_3","operation":"supplementary_collection","resolution":"relax_constraint","resulting_scope_contract_id":"rsc_4","schema":"execution_decision_identity_v1","source_scope_contract_id":"rsc_3","supplementary_queries":[],"target_constraint_id":"scenario"}',
            "baaac6118dcee72800dde36425528c89b8d5d73bc1bd5d24262bc6c1a3fe3214",
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
            '{"coverage_snapshot_id":"scv_2","operation":"supplementary_collection","resolution":"expand_required_constraint","resulting_scope_contract_id":"rsc_2","schema":"execution_decision_identity_v1","source_scope_contract_id":"rsc_2","supplementary_queries":["夏季 防晒 衬衫","通勤 衬衫"],"target_constraint_id":"scenario"}',
            "68678e040e0ca13a793bbedbf715f875d5c930b258440839e9a9bf82a00722b2",
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
            '{"coverage_snapshot_id":"scv_2","operation":"supplementary_collection","resolution":"expand_required_constraint","resulting_scope_contract_id":"rsc_2","schema":"execution_decision_identity_v1","source_scope_contract_id":"rsc_2","supplementary_queries":["夏季 透气 衬衫"],"target_constraint_id":"season"}',
            "1246372b3d98cfbcfafdba9ee18da3eb4baa22de4d5886084293ac60c515e97b",
        ),
    ],
)
def test_execution_decision_identity_has_one_canonical_payload_and_digest(
    kwargs, expected_json, expected_digest
) -> None:
    """Changing serialization, normalization, or operation derivation breaks replay parity."""
    result = build_execution_decision_identity(**kwargs)

    assert result.canonical_json == expected_json
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
