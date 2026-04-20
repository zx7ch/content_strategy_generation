"""Deterministic topic pool generation service for V2 P1-3."""

from __future__ import annotations

import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from app.v2.foundation.models import BrandRecord, utcnow
from app.v2.foundation.service import MasterDataService
from app.v2.ingestion.models import ContentItemRecord, ContentMetricsSnapshotRecord, TopicRecord
from app.v2.ingestion.store import IngestionStore
from app.v2.topic_pool.scorer import ScorerService
from app.v2.topic_pool.models import (
    TopicPoolItemRecord,
    TopicPoolListItem,
    TopicPoolListResult,
    TopicPoolRefreshResult,
)
from app.v2.topic_pool.store import TopicPoolStore

_NON_WORD_RE = re.compile(r"[^0-9a-zA-Z\u4e00-\u9fff]+")
_COMPETITOR_SCAN_AGENT_NAME = "competitor_scan_agent"
_PATTERN_INSIGHT_AGENT_NAME = "pattern_insight_agent"
_TOPIC_AGENT_NAME = "topic_hypothesis_agent"
_DEFAULT_ARCHIVE_THRESHOLD_DAYS = 60
_MAX_PROPOSALS = 8


class TopicPoolError(ValueError):
    """Raised when topic-pool operations violate the V2 topic-pool contract."""


class TopicPoolValidationError(TopicPoolError):
    """Raised when a generated proposal or request payload is invalid."""


@dataclass(frozen=True)
class _EvidenceSignal:
    item: ContentItemRecord
    signal_type: str
    topic_type: str
    seed_label: str
    normalized_name: str
    score: float


@dataclass
class _ProposalSeed:
    normalized_name: str
    display_name: str
    topic_type: str
    evidence: list[_EvidenceSignal] = field(default_factory=list)
    gap_type: str = "market_signal"
    gap_description: str = ""
    agent_lineage: dict[str, Any] = field(default_factory=dict)
    insight_summary: dict[str, Any] = field(default_factory=dict)
    competitor_scan_summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _CompetitorScanResult:
    agent_run_id: str
    scanned_at: str
    source_count: int
    item_count: int
    scan_summary: dict[str, Any]
    status: str = "completed"


@dataclass(frozen=True)
class _PatternInsightResult:
    agent_run_id: str
    analyzed_at: str
    insight_summary: dict[str, Any]
    status: str = "completed"


