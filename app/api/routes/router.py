"""FastAPI routes for session lifecycle, enqueue flow, and SSE updates."""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from datetime import datetime
from time import monotonic
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, Header, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import settings
from app.logging_config import get_logger, log_event
from app.memory.job_store import JobStore, SessionEventRecord
from app.memory.session_state import SessionManager
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowArtifactType
from app.services.creator_intent_router import ACTIVE_JOB_STATUSES, IntentContext, classify_intent
from app.services.conversation_orchestrator import ConversationOrchestrator
from app.services.workflow_artifact_policy import WorkflowArtifactVersionPolicy
from app.models.schemas import (
    CreateSessionResponse,
    EnqueueResponse,
    ErrorResponse,
    InitSessionRequest,
    JobStatusResponse,
    ResumeSessionResponse,
    SessionEvent,
    SessionEventPayload,
    SessionStatusResponse,
    V2BrandChannelCreateRequest,
    V2BrandChannelListResponse,
    V2BrandChannelResponse,
    V2BrandChannelUpdateRequest,
    V2BrandCreateRequest,
    V2DataImportPreviewRequest,
    V2DataImportPreviewResponse,
    V2BrandListResponse,
    V2BrandPolicyConfigResponse,
    V2BrandPolicyConfigUpsertRequest,
    V2BrandResponse,
    V2BrandUpdateRequest,
    V2BrandDataImportRequest,
    V2BrandSourceSyncRequest,
    V2BrandWorkspaceResponse,
    V2ExtensionCaptureSessionCreateRequest,
    V2ExtensionCaptureSessionResponse,
    V2ExtensionCaptureSubmitRequest,
    V2BrandStateSnapshotCreateRequest,
    V2BrandStateSnapshotListResponse,
    V2BrandStateSnapshotResponse,
    V2DiscoveryCustomQueryRequest,
    V2DiscoveryTaskCreateRequest,
    V2DiscoveryTaskResponse,
    V2IngestionAcceptedResponse,
    V2IngestionRunResponse,
    V2TopicPoolBrandSummary,
    V2TopicPoolItemResponse,
    V2TopicPoolListResponse,
    V2TopicPoolRefreshRequest,
    V2TopicPoolRefreshResponse,
    V2TopicPoolStatsResponse,
    V2DefaultWorkspaceResponse,
    V2DecisionBatchItemResponse,
    V2DecisionBatchDetailResponse,
    V2DecisionBatchItemReviewRequest,
    V2DecisionBatchItemReviewResponse,
    V2DecisionRunRequest,
    V2DecisionRunResponse,
    V2EvaluationRunRequest,
    V2EvaluationRunResponse,
    V2EvaluationRunSliceResponse,
    V2WorkspaceCreateRequest,
    V2WorkspaceResponse,
    V2PerformanceImportRequest,
    V2PerformanceSnapshotListResponse,
    V2PerformanceSnapshotResponse,
    V2PublishRecordCreateRequest,
    V2PublishRecordListResponse,
    V2PublishRecordResponse,
    CreatorThreadCreateRequest,
    CreatorThreadUpdateRequest,
    CreatorThreadSummary,
    CreatorThreadDetail,
    CreatorMessageRecord,
    CreatorThreadResponse,
    CreatorThreadListResponse,
    CreatorThreadDetailResponse,
    CreatorThreadTimelineResponse,
    CreatorThreadDeleteResponse,
    CreatorMessageCreateRequest,
    CreatorMessageResponse,
    CreatorWorkflowRequest,
    CreatorWorkflowResponse,
    JobControlResponse,
    PublishCandidate,
    CompleteThreadResponse,
    PublishCandidatesResponse,
    GeneratedNoteItem,
    ThreadResultResponse,
)
from app.models.session import Session, SessionLifecycleState, SessionStage
from app.v2.auth import WorkspaceAuthError, resolve_workspace_principal
from app.v2.decision import (
    DecisionError,
    DecisionNotFoundError,
    DecisionService,
    DecisionValidationError,
)
from app.v2.discovery import (
    DiscoveryError,
    DiscoveryNotFoundError,
    DiscoveryQueryExpansionError,
    DiscoveryScopeError,
    DiscoveryService,
    DiscoveryValidationError,
)
from app.v2.foundation.bootstrap import DEFAULT_USER_ID, DEFAULT_WORKSPACE_ID
from app.v2.feedback import (
    FeedbackError,
    FeedbackNotFoundError,
    FeedbackService,
    FeedbackValidationError,
)
from app.v2.foundation import MasterDataService
from app.v2.foundation.service import (
    MasterDataError,
    MasterDataConflictError,
    MasterDataInvariantError,
    MasterDataNotFoundError,
    MasterDataScopeError,
    MasterDataValidationError,
)
from app.v2.ingestion import IngestionError, IngestionService, IngestionValidationError
from app.v2.topic_pool import TopicPoolError, TopicPoolService, TopicPoolValidationError


class APIError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        error_code: str,
        error_message: str,
        error_details: Optional[dict[str, Any]] = None,
        retryable: bool = False,
        suggested_action: Optional[str] = None,
    ) -> None:
        super().__init__(error_message)
        self.status_code = status_code
        self.payload = ErrorResponse(
            error_code=error_code,
            error_message=error_message,
            error_details=error_details,
            retryable=retryable,
            suggested_action=suggested_action,
        )


app = FastAPI(
    title="XHS Note Generator",
    description="Xiaohongshu content workflow API",
    version="2.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Workspace-Id",
        "X-User-Id",
        "Access-Control-Request-Private-Network",
    ],
    allow_private_network=True,
)
_logger = get_logger(__name__, component="api")
_embedding_prewarm_task: Optional[asyncio.Task] = None
_embedding_prewarm_status: dict[str, Any] = {
    "status": "idle",
    "message": "Embedding model has not been prewarmed.",
}


async def _run_embedding_prewarm() -> None:
    global _embedding_prewarm_status
    _embedding_prewarm_status = {
        "status": "warming",
        "message": "正在初始化本地向量模型（首次较慢）",
        "started_at": datetime.utcnow().isoformat(),
    }
    try:
        from app.services.rag_service import RAGService

        rag = RAGService()
        embedder = await asyncio.to_thread(rag._get_embedder)
        _embedding_prewarm_status = {
            "status": "ready" if embedder is not None else "disabled",
            "message": "本地向量模型已准备就绪" if embedder is not None else "本地向量模型已禁用",
            "completed_at": datetime.utcnow().isoformat(),
        }
    except Exception as exc:  # noqa: BLE001
        _embedding_prewarm_status = {
            "status": "failed",
            "message": "本地向量模型初始化失败",
            "error": str(exc),
            "completed_at": datetime.utcnow().isoformat(),
        }


@app.middleware("http")
async def add_private_network_access_header(request: Request, call_next):
    response = await call_next(request)
    if request.headers.get("access-control-request-private-network") == "true":
        response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


@app.exception_handler(APIError)
async def handle_api_error(_request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.payload.model_dump(mode="json"))


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _session_to_create_response(session: Session) -> CreateSessionResponse:
    return CreateSessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        user_query=session.user_query,
        platform=session.platform,
        mode=session.mode,
        stage=session.stage.value,
        lifecycle_state=session.lifecycle_state.value,
        alive_until=_iso(session.alive_until),
        purge_after=_iso(session.purge_after),
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
    )


def _workspace_to_response(workspace) -> V2WorkspaceResponse:
    return V2WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        slug=workspace.slug,
        timezone=workspace.timezone,
        status=workspace.status,
        created_at=workspace.created_at.isoformat(),
        updated_at=workspace.updated_at.isoformat(),
    )


def _brand_to_response(brand) -> V2BrandResponse:
    return V2BrandResponse(
        id=brand.id,
        workspace_id=brand.workspace_id,
        name=brand.name,
        category=brand.category,
        stage=brand.stage,
        target_audience=brand.target_audience,
        brand_voice=brand.brand_voice,
        goals=brand.goals,
        created_at=brand.created_at.isoformat(),
        updated_at=brand.updated_at.isoformat(),
    )


def _brand_channel_to_response(channel) -> V2BrandChannelResponse:
    return V2BrandChannelResponse(
        id=channel.id,
        workspace_id=channel.workspace_id,
        brand_id=channel.brand_id,
        platform=channel.platform,
        external_account_id=channel.external_account_id,
        account_name=channel.account_name,
        profile_url=channel.profile_url,
        status=channel.status,
        metadata=channel.metadata,
        created_at=channel.created_at.isoformat(),
        updated_at=channel.updated_at.isoformat(),
    )


def _discovery_to_response(result) -> V2DiscoveryTaskResponse:
    return V2DiscoveryTaskResponse(
        task_id=result.task_snapshot.task_id,
        topic=result.task_snapshot.topic,
        query_generation_version=result.query_generation_version,
        query_generation_source=result.query_generation_source,
        token=result.capture_token,
        expires_at=_iso(result.capture_token_expires_at),
        expanded_queries=[
            {
                "query_id": query.query_id,
                "category": query.category,
                "query_text": query.query_text,
                "order": query.order,
            }
            for query in result.task_snapshot.expanded_queries
        ],
        hotspot_status=result.hotspot_snapshot.status,
        hotspot_generated_at=_iso(result.hotspot_snapshot.generated_at),
        hotspot_error_message=result.hotspot_snapshot.error_message,
        hotspots=[
            {
                "metric": hotspot_list.metric,
                "items": [
                    {
                        "note_id": item.note_id,
                        "title": item.title,
                        "source_url": item.source_url,
                        "author": item.author,
                        "excerpt": item.excerpt,
                        "likes": item.likes,
                        "comments": item.comments,
                        "collections": item.collections,
                        "query_sources": item.query_sources,
                    }
                    for item in hotspot_list.items
                ],
            }
            for hotspot_list in result.hotspot_snapshot.lists
        ],
    )


def _policy_to_response(policy) -> V2BrandPolicyConfigResponse:
    return V2BrandPolicyConfigResponse(
        id=policy.id,
        workspace_id=policy.workspace_id,
        brand_id=policy.brand_id,
        policy_name=policy.policy_name,
        policy_version=policy.policy_version,
        hard_filter_rules=policy.hard_filter_rules,
        brand_fit_rules=policy.brand_fit_rules,
        exploration_preset_override=policy.exploration_preset_override,
        topic_type_targets=policy.topic_type_targets,
        is_active=policy.is_active,
        created_at=policy.created_at.isoformat(),
        updated_at=policy.updated_at.isoformat(),
    )


def _state_snapshot_to_response(snapshot) -> V2BrandStateSnapshotResponse:
    return V2BrandStateSnapshotResponse(
        id=snapshot.id,
        workspace_id=snapshot.workspace_id,
        brand_id=snapshot.brand_id,
        state_version=snapshot.state_version,
        stage=snapshot.stage,
        state_features=snapshot.state_features,
        source_type=snapshot.source_type,
        source_version=snapshot.source_version,
        computed_at=snapshot.computed_at.isoformat(),
        valid_from=snapshot.valid_from.isoformat(),
        valid_to=snapshot.valid_to.isoformat() if snapshot.valid_to else None,
        created_at=snapshot.created_at.isoformat(),
    )


def _ingestion_result_to_response(result) -> V2IngestionAcceptedResponse:
    return V2IngestionAcceptedResponse(
        ingestion_run_id=result.ingestion_run_id,
        entry_type=result.entry_type,
        status=result.status,
        accepted_row_count=result.accepted_row_count,
        imported_item_count=result.imported_item_count,
        deduped_item_count=result.deduped_item_count,
    )


def _ingestion_receipt_dict_to_response(payload: dict[str, Any] | None) -> V2IngestionAcceptedResponse | None:
    if payload is None:
        return None
    return V2IngestionAcceptedResponse(
        ingestion_run_id=str(payload.get("ingestion_run_id") or ""),
        entry_type=str(payload.get("entry_type") or "source_sync"),
        status=str(payload.get("status") or ""),
        accepted_row_count=payload.get("accepted_row_count"),
        imported_item_count=payload.get("imported_item_count"),
        deduped_item_count=payload.get("deduped_item_count"),
    )


def _ingestion_run_to_response(run) -> V2IngestionRunResponse:
    return V2IngestionRunResponse(
        ingestion_run_id=run.id,
        entry_type=run.entry_type,
        source_type=run.source_type,
        source_adapter=run.source_adapter,
        status=run.status,
        stats=run.stats,
        error_summary=run.error_summary,
        started_at=run.started_at.isoformat() if run.started_at else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        created_at=run.created_at.isoformat(),
    )


def _extension_capture_session_to_response(result) -> V2ExtensionCaptureSessionResponse:
    return V2ExtensionCaptureSessionResponse(
        capture_session_id=result.capture_session_id,
        capture_token=getattr(result, "capture_token", None),
        status=result.status,
        expires_at=result.expires_at.isoformat(),
        captured_at=result.captured_at.isoformat() if result.captured_at else None,
        preview_payload=result.preview_payload,
        ingestion_receipt=_ingestion_receipt_dict_to_response(result.ingestion_receipt),
        error_summary=result.error_summary,
    )


def _data_import_preview_to_response(result) -> V2DataImportPreviewResponse:
    return V2DataImportPreviewResponse(
        preview_id=result.preview_id,
        file_name=result.file_name,
        status=result.status,
        uploaded_at=result.uploaded_at.isoformat(),
        parsed_row_count=result.parsed_row_count,
        preview_payload=result.preview_payload,
        ingestion_receipt=_ingestion_receipt_dict_to_response(result.ingestion_receipt),
        field_errors=result.field_errors,
        error_summary=result.error_summary,
    )


def _topic_pool_refresh_to_response(result) -> V2TopicPoolRefreshResponse:
    return V2TopicPoolRefreshResponse(
        refresh_run_id=result.refresh_run_id,
        status=result.status,
        generated_item_count=result.generated_item_count,
        archived_item_count=result.archived_item_count,
        total_candidate_count=result.total_candidate_count,
        refreshed_at=result.refreshed_at.isoformat(),
    )


