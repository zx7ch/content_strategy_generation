from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.content_research.admission.candidates import source_text_hash
from app.content_research.analysis_persistence import SQLiteMarketingAnalysisRepository
from app.content_research.contracts import build_default_snapshot, policy_hash
from app.content_research.marketing_analysis_execution import MarketingAnalysisExecutionError
from app.content_research.models import ResearchBriefRecord, SubagentTaskRecord
from app.content_research.persisted_packet_replay import (
    PersistedPacketReplayInput,
    build_persisted_packet_replay_input,
)
from app.content_research.persistence_models import (
    CanonicalSourceRecord,
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
    CoverageManifest,
    DirectionalEvidencePacketRecord,
    ReportPublicationRecord,
    StageCheckpointRecord,
)
from app.content_research.presearch.service import PresearchService
from app.content_research.research_embedding import (
    ResearchEmbeddingBatch,
    ResearchEmbeddingFingerprint,
    ResearchEmbeddingHealth,
)
from app.content_research.scope_contract import ResearchScopeContract, ScopeQueryGroup
from app.content_research.service import (
    ContentResearchService,
    ContentResearchValidationError,
    WorkflowRunManagerRuntime,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.workflow.directional_pipeline import (
    DirectionalExecutionPipeline,
    compile_query_groups,
)
from app.content_research.workflow_mutation_authority import (
    persisted_packet_replay_unavailable_reason,
    project_legacy_recovery_authority,
)
from app.services.llm.failures import LLMProviderFailure
from app.services.llm.types import LLMResponse, TokenUsage


def _repair_authority_context(tmp_path):
    db_path = str(tmp_path / "repair-authority.db")
    store = SQLiteContentResearchStore(db_path)
    workflow_run_id = "run-repair-authority"
    frozen_at = datetime(2026, 8, 22, tzinfo=timezone.utc)
    groups = compile_query_groups(
        direction_id="product_marketing",
        subject="防晒服饰",
        questions=["产品营销"],
        competitors=[],
        run_as_of_at=frozen_at,
    )
    snapshot, policies, contracts = build_default_snapshot(
        snapshot_id="rps-repair-authority",
        workflow_run_id=workflow_run_id,
        brief_id="rb-repair-authority",
        plan_id="rp-repair-authority",
        run_as_of_at=frozen_at,
        direction_ids=("product_marketing",),
        confirmed_subject="防晒服饰",
        query_groups_by_direction={
            "product_marketing": tuple(
                {
                    "id": group.id,
                    "direction_id": group.direction_id,
                    "normalized_query": group.query,
                    "priority": group.priority,
                    "sort": group.sort,
                    "time_window": dict(group.time_window or {}),
                    "candidate_cap": group.candidate_limit,
                }
                for group in groups
            )
        },
        subject_structure={
            "schema_version": "content_research_subject_structure_v1",
            "canonical_subject": "防晒服饰",
            "subject_type": "category",
            "core_entities": [
                {"canonical_name": "防晒服饰", "raw_mentions": ["防晒服"]}
            ],
            "research_intents": ["产品营销"],
            "context_modifiers": ["夏季"],
            "synonym_groups": {},
            "ambiguities": [],
            "resolution_state": "resolved",
        },
        subject_structure_hash="repair-authority-structure",
    )
    store.save_brief(
        ResearchBriefRecord(
            id="rb-repair-authority",
            workflow_run_id=workflow_run_id,
            thread_id="thread-repair-authority",
            schema_version="content_research_brief_v1",
            status="confirmed",
            payload={
                "schema_version": "content_research_brief_payload_v1",
                "confirmed_subject": "防晒服饰",
            },
        )
    )
    store.save_run_policy_snapshot(snapshot)
    for policy in policies:
        store.save_sample_policy(policy)
    for contract in contracts:
        store.save_direction_contract(contract)
    task = SubagentTaskRecord(
        id="task-repair-authority",
        workflow_run_id=workflow_run_id,
        thread_id="thread-repair-authority",
        schema_version="content_research_subagent_task_v1",
        status="completed",
        plan_id="rp-repair-authority",
        direction_id="product_marketing",
        payload={"schema_version": "content_research_subagent_task_payload_v1"},
    )
    store.save_subagent_task(task)
    store.save_canonical_source(
        CanonicalSourceRecord(
            "source-repair-authority",
            "content_research_canonical_source_v1",
            {},
            platform="xiaohongshu",
            platform_source_kind="note",
            platform_source_id="note-repair-authority",
        )
    )
    store.save_directional_evidence_packet(
        DirectionalEvidencePacketRecord(
            "dep-repair-authority",
            "content_research_directional_evidence_packet_v1",
            {
                "field_projection": {
                    "author": "样本作者",
                    "title": "防晒服饰产品营销样本",
                    "content_text": "防晒服饰产品营销样本",
                    "tags": ["防晒服饰"],
                    "source_url": "https://example.test/note-repair-authority",
                },
                "field_availability": {
                    "author": "present",
                    "title": "present",
                    "content_text": "present",
                    "tags": "present",
                },
                "retrieval_context": {
                    "source_kind": "note_detail",
                    "query_group_id": groups[0].id,
                },
            },
            workflow_run_id=workflow_run_id,
            research_direction_id="product_marketing",
            canonical_source_id="source-repair-authority",
            field_projection_hash="projection-repair-authority",
        )
    )
    store.save_stage_checkpoint(
        StageCheckpointRecord(
            id="scp-repair-authority-selection",
            schema_version="content_research_stage_checkpoint_v1",
            payload={
                "direction_id": "product_marketing",
                "selection": {
                    "query_plan_hash": snapshot.effective_policy["locked_query_plan"]
                    ["directions"]["product_marketing"]["query_plan_hash"],
                    "candidate_manifest_hash": "manifest-repair-authority",
                    "decisions": [
                        {
                            "canonical_source_id": "source-repair-authority",
                            "selected": True,
                            "reasons": ["selected_deterministically"],
                            "query_group_ids": [groups[0].id],
                            "query_hits": [
                                {"query_group_id": groups[0].id, "rank": 1}
                            ],
                        }
                    ],
                    "selected_source_count": 1,
                    "eligible_source_count": 1,
                    "independent_source_count": 1,
                    "status": "complete",
                    "coverage_unmet_query_group_ids": [],
                }
            },
            workflow_run_id=workflow_run_id,
            subagent_task_id=task.id,
            stage_name="selection",
            input_fingerprint="repair-authority-selection",
            status="completed",
        )
    )
    store.save_stage_checkpoint(
        StageCheckpointRecord(
            id="scp-repair-authority-packet",
            schema_version="content_research_stage_checkpoint_v1",
            payload={
                "direction_id": "product_marketing",
                "packet_ids": ["dep-repair-authority"],
            },
            workflow_run_id=workflow_run_id,
            subagent_task_id=task.id,
            stage_name="packet",
            input_fingerprint="repair-authority-packet",
            status="completed",
        )
    )
    service = ContentResearchService(
        store=store,
        presearch=PresearchService(None),
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
    )
    report = {
        "publication": {
            "state": "evidence_only_report",
            "publication_reason": "query_subject_not_supported",
        }
    }
    return service, store, task, report


def _checkpoint(store, checkpoint_id):
    record = store.get_typed_record(StageCheckpointRecord, checkpoint_id)
    assert record is not None
    return record


def test_repair_preflight_rejects_malformed_typed_selection(tmp_path):
    """Dropping DirectionSelection decisions must not leave Repair available."""
    _service, store, task, report = _repair_authority_context(tmp_path)
    checkpoint = _checkpoint(store, "scp-repair-authority-selection")
    malformed = dict(checkpoint.payload["selection"])
    malformed.pop("decisions")
    store.save_stage_checkpoint(
        replace(checkpoint, payload={**checkpoint.payload, "selection": malformed})
    )

    assert persisted_packet_replay_unavailable_reason(
        store, task.workflow_run_id, publication=report["publication"]
    ) == "persisted_packet_selection_invalid"


@pytest.mark.parametrize(
    "mutation", ["extra_direction", "non_mapping", "contract_copy"]
)
def test_repair_preflight_requires_exact_frozen_relevance_copies(tmp_path, mutation):
    """Accepting incomplete or unequal relevance copies must expose false Repair."""
    _service, store, task, report = _repair_authority_context(tmp_path)
    snapshot = store.get_run_policy_snapshot_for_workflow(task.workflow_run_id)
    assert snapshot is not None
    contract = store.list_direction_contracts(snapshot.id)[0]
    if mutation == "extra_direction":
        policy = {
            **snapshot.effective_policy,
            "query_relevance": {
                **snapshot.effective_policy["query_relevance"],
                "not-a-frozen-direction": snapshot.effective_policy["query_relevance"]
                ["product_marketing"],
            },
        }
        with store._connect() as connection:
            connection.execute(
                "UPDATE content_research_run_policy_snapshots "
                "SET effective_policy_json=?, effective_policy_hash=? WHERE id=?",
                (json.dumps(policy), policy_hash(policy), snapshot.id),
            )
        expected = "persisted_packet_relevance_directions_mismatch"
    elif mutation == "non_mapping":
        policy = {
            **snapshot.effective_policy,
            "query_relevance": {"product_marketing": "invalid"},
        }
        with store._connect() as connection:
            connection.execute(
                "UPDATE content_research_run_policy_snapshots "
                "SET effective_policy_json=?, effective_policy_hash=? WHERE id=?",
                (json.dumps(policy), policy_hash(policy), snapshot.id),
            )
        expected = "persisted_packet_relevance_invalid"
    else:
        relevance = {
            **contract.metadata["query_relevance"],
            "subject_anchors": ["不一致主题"],
        }
        with store._connect() as connection:
            connection.execute(
                "UPDATE content_research_direction_contracts SET metadata_json=? WHERE id=?",
                (json.dumps({**contract.metadata, "query_relevance": relevance}), contract.id),
            )
        expected = "persisted_packet_relevance_contract_mismatch"

    assert persisted_packet_replay_unavailable_reason(
        store, task.workflow_run_id, publication=report["publication"]
    ) == expected


@pytest.mark.parametrize(
    ("foreign_workflow_run_id", "foreign_direction_id"),
    [
        ("run-foreign", "product_marketing"),
        ("run-repair-authority", "competitor_discovery"),
    ],
)
def test_repair_preflight_rejects_packet_outside_task_ownership(
    tmp_path,
    foreign_workflow_run_id,
    foreign_direction_id,
):
    """Referencing an existing foreign packet must not make it replayable."""
    _service, store, task, report = _repair_authority_context(tmp_path)
    store.save_directional_evidence_packet(
        DirectionalEvidencePacketRecord(
            "dep-foreign",
            "content_research_directional_evidence_packet_v1",
            {"field_projection": {"content_text": "foreign"}},
            workflow_run_id=foreign_workflow_run_id,
            research_direction_id=foreign_direction_id,
            canonical_source_id="source-repair-authority",
            field_projection_hash="projection-foreign",
        )
    )
    checkpoint = _checkpoint(store, "scp-repair-authority-packet")
    store.save_stage_checkpoint(
        replace(
            checkpoint,
            payload={**checkpoint.payload, "packet_ids": ["dep-foreign"]},
        )
    )

    assert persisted_packet_replay_unavailable_reason(
        store, task.workflow_run_id, publication=report["publication"]
    ) == "persisted_packet_record_ownership_mismatch"


def test_repair_preflight_accepts_complete_typed_owned_replay_input(tmp_path):
    """Rejecting the complete frozen bundle would remove valid legacy recovery."""
    _service, store, task, report = _repair_authority_context(tmp_path)

    assert (
        persisted_packet_replay_unavailable_reason(
            store, task.workflow_run_id, publication=report["publication"]
        )
        is None
    )


def test_complete_replay_input_executes_real_admission_without_raw_reparse(tmp_path):
    """A Ready bundle that cannot cross real admission is a false Repair action."""
    _service, store, task, report = _repair_authority_context(tmp_path)
    replay_input = build_persisted_packet_replay_input(
        store,
        task.workflow_run_id,
        publication=report["publication"],
    )
    assert isinstance(replay_input, PersistedPacketReplayInput)

    packet_ids = DirectionalExecutionPipeline(
        store
    ).replay_admission_from_persisted_packets(
        replay_input=replay_input.directions[0],
        snapshot=replay_input.snapshot,
    )

    assert packet_ids == ("dep-repair-authority",)
    decisions = store.list_typed_records(ClaimAdmissionDecisionRecord)
    assert decisions
    assert {item.research_direction_id for item in decisions} == {
        "product_marketing"
    }
    assert {item.policy_snapshot_id for item in decisions} == {
        replay_input.snapshot.id
    }


@pytest.mark.asyncio
async def test_repair_projection_and_direct_action_share_complete_durable_preconditions(
    tmp_path,
    monkeypatch,
):
    """Skipping terminal-task preflight must advertise and execute a false Repair."""
    service, store, task, report = _repair_authority_context(tmp_path)

    eligible = await project_legacy_recovery_authority(
        store,
        store._db_path,
        task.workflow_run_id,
        published_report=report,
    )
    assert [action.action for action in eligible.actions] == [
        "repair_from_persisted_packets"
    ]

    class RepairReport:
        publication = report["publication"]

        def model_dump(self, *, mode):
            assert mode == "json"
            return report

    async def get_report(**_kwargs):
        return RepairReport()

    replay_calls = 0

    async def replay(_workflow_run_id):
        nonlocal replay_calls
        replay_calls += 1
        return {"publication_state": "complete_verified_report"}

    monkeypatch.setattr(service, "get_lite_report", get_report)
    monkeypatch.setattr(service, "replay_downstream_from_persisted_packets", replay)
    repaired = await service.repair_from_persisted_packets(task.workflow_run_id)
    assert repaired["status"] == "completed"
    assert replay_calls == 1

    store.save_subagent_task(replace(task, status="failed"))
    unavailable = await project_legacy_recovery_authority(
        store,
        store._db_path,
        task.workflow_run_id,
        published_report=report,
    )
    assert unavailable.actions == ()
    assert unavailable.unavailable_reason == "persisted_packet_tasks_not_terminal"
    with pytest.raises(
        ContentResearchValidationError,
        match="persisted_packet_tasks_not_terminal",
    ):
        await service.repair_from_persisted_packets(task.workflow_run_id)
    assert replay_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("brief", "persisted_packet_brief_missing"),
        ("policy", "persisted_packet_policy_missing"),
        ("contract", "persisted_packet_direction_contract_missing"),
        ("sample_policy", "persisted_packet_sample_policy_missing"),
        ("selection_checkpoint", "persisted_packet_selection_checkpoint_missing"),
        ("packet_checkpoint", "persisted_packet_packet_checkpoint_missing"),
        ("packet_record", "persisted_packet_record_missing"),
    ],
)
async def test_repair_projection_and_guard_reject_each_missing_durable_parent(
    tmp_path,
    mutation,
    expected_reason,
):
    """Dropping a replay parent must make projection and mutation guard disagree."""
    service, store, task, report = _repair_authority_context(tmp_path)
    contract = store.list_direction_contracts("rps-repair-authority")[0]
    statements = {
        "brief": (
            "DELETE FROM content_research_briefs WHERE workflow_run_id=?",
            (task.workflow_run_id,),
        ),
        "policy": (
            "DELETE FROM content_research_run_policy_snapshots WHERE workflow_run_id=?",
            (task.workflow_run_id,),
        ),
        "contract": (
            "DELETE FROM content_research_direction_contracts WHERE id=?",
            (contract.id,),
        ),
        "sample_policy": (
            "DELETE FROM content_research_sample_policies WHERE id=?",
            (contract.sample_policy_id,),
        ),
        "selection_checkpoint": (
            "DELETE FROM content_research_stage_checkpoints "
            "WHERE workflow_run_id=? AND stage_name='selection'",
            (task.workflow_run_id,),
        ),
        "packet_checkpoint": (
            "DELETE FROM content_research_stage_checkpoints "
            "WHERE workflow_run_id=? AND stage_name='packet'",
            (task.workflow_run_id,),
        ),
        "packet_record": (
            "DELETE FROM content_research_directional_evidence_packets WHERE id=?",
            ("dep-repair-authority",),
        ),
    }
    statement, params = statements[mutation]
    with store._connect() as connection:
        connection.execute(statement, params)

    authority = await project_legacy_recovery_authority(
        store,
        store._db_path,
        task.workflow_run_id,
        published_report=report,
    )
    assert authority.actions == ()
    assert authority.unavailable_reason == expected_reason
    with pytest.raises(ContentResearchValidationError, match=expected_reason):
        await service._require_legacy_recovery_authority(
            workflow_run_id=task.workflow_run_id,
            action="repair_from_persisted_packets",
            published_report=report,
        )

