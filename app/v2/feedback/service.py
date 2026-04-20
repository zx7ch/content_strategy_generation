"""Deterministic publish, performance, and evaluation service for V2 P1-5."""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from app.v2.decision.store import DecisionStore
from app.v2.feedback.models import (
    EvaluationRunDetail,
    EvaluationRunRecord,
    EvaluationRunSliceRecord,
    FeedbackEventRecord,
    PerformanceSnapshotRecord,
    PerformanceSnapshotView,
    PublishRecordRecord,
    PublishRecordView,
)
from app.v2.feedback.store import FeedbackStore
from app.v2.foundation.models import utcnow
from app.v2.foundation.service import MasterDataNotFoundError, MasterDataService, MasterDataScopeError
from app.v2.topic_pool.store import TopicPoolStore


class FeedbackError(ValueError):
    """Raised when publish/performance/evaluation operations fail."""


class FeedbackValidationError(FeedbackError):
    """Raised when the caller sends an invalid payload."""


class FeedbackNotFoundError(FeedbackError):
    """Raised when a requested publish/performance/evaluation entity is missing."""


class FeedbackService:
    def __init__(
        self,
        *,
        master_data_service: MasterDataService,
        topic_pool_store: TopicPoolStore,
        decision_store: DecisionStore,
        feedback_store: FeedbackStore,
    ) -> None:
        self._master_data_service = master_data_service
        self._topic_pool_store = topic_pool_store
        self._decision_store = decision_store
        self._feedback_store = feedback_store

    def create_publish_record(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        channel_id: str,
        publish_status: str,
        topic_pool_item_id: str | None = None,
        decision_event_id: str | None = None,
        decision_batch_id: str | None = None,
        published_at: datetime | None = None,
        content_item_id: str | None = None,
        creative_variant: str | None = None,
    ) -> PublishRecordRecord:
        self._master_data_service.get_brand(workspace_id=workspace_id, brand_id=brand_id)
        channels = self._master_data_service.list_brand_channels(workspace_id=workspace_id, brand_id=brand_id)
        channel = next((item for item in channels if item.id == channel_id), None)
        if channel is None:
            raise FeedbackValidationError(f"Channel not found for brand: {channel_id}")

        resolved_batch_id = decision_batch_id
        resolved_topic_pool_item_id = topic_pool_item_id
        if decision_event_id:
            event = self._decision_store.get_decision_event(decision_event_id)
            if event is None:
                raise FeedbackValidationError(f"Decision event not found: {decision_event_id}")
            if event.workspace_id != workspace_id or event.brand_id != brand_id:
                raise MasterDataScopeError(f"Decision event does not belong to workspace or brand: {decision_event_id}")
            if resolved_batch_id and resolved_batch_id != event.decision_batch_id:
                raise FeedbackValidationError("decision_batch_id does not match decision_event_id lineage")
            resolved_batch_id = event.decision_batch_id
            resolved_topic_pool_item_id = resolved_topic_pool_item_id or event.chosen_action_id
        elif resolved_batch_id:
            raise FeedbackValidationError("decision_batch_id requires decision_event_id in Phase 1")

        topic_type = None
        if resolved_topic_pool_item_id:
            topic_item = self._topic_pool_store.get_topic_pool_item(resolved_topic_pool_item_id)
            if topic_item is not None and topic_item.brand_id == brand_id:
                topic_type = str(topic_item.evidence_summary.get("topic_type") or "")

        now = utcnow()
        record = PublishRecordRecord(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            brand_id=brand_id,
            channel_id=channel_id,
            topic_pool_item_id=resolved_topic_pool_item_id,
            decision_event_id=decision_event_id,
            decision_batch_id=resolved_batch_id,
            publish_status=publish_status,
            published_at=published_at,
            content_item_id=content_item_id,
            creative_variant=creative_variant,
            metadata={"topic_type": topic_type} if topic_type else {},
            created_at=now,
            updated_at=now,
        )
        return self._feedback_store.save_publish_record(record)

    def list_publish_records(self, *, workspace_id: str, brand_id: str) -> list[PublishRecordView]:
        self._master_data_service.get_brand(workspace_id=workspace_id, brand_id=brand_id)
        channels = {
            item.id: item for item in self._master_data_service.list_brand_channels(workspace_id=workspace_id, brand_id=brand_id)
        }
        topics = {
            item.id: item for item in self._topic_pool_store.list_topic_pool_items(brand_id, include_archived=True)
        }
        views: list[PublishRecordView] = []
        for record in self._feedback_store.list_publish_records(brand_id):
            title = "手动发布"
            decision_source = "Manual"
            if record.topic_pool_item_id and record.topic_pool_item_id in topics:
                title = topics[record.topic_pool_item_id].title
            if record.decision_event_id:
                event = self._decision_store.get_decision_event(record.decision_event_id)
                if event is not None:
                    decision_source = f"Batch {event.decision_batch_id[:8]} · Slot {event.slot_index + 1}"
            channel = channels.get(record.channel_id)
            channel_label = channel.account_name or channel.profile_url or channel.platform if channel else record.channel_id
            views.append(
                PublishRecordView(
                    publish_record_id=record.id,
                    brand_id=record.brand_id,
                    channel_id=record.channel_id,
                    channel_label=channel_label,
                    title=title,
                    topic_pool_item_id=record.topic_pool_item_id,
                    decision_event_id=record.decision_event_id,
                    decision_batch_id=record.decision_batch_id,
                    decision_source=decision_source,
                    publish_status=record.publish_status,
                    published_at=record.published_at,
                    creative_variant=record.creative_variant,
                    created_at=record.created_at,
                )
            )
        return views

    def import_performance_snapshot(
        self,
        *,
        workspace_id: str,
        publish_record_id: str,
        observation_window_hours: int,
        snapshot_at: datetime,
        reward_version: str,
        metrics: dict[str, Any],
    ) -> PerformanceSnapshotRecord:
        if observation_window_hours <= 0:
            raise FeedbackValidationError("observation_window_hours must be > 0")
        if reward_version.strip() == "":
            raise FeedbackValidationError("reward_version is required")
        publish_record = self._feedback_store.get_publish_record(publish_record_id)
        if publish_record is None:
            raise FeedbackNotFoundError(f"Publish record not found: {publish_record_id}")
        if publish_record.workspace_id != workspace_id:
            raise MasterDataScopeError(f"Publish record does not belong to workspace: {publish_record_id}")

        reward = self._compute_reward(metrics)
        now = utcnow()
        snapshot = self._feedback_store.save_performance_snapshot(
            PerformanceSnapshotRecord(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                brand_id=publish_record.brand_id,
                publish_record_id=publish_record.id,
                observation_window_hours=observation_window_hours,
                snapshot_at=snapshot_at,
                reward_version=reward_version,
                raw_metrics=metrics,
                normalized_metrics=reward["normalized_metrics"],
                short_term_reward=reward["short_term_reward"],
                long_term_reward=reward["long_term_reward"],
                composite_reward=reward["composite_reward"],
                metadata={},
                created_at=now,
            )
        )

        if publish_record.decision_event_id:
            reward_window_start_at = (publish_record.published_at or snapshot_at) - timedelta(hours=1)
            reward_window_end_at = reward_window_start_at + timedelta(hours=observation_window_hours)
            self._feedback_store.save_feedback_event(
                FeedbackEventRecord(
                    id=str(uuid.uuid4()),
                    workspace_id=workspace_id,
                    brand_id=publish_record.brand_id,
                    publish_record_id=publish_record.id,
                    decision_event_id=publish_record.decision_event_id,
                    event_type="reward_observed",
                    observation_window_hours=observation_window_hours,
                    reward_version=reward_version,
                    reward_window_start_at=reward_window_start_at,
                    reward_window_end_at=reward_window_end_at,
                    reward_payload={
                        "publish_record_id": publish_record.id,
                        "performance_snapshot_id": snapshot.id,
                        "normalized_rewards": {
                            "short_term_reward": snapshot.short_term_reward,
                            "long_term_reward": snapshot.long_term_reward,
                            "composite_reward": snapshot.composite_reward,
                        },
                        "metrics": metrics,
                    },
                    created_at=now,
                )
            )

        return snapshot

    def list_performance_snapshots(
        self,
        *,
        workspace_id: str,
        brand_id: str,
    ) -> list[PerformanceSnapshotView]:
        self._master_data_service.get_brand(workspace_id=workspace_id, brand_id=brand_id)
        publish_lookup = {item.id: item for item in self._feedback_store.list_publish_records(brand_id)}
        title_lookup = {item.publish_record_id: item.title for item in self.list_publish_records(workspace_id=workspace_id, brand_id=brand_id)}
        views: list[PerformanceSnapshotView] = []
        for snapshot in self._feedback_store.list_performance_snapshots(brand_id):
            raw = snapshot.raw_metrics or {}
            conversion_proxy = raw.get("conversion_proxy") if isinstance(raw.get("conversion_proxy"), dict) else None
            proxy_label = "-"
            if conversion_proxy:
                proxy_label = f"{float(conversion_proxy.get('value', 0)):.1%} ({conversion_proxy.get('type', 'proxy')})"
            views.append(
                PerformanceSnapshotView(
                    performance_snapshot_id=snapshot.id,
                    publish_record_id=snapshot.publish_record_id,
                    publish_title=title_lookup.get(snapshot.publish_record_id, "未命名发布"),
                    observation_window_hours=snapshot.observation_window_hours,
                    snapshot_at=snapshot.snapshot_at,
                    reward_version=snapshot.reward_version,
                    impressions=int(raw.get("impressions") or 0),
                    clicks=int(raw.get("clicks") or 0),
                    engagement_rate=float(snapshot.normalized_metrics.get("engagement_rate", 0.0)),
                    conversion_proxy_label=proxy_label,
                    short_term_reward=snapshot.short_term_reward,
                    composite_reward=snapshot.composite_reward,
                )
            )
        return views

    def create_evaluation_run(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        evaluation_type: str = "replay",
        created_by_id: str | None = None,
    ) -> EvaluationRunDetail:
        brand = self._master_data_service.get_brand(workspace_id=workspace_id, brand_id=brand_id)
        snapshots = self._feedback_store.list_performance_snapshots(brand_id)
        publish_lookup = {item.id: item for item in self._feedback_store.list_publish_records(brand_id)}
        feedback_events = self._feedback_store.list_feedback_events(brand_id)
        feedback_by_publish = {item.publish_record_id: item for item in feedback_events if item.decision_event_id}

        dataset: list[dict[str, Any]] = []
        invalid_reasons: list[str] = []
        snapshot_ids = {
            item.id
            for item in self._master_data_service.list_brand_state_snapshots(
                workspace_id=workspace_id,
                brand_id=brand_id,
            )
        }
        for snapshot in snapshots:
            publish = publish_lookup.get(snapshot.publish_record_id)
            if publish is None or publish.decision_event_id is None:
                continue
            event = self._decision_store.get_decision_event(publish.decision_event_id)
            if event is None:
                invalid_reasons.append(f"missing decision_event:{publish.decision_event_id}")
                continue
            if not event.candidate_set or not event.propensities or not event.reward_version:
                invalid_reasons.append(f"incomplete decision_event:{event.id}")
                continue
            if publish.id not in feedback_by_publish:
                invalid_reasons.append(f"missing feedback_event:{publish.id}")
                continue
            chosen_propensity = self._find_propensity(event.propensities, event.chosen_action_id)
            if chosen_propensity is None or chosen_propensity <= 0:
                invalid_reasons.append(f"missing propensity:{event.id}")
                continue
            if not event.brand_state_snapshot_id or event.brand_state_snapshot_id not in snapshot_ids:
                invalid_reasons.append(f"missing state_snapshot:{event.id}")
                continue
            top_ranked_id = str((event.ranked_list or [{}])[0].get("topic_pool_item_id") or "")
            topic_type = str(event.context_features.get("topic_type") or "unknown")
            dataset.append(
                {
                    "decision_event": event,
                    "publish": publish,
                    "snapshot": snapshot,
                    "reward": snapshot.composite_reward,
                    "chosen_propensity": chosen_propensity,
                    "top_ranked_id": top_ranked_id,
                    "matches_target": top_ranked_id == event.chosen_action_id,
                    "topic_type": topic_type,
                    "brand_stage": brand.stage,
                }
            )

        if invalid_reasons:
            raise FeedbackValidationError(
                "evaluation dataset is incomplete for replay-critical lineage: " + ", ".join(sorted(invalid_reasons))
            )
        if not dataset:
            raise FeedbackValidationError("evaluation dataset is empty for the selected brand")

        weights = [1.0 / row["chosen_propensity"] if row["matches_target"] else 0.0 for row in dataset]
        replay_rewards = [row["reward"] for row in dataset if row["matches_target"]]
        supported_count = sum(1 for weight in weights if weight > 0)
        coverage_rate = supported_count / len(dataset)
        replay_value = sum(replay_rewards) / supported_count if supported_count else 0.0
        total_weight = sum(weights)
        snips_value = (
            sum(weight * row["reward"] for weight, row in zip(weights, dataset)) / total_weight
            if total_weight > 0
            else 0.0
        )
        ess = (total_weight ** 2) / sum(weight ** 2 for weight in weights) if total_weight > 0 else 0.0
        ess_ratio = ess / len(dataset)
        importance_weights = [weight for weight in weights if weight > 0]
        candidate_metrics = self._candidate_quality_metrics(dataset)
        exploration_entropy = self._average_exploration_entropy(dataset)
        summary = {
            "estimated_policy_value": round(replay_value, 4),
            "baseline_policy_value": round(snips_value, 4),
            "delta_vs_baseline": round(replay_value - snips_value, 4),
            "sample_count": len(dataset),
            "coverage_rate": round(coverage_rate, 4),
            "effective_sample_size": round(ess, 4),
            "ess_ratio": round(ess_ratio, 4),
            "p95_importance_weight": round(self._percentile(importance_weights, 0.95), 4),
            "max_importance_weight": round(max(importance_weights) if importance_weights else 0.0, 4),
            "unsupported_rate": round(1 - coverage_rate, 4),
            "exploration_entropy": round(exploration_entropy, 4),
            "failure_slices": self._build_failure_slices(dataset, ess_ratio=ess_ratio, coverage_rate=coverage_rate),
            "candidate_quality": candidate_metrics,
            "guardrails": {
                "low_ess": ess_ratio < 0.2,
                "unsupported_high": (1 - coverage_rate) > 0.3,
            },
        }
        now = utcnow()
        policy_name = dataset[0]["decision_event"].serving_policy_name
        policy_version = dataset[0]["decision_event"].serving_policy_version
        run = self._feedback_store.save_evaluation_run(
            EvaluationRunRecord(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                brand_id=brand_id,
                evaluation_type=evaluation_type,
                policy_name=policy_name,
                policy_version=policy_version,
                baseline_policy_name=dataset[0]["decision_event"].logging_policy_name,
                baseline_policy_version=dataset[0]["decision_event"].logging_policy_version,
                dataset_start_at=min(row["snapshot"].snapshot_at for row in dataset),
                dataset_end_at=max(row["snapshot"].snapshot_at for row in dataset),
                sample_count=len(dataset),
                status="completed",
                summary=summary,
                created_by_type="operator",
                created_by_id=created_by_id,
                created_at=now,
                finished_at=now,
            )
        )
        slices = self._persist_evaluation_slices(run.id, dataset)
        return EvaluationRunDetail(
            evaluation_run_id=run.id,
            brand_id=brand_id,
            evaluation_type=run.evaluation_type,
            policy_name=run.policy_name,
            policy_version=run.policy_version,
            status=run.status,
            sample_count=run.sample_count,
            summary=run.summary,
            slices=slices,
            created_at=run.created_at,
            finished_at=run.finished_at,
        )

    def get_evaluation_run(self, *, workspace_id: str, evaluation_run_id: str) -> EvaluationRunDetail:
        run = self._feedback_store.get_evaluation_run(evaluation_run_id)
        if run is None:
            raise FeedbackNotFoundError(f"Evaluation run not found: {evaluation_run_id}")
        if run.workspace_id != workspace_id:
            raise MasterDataScopeError(f"Evaluation run does not belong to workspace: {evaluation_run_id}")
        return EvaluationRunDetail(
            evaluation_run_id=run.id,
            brand_id=run.brand_id,
            evaluation_type=run.evaluation_type,
            policy_name=run.policy_name,
            policy_version=run.policy_version,
            status=run.status,
            sample_count=run.sample_count,
            summary=run.summary,
            slices=self._feedback_store.list_evaluation_run_slices(run.id),
            created_at=run.created_at,
            finished_at=run.finished_at,
        )

    def get_latest_evaluation_run(self, *, workspace_id: str, brand_id: str) -> EvaluationRunDetail:
        self._master_data_service.get_brand(workspace_id=workspace_id, brand_id=brand_id)
        runs = self._feedback_store.list_evaluation_runs(brand_id)
        if not runs:
            raise FeedbackNotFoundError(f"No evaluation run found for brand: {brand_id}")
        latest = runs[0]
        if latest.workspace_id != workspace_id:
            raise MasterDataScopeError(f"Evaluation run does not belong to workspace: {latest.id}")
        return self.get_evaluation_run(workspace_id=workspace_id, evaluation_run_id=latest.id)

    def _persist_evaluation_slices(
        self,
        evaluation_run_id: str,
        dataset: list[dict[str, Any]],
    ) -> list[EvaluationRunSliceRecord]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in dataset:
            grouped[("brand_stage", str(row["brand_stage"]))].append(row)
            grouped[("topic_type", str(row["topic_type"]))].append(row)
        slices: list[EvaluationRunSliceRecord] = []
        now = utcnow()
        for (slice_key, slice_value), rows in grouped.items():
            reward_avg = sum(float(row["reward"]) for row in rows) / len(rows)
            coverage = sum(1 for row in rows if row["matches_target"]) / len(rows)
            slice_record = self._feedback_store.save_evaluation_run_slice(
                EvaluationRunSliceRecord(
                    id=str(uuid.uuid4()),
                    evaluation_run_id=evaluation_run_id,
                    slice_key=slice_key,
                    slice_value=slice_value,
                    sample_count=len(rows),
                    metrics={
                        "coverage_rate": round(coverage, 4),
                        "estimated_policy_value": round(reward_avg, 4),
                    },
                    created_at=now,
                )
            )
            slices.append(slice_record)
        return slices

    def _build_failure_slices(
        self,
        dataset: list[dict[str, Any]],
        *,
        ess_ratio: float,
        coverage_rate: float,
    ) -> list[dict[str, Any]]:
        slices: list[dict[str, Any]] = []
        if ess_ratio < 0.2:
            slices.append(
                {
                    "slice": "全局 ESS",
                    "issue": f"ESS ratio 过低 ({ess_ratio:.2f})，估计可能不稳定",
                    "action": "增加探索覆盖或扩大样本窗口",
                }
            )
        by_topic_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in dataset:
            by_topic_type[str(row["topic_type"])].append(row)
        for topic_type, rows in by_topic_type.items():
            topic_coverage = sum(1 for row in rows if row["matches_target"]) / len(rows)
            if topic_coverage < 0.5:
                slices.append(
                    {
                        "slice": f"topic_type:{topic_type}",
                        "issue": f"coverage 偏低 ({topic_coverage:.2f})",
                        "action": "提高该 topic_type 的探索流量或补充候选多样性",
                    }
                )
        if not slices and coverage_rate >= 0.5:
            slices.append(
                {
                    "slice": "全局",
                    "issue": "未发现显著失败切片",
                    "action": "继续积累样本并监控 ESS / unsupported rate",
                }
            )
        return slices

    def _candidate_quality_metrics(self, dataset: list[dict[str, Any]]) -> dict[str, Any]:
        pool_sizes: list[int] = []
        novelty_scores: list[float] = []
        duplicate_rates: list[float] = []
        available_types: set[str] = set()
        selected_types: set[str] = set()
        for row in dataset:
            candidate_set = row["decision_event"].candidate_set or []
            pool_sizes.append(len(candidate_set))
            titles = [str(item.get("title") or item.get("topic_pool_item_id") or "") for item in candidate_set]
            unique_titles = {title for title in titles if title}
            duplicate_rates.append(0.0 if not titles else 1 - len(unique_titles) / len(titles))
            novelty_scores.extend(
                float(item.get("novelty_score") or 0.0) for item in candidate_set if isinstance(item, dict)
            )
            available_types.update(
                str(item.get("topic_type") or "unknown") for item in candidate_set if isinstance(item, dict)
            )
            selected_types.add(str(row["topic_type"]))
        topic_type_coverage = (
            len(selected_types) / len(available_types) if available_types else 0.0
        )
        return {
            "candidate_pool_size": round(sum(pool_sizes) / len(pool_sizes), 4) if pool_sizes else 0.0,
            "candidate_diversity_score": round(1 - (sum(duplicate_rates) / len(duplicate_rates)), 4)
            if duplicate_rates
            else 0.0,
            "candidate_novelty_score": round(sum(novelty_scores) / len(novelty_scores), 4)
            if novelty_scores
            else 0.0,
            "topic_type_coverage": round(topic_type_coverage, 4),
            "duplicate_topic_rate": round(sum(duplicate_rates) / len(duplicate_rates), 4) if duplicate_rates else 0.0,
        }

    def _average_exploration_entropy(self, dataset: list[dict[str, Any]]) -> float:
        entropies: list[float] = []
        for row in dataset:
            probabilities = [
                float(item.get("probability") or 0.0)
                for item in row["decision_event"].propensities
                if float(item.get("probability") or 0.0) > 0
            ]
            if not probabilities:
                continue
            entropies.append(-sum(probability * math.log(probability) for probability in probabilities))
        if not entropies:
            return 0.0
        return sum(entropies) / len(entropies)

    def _find_propensity(self, propensities: list[dict[str, Any]], topic_pool_item_id: str) -> float | None:
        for item in propensities:
            if str(item.get("topic_pool_item_id")) == topic_pool_item_id:
                return float(item.get("probability") or 0.0)
        return None

    def _percentile(self, values: list[float], fraction: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
        return ordered[index]

    def _compute_reward(self, metrics: dict[str, Any]) -> dict[str, Any]:
        impressions = max(1, int(metrics.get("impressions") or 0))
        clicks = max(0, int(metrics.get("clicks") or 0))
        likes = max(0, int(metrics.get("likes") or 0))
        comments = max(0, int(metrics.get("comments") or 0))
        collects = max(0, int(metrics.get("collects") or 0))
        shares = max(0, int(metrics.get("shares") or 0))
        follows_gained = max(0, int(metrics.get("follows_gained") or 0))
        conversion_proxy = metrics.get("conversion_proxy") if isinstance(metrics.get("conversion_proxy"), dict) else {}
        conversion_value = float(conversion_proxy.get("value") or 0.0)
        if conversion_value < 0 or conversion_value > 1:
            raise FeedbackValidationError("metrics.conversion_proxy.value must be within [0, 1]")

        engagement_rate = (likes + comments + collects + shares) / impressions
        ctr = clicks / impressions
        follows_rate = follows_gained / impressions
        normalized = {
            "engagement_rate": round(min(1.0, engagement_rate), 6),
            "ctr": round(min(1.0, ctr), 6),
            "follows_rate": round(min(1.0, follows_rate), 6),
            "conversion_rate": round(min(1.0, conversion_value), 6),
        }
        short_term = min(1.0, 4.0 * normalized["engagement_rate"] + 1.5 * normalized["ctr"] + 0.5 * normalized["conversion_rate"])
        long_term = min(1.0, 0.7 * normalized["conversion_rate"] + 0.3 * min(1.0, normalized["follows_rate"] * 20))
        composite = 0.6 * short_term + 0.4 * long_term
        return {
            "normalized_metrics": normalized,
            "short_term_reward": round(short_term, 4),
            "long_term_reward": round(long_term, 4),
            "composite_reward": round(composite, 4),
        }