class TopicPoolService:
    def __init__(
        self,
        *,
        master_data_service: MasterDataService,
        ingestion_store: IngestionStore,
        topic_pool_store: TopicPoolStore,
        scorer_service: ScorerService | None = None,
    ) -> None:
        self._master_data_service = master_data_service
        self._ingestion_store = ingestion_store
        self._topic_pool_store = topic_pool_store
        self._scorer_service = scorer_service

    def attach_scorer_service(self, scorer_service: ScorerService) -> None:
        self._scorer_service = scorer_service

    def list_topic_pool(
        self,
        *,
        workspace_id: str,
        brand_id: str,
    ) -> TopicPoolListResult:
        brand = self._master_data_service.get_brand(workspace_id=workspace_id, brand_id=brand_id)
        topic_map = {topic.id: topic for topic in self._ingestion_store.list_topics(brand_id)}
        content_item_map = {item.id: item for item in self._ingestion_store.list_content_items(brand_id)}
        items = self._topic_pool_store.list_topic_pool_items(brand_id)
        if self._scorer_service is not None:
            items = self._scorer_service.ensure_fresh(
                workspace_id=workspace_id,
                brand_id=brand_id,
                items=items,
            )
        list_items: list[TopicPoolListItem] = []
        last_refresh_at = None
        best_score = 0.0

        for item in items:
            topic = topic_map.get(item.topic_id)
            if topic is None:
                continue
            list_items.append(
                TopicPoolListItem(
                    id=item.id,
                    topic_id=topic.id,
                    display_name=topic.display_name,
                    normalized_name=topic.normalized_name,
                    topic_type=topic.topic_type,
                    title=item.title,
                    angle=item.angle,
                    hypothesis=item.hypothesis,
                    evidence_summary=item.evidence_summary,
                    source_agent=item.source_agent,
                    status=item.status,
                    final_score=item.final_score,
                    score_breakdown=self._build_score_breakdown(item),
                    evidence_provenance=self._build_evidence_provenance(
                        evidence_summary=item.evidence_summary,
                        content_item_map=content_item_map,
                    ),
                    updated_at=item.updated_at,
                )
            )
            best_score = max(best_score, item.final_score)
            if last_refresh_at is None or item.updated_at > last_refresh_at:
                last_refresh_at = item.updated_at

        return TopicPoolListResult(
            brand_id=brand.id,
            brand_name=brand.name,
            brand_stage=brand.stage,
            target_audience=brand.target_audience,
            total_candidate_count=len(list_items),
            best_score=best_score,
            last_refresh_at=last_refresh_at,
            items=list_items,
        )

    def refresh_topic_pool(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        archive_threshold_days: int = _DEFAULT_ARCHIVE_THRESHOLD_DAYS,
    ) -> TopicPoolRefreshResult:
        brand = self._master_data_service.get_brand(workspace_id=workspace_id, brand_id=brand_id)
        refresh_run_id = str(uuid.uuid4())
        now = utcnow()
        content_items = self._ingestion_store.list_content_items(brand_id)
        evidence_signals = self._build_evidence_signals(content_items)
        competitor_scan = self._run_competitor_scan(evidence_signals=evidence_signals, now=now)
        pattern_insight = self._run_pattern_insight(
            brand=brand,
            evidence_signals=evidence_signals,
            competitor_scan=competitor_scan,
            now=now,
        )
        grouped = self._build_proposal_groups(
            evidence_signals=evidence_signals,
            competitor_scan=competitor_scan,
            pattern_insight=pattern_insight,
        )

        generated = 0
        for group in grouped[:_MAX_PROPOSALS]:
            topic = self._get_or_create_topic(brand=brand, group=group, now=now)
            proposal = self._build_topic_pool_item(
                brand=brand,
                topic=topic,
                group=group,
                refresh_run_id=refresh_run_id,
                now=now,
            )
            self._topic_pool_store.save_topic_pool_item(proposal)
            generated += 1

        archived = self._archive_stale_candidates(
            brand_id=brand_id,
            archive_threshold_days=archive_threshold_days,
            now=now,
        )
        active_items = self._topic_pool_store.list_topic_pool_items(brand_id)
        if self._scorer_service is not None:
            active_items = self._scorer_service.ensure_fresh(
                workspace_id=workspace_id,
                brand_id=brand_id,
                items=active_items,
                now=now,
            )
        return TopicPoolRefreshResult(
            refresh_run_id=refresh_run_id,
            status="completed",
            generated_item_count=generated,
            archived_item_count=archived,
            total_candidate_count=len(active_items),
            refreshed_at=now,
        )

    def _build_evidence_signals(self, content_items: list[ContentItemRecord]) -> list[_EvidenceSignal]:
        signals: list[_EvidenceSignal] = []
        for item in content_items:
            signal = self._to_evidence_signal(item)
            if signal is not None:
                signals.append(signal)
        return signals

    def _build_proposal_groups(
        self,
        *,
        evidence_signals: list[_EvidenceSignal],
        competitor_scan: _CompetitorScanResult,
        pattern_insight: _PatternInsightResult,
    ) -> list[_ProposalSeed]:
        grouped: dict[str, _ProposalSeed] = {}
        gap_by_type = {
            str(item.get("topic_type")): item
            for item in pattern_insight.insight_summary.get("content_gap_hypotheses", [])
            if isinstance(item, dict) and item.get("topic_type")
        }
        for signal in evidence_signals:
            group = grouped.get(signal.normalized_name)
            if group is None:
                gap = gap_by_type.get(signal.topic_type, {})
                group = _ProposalSeed(
                    normalized_name=signal.normalized_name,
                    display_name=signal.seed_label,
                    topic_type=signal.topic_type,
                    gap_type=str(gap.get("gap_type") or "market_signal"),
                    gap_description=str(gap.get("description") or ""),
                    agent_lineage={
                        _COMPETITOR_SCAN_AGENT_NAME: {
                            "agent_run_id": competitor_scan.agent_run_id,
                            "status": competitor_scan.status,
                        },
                        _PATTERN_INSIGHT_AGENT_NAME: {
                            "agent_run_id": pattern_insight.agent_run_id,
                            "status": pattern_insight.status,
                        },
                        _TOPIC_AGENT_NAME: {
                            "status": "completed",
                        },
                    },
                    insight_summary=pattern_insight.insight_summary,
                    competitor_scan_summary=competitor_scan.scan_summary,
                )
                grouped[signal.normalized_name] = group
            group.evidence.append(signal)

        ranked_groups = sorted(
            grouped.values(),
            key=lambda group: (
                -max(signal.score for signal in group.evidence),
                -len(group.evidence),
                group.normalized_name,
            ),
        )
        return ranked_groups

    def _run_competitor_scan(
        self,
        *,
        evidence_signals: list[_EvidenceSignal],
        now,
    ) -> _CompetitorScanResult:
        signal_scores_by_type: dict[str, list[float]] = {}
        for signal in evidence_signals:
            signal_scores_by_type.setdefault(signal.topic_type, []).append(signal.score)

        avg_engagement_by_type = {
            topic_type: round(sum(scores) / len(scores), 4)
            for topic_type, scores in signal_scores_by_type.items()
        }
        top_topic_types = [
            topic_type
            for topic_type, _score in sorted(
                avg_engagement_by_type.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]
        high_signal_count = max(1, math.ceil(len(evidence_signals) * 0.1)) if evidence_signals else 0
        high_signal_items = [
            {
                "content_item_id": signal.item.id,
                "normalized_name": signal.normalized_name,
                "topic_type": signal.topic_type,
                "engagement_signal": signal.score,
                "evidence_url": signal.item.source_url,
            }
            for signal in sorted(
                evidence_signals,
                key=lambda item: (-item.score, item.item.id),
            )[:high_signal_count]
        ]
        unique_sources = {
            signal.item.author_id or signal.item.platform_content_id or signal.item.id
            for signal in evidence_signals
        }
        return _CompetitorScanResult(
            agent_run_id=str(uuid.uuid4()),
            scanned_at=now.isoformat(),
            source_count=len(unique_sources),
            item_count=len(evidence_signals),
            scan_summary={
                "top_topic_types": top_topic_types,
                "avg_engagement_by_topic_type": avg_engagement_by_type,
                "high_signal_items": high_signal_items,
            },
        )

    def _run_pattern_insight(
        self,
        *,
        brand: BrandRecord,
        evidence_signals: list[_EvidenceSignal],
        competitor_scan: _CompetitorScanResult,
        now,
    ) -> _PatternInsightResult:
        owned_signals = [
            signal for signal in evidence_signals if signal.item.source_type == "historical_note_import_v1"
        ]
        owned_by_type: dict[str, list[float]] = {}
        for signal in owned_signals:
            owned_by_type.setdefault(signal.topic_type, []).append(signal.score)
        avg_owned_reward_by_type = {
            topic_type: {
                "value": round(sum(scores) / len(scores), 4),
                "sample_count": len(scores),
            }
            for topic_type, scores in owned_by_type.items()
        }
        high_performing = [
            topic_type
            for topic_type, payload in sorted(
                avg_owned_reward_by_type.items(),
                key=lambda item: (-float(item[1]["value"]), item[0]),
            )
            if float(payload["value"]) >= 0.55
        ]
        underperforming = [
            topic_type
            for topic_type, payload in sorted(
                avg_owned_reward_by_type.items(),
                key=lambda item: (float(item[1]["value"]), item[0]),
            )
            if float(payload["value"]) < 0.4
        ]
        market_avg = competitor_scan.scan_summary.get("avg_engagement_by_topic_type", {})
        rising_topic_types = [
            topic_type
            for topic_type, value in sorted(
                market_avg.items(),
                key=lambda item: (-float(item[1]), item[0]),
            )
            if float(value) >= 0.5
        ]
        saturation_signals = [
            topic_type
            for topic_type, value in sorted(
                market_avg.items(),
                key=lambda item: (float(item[1]), item[0]),
            )
            if float(value) < 0.35
        ]
        audience_sources: list[str] = []
        inferred_segments: list[str] = []
        if brand.target_audience:
            audience_sources.append("brand_profile")
            inferred_segments.extend(
                value
                for key in ("interests", "lifestyle_descriptors", "geographic_focus")
                for value in brand.target_audience.get(key, [])
                if isinstance(value, str)
            )
        if evidence_signals:
            audience_sources.append("behavior_inference")
            inferred_segments.extend(
                signal.seed_label
                for signal in evidence_signals
                if signal.topic_type in {"audience", "scenario"}
            )
        unique_segments = []
        for value in inferred_segments:
            clipped = value.strip()[:18]
            if clipped and clipped not in unique_segments:
                unique_segments.append(clipped)
        confidence = "low"
        if len(audience_sources) >= 2 and len(unique_segments) >= 2:
            confidence = "medium"

        gap_hypotheses: list[dict[str, Any]] = []
        for topic_type in competitor_scan.scan_summary.get("top_topic_types", []):
            market_signal = float(market_avg.get(topic_type, 0.0))
            if topic_type in high_performing or market_signal < 0.45:
                continue
            support_count = sum(1 for signal in evidence_signals if signal.topic_type == topic_type)
            if support_count == 0:
                continue
            gap_hypotheses.append(
                {
                    "gap_type": "underserved_scenario" if topic_type == "scenario" else "market_gap",
                    "topic_type": topic_type,
                    "description": f"{brand.name} 在 {topic_type} 方向覆盖不足，但市场证据显示存在增长机会。",
                    "supporting_evidence_count": support_count,
                }
            )

        insight_summary = {
            "owned_content_patterns": {
                "high_performing_topic_types": high_performing,
                "underperforming_topic_types": underperforming,
                "avg_composite_reward_by_type": avg_owned_reward_by_type,
            },
            "market_patterns": {
                "rising_topic_types": rising_topic_types,
                "saturation_signals": saturation_signals,
            },
            "audience_signals": {
                "source": audience_sources,
                "inferred_segments": unique_segments[:5],
                "confidence": confidence,
            },
            "content_gap_hypotheses": gap_hypotheses,
        }
        return _PatternInsightResult(
            agent_run_id=str(uuid.uuid4()),
            analyzed_at=now.isoformat(),
            insight_summary=insight_summary,
        )

    def _to_evidence_signal(self, item: ContentItemRecord) -> _EvidenceSignal | None:
        seed_label = self._extract_seed_label(item)
        normalized_name = self._normalize_name(seed_label)
        if not normalized_name:
            return None
        metrics = self._latest_metrics(item.id)
        combined_text = self._combine_text(item)
        signal_type = self._infer_signal_type(item=item, combined_text=combined_text)
        topic_type = self._infer_topic_type(seed_label=seed_label, combined_text=combined_text)
        score = self._compute_engagement_proxy(metrics)
        return _EvidenceSignal(
            item=item,
            signal_type=signal_type,
            topic_type=topic_type,
            seed_label=seed_label,
            normalized_name=normalized_name,
            score=score,
        )

    def _get_or_create_topic(
        self,
        *,
        brand: BrandRecord,
        group: _ProposalSeed,
        now,
    ) -> TopicRecord:
        existing = self._ingestion_store.get_topic_by_normalized_name(
            brand_id=brand.id,
            normalized_name=group.normalized_name,
        )
        topic = TopicRecord(
            id=existing.id if existing else str(uuid.uuid4()),
            workspace_id=brand.workspace_id,
            brand_id=brand.id,
            normalized_name=group.normalized_name,
            display_name=group.display_name,
            topic_type=group.topic_type,
            metadata={
                "source": "topic_pool_refresh",
                "evidence_count": len(group.evidence),
            },
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        return self._ingestion_store.save_topic(topic)

    def _build_topic_pool_item(
        self,
        *,
        brand: BrandRecord,
        topic: TopicRecord,
        group: _ProposalSeed,
        refresh_run_id: str,
        now,
    ) -> TopicPoolItemRecord:
        evidence_summary = self._assemble_evidence_summary(group.evidence, now=now)
        evidence_summary = {
            **evidence_summary,
            "topic_type": topic.topic_type,
            "gap_type": group.gap_type,
            "gap_description": group.gap_description,
            "agent_lineage": group.agent_lineage,
            "competitor_scan_summary": group.competitor_scan_summary,
            "insight_summary": group.insight_summary,
        }
        title = self._build_title(group.display_name, group.topic_type)
        angle = self._build_angle(group.topic_type, gap_description=group.gap_description)
        hypothesis = self._build_hypothesis(
            brand=brand,
            display_name=group.display_name,
            evidence_count=len(group.evidence),
            dominant_signal_type=evidence_summary["dominant_signal_type"],
            gap_description=group.gap_description,
        )
        self._validate_required_candidate_fields(
            normalized_name=topic.normalized_name,
            display_name=topic.display_name,
            topic_type=topic.topic_type,
            title=title,
            angle=angle,
            hypothesis=hypothesis,
        )

        existing = self._topic_pool_store.get_topic_pool_item_by_topic(brand_id=brand.id, topic_id=topic.id)
        return TopicPoolItemRecord(
            id=existing.id if existing else str(uuid.uuid4()),
            workspace_id=brand.workspace_id,
            brand_id=brand.id,
            topic_id=topic.id,
            title=title,
            angle=angle,
            hypothesis=hypothesis,
            evidence_summary=evidence_summary,
            source_agent=_TOPIC_AGENT_NAME,
            source_run_id=refresh_run_id,
            status="candidate",
            novelty_score=0.0,
            fit_score=0.0,
            trend_score=0.0,
            historical_reward_score=0.0,
            policy_score=0.0,
            final_score=0.0,
            last_scored_at=None,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )

    def _assemble_evidence_summary(self, evidence: list[_EvidenceSignal], *, now) -> dict[str, Any]:
        unique_by_item: dict[str, _EvidenceSignal] = {}
        for signal in evidence:
            unique_by_item.setdefault(signal.item.id, signal)

        unique_signals = sorted(
            unique_by_item.values(),
            key=lambda item: (-item.score, item.item.id),
        )
        if not unique_signals:
            raise TopicPoolValidationError("supporting_evidence_ids must not be empty")

        source_count = len(unique_signals)
        weight = round(1 / source_count, 6)
        signal_counts = Counter(signal.signal_type for signal in unique_signals)
        dominant_signal_type = sorted(
            signal_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[0][0]
        return {
            "sources": [
                {
                    "item_id": signal.item.id,
                    "signal_type": signal.signal_type,
                    "weight": weight,
                    "signal_score": signal.score,
                }
                for signal in unique_signals
            ],
            "source_count": source_count,
            "dominant_signal_type": dominant_signal_type,
            "snapshot_at": now.isoformat(),
        }

    def _build_score_breakdown(self, item: TopicPoolItemRecord) -> dict[str, Any]:
        if self._scorer_service is not None:
            return self._scorer_service.build_score_breakdown(item)
        return {
            "novelty_score": round(item.novelty_score, 4),
            "fit_score": round(item.fit_score, 4),
            "trend_score": round(item.trend_score, 4),
            "historical_reward_score": round(item.historical_reward_score, 4),
            "policy_score": round(item.policy_score, 4),
            "final_score": round(item.final_score, 4),
        }

    def _build_evidence_provenance(
        self,
        *,
        evidence_summary: dict[str, Any],
        content_item_map: dict[str, ContentItemRecord],
    ) -> list[dict[str, Any]]:
        provenance: list[dict[str, Any]] = []
        sources = evidence_summary.get("sources", [])
        if not isinstance(sources, list):
            return provenance
        for source in sources:
            if not isinstance(source, dict):
                continue
            item_id = str(source.get("item_id") or "")
            content_item = content_item_map.get(item_id)
            metrics = self._latest_metrics(item_id) if item_id else None
            provenance.append(
                {
                    "item_id": item_id,
                    "source_url": content_item.source_url if content_item else None,
                    "original_title": (content_item.title if content_item and content_item.title else item_id) or "unknown",
                    "signal_type": str(source.get("signal_type") or "unknown"),
                    "contribution_weight": round(float(source.get("weight", 0.0)), 6),
                    "signal_score": round(float(source.get("signal_score", 0.0)), 4),
                    "likes": metrics.likes if metrics else 0,
                    "comments": metrics.comments if metrics else 0,
                    "collects": metrics.collects if metrics else 0,
                    "shares": metrics.shares if metrics else 0,
                }
            )
        return provenance

    def _archive_stale_candidates(self, *, brand_id: str, archive_threshold_days: int, now) -> int:
        archived = 0
        stale_before = now - timedelta(days=archive_threshold_days)
        for item in self._topic_pool_store.list_topic_pool_items(brand_id, include_archived=True):
            if item.status == "archived":
                continue
            if item.updated_at >= stale_before:
                continue
            self._topic_pool_store.save_topic_pool_item(
                TopicPoolItemRecord(
                    id=item.id,
                    workspace_id=item.workspace_id,
                    brand_id=item.brand_id,
                    topic_id=item.topic_id,
                    title=item.title,
                    angle=item.angle,
                    hypothesis=item.hypothesis,
                    evidence_summary=item.evidence_summary,
                    source_agent=item.source_agent,
                    source_run_id=item.source_run_id,
                    status="archived",
                    novelty_score=item.novelty_score,
                    fit_score=item.fit_score,
                    trend_score=item.trend_score,
                    historical_reward_score=item.historical_reward_score,
                    policy_score=item.policy_score,
                    final_score=item.final_score,
                    last_scored_at=item.last_scored_at,
                    created_at=item.created_at,
                    updated_at=now,
                )
            )
            archived += 1
        return archived

    def _latest_metrics(self, content_item_id: str) -> ContentMetricsSnapshotRecord | None:
        snapshots = self._ingestion_store.list_metrics_snapshots(content_item_id)
        if not snapshots:
            return None
        return snapshots[-1]

    @staticmethod
    def _compute_engagement_proxy(metrics: ContentMetricsSnapshotRecord | None) -> float:
        if metrics is None:
            return 0.25
        raw = (
            metrics.likes * 1.0
            + metrics.comments * 1.4
            + metrics.collects * 1.6
            + metrics.shares * 1.8
        )
        return round(min(1.0, raw / 400), 4)

    @staticmethod
    def _combine_text(item: ContentItemRecord) -> str:
        return " ".join(
            part
            for part in (
                item.title or "",
                item.body_text or "",
                " ".join(item.tags),
            )
            if part
        )

    def _extract_seed_label(self, item: ContentItemRecord) -> str:
        if item.tags:
            for tag in item.tags:
                normalized = self._normalize_name(tag)
                if normalized:
                    return tag.strip()[:18]
        for source in (item.title, item.body_text):
            if not source:
                continue
            candidate = source.strip().split("，")[0].split(" ")[0].split("：")[0].strip()
            candidate = candidate[:18]
            if self._normalize_name(candidate):
                return candidate
        return ""

    def _infer_signal_type(self, *, item: ContentItemRecord, combined_text: str) -> str:
        lowered = combined_text.lower()
        if any(keyword in lowered for keyword in ("趋势", "热门", "爆款", "热度", "trend")):
            return "trend"
        if item.brand_id and item.source_type == "historical_note_import_v1":
            return "owned_performance"
        if any(keyword in lowered for keyword in ("问题", "痛点", "踩坑", "敏感", "不便", "焦虑")):
            return "gap"
        return "engagement"

    def _infer_topic_type(self, *, seed_label: str, combined_text: str) -> str:
        text = f"{seed_label} {combined_text}"
        if any(keyword in text for keyword in ("学生", "宝妈", "白领", "新手", "人群", "女生", "男生")):
            return "audience"
        if any(keyword in text for keyword in ("通勤", "露营", "周末", "办公室", "上下班", "场景", "旅行")):
            return "scenario"
        if any(keyword in text for keyword in ("问题", "痛点", "踩坑", "敏感", "不便", "焦虑")):
            return "problem"
        if any(keyword in text for keyword in ("竞品", "对标", "同行")):
            return "competitor"
        if any(keyword in text for keyword in ("趋势", "热门", "爆款", "热度")):
            return "trend"
        return "core"

    @staticmethod
    def _build_title(display_name: str, topic_type: str) -> str:
        templates = {
            "scenario": f"{display_name} 场景下还能怎么讲",
            "problem": f"{display_name} 的用户问题拆解",
            "audience": f"围绕 {display_name} 的内容机会",
            "competitor": f"{display_name} 的竞品切口复盘",
            "trend": f"{display_name} 的趋势选题机会",
            "core": f"{display_name} 的核心内容方向",
        }
        return templates.get(topic_type, templates["core"])

    @staticmethod
    def _build_angle(topic_type: str, *, gap_description: str = "") -> str:
        templates = {
            "scenario": "从具体使用场景切入，强调真实体验与可执行建议。",
            "problem": "从用户痛点和常见误区切入，强调解决方案。",
            "audience": "围绕目标人群的需求、表达和转化场景切入。",
            "competitor": "从竞品高信号内容抽取结构，但避免同质化复刻。",
            "trend": "结合近期高热话题切入，强调时效性与品牌关联。",
            "core": "围绕品牌核心卖点切入，强调长期可复用内容结构。",
        }
        base = templates.get(topic_type, templates["core"])
        if gap_description:
            return f"{base} 当前模式洞察：{gap_description}"
        return base

    @staticmethod
    def _build_hypothesis(
        *,
        brand: BrandRecord,
        display_name: str,
        evidence_count: int,
        dominant_signal_type: str,
        gap_description: str = "",
    ) -> str:
        signal_label = {
            "engagement": "高互动证据",
            "gap": "内容缺口证据",
            "trend": "趋势证据",
            "owned_performance": "自有历史表现证据",
        }.get(dominant_signal_type, dominant_signal_type)
        hypothesis = (
            f"基于 {evidence_count} 条已入库证据，{display_name} 在 {brand.name} 当前阶段"
            f"具备可验证的内容机会，主导信号来自 {signal_label}。"
        )
        if gap_description:
            return f"{hypothesis} 重点洞察：{gap_description}"
        return hypothesis

    @classmethod
    def _normalize_name(cls, value: str) -> str:
        normalized = _NON_WORD_RE.sub("-", value.strip().lower()).strip("-")
        return normalized[:48]

    @staticmethod
    def _validate_required_candidate_fields(
        *,
        normalized_name: str,
        display_name: str,
        topic_type: str,
        title: str,
        angle: str,
        hypothesis: str,
    ) -> None:
        required = {
            "normalized_name": normalized_name,
            "display_name": display_name,
            "topic_type": topic_type,
            "title": title,
            "angle": angle,
            "hypothesis": hypothesis,
        }
        for field_name, value in required.items():
            if not value or not str(value).strip():
                raise TopicPoolValidationError(f"missing required field: {field_name}")