def _topic_pool_list_to_response(result) -> V2TopicPoolListResponse:
    return V2TopicPoolListResponse(
        brand=V2TopicPoolBrandSummary(
            id=result.brand_id,
            name=result.brand_name,
            stage=result.brand_stage,
            target_audience=result.target_audience,
        ),
        stats=V2TopicPoolStatsResponse(
            total_candidate_count=result.total_candidate_count,
            best_score=result.best_score,
            last_refresh_at=result.last_refresh_at.isoformat() if result.last_refresh_at else None,
        ),
        items=[
            V2TopicPoolItemResponse(
                id=item.id,
                topic_id=item.topic_id,
                display_name=item.display_name,
                normalized_name=item.normalized_name,
                topic_type=item.topic_type,
                title=item.title,
                angle=item.angle,
                hypothesis=item.hypothesis,
                evidence_summary=item.evidence_summary,
                source_agent=item.source_agent,
                status=item.status,
                final_score=item.final_score,
                score_breakdown=item.score_breakdown,
                evidence_provenance=item.evidence_provenance,
                updated_at=item.updated_at.isoformat(),
            )
            for item in result.items
        ],
    )


def _decision_selection_to_response(item) -> V2DecisionBatchItemResponse:
    return V2DecisionBatchItemResponse(
        slot_index=item.slot_index,
        topic_pool_item_id=item.topic_pool_item_id,
        decision_event_id=item.decision_event_id,
        title=item.edited_title or item.title,
        angle=item.edited_angle or item.angle,
        hypothesis=item.edited_hypothesis or item.hypothesis,
        score=item.score,
        topic_type=item.topic_type,
        decision_mode=item.decision_mode,
        review_status=item.review_status,
        reason_codes=item.reason_codes,
        review_notes=item.review_notes,
        reviewed_at=None,
    )


def _decision_run_to_response(result) -> V2DecisionRunResponse:
    return V2DecisionRunResponse(
        batch_id=result.batch_id,
        workspace_id=result.workspace_id,
        brand_id=result.brand_id,
        brand_state_snapshot_id=result.brand_state_snapshot_id,
        brand_policy_config_id=result.brand_policy_config_id,
        objective=result.objective,
        exploration_mode=result.exploration_mode,
        requested_slot_count=result.requested_slot_count,
        candidate_count=result.candidate_count,
        chosen_count=result.chosen_count,
        created_at=result.created_at.isoformat(),
        items=[_decision_selection_to_response(item) for item in result.items],
    )


def _decision_detail_to_response(result) -> V2DecisionBatchDetailResponse:
    return V2DecisionBatchDetailResponse(
        batch_id=result.batch_id,
        workspace_id=result.workspace_id,
        brand_id=result.brand_id,
        brand_state_snapshot_id=result.brand_state_snapshot_id,
        brand_policy_config_id=result.brand_policy_config_id,
        objective=result.objective,
        exploration_mode=result.exploration_mode,
        requested_slot_count=result.requested_slot_count,
        candidate_count=result.candidate_count,
        chosen_count=result.chosen_count,
        created_at=result.created_at.isoformat(),
        items=[_decision_selection_to_response(item) for item in result.items],
    )


def _decision_review_to_response(result) -> V2DecisionBatchItemReviewResponse:
    return V2DecisionBatchItemReviewResponse(
        batch_id=result.batch_id,
        slot_index=result.slot_index,
        topic_pool_item_id=result.topic_pool_item_id,
        decision_event_id=result.decision_event_id,
        review_status=result.review_status,
        title=result.title,
        angle=result.angle,
        hypothesis=result.hypothesis,
        score=result.score,
        reason_codes=result.reason_codes,
        review_notes=result.review_notes,
        reviewed_at=result.reviewed_at.isoformat() if result.reviewed_at else None,
    )


def _publish_record_to_response(record) -> V2PublishRecordResponse:
    return V2PublishRecordResponse(
        publish_record_id=record.publish_record_id,
        brand_id=record.brand_id,
        channel_id=record.channel_id,
        channel_label=record.channel_label,
        title=record.title,
        topic_pool_item_id=record.topic_pool_item_id,
        decision_event_id=record.decision_event_id,
        decision_batch_id=record.decision_batch_id,
        decision_source=record.decision_source,
        publish_status=record.publish_status,
        published_at=record.published_at.isoformat() if record.published_at else None,
        creative_variant=record.creative_variant,
        created_at=record.created_at.isoformat(),
    )


def _performance_snapshot_to_response(snapshot) -> V2PerformanceSnapshotResponse:
    return V2PerformanceSnapshotResponse(
        performance_snapshot_id=snapshot.performance_snapshot_id,
        publish_record_id=snapshot.publish_record_id,
        publish_title=snapshot.publish_title,
        observation_window_hours=snapshot.observation_window_hours,
        snapshot_at=snapshot.snapshot_at.isoformat(),
        reward_version=snapshot.reward_version,
        impressions=snapshot.impressions,
        clicks=snapshot.clicks,
        engagement_rate=snapshot.engagement_rate,
        conversion_proxy_label=snapshot.conversion_proxy_label,
        short_term_reward=snapshot.short_term_reward,
        composite_reward=snapshot.composite_reward,
    )


def _evaluation_run_to_response(result) -> V2EvaluationRunResponse:
    return V2EvaluationRunResponse(
        evaluation_run_id=result.evaluation_run_id,
        brand_id=result.brand_id,
        evaluation_type=result.evaluation_type,
        policy_name=result.policy_name,
        policy_version=result.policy_version,
        status=result.status,
        sample_count=result.sample_count,
        summary=result.summary,
        slices=[
            V2EvaluationRunSliceResponse(
                slice_key=item.slice_key,
                slice_value=item.slice_value,
                sample_count=item.sample_count,
                metrics=item.metrics,
            )
            for item in result.slices
        ],
        created_at=result.created_at.isoformat(),
        finished_at=result.finished_at.isoformat() if result.finished_at else None,
    )


def _get_v2_master_data_service(request: Request) -> MasterDataService:
    service = getattr(request.app.state, "v2_master_data_service", None)
    if service is None:
        raise APIError(
            status_code=500,
            error_code="MASTER_DATA_SERVICE_UNAVAILABLE",
            error_message="V2 master data service is not initialized",
            suggested_action="请通过应用 lifespan 初始化 V2 master data service 后重试",
        )
    return service


def _get_v2_ingestion_service(request: Request) -> IngestionService:
    service = getattr(request.app.state, "v2_ingestion_service", None)
    if service is None:
        raise APIError(
            status_code=500,
            error_code="INGESTION_SERVICE_UNAVAILABLE",
            error_message="V2 ingestion service is not initialized",
            suggested_action="请通过应用 lifespan 初始化 V2 ingestion service 后重试",
        )
    return service


def _get_v2_discovery_service(request: Request) -> DiscoveryService:
    service = getattr(request.app.state, "v2_discovery_service", None)
    if service is None:
        raise APIError(
            status_code=500,
            error_code="DISCOVERY_SERVICE_UNAVAILABLE",
            error_message="V2 discovery service is not initialized",
            suggested_action="请通过应用 lifespan 初始化 V2 discovery service 后重试",
        )
    return service


def _get_v2_topic_pool_service(request: Request) -> TopicPoolService:
    service = getattr(request.app.state, "v2_topic_pool_service", None)
    if service is None:
        raise APIError(
            status_code=500,
            error_code="TOPIC_POOL_SERVICE_UNAVAILABLE",
            error_message="V2 topic pool service is not initialized",
            suggested_action="请通过应用 lifespan 初始化 V2 topic pool service 后重试",
        )
    return service


def _get_v2_decision_service(request: Request) -> DecisionService:
    service = getattr(request.app.state, "v2_decision_service", None)
    if service is None:
        raise APIError(
            status_code=500,
            error_code="DECISION_SERVICE_UNAVAILABLE",
            error_message="V2 decision service is not initialized",
            suggested_action="请通过应用 lifespan 初始化 V2 decision service 后重试",
        )
    return service


def _get_v2_feedback_service(request: Request) -> FeedbackService:
    service = getattr(request.app.state, "v2_feedback_service", None)
    if service is None:
        raise APIError(
            status_code=500,
            error_code="FEEDBACK_SERVICE_UNAVAILABLE",
            error_message="V2 feedback service is not initialized",
            suggested_action="请通过应用 lifespan 初始化 V2 feedback service 后重试",
        )
    return service


def _get_thread_store(request: Request) -> ThreadStore:
    store = getattr(request.app.state, "thread_store", None)
    if store is None:
        raise APIError(
            status_code=500,
            error_code="THREAD_STORE_UNAVAILABLE",
            error_message="Thread store is not initialized",
            suggested_action="请通过应用 lifespan 初始化 thread store 后重试",
        )
    return store


def _get_job_store(request: Request) -> JobStore:
    store = getattr(request.app.state, "job_store", None)
    if store is None:
        raise APIError(
            status_code=500,
            error_code="JOB_STORE_UNAVAILABLE",
            error_message="Job store is not initialized",
            suggested_action="请通过应用 lifespan 初始化 job store 后重试",
        )
    return store


async def _generate_thread_title(user_message: str) -> str:
    """Use Haiku to summarise the user's first message into a short thread title.
    Falls back to a 10-char hard truncation on any error."""
    import anthropic as _anthropic

    def _sync_call() -> str:
        client = _anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            temperature=0.3,
            system=(
                "你是一个对话标题生成器。"
                "根据用户消息，用10个字以内的中文短语概括其核心需求，作为对话标题。"
                "直接输出标题文字，不加引号、标点或任何解释。"
            ),
            messages=[{"role": "user", "content": user_message[:200]}],
        )
        return response.content[0].text.strip()

    try:
        return await asyncio.to_thread(_sync_call)
    except Exception:
        raw = user_message.strip().replace("\n", " ")
        return raw[:10] + "…" if len(raw) > 10 else raw


def _resolve_workspace_principal_or_error(request: Request):
    try:
        return resolve_workspace_principal(request.headers)
    except WorkspaceAuthError as exc:
        raise APIError(
            status_code=401,
            error_code="WORKSPACE_AUTH_REQUIRED",
            error_message=str(exc),
            suggested_action="请提供 V2 workspace 作用域请求头后重试",
        ) from exc


def _raise_master_data_api_error(exc: Exception) -> None:
    if isinstance(exc, MasterDataNotFoundError):
        raise APIError(
            status_code=404,
            error_code="MASTER_DATA_NOT_FOUND",
            error_message=str(exc),
            suggested_action="请先创建所需的 workspace 或 brand",
        ) from exc
    if isinstance(exc, MasterDataScopeError):
        raise APIError(
            status_code=403,
            error_code="WORKSPACE_SCOPE_MISMATCH",
            error_message=str(exc),
            suggested_action="请确认请求头中的 workspace 与目标 brand 属于同一空间",
        ) from exc
    if isinstance(exc, MasterDataConflictError):
        raise APIError(
            status_code=409,
            error_code="MASTER_DATA_CONFLICT",
            error_message=str(exc),
            suggested_action="请检查 slug 或 active config 是否重复",
        ) from exc
    if isinstance(exc, MasterDataValidationError):
        raise APIError(
            status_code=422,
            error_code="INVALID_MASTER_DATA_PAYLOAD",
            error_message=str(exc),
            suggested_action="请修正请求体后重试",
        ) from exc
    if isinstance(exc, MasterDataInvariantError):
        raise APIError(
            status_code=500,
            error_code="MASTER_DATA_INVARIANT_VIOLATION",
            error_message=str(exc),
            suggested_action="请修复重复 active 配置后重试",
        ) from exc
    if isinstance(exc, MasterDataError):
        raise APIError(
            status_code=500,
            error_code="MASTER_DATA_ERROR",
            error_message=str(exc),
            suggested_action="请检查 master data 服务状态后重试",
        ) from exc
    raise exc


def _raise_discovery_api_error(exc: Exception) -> None:
    if isinstance(exc, DiscoveryNotFoundError):
        raise APIError(
            status_code=404,
            error_code="DISCOVERY_TASK_NOT_FOUND",
            error_message=str(exc),
            suggested_action="请重新创建搜索任务后重试",
        ) from exc
    if isinstance(exc, DiscoveryScopeError):
        raise APIError(
            status_code=403,
            error_code="WORKSPACE_SCOPE_MISMATCH",
            error_message=str(exc),
            suggested_action="请确认任务属于当前品牌后重试",
        ) from exc
    if isinstance(exc, DiscoveryValidationError):
        raise APIError(
            status_code=422,
            error_code="INVALID_DISCOVERY_PAYLOAD",
            error_message=str(exc),
            suggested_action="请修正搜索主题或拓展搜索词后重试",
        ) from exc
    if isinstance(exc, DiscoveryQueryExpansionError):
        raise APIError(
            status_code=503,
            error_code="DISCOVERY_QUERY_EXPANSION_UNAVAILABLE",
            error_message=str(exc),
            suggested_action="请检查 LLM 配置或稍后重试",
        ) from exc
    if isinstance(exc, DiscoveryError):
        raise APIError(
            status_code=500,
            error_code="DISCOVERY_ERROR",
            error_message=str(exc),
            suggested_action="请检查搜索观察服务状态后重试",
        ) from exc
    raise exc


def _raise_ingestion_api_error(exc: Exception) -> None:
    if isinstance(exc, IngestionValidationError):
        raise APIError(
            status_code=422,
            error_code="INVALID_INGESTION_PAYLOAD",
            error_message=str(exc),
            suggested_action="请修正 ingestion 请求体后重试",
        ) from exc
    if isinstance(exc, IngestionError):
        raise APIError(
            status_code=500,
            error_code="INGESTION_ERROR",
            error_message=str(exc),
            suggested_action="请检查 ingestion 服务状态后重试",
        ) from exc
    raise exc


def _raise_topic_pool_api_error(exc: Exception) -> None:
    if isinstance(exc, TopicPoolValidationError):
        raise APIError(
            status_code=422,
            error_code="INVALID_TOPIC_POOL_PAYLOAD",
            error_message=str(exc),
            suggested_action="请修正 topic pool 请求或上游输入后重试",
        ) from exc
    if isinstance(exc, TopicPoolError):
        raise APIError(
            status_code=500,
            error_code="TOPIC_POOL_ERROR",
            error_message=str(exc),
            suggested_action="请检查 topic pool 服务状态后重试",
        ) from exc
    raise exc


def _raise_decision_api_error(exc: Exception) -> None:
    if isinstance(exc, DecisionNotFoundError):
        raise APIError(
            status_code=404,
            error_code="DECISION_NOT_FOUND",
            error_message=str(exc),
            suggested_action="请确认 batch_id 和 slot_index 是否正确",
        ) from exc
    if isinstance(exc, DecisionValidationError):
        raise APIError(
            status_code=422,
            error_code="INVALID_DECISION_PAYLOAD",
            error_message=str(exc),
            suggested_action="请修正 decision 请求体后重试",
        ) from exc
    if isinstance(exc, DecisionError):
        raise APIError(
            status_code=500,
            error_code="DECISION_ERROR",
            error_message=str(exc),
            suggested_action="请检查 decision 服务状态后重试",
        ) from exc
    raise exc