class _ConclusionLLM:
    def __init__(self, *, failures_remaining: int = 0) -> None:
        self.calls = 0
        self.failures_remaining = failures_remaining

    async def generate(self, request):
        self.calls += 1
        payload = json.loads(request.messages[-1].content)
        if request.task_type == "content_research.marketing_evidence_extraction":
            evidence = []
            for note in payload["notes"]:
                body = note["content_text"]
                title = note["title"]
                if body:
                    evidence.extend(
                        {
                            "note_id": note["note_id"],
                            "field_path": "content_text",
                            "quote": body,
                            "text_start": 0,
                            "text_end": len(body),
                            "track": track,
                            "aspect": "轻量透气体验",
                            "evidence_type": "experience",
                            "polarity": "support",
                            "scenes": [],
                            "audiences": [],
                        }
                        for track in ("need", "value")
                    )
                if title:
                    evidence.append(
                        {
                            "note_id": note["note_id"],
                            "field_path": "title",
                            "quote": title,
                            "text_start": 0,
                            "text_end": len(title),
                            "track": "message",
                            "aspect": "标题表达",
                            "evidence_type": "message_expression",
                            "polarity": "support",
                            "scenes": [],
                            "audiences": [],
                        }
                    )
            return LLMResponse(
                content=json.dumps({"evidence": evidence}, ensure_ascii=False),
                provider="fake",
                model="fake",
                usage=TokenUsage(total_tokens=1),
                latency_ms=1,
            )
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise LLMProviderFailure(
                "llm_service_unavailable",
                "模型服务暂时不可用",
                True,
                None,
            )
        track = payload["tracks"][0]
        return LLMResponse(
            content=json.dumps(
                {
                    "candidates": [
                        {
                            "track": track,
                            "statement": f"样本明确表达 {track} 方向的轻量透气体验",
                            "supporting_claim_ids": [
                                item["claim_id"] for item in payload["claims"]
                            ],
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            provider="fake",
            model="fake",
            usage=TokenUsage(total_tokens=1),
            latency_ms=1,
        )


class _DeterministicResearchEmbedding:
    def __init__(self) -> None:
        self._fingerprint = ResearchEmbeddingFingerprint(
            provider="deterministic",
            model="analysis-replay",
            revision="v1",
            dimensions=3,
        )

    @property
    def health(self):
        return ResearchEmbeddingHealth("ready", self._fingerprint)

    def embed_documents(self, documents):
        return ResearchEmbeddingBatch(
            document_ids=tuple(item.note_id for item in documents),
            input_fingerprints=tuple(f"input-{item.note_id}" for item in documents),
            vectors=tuple((1.0, 0.0, 0.0) for _item in documents),
            embedding_fingerprint=self._fingerprint,
        )


@pytest.mark.asyncio
async def test_one_track_failure_blocks_publication_and_retry_reuses_successful_tracks(
    tmp_path,
):
    db_path = str(tmp_path / "marketing-conclusion-replay.db")
    store = SQLiteContentResearchStore(db_path)
    frozen_at = datetime(2026, 8, 5, tzinfo=timezone.utc)
    snapshot, _policies, _contracts = build_default_snapshot(
        snapshot_id="rps-conclusion-replay",
        workflow_run_id="run-conclusion-replay",
        brief_id="rb-conclusion-replay",
        plan_id="rp-conclusion-replay",
        run_as_of_at=frozen_at,
        direction_ids=("product_marketing",),
        primary_marketing_goal="content_seeding",
    )
    store.save_run_policy_snapshot(snapshot)
    scope = ResearchScopeContract(
        id="scope-conclusion-replay",
        workflow_run_id="run-conclusion-replay",
        research_plan_id="rp-conclusion-replay",
        version=1,
        schema_version="content_research_scope_contract_v2",
        constraints=(),
        query_groups=(
            ScopeQueryGroup(
                id="query-core",
                suggested_query="轻量透气上衣",
                final_query="轻量透气上衣",
                origin="system_suggested",
                execution_role="primary",
            ),
        ),
        created_at=frozen_at,
    )
    store.save_scope_contract(scope)
    store.save_brief(
        ResearchBriefRecord(
            id="rb-conclusion-replay",
            workflow_run_id="run-conclusion-replay",
            thread_id="thread-conclusion-replay",
            schema_version="content_research_brief_v1",
            status="ready",
            payload={
                "schema_version": "content_research_brief_v1",
                "workspace_id": "ws-1",
                "user_id": "user-1",
            },
        )
    )
    quote = "产品营销样本明确提到轻量透气"
    for index, author_id in enumerate(
        ("author-a", "author-a", "author-b", "author-c"), start=1
    ):
        source_id = f"source-{index}"
        packet_id = f"packet-{index}"
        store.save_canonical_source(
            CanonicalSourceRecord(
                source_id,
                "canonical-source-v1",
                {},
                platform="xiaohongshu",
                platform_source_kind="note",
                platform_source_id=f"note-{index}",
            )
        )
        store.save_directional_evidence_packet(
            DirectionalEvidencePacketRecord(
                packet_id,
                "directional-packet-v1",
                {
                    "field_projection": {
                        "title": "轻量透气体验",
                        "content_text": quote,
                        "source_url": f"https://example.test/{index}",
                        "author_id": author_id,
                    },
                    "retrieval_context": {"query_group_ids": ["query-core"]},
                    "field_availability": {"content_text": "present"},
                },
                workflow_run_id="run-conclusion-replay",
                research_direction_id="product_marketing",
                canonical_source_id=source_id,
                field_projection_hash=f"projection-{index}",
                scope_contract_id=scope.id,
                execution_unit_id="retrieval-unit-replay",
                attempt_no=1,
            )
        )
        for track, claim_type, intent_id in (
            ("need", "use_context", "usage_context"),
            ("value", "product_value_expression", "value_proposition"),
            ("message", "message_angle", "message_angle"),
        ):
            claim_id = f"claim-{track}-{index}"
            store.save_claim_candidate(
                ClaimCandidateRecord(
                    claim_id,
                    "claim-candidate-v1",
                    {
                        "quote_refs": [
                            {
                                "field_path": "content_text",
                                "quote": quote,
                                "text_start": 0,
                                "text_end": len(quote),
                                "source_text_hash": source_text_hash(quote),
                                "source_url": f"https://example.test/{index}",
                            }
                        ],
                        "scope": {
                            "sample": "selected_packets",
                            "qualifiers": {"scenes": [], "audiences": []},
                            "polarity": "support",
                        },
                    },
                    workflow_run_id="run-conclusion-replay",
                    research_direction_id="product_marketing",
                    evidence_packet_id=packet_id,
                    statement=quote,
                    intent_id=intent_id,
                    claim_type=claim_type,
                    scope_contract_id=scope.id,
                    execution_unit_id="retrieval-unit-replay",
                    attempt_no=1,
                )
            )
            store.save_claim_admission_decision(
                ClaimAdmissionDecisionRecord(
                    f"decision-{track}-{index}",
                    "admission-decision-v1",
                    {
                        "policy_snapshot_hash": snapshot.effective_policy_hash,
                        "reason_codes": [],
                    },
                    research_direction_id="product_marketing",
                    claim_candidate_id=claim_id,
                    decision="admitted",
                    policy_snapshot_id=snapshot.id,
                )
            )

    manifest = CoverageManifest(
        workflow_run_id="run-conclusion-replay",
        scope_contract_id=scope.id,
        execution_unit_id="retrieval-unit-replay",
        attempt_no=1,
        execution_revision=1,
        packet_ids=tuple(f"packet-{index}" for index in range(1, 5)),
    )
    llm = _ConclusionLLM(failures_remaining=1)
    service = ContentResearchService(
        store=store,
        presearch=PresearchService(None),
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
        analysis_llm=llm,
        research_embedding_runtime=_DeterministicResearchEmbedding(),
    )
    operation_ids_before = {
        item.id
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "operation"
    }
    packet_ids_before = {
        item.id for item in store.list_typed_records(DirectionalEvidencePacketRecord)
    }

    with pytest.raises(MarketingAnalysisExecutionError) as failure:
        await service._govern_marketing_conclusions(
            workflow_run_id="run-conclusion-replay",
            research_plan_id="rp-conclusion-replay",
            manifest=manifest,
        )
    assert failure.value.failures == {"need": "llm_service_unavailable"}
    unavailable = store.list_marketing_conclusion_decisions(
        "run-conclusion-replay", "rp-conclusion-replay"
    )
    assert {(item.track, item.state) for item in unavailable} == {
        ("need", "analysis_unavailable"),
        ("message", "selected"),
        ("value", "selected"),
    }
    assert store.list_typed_records(ReportPublicationRecord) == []
    assert llm.calls == 4  # one structured extraction + three independent tracks
    packet_ids_after_failure = {
        item.id
        for item in store.list_typed_records(DirectionalEvidencePacketRecord)
    }
    assert packet_ids_before < packet_ids_after_failure
    assert len(packet_ids_after_failure - packet_ids_before) == 4

    # The lifecycle Coordinator owns successor creation. This execution-layer
    # test simulates the already-authorized retry before proving checkpoint reuse.
    analysis_repository = SQLiteMarketingAnalysisRepository(db_path)
    with store._connect() as connection:
        analysis_unit_id = str(
            connection.execute(
                "SELECT id FROM content_research_analysis_units WHERE workflow_run_id=?",
                ("run-conclusion-replay",),
            ).fetchone()[0]
        )
    failed_attempt = analysis_repository.get_latest_attempt_for_unit(analysis_unit_id)
    assert failed_attempt is not None
    assert failed_attempt.state == "running"
    failed_attempt = analysis_repository.fail_analysis_attempt(
        failed_attempt.id,
        lease_token=str(failed_attempt.lease_token),
    )
    analysis_repository.create_analysis_attempt(
        failed_attempt.analysis_unit_id,
        successor_of_attempt_id=failed_attempt.id,
    )

    first = await service._govern_marketing_conclusions(
        workflow_run_id="run-conclusion-replay",
        research_plan_id="rp-conclusion-replay",
        manifest=manifest,
    )
    candidates_after_first = store.list_marketing_conclusion_candidates(
        "run-conclusion-replay", "rp-conclusion-replay"
    )
    decisions_after_first = store.list_marketing_conclusion_decisions(
        "run-conclusion-replay", "rp-conclusion-replay"
    )
    selected_decision = next(
        item for item in decisions_after_first if item.state == "selected"
    )
    assert selected_decision.payload["additional_qualified_count"] == 0
    replay = await service._govern_marketing_conclusions(
        workflow_run_id="run-conclusion-replay",
        research_plan_id="rp-conclusion-replay",
        manifest=manifest,
    )

    assert replay.id == first.id
    assert llm.calls == 5
    with store._connect() as connection:
        attempts = connection.execute(
            "SELECT attempt_no, state FROM content_research_analysis_attempts "
            "ORDER BY attempt_no"
        ).fetchall()
        checkpoints = connection.execute(
            "SELECT track, completed_by_attempt_id FROM content_research_analysis_checkpoints "
            "WHERE stage='verifier' ORDER BY track"
        ).fetchall()
    assert [tuple(row) for row in attempts] == [(1, "failed"), (2, "succeeded")]
    assert {row[0] for row in checkpoints} == {"need", "value", "message"}
    assert len({row[1] for row in checkpoints}) == 2
    assert store.list_marketing_conclusion_candidates(
        "run-conclusion-replay", "rp-conclusion-replay"
    ) == candidates_after_first
    assert store.list_marketing_conclusion_decisions(
        "run-conclusion-replay", "rp-conclusion-replay"
    ) == decisions_after_first
    assert len(
        [
            item
            for item in store.list_typed_records(StageCheckpointRecord)
            if item.stage_name == "marketing_conclusion"
        ]
    ) == 1
    assert {
        item.id
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "operation"
    } == operation_ids_before
    assert {
        item.id for item in store.list_typed_records(DirectionalEvidencePacketRecord)
    } == packet_ids_after_failure
