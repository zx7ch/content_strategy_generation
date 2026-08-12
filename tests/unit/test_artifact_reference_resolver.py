"""Unit tests for T8.2 artifact reference resolution."""

from __future__ import annotations

import json

from app.services.conversation_orchestrator import ArtifactReferenceResolverV1


def _artifact_message(refs: list[dict]) -> dict:
    return {
        "message_type": "artifact_result",
        "artifact_refs_json": json.dumps(refs, ensure_ascii=False),
    }


def test_resolves_numbered_generated_note_from_latest_artifact_result_message():
    resolver = ArtifactReferenceResolverV1(
        messages=[
            _artifact_message(
                [
                    {"artifact_id": "artifact_note_1", "artifact_type": "generated_note", "artifact_version": 1},
                    {"artifact_id": "artifact_note_2", "artifact_type": "generated_note", "artifact_version": 1},
                ]
            )
        ]
    )

    assert resolver.resolve("把第 2 篇改生活化") == [
        {
            "artifact_id": "artifact_note_2",
            "artifact_type": "generated_note",
            "artifact_version": 1,
            "parent_artifact_id": None,
        }
    ]


def test_resolves_chinese_ordinal_current_previous_and_explicit_artifact_id():
    resolver = ArtifactReferenceResolverV1(
        messages=[
            _artifact_message(
                [
                    {"artifact_id": "artifact_note_1", "artifact_type": "generated_note", "artifact_version": 1},
                    {"artifact_id": "artifact_note_2", "artifact_type": "generated_note", "artifact_version": 1},
                    {
                        "artifact_id": "artifact_note_2_patch",
                        "artifact_type": "generated_note",
                        "artifact_version": 2,
                        "parent_artifact_id": "artifact_note_2",
                    },
                ]
            )
        ],
        artifacts=[
            {"artifact_id": "artifact_note_1", "artifact_type": "generated_note", "artifact_version": 1},
            {"artifact_id": "artifact_note_2", "artifact_type": "generated_note", "artifact_version": 1},
            {
                "artifact_id": "artifact_note_2_patch",
                "artifact_type": "generated_note",
                "artifact_version": 2,
                "parent_artifact_id": "artifact_note_2",
            },
        ],
    )

    assert resolver.resolve("第二篇标题换一下")[0]["artifact_id"] == "artifact_note_2"
    assert resolver.resolve("这篇再口语化")[0]["artifact_id"] == "artifact_note_2_patch"
    assert resolver.resolve("上一版再改")[0]["artifact_id"] == "artifact_note_2"
    assert resolver.resolve("修改 artifact_note_1")[0]["artifact_id"] == "artifact_note_1"