def _raise_feedback_api_error(exc: Exception) -> None:
    if isinstance(exc, FeedbackNotFoundError):
        raise APIError(
            status_code=404,
            error_code="FEEDBACK_NOT_FOUND",
            error_message=str(exc),
            suggested_action="请确认 publish_record_id 或 evaluation_run_id 是否正确",
        ) from exc
    if isinstance(exc, FeedbackValidationError):
        raise APIError(
            status_code=422,
            error_code="INVALID_FEEDBACK_PAYLOAD",
            error_message=str(exc),
            suggested_action="请修正 publish/performance/evaluation 请求体后重试",
        ) from exc
    if isinstance(exc, FeedbackError):
        raise APIError(
            status_code=500,
            error_code="FEEDBACK_ERROR",
            error_message=str(exc),
            suggested_action="请检查 feedback 服务状态后重试",
        ) from exc
    raise exc


def _event_record_to_schema(record: SessionEventRecord) -> SessionEvent:
    payload = SessionEventPayload.model_validate(record.payload)
    return SessionEvent(
        event_id=record.event_id,
        event_name=record.event_name,
        session_id=record.session_id,
        job_id=record.job_id,
        stage=record.stage,
        timestamp=datetime.fromisoformat(str(record.created_at)),
        payload=payload,
    )


def _resolve_budget_snapshot(session: Session) -> dict[str, Any]:
    report = session.similarity_report if isinstance(session.similarity_report, dict) else {}
    token_budget = int(report.get("token_budget") or settings.SESSION_TOKEN_BUDGET)
    token_used = int(report.get("token_used") or 0)
    budget_remaining = int(report.get("budget_remaining") or max(0, token_budget - token_used))
    budget_degraded = bool(report.get("budget_degraded", False))
    return {
        "token_used": token_used,
        "token_budget": token_budget,
        "budget_remaining": budget_remaining,
        "budget_degraded": budget_degraded,
    }


def _parse_last_event_id(raw_value: Optional[str]) -> Optional[int]:
    if raw_value is None:
        return None
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise APIError(
            status_code=400,
            error_code="INVALID_LAST_EVENT_ID",
            error_message="Last-Event-ID 必须是非负整数",
            error_details={"last_event_id": raw_value},
            suggested_action="请使用最近一次持久化事件的 event_id 重新连接",
        ) from exc
    if parsed < 0:
        raise APIError(
            status_code=400,
            error_code="INVALID_LAST_EVENT_ID",
            error_message="Last-Event-ID 必须是非负整数",
            error_details={"last_event_id": raw_value},
            suggested_action="请使用最近一次持久化事件的 event_id 重新连接",
        )
    return parsed


def _format_sse_event(event: SessionEvent, *, include_id: bool = True) -> str:
    lines: list[str] = []
    if include_id:
        lines.append(f"id: {event.event_id}")
    lines.append(f"event: {event.event_name}")
    lines.append(f"data: {event.model_dump_json()}")
    return "\n".join(lines) + "\n\n"


def _serialize_model(model) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _format_workflow_sse_event(event) -> str:
    data = {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "thread_id": event.thread_id,
        "step_id": event.step_id,
        "child_task_id": event.child_task_id,
        "job_id": event.job_id,
        "event_type": event.event_type,
        "event_level": event.event_level,
        "payload": event.payload_json,
        "created_at": event.created_at.isoformat(),
    }
    return "\n".join(
        [
            f"id: {event.event_id}",
            f"event: {event.event_type}",
            f"data: {json.dumps(data, ensure_ascii=False, default=str)}",
        ]
    ) + "\n\n"


async def _get_active_workflow_job(store: WorkflowStore, active_job_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not active_job_id:
        return None
    assert store._conn is not None
    async with store._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
    ) as cursor:
        if await cursor.fetchone() is None:
            return None
    async with store._conn.execute("SELECT * FROM jobs WHERE id = ?", (active_job_id,)) as cursor:
        row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def _load_workflow_snapshot(
    run_id: str,
    *,
    expected_thread_id: Optional[str] = None,
) -> dict[str, Any]:
    async with WorkflowStore(settings.SQLITE_DB_PATH) as store:
        run = await store.get_run(run_id)
        if run is None:
            raise APIError(
                status_code=404,
                error_code="WORKFLOW_RUN_NOT_FOUND",
                error_message=f"Workflow run {run_id} not found",
                suggested_action="请检查 run_id 是否正确",
            )
        if expected_thread_id is not None and run.thread_id != expected_thread_id:
            raise APIError(
                status_code=409,
                error_code="THREAD_RUN_MISMATCH",
                error_message=f"Workflow run {run_id} does not belong to thread {expected_thread_id}",
                error_details={"run_thread_id": run.thread_id, "requested_thread_id": expected_thread_id},
                suggested_action="请确认 thread_id 与 run_id 是否匹配",
            )
        steps = await store.list_steps(run_id)
        child_tasks = await store.list_child_tasks(run_id)
        constraints = await store.list_constraints(run_id)
        active_job = await _get_active_workflow_job(store, run.active_job_id)
    artifacts = await WorkflowArtifactVersionPolicy(settings.SQLITE_DB_PATH).safe_materialize_run_artifacts(run_id)
    return {
        "run": _serialize_model(run),
        "steps": [_serialize_model(step) for step in steps],
        "child_tasks": [_serialize_model(task) for task in child_tasks],
        "artifacts": artifacts,
        "constraints": [_serialize_model(constraint) for constraint in constraints],
        "active_job": active_job,
    }


def _message_record_from_row(row: dict[str, Any], artifact_refs: Optional[list[dict[str, Any]]] = None) -> CreatorMessageRecord:
    refs = artifact_refs
    if refs is None:
        try:
            refs = json.loads(row.get("artifact_refs_json") or "[]")
        except json.JSONDecodeError:
            refs = []
    return CreatorMessageRecord(
        message_id=row["id"],
        thread_id=row["thread_id"],
        role=row["role"],
        text=row["text"],
        message_type=row.get("message_type") or "text",
        intent=row.get("intent"),
        linked_session_id=row.get("linked_session_id"),
        linked_job_id=row.get("linked_job_id"),
        run_id=row.get("run_id"),
        artifact_refs=refs,
        created_at=row["created_at"],
    )


