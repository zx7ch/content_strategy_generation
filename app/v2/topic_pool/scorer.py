"""Deterministic scorer boundary for V2 Phase 1 topic-pool items."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

from app.v2.feedback.store import FeedbackStore
from app.v2.foundation.models import BrandPolicyConfigRecord, BrandRecord, utcnow
from app.v2.foundation.service import MasterDataService
from app.v2.topic_pool.models import TopicPoolItemRecord
from app.v2.topic_pool.store import TopicPoolStore

_DEFAULT_CONFIDENCE_THRESHOLD = 3
_DEFAULT_MAX_AGE = timedelta(hours=6)


class ScorerValidationError(ValueError):
    """Raised when scorer input or policy shape is invalid."""


class BrandFitEvaluator:
    def evaluate(
        self,
        *,
        item: TopicPoolItemRecord,
        policy: BrandPolicyConfigRecord | None,
    ) -> dict[str, Any]:
        hard_filter_rules = policy.hard_filter_rules if policy else {}
        brand_fit_rules = policy.brand_fit_rules if policy else {}
        topic_type = str(item.evidence_summary.get("topic_type") or "core")
        violations: list[str] = []

        banned_topic_types = self._string_list(hard_filter_rules.get("blocked_topic_types"))
        if topic_type in banned_topic_types:
            violations.append(f"blocked_topic_type:{topic_type}")

        banned_terms = self._string_list(hard_filter_rules.get("blocked_terms"))
        combined_text = " ".join(
            part.lower()
            for part in (
                item.title,
                item.angle,
                item.hypothesis,
            )
            if isinstance(part, str)
        )
        for term in banned_terms:
            if term.lower() in combined_text:
                violations.append(f"blocked_term:{term}")

        preferred_topic_types = self._string_list(brand_fit_rules.get("preferred_topic_types"))
        topic_type_bonus = 0.12 if topic_type in preferred_topic_types else 0.0

        required_terms = self._string_list(brand_fit_rules.get("required_terms"))
        required_term_hits = sum(1 for term in required_terms if term.lower() in combined_text)
        required_term_bonus = 0.04 * required_term_hits if required_terms else 0.0

        minimum_source_count = self._coerce_non_negative_int(brand_fit_rules.get("minimum_source_count"), default=0)
        source_count = int(item.evidence_summary.get("source_count") or 0)
        if source_count < minimum_source_count:
            violations.append(f"insufficient_sources:{source_count}")

        minimum_fit_score = self._coerce_probability(brand_fit_rules.get("minimum_fit_score"), default=0.0)
        fit_score = round(
            max(
                0.0,
                min(
                    1.0,
                    0.42
                    + topic_type_bonus
                    + required_term_bonus
                    + min(0.08, 0.03 * source_count)
                    - 0.18 * len(violations),
                ),
            ),
            4,
        )
        passes = len(violations) == 0 and fit_score >= minimum_fit_score
        if not passes and fit_score < minimum_fit_score:
            violations.append(f"below_minimum_fit_score:{minimum_fit_score}")
        return {
            "brand_fit_check": passes,
            "brand_fit_violations": violations,
            "fit_score": fit_score,
        }

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not value:
            return []
        if not isinstance(value, list):
            raise ScorerValidationError("brand-fit policy list fields must be arrays")
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _coerce_probability(value: Any, *, default: float) -> float:
        if value in (None, ""):
            return default
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ScorerValidationError("brand-fit probability fields must be numeric") from exc
        if numeric < 0 or numeric > 1:
            raise ScorerValidationError("brand-fit probability fields must be between 0 and 1")
        return numeric

    @staticmethod
    def _coerce_non_negative_int(value: Any, *, default: int) -> int:
        if value in (None, ""):
            return default
        try:
            numeric = int(value)
        except (TypeError, ValueError) as exc:
            raise ScorerValidationError("brand-fit integer fields must be integer-compatible") from exc
        if numeric < 0:
            raise ScorerValidationError("brand-fit integer fields must be >= 0")
        return numeric


class TopicPoolScorer:
    def __init__(self, feedback_store: FeedbackStore) -> None:
        self._feedback_store = feedback_store

    def score(
        self,
        *,
        item: TopicPoolItemRecord,
        brand: BrandRecord,
        policy: BrandPolicyConfigRecord | None,
        fit_evaluation: dict[str, Any],
    ) -> dict[str, float]:
        source_count = int(item.evidence_summary.get("source_count") or 0)
        dominant_signal_type = str(item.evidence_summary.get("dominant_signal_type") or "engagement")
        novelty_score = round(min(1.0, 0.35 + 0.1 * (1 / max(source_count, 1))), 4)
        trend_score = round(0.65 if dominant_signal_type == "trend" else 0.45, 4)
        historical_reward_payload = self._historical_reward_for_topic_type(
            brand_id=brand.id,
            topic_type=str(item.evidence_summary.get("topic_type") or "core"),
        )
        historical_reward_score = historical_reward_payload["effective_historical_reward"]
        fit_score = float(fit_evaluation["fit_score"])
        policy_score = self._policy_score(
            topic_type=str(item.evidence_summary.get("topic_type") or "core"),
            policy=policy,
            fit_score=fit_score,
            brand_fit_check=bool(fit_evaluation["brand_fit_check"]),
        )
        final_score = round(
            min(
                0.99,
                0.2 * novelty_score
                + 0.2 * fit_score
                + 0.15 * trend_score
                + 0.25 * historical_reward_score
                + 0.2 * policy_score,
            ),
            4,
        )
        return {
            "novelty_score": novelty_score,
            "fit_score": fit_score,
            "trend_score": trend_score,
            "historical_reward_score": historical_reward_score,
            "policy_score": policy_score,
            "final_score": final_score,
            "confidence_weight": historical_reward_payload["confidence_weight"],
            "historical_reward_mean": historical_reward_payload["historical_reward_mean"],
            "global_mean": historical_reward_payload["global_mean"],
            "sample_count": historical_reward_payload["sample_count"],
        }

    def _historical_reward_for_topic_type(self, *, brand_id: str, topic_type: str) -> dict[str, float]:
        snapshots = self._feedback_store.list_performance_snapshots(brand_id)
        eligible_rewards = [float(snapshot.composite_reward) for snapshot in snapshots]
        if not eligible_rewards:
            return {
                "historical_reward_mean": 0.0,
                "global_mean": 0.0,
                "sample_count": 0.0,
                "confidence_weight": 0.0,
                "effective_historical_reward": 0.0,
            }

        reward_by_publish = {snapshot.publish_record_id: float(snapshot.composite_reward) for snapshot in snapshots}
        publish_records = self._feedback_store.list_publish_records(brand_id)
        matching_rewards = [
            reward_by_publish[record.id]
            for record in publish_records
            if record.id in reward_by_publish and record.metadata.get("topic_type") == topic_type
        ]
        global_mean = sum(eligible_rewards) / len(eligible_rewards)
        if not matching_rewards:
            return {
                "historical_reward_mean": 0.0,
                "global_mean": round(global_mean, 4),
                "sample_count": 0.0,
                "confidence_weight": 0.0,
                "effective_historical_reward": round(global_mean, 4),
            }

        historical_reward_mean = sum(matching_rewards) / len(matching_rewards)
        sample_count = len(matching_rewards)
        confidence_weight = min(1.0, sample_count / _DEFAULT_CONFIDENCE_THRESHOLD)
        effective_historical_reward = historical_reward_mean * confidence_weight + global_mean * (1 - confidence_weight)
        return {
            "historical_reward_mean": round(historical_reward_mean, 4),
            "global_mean": round(global_mean, 4),
            "sample_count": float(sample_count),
            "confidence_weight": round(confidence_weight, 4),
            "effective_historical_reward": round(effective_historical_reward, 4),
        }

    @staticmethod
    def _policy_score(
        *,
        topic_type: str,
        policy: BrandPolicyConfigRecord | None,
        fit_score: float,
        brand_fit_check: bool,
    ) -> float:
        boost = 0.0
        if policy and isinstance(policy.topic_type_targets, dict):
            targets = policy.topic_type_targets.get("targets")
            if isinstance(targets, list):
                for entry in targets:
                    if not isinstance(entry, dict):
                        continue
                    if str(entry.get("topic_type")) == topic_type:
                        boost = float(entry.get("priority_boost") or 0.0)
                        break
        check_bonus = 0.08 if brand_fit_check else 0.0
        return round(min(1.0, fit_score + boost + check_bonus), 4)


class ScorerService:
    def __init__(
        self,
        *,
        master_data_service: MasterDataService,
        topic_pool_store: TopicPoolStore,
        feedback_store: FeedbackStore,
        brand_fit_evaluator: BrandFitEvaluator | None = None,
        topic_pool_scorer: TopicPoolScorer | None = None,
    ) -> None:
        self._master_data_service = master_data_service
        self._topic_pool_store = topic_pool_store
        self._feedback_store = feedback_store
        self._brand_fit_evaluator = brand_fit_evaluator or BrandFitEvaluator()
        self._topic_pool_scorer = topic_pool_scorer or TopicPoolScorer(feedback_store)

    def ensure_fresh(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        items: list[TopicPoolItemRecord] | None = None,
        now=None,
    ) -> list[TopicPoolItemRecord]:
        now = now or utcnow()
        brand = self._master_data_service.get_brand(workspace_id=workspace_id, brand_id=brand_id)
        policy = self._master_data_service.get_active_brand_policy_config(
            workspace_id=workspace_id,
            brand_id=brand_id,
        )
        candidate_items = items or self._topic_pool_store.list_topic_pool_items(brand_id)
        latest_feedback_at = self._latest_feedback_at(brand_id=brand_id)
        refreshed: list[TopicPoolItemRecord] = []
        for item in candidate_items:
            if item.workspace_id != workspace_id or item.brand_id != brand_id:
                continue
            if not self._is_stale(item=item, now=now, latest_feedback_at=latest_feedback_at):
                refreshed.append(item)
                continue
            fit_evaluation = self._brand_fit_evaluator.evaluate(item=item, policy=policy)
            scores = self._topic_pool_scorer.score(
                item=item,
                brand=brand,
                policy=policy,
                fit_evaluation=fit_evaluation,
            )
            updated = replace(
                item,
                evidence_summary={
                    **item.evidence_summary,
                    "brand_fit_check": fit_evaluation["brand_fit_check"],
                    "brand_fit_violations": fit_evaluation["brand_fit_violations"],
                },
                novelty_score=scores["novelty_score"],
                fit_score=scores["fit_score"],
                trend_score=scores["trend_score"],
                historical_reward_score=scores["historical_reward_score"],
                policy_score=scores["policy_score"],
                final_score=scores["final_score"],
                last_scored_at=now,
                updated_at=now,
            )
            refreshed.append(self._topic_pool_store.save_topic_pool_item(updated))
        refreshed.sort(key=lambda item: (-item.final_score, item.updated_at.isoformat(), item.title))
        return refreshed

    @staticmethod
    def build_score_breakdown(item: TopicPoolItemRecord) -> dict[str, float | list[str] | bool]:
        source_count = int(item.evidence_summary.get("source_count") or 0)
        return {
            "novelty_score": round(item.novelty_score, 4),
            "fit_score": round(item.fit_score, 4),
            "trend_score": round(item.trend_score, 4),
            "historical_reward_score": round(item.historical_reward_score, 4),
            "policy_score": round(item.policy_score, 4),
            "final_score": round(item.final_score, 4),
            "source_count": float(source_count),
            "brand_fit_check": bool(item.evidence_summary.get("brand_fit_check", True)),
            "brand_fit_violations": [
                str(entry)
                for entry in item.evidence_summary.get("brand_fit_violations", [])
                if str(entry).strip()
            ],
        }

    @staticmethod
    def _is_stale(*, item: TopicPoolItemRecord, now, latest_feedback_at) -> bool:
        if item.last_scored_at is None:
            return True
        if (now - item.last_scored_at) > _DEFAULT_MAX_AGE:
            return True
        if latest_feedback_at is not None and latest_feedback_at > item.last_scored_at:
            return True
        return False

    def _latest_feedback_at(self, *, brand_id: str):
        snapshots = self._feedback_store.list_performance_snapshots(brand_id)
        if not snapshots:
            return None
        return max(snapshot.created_at for snapshot in snapshots)