def _thread_detail_from_row(row: dict[str, Any]) -> CreatorThreadDetail:
    return CreatorThreadDetail(
        thread_id=row["id"],
        workspace_id=row.get("workspace_id"),
        brand_id=row.get("brand_id"),
        title=row["title"],
        status=row["status"],
        active_workflow_session_id=row["active_workflow_session_id"],
        active_job_id=row["active_job_id"],
        active_run_id=row.get("active_run_id"),
        accepted_at=row["accepted_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _hydrate_timeline_artifact_refs(messages: list[dict[str, Any]]) -> list[CreatorMessageRecord]:
    run_ids = sorted({m.get("run_id") for m in messages if m.get("run_id")})
    artifact_by_id: dict[str, dict[str, Any]] = {}
    if run_ids:
        policy = WorkflowArtifactVersionPolicy(settings.SQLITE_DB_PATH)
        for run_id in run_ids:
            for payload in await policy.safe_materialize_run_artifacts(run_id):
                artifact_by_id[payload["artifact_id"]] = payload

    records: list[CreatorMessageRecord] = []
    for message in messages:
        try:
            raw_refs = json.loads(message.get("artifact_refs_json") or "[]")
        except json.JSONDecodeError:
            raw_refs = []
        hydrated_refs: list[dict[str, Any]] = []
        for ref in raw_refs:
            artifact = artifact_by_id.get(ref.get("artifact_id"))
            hydrated_refs.append({**ref, "artifact": artifact} if artifact is not None else ref)
        records.append(_message_record_from_row(message, hydrated_refs))
    return records


def _note_from_payload(payload: dict[str, Any], *, fallback_id: str) -> Optional[dict[str, Any]]:
    return WorkflowArtifactVersionPolicy.note_from_payload(payload, fallback_id=fallback_id)


def _notes_from_final_result(payload: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    candidates = payload.get("notes") or payload.get("generated_notes") or []
    notes: list[dict[str, Any]] = []
    for index, item in enumerate(candidates):
        if not isinstance(item, dict):
            continue
        nested_payload = item.get("payload_json") if isinstance(item.get("payload_json"), dict) else item
        note = _note_from_payload(nested_payload, fallback_id=str(item.get("artifact_id") or f"note-{index}"))
        if note is not None:
            notes.append(note)
    return notes


async def _ensure_publish_candidate_artifacts(
    *,
    thread_id: str,
    run_id: str,
    workspace_id: Optional[str],
    brand_id: Optional[str],
    notes: list[dict[str, Any]],
) -> int:
    async with WorkflowStore(settings.SQLITE_DB_PATH) as store:
        existing = await store.list_artifacts(run_id)
        existing_note_ids = {
            str((artifact.payload_json or {}).get("note_id"))
            for artifact in existing
            if artifact.artifact_type == WorkflowArtifactType.PUBLISH_CANDIDATE
        }
        next_version = max((artifact.artifact_version for artifact in existing), default=0) + 1
        created_count = 0
        for note in notes:
            note_id = str(note.get("note_id") or "")
            if not note_id or note_id in existing_note_ids:
                continue
            await store.create_artifact(
                run_id=run_id,
                thread_id=thread_id,
                artifact_type=WorkflowArtifactType.PUBLISH_CANDIDATE,
                artifact_version=next_version,
                payload={
                    "note_id": note_id,
                    "workspace_id": workspace_id,
                    "brand_id": brand_id,
                    "title": note.get("title") or "未命名笔记",
                    "content": note.get("content") or "",
                    "tags": note.get("tags") or [],
                    "topic_type": note.get("topic_type") or "方法",
                    "core_hypothesis": note.get("core_hypothesis") or "认可笔记可沉淀为后续创作选题",
                    "score": float(note.get("score") or 0.0),
                    "score_type": "predicted",
                    "source": "publish_candidate",
                },
                summary_text=str(note.get("title") or note_id),
            )
            existing_note_ids.add(note_id)
            next_version += 1
            created_count += 1
        return len(existing_note_ids) if existing_note_ids else created_count


async def _list_publish_candidate_artifacts(
    *,
    workspace_id: Optional[str] = None,
    brand_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    async with WorkflowStore(settings.SQLITE_DB_PATH) as store:
        artifacts = await store.list_artifacts_by_type(WorkflowArtifactType.PUBLISH_CANDIDATE)
    rows: list[dict[str, Any]] = []
    for artifact in artifacts:
        payload = artifact.payload_json or {}
        if workspace_id is not None and payload.get("workspace_id") != workspace_id:
            continue
        if brand_id is not None and payload.get("brand_id") != brand_id:
            continue
        if thread_id is not None and artifact.thread_id != thread_id:
            continue
        if run_id is not None and artifact.run_id != run_id:
            continue
        rows.append(
            {
                "candidate_id": artifact.artifact_id,
                "workspace_id": payload.get("workspace_id"),
                "brand_id": payload.get("brand_id"),
                "thread_id": artifact.thread_id,
                "session_id": artifact.run_id,
                "note_id": str(payload.get("note_id") or artifact.artifact_id),
                "title": str(payload.get("title") or "未命名笔记"),
                "content": str(payload.get("content") or ""),
                "tags": payload.get("tags") if isinstance(payload.get("tags"), list) else [],
                "topic_type": str(payload.get("topic_type") or "方法"),
                "core_hypothesis": str(payload.get("core_hypothesis") or "认可笔记可沉淀为后续创作选题"),
                "score": float(payload.get("score") or 0.0),
                "score_type": str(payload.get("score_type") or "predicted"),
                "source": str(payload.get("source") or "publish_candidate"),
                "created_at": artifact.created_at.isoformat(),
            }
        )
    return rows


async def _load_workflow_result(
    thread: dict[str, Any],
    *,
    publishable_only: bool = False,
) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    run_id = thread.get("active_run_id")
    if not run_id:
        return None, [], []
    policy = WorkflowArtifactVersionPolicy(settings.SQLITE_DB_PATH)
    artifacts = await policy.safe_materialize_run_artifacts(run_id)
    strategy_artifacts = [a for a in artifacts if a["artifact_type"] == "strategy"]
    final_artifacts = [a for a in artifacts if a["artifact_type"] == "final_result"]
    note_artifacts = [a for a in artifacts if a["artifact_type"] == "generated_note"]
    strategy = (
        strategy_artifacts[-1].get("materialized_payload_json") or strategy_artifacts[-1].get("payload_json")
        if strategy_artifacts
        else None
    )
    notes = policy.select_publishable_notes(artifacts)
    if not notes and not publishable_only:
        for artifact in note_artifacts:
            if artifact.get("status") in WorkflowArtifactVersionPolicy.NON_PUBLISHABLE_STATUSES:
                continue
            note = _note_from_payload(
                artifact.get("materialized_payload_json") or artifact.get("payload_json") or {},
                fallback_id=artifact["artifact_id"],
            )
            if note is not None:
                notes.append(note)
    result_refs = final_artifacts[-1:] or note_artifacts or strategy_artifacts
    artifact_refs = [
        {
            "artifact_id": artifact["artifact_id"],
            "artifact_type": artifact["artifact_type"],
            "artifact_version": artifact["artifact_version"],
        }
        for artifact in result_refs
    ]
    return strategy, notes, artifact_refs


def _resolve_workflow_event_cursor(
    *,
    after_event_id: Optional[int],
    last_event_id: Optional[str],
) -> Optional[int]:
    if after_event_id is not None:
        if after_event_id < 0:
            raise APIError(
                status_code=400,
                error_code="INVALID_AFTER_EVENT_ID",
                error_message="after_event_id 必须是非负整数",
                error_details={"after_event_id": after_event_id},
                suggested_action="请使用最近一次持久化事件的 event_id 重新连接",
            )
        return after_event_id
    return _parse_last_event_id(last_event_id)


async def _workflow_event_stream(
    request: Request,
    *,
    run_id: str,
    after_event_id: Optional[int],
) -> AsyncIterator[str]:
    async with WorkflowStore(settings.SQLITE_DB_PATH) as store:
        last_sent_event_id = after_event_id or 0
        replay_events = await store.list_events(run_id, after_event_id=after_event_id)
        for event in replay_events:
            last_sent_event_id = event.event_id
            yield _format_workflow_sse_event(event)

        heartbeat_deadline = monotonic() + settings.SSE_HEARTBEAT_SECONDS
        poll_interval = min(0.2, settings.SSE_HEARTBEAT_SECONDS)
        while not await request.is_disconnected():
            live_events = await store.list_events(run_id, after_event_id=last_sent_event_id)
            if live_events:
                for event in live_events:
                    last_sent_event_id = event.event_id
                    yield _format_workflow_sse_event(event)
                heartbeat_deadline = monotonic() + settings.SSE_HEARTBEAT_SECONDS
                continue

            now = monotonic()
            if now >= heartbeat_deadline:
                yield ": heartbeat\n\n"
                heartbeat_deadline = monotonic() + settings.SSE_HEARTBEAT_SECONDS
                continue

            await asyncio.sleep(min(poll_interval, heartbeat_deadline - now))


async def _load_session_or_error(
    session_id: str,
    *,
    allow_frozen: bool = True,
    allow_purged: bool = False,
) -> Session:
    async with SessionManager(settings.SQLITE_DB_PATH) as session_manager:
        session = await session_manager.get_session(session_id)
    if session is None:
        raise APIError(
            status_code=404,
            error_code="SESSION_NOT_FOUND",
            error_message="会话不存在",
            suggested_action="请先创建会话",
        )
    if session.lifecycle_state == SessionLifecycleState.PURGED and not allow_purged:
        raise APIError(
            status_code=410,
            error_code="SESSION_PURGED",
            error_message="会话已被清理，无法恢复",
            suggested_action="请重新创建会话",
        )
    if session.lifecycle_state == SessionLifecycleState.FROZEN and not allow_frozen:
        raise APIError(
            status_code=423,
            error_code="SESSION_FROZEN",
            error_message="会话已冻结，请先调用 /resume",
            suggested_action="调用 /sessions/{id}/resume 恢复会话",
        )
    return session


def _ensure_stage(session: Session, *, expected: SessionStage, message: str) -> None:
    if session.stage != expected:
        raise APIError(
            status_code=409,
            error_code="INVALID_STAGE",
            error_message=message,
            error_details={"current_stage": session.stage.value, "expected_stage": expected.value},
            suggested_action=f"当前阶段为 {session.stage.value}",
        )


def _assert_not_in_cooldown(session: Session) -> None:
    if session.spider_cooldown_until and datetime.utcnow() < session.spider_cooldown_until:
        raise APIError(
            status_code=429,
            error_code="SPIDER_COOLDOWN_ACTIVE",
            error_message="Spider 正处于冷却窗口，请稍后重试",
            error_details={"spider_cooldown_until": session.spider_cooldown_until.isoformat()},
            retryable=True,
            suggested_action="等待冷却窗口结束后重试",
        )


async def _build_session_status(session: Session) -> SessionStatusResponse:
    async with JobStore(settings.SQLITE_DB_PATH) as store:
        latest_job = await store.get_latest_job_for_session(session.session_id)
    budget = _resolve_budget_snapshot(session)
    return SessionStatusResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        stage=session.stage.value,
        lifecycle_state=session.lifecycle_state.value,
        alive_until=_iso(session.alive_until),
        spider_cooldown_until=_iso(session.spider_cooldown_until),
        purge_after=_iso(session.purge_after),
        job_status=latest_job.status if latest_job else None,
        current_job_id=latest_job.id if latest_job else None,
        token_used=budget["token_used"],
        token_budget=budget["token_budget"],
        budget_remaining=budget["budget_remaining"],
        budget_degraded=budget["budget_degraded"],
        reindex_state=session.reindex_state,
        reindex_attempts=session.reindex_attempts,
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
        error=session.error.message if session.error else None,
    )


async def _enqueue_with_stage(
    *,
    session_id: str,
    job_type: str,
    next_stage: SessionStage,
    payload: Optional[dict[str, Any]],
    idempotency_key: Optional[str],
    previous_stage: SessionStage,
) -> EnqueueResponse:
    async with SessionManager(settings.SQLITE_DB_PATH) as session_manager:
        await session_manager.touch_user_activity(session_id)
        await session_manager.update_session(session_id, stage=next_stage)
    log_event(
        _logger,
        event_name="stage_changed",
        level="info",
        component="api",
        session_id=session_id,
        stage=next_stage.value,
        from_stage=previous_stage.value,
        to_stage=next_stage.value,
        job_type=job_type,
    )

    async with JobStore(settings.SQLITE_DB_PATH) as store:
        job, created = await store.enqueue(
            session_id=session_id,
            job_type=job_type,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        if created:
            await store.append_session_event(
                session_id=session_id,
                job_id=job.id,
                event_name="stage_changed",
                stage=next_stage.value,
                payload={
                    "message": "正在准备笔记生成任务..." if job_type == "generate" else "策略分析任务已就绪，等待执行...",
                    "progress": 0,
                    "error_code": None,
                    "details": {"to_stage": next_stage.value, "job_status": job.status},
                },
            )
    return EnqueueResponse(
        session_id=session_id,
        stage=next_stage.value,
        job_id=job.id,
        job_status=job.status,
    )


async def _try_replay_idempotent_enqueue(
    *,
    session: Session,
    job_type: str,
    next_stage: SessionStage,
    idempotency_key: Optional[str],
) -> Optional[EnqueueResponse]:
    del next_stage
    if not idempotency_key:
        return None

    async with JobStore(settings.SQLITE_DB_PATH) as store:
        job = await store.get_job_by_idempotency(
            session_id=session.session_id,
            job_type=job_type,
            idempotency_key=idempotency_key,
        )
    if job is None:
        return None

    async with SessionManager(settings.SQLITE_DB_PATH) as session_manager:
        await session_manager.touch_user_activity(session.session_id)

    return EnqueueResponse(
        session_id=session.session_id,
        stage=session.stage.value,
        job_id=job.id,
        job_status=job.status,
    )


@app.get("/workspaces/default", response_model=V2DefaultWorkspaceResponse)
async def get_default_workspace() -> V2DefaultWorkspaceResponse:
    """Return the default workspace identity so the frontend needs no configuration."""
    return V2DefaultWorkspaceResponse(
        workspace_id=DEFAULT_WORKSPACE_ID,
        user_id=DEFAULT_USER_ID,
    )


@app.post("/workspaces", status_code=status.HTTP_201_CREATED, response_model=V2WorkspaceResponse)
async def create_workspace_v2(payload: V2WorkspaceCreateRequest, request: Request) -> V2WorkspaceResponse:
    service = _get_v2_master_data_service(request)
    try:
        workspace = service.create_workspace(
            name=payload.name,
            slug=payload.slug,
            timezone=payload.timezone,
        )
    except Exception as exc:  # pragma: no cover - routed through deterministic mapper
        _raise_master_data_api_error(exc)
    return _workspace_to_response(workspace)


@app.post("/brands", status_code=status.HTTP_201_CREATED, response_model=V2BrandResponse)
async def create_brand_v2(payload: V2BrandCreateRequest, request: Request) -> V2BrandResponse:
    principal = _resolve_workspace_principal_or_error(request)
    service = _get_v2_master_data_service(request)
    try:
        brand = service.create_brand(
            workspace_id=principal.workspace_id,
            name=payload.name,
            category=payload.category,
            stage=payload.stage,
            target_audience=payload.target_audience,
            brand_voice=payload.brand_voice,
            goals=payload.goals,
        )
    except Exception as exc:  # pragma: no cover - routed through deterministic mapper
        _raise_master_data_api_error(exc)
    return _brand_to_response(brand)


@app.get("/brands", response_model=V2BrandListResponse)
async def list_brands_v2(request: Request) -> V2BrandListResponse:
    principal = _resolve_workspace_principal_or_error(request)
    service = _get_v2_master_data_service(request)
    try:
        brands = service.list_brands(workspace_id=principal.workspace_id)
    except Exception as exc:  # pragma: no cover - routed through deterministic mapper
        _raise_master_data_api_error(exc)
    return V2BrandListResponse(items=[_brand_to_response(brand) for brand in brands])


@app.get("/brands/{brand_id}", response_model=V2BrandResponse)
async def get_brand_v2(brand_id: str, request: Request) -> V2BrandResponse:
    principal = _resolve_workspace_principal_or_error(request)
    service = _get_v2_master_data_service(request)
    try:
        brand = service.get_brand(workspace_id=principal.workspace_id, brand_id=brand_id)
    except Exception as exc:  # pragma: no cover - routed through deterministic mapper
        _raise_master_data_api_error(exc)
    return _brand_to_response(brand)


@app.patch("/brands/{brand_id}", response_model=V2BrandResponse)
async def update_brand_v2(
    brand_id: str,
    payload: V2BrandUpdateRequest,
    request: Request,
) -> V2BrandResponse:
    principal = _resolve_workspace_principal_or_error(request)
    service = _get_v2_master_data_service(request)
    try:
        brand = service.update_brand(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            name=payload.name,
            category=payload.category,
            stage=payload.stage,
            target_audience=payload.target_audience,
            brand_voice=payload.brand_voice,
            goals=payload.goals,
        )
    except Exception as exc:  # pragma: no cover - routed through deterministic mapper
        _raise_master_data_api_error(exc)
    return _brand_to_response(brand)


@app.post(
    "/brands/{brand_id}/channels",
    status_code=status.HTTP_201_CREATED,
    response_model=V2BrandChannelResponse,
)
async def create_brand_channel_v2(
    brand_id: str,
    payload: V2BrandChannelCreateRequest,
    request: Request,
) -> V2BrandChannelResponse:
    principal = _resolve_workspace_principal_or_error(request)
    service = _get_v2_master_data_service(request)
    try:
        channel = service.create_brand_channel(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            platform=payload.platform,
            external_account_id=payload.external_account_id,
            account_name=payload.account_name,
            profile_url=payload.profile_url,
            metadata=payload.metadata,
        )
    except Exception as exc:  # pragma: no cover - routed through deterministic mapper
        _raise_master_data_api_error(exc)
    return _brand_channel_to_response(channel)


@app.get(
    "/brands/{brand_id}/channels",
    response_model=V2BrandChannelListResponse,
)
async def list_brand_channels_v2(
    brand_id: str,
    request: Request,
) -> V2BrandChannelListResponse:
    principal = _resolve_workspace_principal_or_error(request)
    service = _get_v2_master_data_service(request)
    try:
        channels = service.list_brand_channels(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
        )
    except Exception as exc:  # pragma: no cover - routed through deterministic mapper
        _raise_master_data_api_error(exc)
    return V2BrandChannelListResponse(items=[_brand_channel_to_response(channel) for channel in channels])


@app.patch(
    "/brands/{brand_id}/channels/{channel_id}",
    response_model=V2BrandChannelResponse,
)
async def update_brand_channel_v2(
    brand_id: str,
    channel_id: str,
    payload: V2BrandChannelUpdateRequest,
    request: Request,
) -> V2BrandChannelResponse:
    principal = _resolve_workspace_principal_or_error(request)
    service = _get_v2_master_data_service(request)
    try:
        channel = service.update_brand_channel(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            channel_id=channel_id,
            external_account_id=payload.external_account_id,
            account_name=payload.account_name,
            profile_url=payload.profile_url,
            metadata=payload.metadata,
        )
    except Exception as exc:  # pragma: no cover - routed through deterministic mapper
        _raise_master_data_api_error(exc)
    return _brand_channel_to_response(channel)


@app.get(
    "/brands/{brand_id}/workspace",
    response_model=V2BrandWorkspaceResponse,
)
async def get_brand_workspace_v2(
    brand_id: str,
    request: Request,
) -> V2BrandWorkspaceResponse:
    principal = _resolve_workspace_principal_or_error(request)
    master_service = _get_v2_master_data_service(request)
    ingestion_service = _get_v2_ingestion_service(request)
    try:
        brand = master_service.get_brand(workspace_id=principal.workspace_id, brand_id=brand_id)
        channels = master_service.list_brand_channels(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
        )
        active_policy = master_service.get_active_brand_policy_config(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
        )
        latest_extension_session = next(
            iter(ingestion_service.list_extension_capture_sessions(brand_id=brand_id, limit=1)),
            None,
        )
        latest_data_import_preview = next(
            iter(ingestion_service.list_data_import_previews(brand_id=brand_id, limit=1)),
            None,
        )
        recent_ingestion_runs = ingestion_service.list_ingestion_runs(brand_id=brand_id, limit=5)
    except Exception as exc:  # pragma: no cover - routed through deterministic mapper
        _raise_master_data_api_error(exc)

    return V2BrandWorkspaceResponse(
        brand=_brand_to_response(brand),
        channels=[_brand_channel_to_response(channel) for channel in channels],
        active_policy=_policy_to_response(active_policy) if active_policy else None,
        latest_extension_capture_session=(
            _extension_capture_session_to_response(latest_extension_session)
            if latest_extension_session
            else None
        ),
        latest_data_import_preview=(
            _data_import_preview_to_response(latest_data_import_preview)
            if latest_data_import_preview
            else None
        ),
        recent_ingestion_runs=[_ingestion_run_to_response(run) for run in recent_ingestion_runs],
    )


@app.put(
    "/brands/{brand_id}/policy-configs/active",
    response_model=V2BrandPolicyConfigResponse,
)
async def replace_active_brand_policy_config_v2(
    brand_id: str,
    payload: V2BrandPolicyConfigUpsertRequest,
    request: Request,
) -> V2BrandPolicyConfigResponse:
    principal = _resolve_workspace_principal_or_error(request)
    service = _get_v2_master_data_service(request)
    try:
        policy = service.replace_active_brand_policy_config(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            policy_name=payload.policy_name,
            policy_version=payload.policy_version,
            hard_filter_rules=payload.hard_filter_rules,
            brand_fit_rules=payload.brand_fit_rules,
            exploration_preset_override=payload.exploration_preset_override,
            topic_type_targets=payload.topic_type_targets,
        )
    except Exception as exc:  # pragma: no cover - routed through deterministic mapper
        _raise_master_data_api_error(exc)
    return _policy_to_response(policy)


@app.get(
    "/brands/{brand_id}/policy-configs/active",
    response_model=V2BrandPolicyConfigResponse,
)
async def get_active_brand_policy_config_v2(
    brand_id: str,
    request: Request,
) -> V2BrandPolicyConfigResponse:
    principal = _resolve_workspace_principal_or_error(request)
    service = _get_v2_master_data_service(request)
    try:
        policy = service.get_active_brand_policy_config(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
        )
    except Exception as exc:  # pragma: no cover - routed through deterministic mapper
        _raise_master_data_api_error(exc)
    if policy is None:
        raise APIError(
            status_code=404,
            error_code="MASTER_DATA_NOT_FOUND",
            error_message=f"Active policy config not found for brand: {brand_id}",
            suggested_action="请先写入 active policy config",
        )
    return _policy_to_response(policy)


@app.post(
    "/brands/{brand_id}/state-snapshots",
    status_code=status.HTTP_201_CREATED,
    response_model=V2BrandStateSnapshotResponse,
)
async def create_brand_state_snapshot_v2(
    brand_id: str,
    payload: V2BrandStateSnapshotCreateRequest,
    request: Request,
) -> V2BrandStateSnapshotResponse:
    principal = _resolve_workspace_principal_or_error(request)
    service = _get_v2_master_data_service(request)
    try:
        snapshot = service.create_brand_state_snapshot(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            state_version=payload.state_version,
            stage=payload.stage,
            state_features=payload.state_features,
            source_version=payload.source_version,
        )
    except Exception as exc:  # pragma: no cover - routed through deterministic mapper
        _raise_master_data_api_error(exc)
    return _state_snapshot_to_response(snapshot)


@app.get(
    "/brands/{brand_id}/state-snapshots",
    response_model=V2BrandStateSnapshotListResponse,
)
async def list_brand_state_snapshots_v2(
    brand_id: str,
    request: Request,
) -> V2BrandStateSnapshotListResponse:
    principal = _resolve_workspace_principal_or_error(request)
    service = _get_v2_master_data_service(request)
    try:
        snapshots = service.list_brand_state_snapshots(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
        )
    except Exception as exc:  # pragma: no cover - routed through deterministic mapper
        _raise_master_data_api_error(exc)
    return V2BrandStateSnapshotListResponse(
        items=[_state_snapshot_to_response(snapshot) for snapshot in snapshots]
    )


@app.post(
    "/brands/{brand_id}/extension-capture-sessions",
    status_code=status.HTTP_201_CREATED,
    response_model=V2ExtensionCaptureSessionResponse,
)
async def create_brand_extension_capture_session_v2(
    brand_id: str,
    payload: V2ExtensionCaptureSessionCreateRequest,
    request: Request,
) -> V2ExtensionCaptureSessionResponse:
    principal = _resolve_workspace_principal_or_error(request)
    master_data_service = _get_v2_master_data_service(request)
    ingestion_service = _get_v2_ingestion_service(request)
    try:
        master_data_service.get_brand(workspace_id=principal.workspace_id, brand_id=brand_id)
        result = ingestion_service.create_extension_capture_session(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            channel_id=payload.channel_id,
        )
    except Exception as exc:
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_ingestion_api_error(exc)
    return _extension_capture_session_to_response(result)


@app.get(
    "/brands/{brand_id}/extension-capture-sessions/{capture_session_id}",
    response_model=V2ExtensionCaptureSessionResponse,
)
async def get_brand_extension_capture_session_v2(
    brand_id: str,
    capture_session_id: str,
    request: Request,
) -> V2ExtensionCaptureSessionResponse:
    principal = _resolve_workspace_principal_or_error(request)
    ingestion_service = _get_v2_ingestion_service(request)
    try:
        result = ingestion_service.get_extension_capture_session(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            capture_session_id=capture_session_id,
        )
    except Exception as exc:
        _raise_ingestion_api_error(exc)
    return _extension_capture_session_to_response(result)


@app.post(
    "/extension-captures",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=V2ExtensionCaptureSessionResponse,
)
async def submit_extension_capture_v2(
    payload: V2ExtensionCaptureSubmitRequest,
    request: Request,
) -> V2ExtensionCaptureSessionResponse:
    _resolve_workspace_principal_or_error(request)
    ingestion_service = _get_v2_ingestion_service(request)
    try:
        result = ingestion_service.submit_extension_capture(
            capture_session_id=payload.capture_session_id,
            capture_token=payload.capture_token,
            capture_payload=payload.capture_payload.model_dump(mode="json"),
        )
    except Exception as exc:
        _raise_ingestion_api_error(exc)
    return _extension_capture_session_to_response(result)


@app.post(
    "/brands/{brand_id}/data-import-previews",
    status_code=status.HTTP_201_CREATED,
    response_model=V2DataImportPreviewResponse,
)
async def create_brand_data_import_preview_v2(
    brand_id: str,
    payload: V2DataImportPreviewRequest,
    request: Request,
) -> V2DataImportPreviewResponse:
    principal = _resolve_workspace_principal_or_error(request)
    master_data_service = _get_v2_master_data_service(request)
    ingestion_service = _get_v2_ingestion_service(request)
    try:
        master_data_service.get_brand(workspace_id=principal.workspace_id, brand_id=brand_id)
        rows = [row.model_dump(mode="json") for row in payload.rows]
        if payload.file_content_base64:
            try:
                file_bytes = base64.b64decode(payload.file_content_base64, validate=True)
            except ValueError as exc:
                raise IngestionValidationError("file_content_base64 is not valid base64") from exc
            rows = ingestion_service.parse_uploaded_import_file(
                file_name=payload.file_name,
                file_bytes=file_bytes,
            )
        result = ingestion_service.create_data_import_preview(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            file_name=payload.file_name,
            import_type=payload.import_type,
            platform=payload.platform,
            rows=rows,
        )
    except Exception as exc:
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_ingestion_api_error(exc)
    return _data_import_preview_to_response(result)


@app.get(
    "/brands/{brand_id}/data-import-previews/{preview_id}",
    response_model=V2DataImportPreviewResponse,
)
async def get_brand_data_import_preview_v2(
    brand_id: str,
    preview_id: str,
    request: Request,
) -> V2DataImportPreviewResponse:
    principal = _resolve_workspace_principal_or_error(request)
    ingestion_service = _get_v2_ingestion_service(request)
    try:
        result = ingestion_service.get_data_import_preview(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            preview_id=preview_id,
        )
    except Exception as exc:
        _raise_ingestion_api_error(exc)
    return _data_import_preview_to_response(result)


@app.post(
    "/brands/{brand_id}/extension-capture-sessions/{capture_session_id}/retry-sync",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=V2ExtensionCaptureSessionResponse,
)
async def retry_brand_extension_capture_session_sync_v2(
    brand_id: str,
    capture_session_id: str,
    request: Request,
) -> V2ExtensionCaptureSessionResponse:
    principal = _resolve_workspace_principal_or_error(request)
    ingestion_service = _get_v2_ingestion_service(request)
    try:
        result = ingestion_service.retry_extension_capture_sync(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            capture_session_id=capture_session_id,
        )
    except Exception as exc:
        _raise_ingestion_api_error(exc)
    return _extension_capture_session_to_response(result)


@app.post(
    "/brands/{brand_id}/data-import-previews/{preview_id}/retry-sync",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=V2DataImportPreviewResponse,
)
async def retry_brand_data_import_preview_sync_v2(
    brand_id: str,
    preview_id: str,
    request: Request,
) -> V2DataImportPreviewResponse:
    principal = _resolve_workspace_principal_or_error(request)
    ingestion_service = _get_v2_ingestion_service(request)
    try:
        result = ingestion_service.retry_data_import_sync(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            preview_id=preview_id,
        )
    except Exception as exc:
        _raise_ingestion_api_error(exc)
    return _data_import_preview_to_response(result)


@app.post(
    "/brands/{brand_id}/discovery/tasks",
    status_code=status.HTTP_201_CREATED,
    response_model=V2DiscoveryTaskResponse,
)
async def create_brand_discovery_task_v2(
    brand_id: str,
    payload: V2DiscoveryTaskCreateRequest,
    request: Request,
) -> V2DiscoveryTaskResponse:
    principal = _resolve_workspace_principal_or_error(request)
    master_data_service = _get_v2_master_data_service(request)
    discovery_service = _get_v2_discovery_service(request)
    try:
        master_data_service.get_brand(workspace_id=principal.workspace_id, brand_id=brand_id)
        result = await discovery_service.create_task(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            topic=payload.topic,
        )
    except Exception as exc:
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_discovery_api_error(exc)
    return _discovery_to_response(result)


@app.get(
    "/brands/{brand_id}/discovery/tasks/{task_id}",
    response_model=V2DiscoveryTaskResponse,
)
async def get_brand_discovery_task_v2(
    brand_id: str,
    task_id: str,
    request: Request,
) -> V2DiscoveryTaskResponse:
    principal = _resolve_workspace_principal_or_error(request)
    master_data_service = _get_v2_master_data_service(request)
    discovery_service = _get_v2_discovery_service(request)
    try:
        master_data_service.get_brand(workspace_id=principal.workspace_id, brand_id=brand_id)
        result = discovery_service.get_task_workspace(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            task_id=task_id,
        )
    except Exception as exc:
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_discovery_api_error(exc)
    return _discovery_to_response(result)


@app.post(
    "/brands/{brand_id}/discovery/tasks/{task_id}/hotspots/refresh",
    response_model=V2DiscoveryTaskResponse,
)
async def refresh_brand_discovery_task_hotspots_v2(
    brand_id: str,
    task_id: str,
    request: Request,
) -> V2DiscoveryTaskResponse:
    principal = _resolve_workspace_principal_or_error(request)
    master_data_service = _get_v2_master_data_service(request)
    discovery_service = _get_v2_discovery_service(request)
    try:
        master_data_service.get_brand(workspace_id=principal.workspace_id, brand_id=brand_id)
        result = await discovery_service.refresh_hotspots(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            task_id=task_id,
        )
    except Exception as exc:
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_discovery_api_error(exc)
    return _discovery_to_response(result)


@app.post(
    "/brands/{brand_id}/discovery/tasks/{task_id}/queries",
    response_model=V2DiscoveryTaskResponse,
)
async def add_brand_discovery_task_query_v2(
    brand_id: str,
    task_id: str,
    payload: V2DiscoveryCustomQueryRequest,
    request: Request,
) -> V2DiscoveryTaskResponse:
    principal = _resolve_workspace_principal_or_error(request)
    master_data_service = _get_v2_master_data_service(request)
    discovery_service = _get_v2_discovery_service(request)
    try:
        master_data_service.get_brand(workspace_id=principal.workspace_id, brand_id=brand_id)
        result = discovery_service.add_custom_queries(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            task_id=task_id,
            text=payload.text,
        )
    except Exception as exc:
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_discovery_api_error(exc)
    return _discovery_to_response(result)


@app.delete(
    "/brands/{brand_id}/discovery/tasks/{task_id}/queries/{query_id}",
    response_model=V2DiscoveryTaskResponse,
)
async def delete_brand_discovery_task_query_v2(
    brand_id: str,
    task_id: str,
    query_id: str,
    request: Request,
) -> V2DiscoveryTaskResponse:
    principal = _resolve_workspace_principal_or_error(request)
    master_data_service = _get_v2_master_data_service(request)
    discovery_service = _get_v2_discovery_service(request)
    try:
        master_data_service.get_brand(workspace_id=principal.workspace_id, brand_id=brand_id)
        result = discovery_service.delete_custom_query(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            task_id=task_id,
            query_id=query_id,
        )
    except Exception as exc:
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_discovery_api_error(exc)
    return _discovery_to_response(result)


@app.post(
    "/brands/{brand_id}/source-syncs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=V2IngestionAcceptedResponse,
)
async def create_brand_source_sync_v2(
    brand_id: str,
    payload: V2BrandSourceSyncRequest,
    request: Request,
) -> V2IngestionAcceptedResponse:
    principal = _resolve_workspace_principal_or_error(request)
    master_data_service = _get_v2_master_data_service(request)
    ingestion_service = _get_v2_ingestion_service(request)
    try:
        master_data_service.get_brand(workspace_id=principal.workspace_id, brand_id=brand_id)
        result = ingestion_service.create_source_sync(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            source_type=payload.source_type,
            source_adapter=payload.source_adapter,
            channel_id=payload.channel_id,
            capture_payload=payload.capture_payload.model_dump(mode="json"),
        )
    except Exception as exc:  # pragma: no cover - routed through deterministic mapper
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_ingestion_api_error(exc)
    return _ingestion_result_to_response(result)


@app.post(
    "/brands/{brand_id}/data-imports",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=V2IngestionAcceptedResponse,
)
async def create_brand_data_import_v2(
    brand_id: str,
    payload: V2BrandDataImportRequest,
    request: Request,
) -> V2IngestionAcceptedResponse:
    principal = _resolve_workspace_principal_or_error(request)
    master_data_service = _get_v2_master_data_service(request)
    ingestion_service = _get_v2_ingestion_service(request)
    try:
        master_data_service.get_brand(workspace_id=principal.workspace_id, brand_id=brand_id)
        result = ingestion_service.create_data_import(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            import_type=payload.import_type,
            platform=payload.platform,
            rows=[row.model_dump(mode="json") for row in payload.rows],
        )
    except Exception as exc:  # pragma: no cover - routed through deterministic mapper
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_ingestion_api_error(exc)
    return _ingestion_result_to_response(result)


@app.post(
    "/brands/{brand_id}/topic-pool/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=V2TopicPoolRefreshResponse,
)
async def refresh_brand_topic_pool_v2(
    brand_id: str,
    payload: V2TopicPoolRefreshRequest,
    request: Request,
) -> V2TopicPoolRefreshResponse:
    principal = _resolve_workspace_principal_or_error(request)
    master_data_service = _get_v2_master_data_service(request)
    topic_pool_service = _get_v2_topic_pool_service(request)
    try:
        master_data_service.get_brand(workspace_id=principal.workspace_id, brand_id=brand_id)
        result = topic_pool_service.refresh_topic_pool(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            archive_threshold_days=payload.archive_threshold_days,
        )
    except Exception as exc:  # pragma: no cover - routed through deterministic mapper
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_topic_pool_api_error(exc)
    return _topic_pool_refresh_to_response(result)


@app.get(
    "/brands/{brand_id}/topic-pool",
    response_model=V2TopicPoolListResponse,
)
async def list_brand_topic_pool_v2(
    brand_id: str,
    request: Request,
) -> V2TopicPoolListResponse:
    principal = _resolve_workspace_principal_or_error(request)
    topic_pool_service = _get_v2_topic_pool_service(request)
    try:
        result = topic_pool_service.list_topic_pool(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
        )
    except Exception as exc:  # pragma: no cover - routed through deterministic mapper
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_topic_pool_api_error(exc)
    return _topic_pool_list_to_response(result)


@app.post(
    "/brands/{brand_id}/decisions/run",
    status_code=status.HTTP_201_CREATED,
    response_model=V2DecisionRunResponse,
)
async def run_brand_decisions_v2(
    brand_id: str,
    payload: V2DecisionRunRequest,
    request: Request,
) -> V2DecisionRunResponse:
    principal = _resolve_workspace_principal_or_error(request)
    decision_service = _get_v2_decision_service(request)
    try:
        result = decision_service.run_decision_batch(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
            requested_slot_count=payload.requested_slot_count,
            objective=payload.objective,
            exploration_mode=payload.exploration_mode,
        )
    except Exception as exc:  # pragma: no cover
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_decision_api_error(exc)
    return _decision_run_to_response(result)


@app.get(
    "/decision-batches/{batch_id}",
    response_model=V2DecisionBatchDetailResponse,
)
async def get_decision_batch_v2(
    batch_id: str,
    request: Request,
) -> V2DecisionBatchDetailResponse:
    principal = _resolve_workspace_principal_or_error(request)
    decision_service = _get_v2_decision_service(request)
    try:
        result = decision_service.get_decision_batch(
            workspace_id=principal.workspace_id,
            batch_id=batch_id,
        )
    except Exception as exc:  # pragma: no cover
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_decision_api_error(exc)
    return _decision_detail_to_response(result)


@app.get(
    "/brands/{brand_id}/decision-batches/latest",
    response_model=V2DecisionBatchDetailResponse,
)
async def get_latest_decision_batch_v2(
    brand_id: str,
    request: Request,
) -> V2DecisionBatchDetailResponse:
    principal = _resolve_workspace_principal_or_error(request)
    decision_service = _get_v2_decision_service(request)
    try:
        result = decision_service.get_latest_decision_batch(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
        )
    except Exception as exc:  # pragma: no cover
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_decision_api_error(exc)
    return _decision_detail_to_response(result)


@app.patch(
    "/decision-batches/{batch_id}/items/{slot_index}",
    response_model=V2DecisionBatchItemReviewResponse,
)
async def review_decision_batch_item_v2(
    batch_id: str,
    slot_index: int,
    payload: V2DecisionBatchItemReviewRequest,
    request: Request,
) -> V2DecisionBatchItemReviewResponse:
    principal = _resolve_workspace_principal_or_error(request)
    decision_service = _get_v2_decision_service(request)
    try:
        result = decision_service.review_batch_item(
            workspace_id=principal.workspace_id,
            batch_id=batch_id,
            slot_index=slot_index,
            review_action=payload.review_action,
            edited_title=payload.edited_title,
            edited_angle=payload.edited_angle,
            edited_hypothesis=payload.edited_hypothesis,
            review_notes=payload.review_notes,
            reviewed_by_id=principal.user_id,
        )
    except Exception as exc:  # pragma: no cover
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_decision_api_error(exc)
    return _decision_review_to_response(result)


@app.post(
    "/publish-records",
    status_code=status.HTTP_201_CREATED,
    response_model=V2PublishRecordResponse,
)
async def create_publish_record_v2(
    payload: V2PublishRecordCreateRequest,
    request: Request,
) -> V2PublishRecordResponse:
    principal = _resolve_workspace_principal_or_error(request)
    feedback_service = _get_v2_feedback_service(request)
    try:
        record = feedback_service.create_publish_record(
            workspace_id=principal.workspace_id,
            brand_id=payload.brand_id,
            channel_id=payload.channel_id,
            topic_pool_item_id=payload.topic_pool_item_id,
            decision_event_id=payload.decision_event_id,
            decision_batch_id=payload.decision_batch_id,
            publish_status=payload.publish_status,
            published_at=payload.published_at,
            content_item_id=payload.content_item_id,
            creative_variant=payload.creative_variant,
        )
        result = feedback_service.list_publish_records(
            workspace_id=principal.workspace_id,
            brand_id=payload.brand_id,
        )
    except Exception as exc:  # pragma: no cover
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_feedback_api_error(exc)
    return _publish_record_to_response(
        next(item for item in result if item.publish_record_id == record.id)
    )


@app.get(
    "/brands/{brand_id}/publish-records",
    response_model=V2PublishRecordListResponse,
)
async def list_publish_records_v2(
    brand_id: str,
    request: Request,
) -> V2PublishRecordListResponse:
    principal = _resolve_workspace_principal_or_error(request)
    feedback_service = _get_v2_feedback_service(request)
    try:
        rows = feedback_service.list_publish_records(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
        )
    except Exception as exc:  # pragma: no cover
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_feedback_api_error(exc)
    return V2PublishRecordListResponse(items=[_publish_record_to_response(item) for item in rows])


@app.post(
    "/performance/import",
    status_code=status.HTTP_201_CREATED,
    response_model=V2PerformanceSnapshotResponse,
)
async def import_performance_v2(
    payload: V2PerformanceImportRequest,
    request: Request,
) -> V2PerformanceSnapshotResponse:
    principal = _resolve_workspace_principal_or_error(request)
    feedback_service = _get_v2_feedback_service(request)
    try:
        snapshot = feedback_service.import_performance_snapshot(
            workspace_id=principal.workspace_id,
            publish_record_id=payload.publish_record_id,
            observation_window_hours=payload.observation_window_hours,
            snapshot_at=payload.snapshot_at,
            reward_version=payload.reward_version,
            metrics=payload.metrics.model_dump(),
        )
        views = feedback_service.list_performance_snapshots(
            workspace_id=principal.workspace_id,
            brand_id=snapshot.brand_id,
        )
    except Exception as exc:  # pragma: no cover
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_feedback_api_error(exc)
    return _performance_snapshot_to_response(
        next(item for item in views if item.performance_snapshot_id == snapshot.id)
    )


@app.get(
    "/brands/{brand_id}/performance-snapshots",
    response_model=V2PerformanceSnapshotListResponse,
)
async def list_performance_snapshots_v2(
    brand_id: str,
    request: Request,
) -> V2PerformanceSnapshotListResponse:
    principal = _resolve_workspace_principal_or_error(request)
    feedback_service = _get_v2_feedback_service(request)
    try:
        rows = feedback_service.list_performance_snapshots(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
        )
    except Exception as exc:  # pragma: no cover
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_feedback_api_error(exc)
    return V2PerformanceSnapshotListResponse(items=[_performance_snapshot_to_response(item) for item in rows])


@app.post(
    "/evaluation-runs",
    status_code=status.HTTP_201_CREATED,
    response_model=V2EvaluationRunResponse,
)
async def create_evaluation_run_v2(
    payload: V2EvaluationRunRequest,
    request: Request,
) -> V2EvaluationRunResponse:
    principal = _resolve_workspace_principal_or_error(request)
    feedback_service = _get_v2_feedback_service(request)
    try:
        result = feedback_service.create_evaluation_run(
            workspace_id=principal.workspace_id,
            brand_id=payload.brand_id,
            evaluation_type=payload.evaluation_type,
            created_by_id=principal.user_id,
        )
    except Exception as exc:  # pragma: no cover
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_feedback_api_error(exc)
    return _evaluation_run_to_response(result)


@app.get(
    "/evaluation-runs/{evaluation_run_id}",
    response_model=V2EvaluationRunResponse,
)
async def get_evaluation_run_v2(
    evaluation_run_id: str,
    request: Request,
) -> V2EvaluationRunResponse:
    principal = _resolve_workspace_principal_or_error(request)
    feedback_service = _get_v2_feedback_service(request)
    try:
        result = feedback_service.get_evaluation_run(
            workspace_id=principal.workspace_id,
            evaluation_run_id=evaluation_run_id,
        )
    except Exception as exc:  # pragma: no cover
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_feedback_api_error(exc)
    return _evaluation_run_to_response(result)


@app.get(
    "/brands/{brand_id}/evaluation-runs/latest",
    response_model=V2EvaluationRunResponse,
)
async def get_latest_evaluation_run_v2(
    brand_id: str,
    request: Request,
) -> V2EvaluationRunResponse:
    principal = _resolve_workspace_principal_or_error(request)
    feedback_service = _get_v2_feedback_service(request)
    try:
        result = feedback_service.get_latest_evaluation_run(
            workspace_id=principal.workspace_id,
            brand_id=brand_id,
        )
    except Exception as exc:  # pragma: no cover
        if isinstance(exc, MasterDataError):
            _raise_master_data_api_error(exc)
        _raise_feedback_api_error(exc)
    return _evaluation_run_to_response(result)


@app.post("/sessions", status_code=status.HTTP_201_CREATED, response_model=CreateSessionResponse)
async def create_session(payload: InitSessionRequest) -> CreateSessionResponse:
    session_id = str(uuid.uuid4())
    async with SessionManager(settings.SQLITE_DB_PATH) as session_manager:
        session = await session_manager.create_session(
            session_id=session_id,
            user_id=payload.user_id,
            user_query=payload.user_query,
            platform=payload.platform,
            mode=payload.mode,
        )
    async with JobStore(settings.SQLITE_DB_PATH) as store:
        await store.append_session_event(
            session_id=session_id,
            event_name="task_progress",
            stage=session.stage.value,
            payload={
                "message": "session created",
                "progress": 0,
                "error_code": None,
                "details": {"stage": session.stage.value},
            },
        )
    log_event(
        _logger,
        event_name="session_created",
        level="info",
        component="api",
        session_id=session_id,
        stage=session.stage.value,
        user_id=session.user_id,
    )
    return _session_to_create_response(session)


@app.post(
    "/sessions/{session_id}/strategy",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EnqueueResponse,
)
async def enqueue_strategy(
    session_id: str,
    payload: Optional[dict[str, Any]] = None,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> EnqueueResponse:
    session = await _load_session_or_error(session_id, allow_frozen=False)
    _assert_not_in_cooldown(session)
    replay = await _try_replay_idempotent_enqueue(
        session=session,
        job_type="strategy",
        next_stage=SessionStage.STRATEGY,
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        return replay
    _ensure_stage(session, expected=SessionStage.INIT, message="当前状态不支持策略执行")
    return await _enqueue_with_stage(
        session_id=session_id,
        job_type="strategy",
        next_stage=SessionStage.STRATEGY,
        payload=payload,
        idempotency_key=idempotency_key,
        previous_stage=session.stage,
    )


@app.post(
    "/sessions/{session_id}/generate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EnqueueResponse,
)
async def enqueue_generate(
    session_id: str,
    payload: Optional[dict[str, Any]] = None,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> EnqueueResponse:
    session = await _load_session_or_error(session_id, allow_frozen=False)
    _assert_not_in_cooldown(session)
    replay = await _try_replay_idempotent_enqueue(
        session=session,
        job_type="generate",
        next_stage=SessionStage.GENERATION,
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        return replay
    _ensure_stage(session, expected=SessionStage.STRATEGY, message="当前状态不支持生成，请先完成策略阶段")
    return await _enqueue_with_stage(
        session_id=session_id,
        job_type="generate",
        next_stage=SessionStage.GENERATION,
        payload=payload,
        idempotency_key=idempotency_key,
        previous_stage=session.stage,
    )


@app.post("/sessions/{session_id}/resume", response_model=ResumeSessionResponse)
async def resume_session(session_id: str) -> ResumeSessionResponse:
    session = await _load_session_or_error(session_id, allow_frozen=True)
    if session.lifecycle_state == SessionLifecycleState.PURGED:
        raise APIError(
            status_code=410,
            error_code="SESSION_PURGED",
            error_message="会话已被清理，无法恢复",
            suggested_action="请重新创建会话",
        )

    async with SessionManager(settings.SQLITE_DB_PATH) as session_manager:
        await session_manager.update_activity(session_id)
        refreshed = await session_manager.get_session(session_id)

    async with JobStore(settings.SQLITE_DB_PATH) as store:
        resumed_jobs = await store.resume_paused_jobs(session_id)
        await store.append_session_event(
            session_id=session_id,
            event_name="session_resumed",
            stage=refreshed.stage.value if refreshed else None,
            payload={
                "message": "session resumed",
                "progress": None,
                "error_code": None,
                "details": {"resumed_jobs": resumed_jobs},
            },
        )
    log_event(
        _logger,
        event_name="session_resumed",
        level="info",
        component="api",
        session_id=session_id,
        stage=refreshed.stage.value if refreshed else None,
        resumed_jobs=resumed_jobs,
    )

    if refreshed is None:
        raise APIError(
            status_code=404,
            error_code="SESSION_NOT_FOUND",
            error_message="会话不存在",
            suggested_action="请先创建会话",
        )

    return ResumeSessionResponse(
        session_id=session_id,
        lifecycle_state=refreshed.lifecycle_state.value,
        resumed_jobs=resumed_jobs,
        alive_until=_iso(refreshed.alive_until),
        purge_after=_iso(refreshed.purge_after),
    )


@app.get("/sessions/{session_id}", response_model=SessionStatusResponse)
async def get_session_status(session_id: str) -> SessionStatusResponse:
    session = await _load_session_or_error(session_id, allow_frozen=True)
    if session.lifecycle_state == SessionLifecycleState.PURGED:
        raise APIError(
            status_code=410,
            error_code="SESSION_PURGED",
            error_message="会话已被清理，无法恢复",
            suggested_action="请重新创建会话",
        )
    return await _build_session_status(session)


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str) -> JobStatusResponse:
    async with JobStore(settings.SQLITE_DB_PATH) as store:
        job = await store.get_job(job_id)
    if job is None:
        raise APIError(
            status_code=404,
            error_code="JOB_NOT_FOUND",
            error_message="任务不存在",
            suggested_action="请检查 job_id 是否正确",
        )
    return JobStatusResponse(
        job_id=job.id,
        session_id=job.session_id,
        job_type=job.job_type,
        status=job.status,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        last_error_code=job.last_error_code,
        last_error_message=job.last_error_message,
        cancel_reason=job.cancel_reason,
    )


_SESSION_TO_THREAD_EVENT: dict[str, str] = {
    "task_progress":   "workflow_task_progress",
    "task_completed":  "workflow_task_completed",
    "task_failed":     "workflow_task_failed",
    "task_cancelled":  "workflow_cancelled",
    "stage_changed":   "workflow_stage_changed",
    "session_resumed": "workflow_resumed",
    "session_paused":  "workflow_paused",
}


def _format_sse_thread_event(
    record: SessionEventRecord,
    thread_event_name: str,
    thread_id: str,
    *,
    include_id: bool = True,
) -> str:
    lines: list[str] = []
    if include_id:
        lines.append(f"id: {record.event_id}")
    lines.append(f"event: {thread_event_name}")
    data = {
        "event_id": record.event_id,
        "thread_id": thread_id,
        "session_id": record.session_id,
        "job_id": record.job_id,
        "stage": record.stage,
        "event_name": thread_event_name,
        "payload": record.payload,
    }
    lines.append(f"data: {json.dumps(data, ensure_ascii=False, default=str)}")
    return "\n".join(lines) + "\n\n"


async def _thread_event_stream(
    request: Request,
    *,
    thread_id: str,
    session_id: str,
    job_store: JobStore,
    last_event_id: Optional[int],
) -> AsyncIterator[str]:
    replay_events = await job_store.list_session_events(
        session_id,
        after_event_id=last_event_id,
        limit=settings.SSE_REPLAY_LIMIT,
    )
    last_sent_event_id = last_event_id or 0
    for record in replay_events:
        thread_event_name = _SESSION_TO_THREAD_EVENT.get(record.event_name, record.event_name)
        last_sent_event_id = record.event_id
        yield _format_sse_thread_event(record, thread_event_name, thread_id)

    heartbeat_deadline = monotonic() + settings.SSE_HEARTBEAT_SECONDS
    poll_interval = min(0.2, settings.SSE_HEARTBEAT_SECONDS)
    while not await request.is_disconnected():
        live_events = await job_store.list_session_events(
            session_id,
            after_event_id=last_sent_event_id,
            limit=settings.SSE_REPLAY_LIMIT,
        )
        if live_events:
            for record in live_events:
                thread_event_name = _SESSION_TO_THREAD_EVENT.get(record.event_name, record.event_name)
                last_sent_event_id = record.event_id
                yield _format_sse_thread_event(record, thread_event_name, thread_id)
            heartbeat_deadline = monotonic() + settings.SSE_HEARTBEAT_SECONDS
            continue

        now = monotonic()
        if now >= heartbeat_deadline:
            yield ": heartbeat\n\n"
            heartbeat_deadline = monotonic() + settings.SSE_HEARTBEAT_SECONDS
            continue

        await asyncio.sleep(min(poll_interval, heartbeat_deadline - now))


async def _event_stream(
    request: Request,
    *,
    session_id: str,
    last_event_id: Optional[int],
) -> AsyncIterator[str]:
    async with JobStore(settings.SQLITE_DB_PATH) as store:
        replay_events = await store.list_session_events(
            session_id,
            after_event_id=last_event_id,
            limit=settings.SSE_REPLAY_LIMIT,
        )
        last_sent_event_id = last_event_id or 0
        for record in replay_events:
            last_sent_event_id = record.event_id
            yield _format_sse_event(_event_record_to_schema(record))

        heartbeat_deadline = monotonic() + settings.SSE_HEARTBEAT_SECONDS
        poll_interval = min(0.2, settings.SSE_HEARTBEAT_SECONDS)
        while not await request.is_disconnected():
            live_events = await store.list_session_events(
                session_id,
                after_event_id=last_sent_event_id,
                limit=settings.SSE_REPLAY_LIMIT,
            )
            if live_events:
                for record in live_events:
                    last_sent_event_id = record.event_id
                    yield _format_sse_event(_event_record_to_schema(record))
                heartbeat_deadline = monotonic() + settings.SSE_HEARTBEAT_SECONDS
                continue

            now = monotonic()
            if now >= heartbeat_deadline:
                heartbeat = SessionEvent(
                    event_id=last_sent_event_id,
                    event_name="heartbeat",
                    session_id=session_id,
                    job_id=None,
                    stage=None,
                    timestamp=datetime.utcnow(),
                    payload=SessionEventPayload(
                        message="heartbeat",
                        progress=None,
                        error_code=None,
                        details={},
                    ),
                )
                # Heartbeats keep the connection alive but must not advance
                # the browser/client reconnect cursor past persisted event ids.
                log_event(
                    _logger,
                    event_name="sse_heartbeat",
                    level="debug",
                    component="api",
                    session_id=session_id,
                    connection_id=f"req_{id(request)}",
                )
                yield _format_sse_event(heartbeat, include_id=False)
                heartbeat_deadline = monotonic() + settings.SSE_HEARTBEAT_SECONDS
                continue

            await asyncio.sleep(min(poll_interval, heartbeat_deadline - now))


@app.get("/sessions/{session_id}/events")
async def stream_session_events(
    session_id: str,
    request: Request,
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    session = await _load_session_or_error(session_id, allow_frozen=True)
    if session.lifecycle_state == SessionLifecycleState.PURGED:
        raise APIError(
            status_code=410,
            error_code="SESSION_PURGED",
            error_message="会话已被清理，无法恢复",
            suggested_action="请重新创建会话",
        )

    parsed_last_event_id = _parse_last_event_id(last_event_id)
    return StreamingResponse(
        _event_stream(request, session_id=session_id, last_event_id=parsed_last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.get("/workflow-runs/{run_id}/snapshot")
async def get_workflow_run_snapshot(
    run_id: str,
    thread_id: Optional[str] = None,
) -> dict[str, Any]:
    return await _load_workflow_snapshot(run_id, expected_thread_id=thread_id)


@app.get("/workflow-runs/{run_id}/events")
async def stream_workflow_events(
    run_id: str,
    request: Request,
    after_event_id: Optional[int] = Query(default=None),
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    async with WorkflowStore(settings.SQLITE_DB_PATH) as store:
        if await store.get_run(run_id) is None:
            raise APIError(
                status_code=404,
                error_code="WORKFLOW_RUN_NOT_FOUND",
                error_message=f"Workflow run {run_id} not found",
                suggested_action="请检查 run_id 是否正确",
            )

    cursor = _resolve_workflow_event_cursor(
        after_event_id=after_event_id,
        last_event_id=last_event_id,
    )
    return StreamingResponse(
        _workflow_event_stream(request, run_id=run_id, after_event_id=cursor),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.post("/threads", status_code=201)
async def create_thread(
    body: CreatorThreadCreateRequest, request: Request
) -> CreatorThreadResponse:
    store = _get_thread_store(request)
    principal = None
    try:
        principal = _resolve_workspace_principal_or_error(request)
    except APIError:
        principal = None
    row = await store.create_thread(
        title=body.title,
        workspace_id=principal.workspace_id if principal is not None else None,
        brand_id=body.brand_id,
    )
    return CreatorThreadResponse(
        thread_id=row["id"],
        workspace_id=row.get("workspace_id"),
        brand_id=row.get("brand_id"),
        title=row["title"],
        status=row["status"],
        active_workflow_session_id=row["active_workflow_session_id"],
        active_job_id=row["active_job_id"],
    )


@app.get("/threads")
async def list_threads(request: Request) -> CreatorThreadListResponse:
    store = _get_thread_store(request)
    principal = None
    try:
        principal = _resolve_workspace_principal_or_error(request)
    except APIError:
        principal = None
    brand_id = request.query_params.get("brand_id") or None
    rows = await store.list_threads(
        workspace_id=principal.workspace_id if principal is not None else None,
        brand_id=brand_id,
    )
    items = [
        CreatorThreadSummary(
            thread_id=r["id"],
            workspace_id=r.get("workspace_id"),
            brand_id=r.get("brand_id"),
            title=r["title"],
            status=r["status"],
            active_job_id=r["active_job_id"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]
    return CreatorThreadListResponse(items=items)


@app.get("/threads/{thread_id}")
async def get_thread(thread_id: str, request: Request) -> CreatorThreadDetailResponse:
    store = _get_thread_store(request)
    row = await store.get_thread(thread_id)
    if row is None:
        raise APIError(
            status_code=404,
            error_code="THREAD_NOT_FOUND",
            error_message=f"Thread {thread_id} not found",
            suggested_action="请检查 thread_id 是否正确",
        )
    messages = await store.get_thread_messages(thread_id)
    thread_detail = _thread_detail_from_row(row)
    message_records = [_message_record_from_row(m) for m in messages]
    return CreatorThreadDetailResponse(thread=thread_detail, messages=message_records)


@app.get("/threads/{thread_id}/timeline")
async def get_thread_timeline(thread_id: str, request: Request) -> CreatorThreadTimelineResponse:
    store = _get_thread_store(request)
    row = await store.get_thread(thread_id)
    if row is None:
        raise APIError(
            status_code=404,
            error_code="THREAD_NOT_FOUND",
            error_message=f"Thread {thread_id} not found",
            suggested_action="请检查 thread_id 是否正确",
        )
    messages = await store.get_thread_messages(thread_id)
    return CreatorThreadTimelineResponse(
        thread=_thread_detail_from_row(row),
        messages=await _hydrate_timeline_artifact_refs(messages),
    )


@app.patch("/threads/{thread_id}")
async def update_thread(
    thread_id: str, body: CreatorThreadUpdateRequest, request: Request
) -> CreatorThreadResponse:
    store = _get_thread_store(request)
    row = await store.get_thread(thread_id)
    if row is None:
        raise APIError(
            status_code=404,
            error_code="THREAD_NOT_FOUND",
            error_message=f"Thread {thread_id} not found",
            suggested_action="请检查 thread_id 是否正确",
        )
    title = body.title.strip()
    if not title:
        raise APIError(
            status_code=422,
            error_code="INVALID_THREAD_TITLE",
            error_message="Thread title cannot be empty",
            suggested_action="请输入有效的对话名称",
        )
    await store.update_thread_title(thread_id, title)
    updated = await store.get_thread(thread_id)
    assert updated is not None
    return CreatorThreadResponse(
        thread_id=updated["id"],
        title=updated["title"],
        status=updated["status"],
        active_workflow_session_id=updated["active_workflow_session_id"],
        active_job_id=updated["active_job_id"],
    )


@app.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str, request: Request) -> CreatorThreadDeleteResponse:
    thread_store = _get_thread_store(request)
    job_store = _get_job_store(request)
    row = await thread_store.get_thread(thread_id)
    if row is None:
        raise APIError(
            status_code=404,
            error_code="THREAD_NOT_FOUND",
            error_message=f"Thread {thread_id} not found",
            suggested_action="请检查 thread_id 是否正确",
        )
    active_session_id: Optional[str] = row["active_workflow_session_id"]
    if active_session_id:
        await job_store.cancel_session_jobs(active_session_id, reason="thread_deleted")
    deleted = await thread_store.delete_thread(thread_id)
    return CreatorThreadDeleteResponse(thread_id=thread_id, deleted=deleted)


@app.post("/threads/{thread_id}/messages", status_code=201)
async def append_thread_message(
    thread_id: str, body: CreatorMessageCreateRequest, request: Request
) -> CreatorMessageResponse:
    thread_store = _get_thread_store(request)

    thread = await thread_store.get_thread(thread_id)
    if thread is None:
        raise APIError(
            status_code=404,
            error_code="THREAD_NOT_FOUND",
            error_message=f"Thread {thread_id} not found",
            suggested_action="请检查 thread_id 是否正确",
        )

    if thread.get("active_run_id") or not thread.get("active_workflow_session_id"):
        orchestrator = ConversationOrchestrator(
            db_path=settings.SQLITE_DB_PATH,
            thread_store=thread_store,
        )
        result = await orchestrator.handle_message(
            thread=thread,
            text=body.text,
            user_id=DEFAULT_USER_ID,
        )
        msg_row = result["message_row"]
        message_record = _message_record_from_row(msg_row)
        return CreatorMessageResponse(
            message=message_record,
            intent=result["intent"],
            job_action_result=None,
            command_result=result["command_result"],
            active_run_snapshot=result["active_run_snapshot"],
            updated_title=None,
            assistant_reply=result["assistant_reply"],
        )

    active_session_id: Optional[str] = thread["active_workflow_session_id"]
    active_job_id: Optional[str] = thread["active_job_id"]
    job_store = _get_job_store(request)
    active_job = None
    if active_job_id:
        active_job = await job_store.get_job(active_job_id)

    has_active_job = active_job is not None and active_job.status in ACTIVE_JOB_STATUSES

    intent = await classify_intent(
        body.text,
        IntentContext(
            has_active_job=has_active_job,
            active_job_status=active_job.status if active_job else None,
        ),
    )

    job_action_result: Optional[dict] = None
    if intent == "pause_job" and active_session_id:
        count = await job_store.pause_session_jobs(active_session_id)
        job_action_result = {
            "action": "pause", "affected_jobs": count,
            "session_id": active_session_id, "job_id": active_job_id,
        }
    elif intent == "resume_job" and active_session_id:
        count = await job_store.resume_paused_jobs(active_session_id)
        job_action_result = {
            "action": "resume", "affected_jobs": count,
            "session_id": active_session_id, "job_id": active_job_id,
        }
    elif intent == "cancel_job" and active_session_id:
        count = await job_store.cancel_session_jobs(active_session_id, reason="user_cancelled")
        job_action_result = {
            "action": "cancel", "affected_jobs": count,
            "session_id": active_session_id, "job_id": active_job_id,
        }
    elif intent == "ask_status":
        job_action_result = {
            "job_id": active_job_id,
            "job_status": active_job.status if active_job else None,
            "job_type": active_job.job_type if active_job else None,
            "session_id": active_session_id,
        }

    # Auto-rename thread on first user message (placeholder titles start with "对话 ")
    updated_title: Optional[str] = None
    current_title: str = thread["title"]
    is_placeholder = current_title.startswith("对话 ")
    if is_placeholder:
        prior_count = await thread_store.count_user_messages(thread_id)
        if prior_count == 0:
            updated_title = await _generate_thread_title(body.text)
            await thread_store.update_thread_title(thread_id, updated_title)

    msg_row = await thread_store.append_message(
        thread_id=thread_id,
        role="user",
        text=body.text,
        intent=intent,
        linked_session_id=active_session_id,
        linked_job_id=active_job_id,
    )
    message_record = _message_record_from_row(msg_row)

    # Generate and persist the assistant reply so it survives thread switches
    _JOB_TYPE_LABEL = {"strategy": "策略生成", "generate": "笔记生成"}
    _JOB_STATUS_LABEL = {
        "running": "进行中", "paused": "已暂停",
        "cancelled": "已中断", "succeeded": "已完成",
    }
    assistant_reply: Optional[str] = None
    if intent == "pause_job":
        assistant_reply = "已暂停当前任务。"
    elif intent == "resume_job":
        assistant_reply = "已恢复任务，继续执行中。"
    elif intent == "cancel_job":
        assistant_reply = "已取消当前任务。"
    elif intent == "ask_status":
        if active_job:
            type_label = _JOB_TYPE_LABEL.get(active_job.job_type, active_job.job_type)
            status_label = _JOB_STATUS_LABEL.get(active_job.status, active_job.status)
            assistant_reply = f"当前任务：{type_label} · {status_label}"
        else:
            assistant_reply = "当前没有运行中的任务。"
    elif intent == "add_constraint":
        assistant_reply = "已收到，后台任务继续执行。"
    elif intent == "free_chat":
        assistant_reply = "已收到。如需生成内容，请描述你的具体需求。"

    if assistant_reply:
        await thread_store.append_message(
            thread_id=thread_id,
            role="assistant",
            text=assistant_reply,
            linked_session_id=active_session_id,
            linked_job_id=active_job_id,
        )

    return CreatorMessageResponse(message=message_record, intent=intent,
                                  job_action_result=job_action_result,
                                  updated_title=updated_title,
                                  assistant_reply=assistant_reply)


@app.post("/threads/{thread_id}/workflow", status_code=201)
async def start_thread_workflow(
    thread_id: str, body: CreatorWorkflowRequest, request: Request
) -> CreatorWorkflowResponse:
    thread_store = _get_thread_store(request)

    thread = await thread_store.get_thread(thread_id)
    if thread is None:
        raise APIError(
            status_code=404,
            error_code="THREAD_NOT_FOUND",
            error_message=f"Thread {thread_id} not found",
            suggested_action="请检查 thread_id 是否正确",
        )

    user_id = body.user_id or DEFAULT_USER_ID
    orchestrator = ConversationOrchestrator(
        db_path=settings.SQLITE_DB_PATH,
        thread_store=thread_store,
    )
    # T10: keep this legacy route as a compatibility wrapper only. New creator
    # workflow truth must come from workflow-v2 run/snapshot state, not sessions.
    result = await orchestrator.handle_message(
        thread=thread,
        text=body.user_query,
        user_id=user_id,
    )
    active_run_snapshot = result.get("active_run_snapshot") or {}
    command_result = result.get("command_result") or {}
    run = active_run_snapshot.get("run") or {}
    run_id = command_result.get("run_id") or run.get("run_id")
    if not run_id:
        raise APIError(
            status_code=400,
            error_code="WORKFLOW_V2_START_FAILED",
            error_message="Legacy workflow endpoint could not start a workflow-v2 run",
            suggested_action="请改用 POST /threads/{thread_id}/messages 发送自然语言需求",
        )
    stage = run.get("current_step") or run.get("phase") or "workflow-v2"

    return CreatorWorkflowResponse(
        thread_id=thread_id,
        session_id=run_id,
        job_id="",
        stage=stage,
        run_id=run_id,
        command_result=command_result,
        active_run_snapshot=active_run_snapshot,
        compatibility_mode="workflow-v2",
    )


@app.get("/threads/{thread_id}/events")
async def stream_thread_events(
    thread_id: str,
    request: Request,
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    thread_store = _get_thread_store(request)
    job_store = _get_job_store(request)

    thread = await thread_store.get_thread(thread_id)
    if thread is None:
        raise APIError(
            status_code=404,
            error_code="THREAD_NOT_FOUND",
            error_message=f"Thread {thread_id} not found",
            suggested_action="请检查 thread_id 是否正确",
        )

    parsed_last_event_id = _parse_last_event_id(last_event_id)
    session_id: Optional[str] = thread["active_workflow_session_id"]

    if session_id is None:
        async def _empty_stream() -> AsyncIterator[str]:
            yield ": no active session\n\n"
        return StreamingResponse(
            _empty_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    return StreamingResponse(
        _thread_event_stream(
            request,
            thread_id=thread_id,
            session_id=session_id,
            job_store=job_store,
            last_event_id=parsed_last_event_id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


_TERMINAL_JOB_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


@app.post("/jobs/{job_id}/pause", response_model=JobControlResponse)
async def pause_job_endpoint(job_id: str, request: Request) -> JobControlResponse:
    job_store = _get_job_store(request)
    existing = await job_store.get_job(job_id)
    if existing is None:
        raise APIError(
            status_code=404,
            error_code="JOB_NOT_FOUND",
            error_message=f"Job {job_id} not found",
            suggested_action="请检查 job_id 是否正确",
        )
    if existing.status == "running":
        raise APIError(
            status_code=409,
            error_code="JOB_RUNNING",
            error_message="Job is currently running; only queued/retrying jobs can be paused",
            suggested_action="等待 job 完成后再操作，或调用 cancel",
        )
    if existing.status in _TERMINAL_JOB_STATUSES:
        raise APIError(
            status_code=409,
            error_code="JOB_TERMINAL",
            error_message=f"Job is already in terminal state: {existing.status}",
            suggested_action="该 job 已结束，无法暂停",
        )
    job = await job_store.pause_job(job_id)
    assert job is not None
    return JobControlResponse(job_id=job.id, session_id=job.session_id, status=job.status)


@app.post("/jobs/{job_id}/resume", response_model=JobControlResponse)
async def resume_job_endpoint(job_id: str, request: Request) -> JobControlResponse:
    job_store = _get_job_store(request)
    existing = await job_store.get_job(job_id)
    if existing is None:
        raise APIError(
            status_code=404,
            error_code="JOB_NOT_FOUND",
            error_message=f"Job {job_id} not found",
            suggested_action="请检查 job_id 是否正确",
        )
    if existing.status != "paused":
        raise APIError(
            status_code=409,
            error_code="JOB_NOT_PAUSED",
            error_message=f"Job cannot be resumed from status: {existing.status}",
            suggested_action="只有 paused 状态的 job 可以恢复",
        )
    job = await job_store.resume_job(job_id)
    assert job is not None
    return JobControlResponse(job_id=job.id, session_id=job.session_id, status=job.status)


@app.post("/jobs/{job_id}/cancel", response_model=JobControlResponse)
async def cancel_job_endpoint(job_id: str, request: Request) -> JobControlResponse:
    job_store = _get_job_store(request)
    existing = await job_store.get_job(job_id)
    if existing is None:
        raise APIError(
            status_code=404,
            error_code="JOB_NOT_FOUND",
            error_message=f"Job {job_id} not found",
            suggested_action="请检查 job_id 是否正确",
        )
    if existing.status in _TERMINAL_JOB_STATUSES:
        raise APIError(
            status_code=409,
            error_code="JOB_TERMINAL",
            error_message=f"Job is already in terminal state: {existing.status}",
            suggested_action="该 job 已结束，无法取消",
        )
    job = await job_store.cancel_job(job_id, reason="user_cancelled")
    assert job is not None
    return JobControlResponse(job_id=job.id, session_id=job.session_id, status=job.status)


@app.post("/threads/{thread_id}/complete", status_code=200)
async def complete_thread_endpoint(thread_id: str, request: Request) -> CompleteThreadResponse:
    thread_store = _get_thread_store(request)
    thread = await thread_store.get_thread(thread_id)
    if thread is None:
        raise APIError(
            status_code=404,
            error_code="THREAD_NOT_FOUND",
            error_message=f"Thread {thread_id} not found",
            suggested_action="请检查 thread_id 是否正确",
        )

    if thread["status"] == "accepted":
        if thread.get("active_run_id"):
            candidates = [
                item for item in await _list_publish_candidate_artifacts(
                    workspace_id=thread.get("workspace_id"),
                    brand_id=thread.get("brand_id"),
                )
                if item["thread_id"] == thread_id and item["session_id"] == thread["active_run_id"]
            ]
            count = len(candidates)
        else:
            count = await thread_store.count_publish_candidates(thread_id)
        return CompleteThreadResponse(thread_id=thread_id, status="accepted", publish_candidate_count=count)

    if thread.get("active_run_id"):
        _strategy, notes, artifact_refs = await _load_workflow_result(thread, publishable_only=True)
        await thread_store.complete_thread(thread_id)
        if artifact_refs:
            await thread_store.append_artifact_result_message(
                thread_id=thread_id,
                run_id=thread["active_run_id"],
                artifact_refs=artifact_refs,
            )
        if notes:
            count = await _ensure_publish_candidate_artifacts(
                thread_id=thread_id,
                run_id=thread["active_run_id"],
                workspace_id=thread.get("workspace_id"),
                brand_id=thread.get("brand_id"),
                notes=notes,
            )
        else:
            count = 0
        return CompleteThreadResponse(thread_id=thread_id, status="accepted", publish_candidate_count=count)

    session_id: Optional[str] = thread.get("active_workflow_session_id")
    candidates: list[dict] = []
    if session_id:
        try:
            import aiosqlite as _aiosqlite
            from app.memory.session_data_store import SessionDataStore as _SessionDataStore

            async with _aiosqlite.connect(settings.SQLITE_DB_PATH) as _conn:
                _conn.row_factory = _aiosqlite.Row
                _ds = _SessionDataStore(_conn)
                await _ds.init_tables()
                notes = await _ds.get_generated_notes(session_id, note_ids=None)
                candidates = [
                    {
                        "note_id": n.note_id,
                        "title": n.title,
                        "content": n.content,
                        "tags": getattr(n, "tags", []) or [],
                    }
                    for n in notes
                ]
        except Exception:
            pass

    await thread_store.complete_thread(thread_id)
    if candidates:
        await thread_store.save_publish_candidates(thread_id, session_id, candidates)

    count = await thread_store.count_publish_candidates(thread_id)
    return CompleteThreadResponse(thread_id=thread_id, status="accepted", publish_candidate_count=count)


@app.get("/publish-candidates")
async def list_publish_candidates_endpoint(
    request: Request,
    brand_id: Optional[str] = Query(default=None),
    thread_id: Optional[str] = Query(default=None),
    run_id: Optional[str] = Query(default=None),
) -> PublishCandidatesResponse:
    thread_store = _get_thread_store(request)
    principal = None
    try:
        principal = _resolve_workspace_principal_or_error(request)
    except APIError:
        principal = None
    rows = await _list_publish_candidate_artifacts(
        workspace_id=principal.workspace_id if principal is not None else None,
        brand_id=brand_id,
        thread_id=thread_id,
        run_id=run_id,
    )
    if not rows and principal is None and brand_id is None and thread_id is None and run_id is None:
        rows = await thread_store.list_publish_candidates()
    items = [
        PublishCandidate(
            candidate_id=r["candidate_id"],
            workspace_id=r.get("workspace_id"),
            brand_id=r.get("brand_id"),
            thread_id=r["thread_id"],
            session_id=r["session_id"],
            note_id=r["note_id"],
            title=r["title"],
            content=r["content"],
            tags=r["tags"] if isinstance(r.get("tags"), list) else (r["tags"].split(",") if r.get("tags") else []),
            topic_type=r.get("topic_type") or "方法",
            core_hypothesis=r.get("core_hypothesis") or "认可笔记可沉淀为后续创作选题",
            score=float(r.get("score") or 0.0),
            score_type=r.get("score_type") or "predicted",
            source=r.get("source") or "publish_candidate",
            created_at=r["created_at"],
        )
        for r in rows
    ]
    return PublishCandidatesResponse(items=items)


@app.get("/threads/{thread_id}/result")
async def get_thread_result_endpoint(thread_id: str, request: Request) -> ThreadResultResponse:
    thread_store = _get_thread_store(request)
    thread = await thread_store.get_thread(thread_id)
    if thread is None:
        raise APIError(
            status_code=404,
            error_code="THREAD_NOT_FOUND",
            error_message=f"Thread {thread_id} not found",
            suggested_action="请检查 thread_id 是否正确",
        )

    if thread.get("active_run_id"):
        strategy, notes, artifact_refs = await _load_workflow_result(thread)
        if artifact_refs:
            await thread_store.append_artifact_result_message(
                thread_id=thread_id,
                run_id=thread["active_run_id"],
                artifact_refs=artifact_refs,
            )
        return ThreadResultResponse(
            thread_id=thread_id,
            session_id=thread["active_run_id"],
            strategy=strategy,
            notes=[GeneratedNoteItem(**note) for note in notes],
        )

    session_id: Optional[str] = thread.get("active_workflow_session_id")
    if not session_id:
        return ThreadResultResponse(thread_id=thread_id, session_id=None, strategy=None, notes=[])

    strategy_dict: Optional[dict] = None
    notes_list: list[GeneratedNoteItem] = []
    try:
        import aiosqlite as _aiosqlite
        from app.memory.session_data_store import SessionDataStore as _SessionDataStore

        async with _aiosqlite.connect(settings.SQLITE_DB_PATH) as _conn:
            _conn.row_factory = _aiosqlite.Row
            _ds = _SessionDataStore(_conn)
            await _ds.init_tables()
            try:
                strategy, _pref, _sid = await _ds.get_strategy(session_id, None)
                strategy_dict = strategy.model_dump() if strategy else None
            except Exception:
                pass
            notes = await _ds.get_generated_notes(session_id, note_ids=None)
            notes_list = [
                GeneratedNoteItem(
                    note_id=n.note_id,
                    title=n.title,
                    content=n.content,
                    tags=getattr(n, "tags", []) or [],
                )
                for n in notes
            ]
    except Exception:
        pass

    return ThreadResultResponse(
        thread_id=thread_id,
        session_id=session_id,
        strategy=strategy_dict,
        notes=notes_list,
    )


@app.get("/health")
@app.head("/health")
async def health_check() -> dict[str, Any]:
    return {
        "service": settings.RUNTIME_SERVICE_NAME,
        "status": "healthy",
        "version": settings.RUNTIME_VERSION,
        "api_contract": settings.RUNTIME_API_CONTRACT,
        "features": {
            "workflow_v2": True,
            "publish_candidate_artifacts": True,
            "sse_progress": True,
            "runtime_commands": True,
            "embedding_prewarm": True,
        },
        "timestamp": datetime.utcnow().isoformat(),
        "queue": "active",
    }


@app.post("/runtime/prewarm")
async def prewarm_runtime() -> dict[str, Any]:
    global _embedding_prewarm_task
    if _embedding_prewarm_task is None or _embedding_prewarm_task.done():
        if _embedding_prewarm_status.get("status") != "ready":
            _embedding_prewarm_task = asyncio.create_task(_run_embedding_prewarm())
    return {
        "embedding": _embedding_prewarm_status,
    }


@app.get("/runtime/prewarm")
async def get_runtime_prewarm_status() -> dict[str, Any]:
    return {
        "embedding": _embedding_prewarm_status,
    }
