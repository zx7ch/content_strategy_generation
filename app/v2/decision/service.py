"""Deterministic decision service for V2 P1-4."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, replace
from typing import Any

from app.v2.decision.models import (
    DecisionBatchDetailResult,
    CandidateSetSnapshotRecord,
    DecisionBatchItemRecord,
    DecisionBatchRecord,
    DecisionEventRecord,
    DecisionReviewResult,
    DecisionRunResult,
    DecisionSelection,
)
from app.v2.decision.store import DecisionStore
from app.v2.foundation.models import BrandPolicyConfigRecord, BrandStateSnapshotRecord, utcnow
from app.v2.foundation.service import (
    MasterDataNotFoundError,
    MasterDataService,
    MasterDataScopeError,
)
from app.v2.topic_pool.models import TopicPoolItemRecord
from app.v2.topic_pool.scorer import ScorerService
from app.v2.topic_pool.store import TopicPoolStore

_DEFAULT_SLOT_COUNT = 3


class DecisionError(ValueError):
    """Raised when decision operations violate the P1-4 contract."""


class DecisionValidationError(DecisionError):
    """Raised when the caller sends an invalid decision payload."""


class DecisionNotFoundError(DecisionError):
    """Raised when the requested batch or slot does not exist."""


@dataclass(frozen=True)
class _TopicTypeConstraint:
    topic_type: str
    min_ratio: float
    max_ratio: float
    priority_boost: float
    min_slots: int
    max_slots: int
    available_candidates: int


@dataclass(frozen=True)
class _SelectionResult:
    selected: list[TopicPoolItemRecord]
    constraint_infeasible: bool
    under_exploration_types: tuple[str, ...]
    quota_plan: dict[str, dict[str, Any]]


class DecisionService:
    def __init__(
        self,
        *,
        master_data_service: MasterDataService,
        topic_pool_store: TopicPoolStore,
        decision_store: DecisionStore,
        scorer_service: ScorerService | None = None,
    ) -> None:
        self._master_data_service = master_data_service
        self._topic_pool_store = topic_pool_store
        self._decision_store = decision_store
        self._scorer_service = scorer_service

    def attach_scorer_service(self, scorer_service: ScorerService) -> None:
        self._scorer_service = scorer_service

    def run_decision_batch(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        requested_slot_count: int = _DEFAULT_SLOT_COUNT,
        objective: str = "topic_recommendation",
        exploration_mode: str = "balanced",
    ) -> DecisionRunResult:
        if requested_slot_count <= 0:
            raise DecisionValidationError("requested_slot_count must be >= 1")
        brand = self._master_data_service.get_brand(workspace_id=workspace_id, brand_id=brand_id)
        policy = self._require_active_policy(workspace_id=workspace_id, brand_id=brand_id)
        snapshot = self._require_latest_state_snapshot(workspace_id=workspace_id, brand_id=brand_id)
        refreshed = self._refresh_candidates(brand_id=brand_id, policy=policy, state_snapshot=snapshot)
        if not refreshed:
            raise DecisionValidationError("topic pool is empty; refresh candidates before running decisions")

        ranked = self._rank_candidates(refreshed, policy=policy)
        selection_result = self._select_candidates(
            ranked=ranked,
            policy=policy,
            requested_slot_count=requested_slot_count,
        )
        now = utcnow()
        batch_id = str(uuid.uuid4())
        batch = self._decision_store.save_decision_batch(
            DecisionBatchRecord(
                id=batch_id,
                workspace_id=workspace_id,
                brand_id=brand_id,
                brand_state_snapshot_id=snapshot.id,
                brand_policy_config_id=policy.id,
                objective=objective,
                exploration_mode=exploration_mode,
                context_snapshot={
                    "brand_stage": brand.stage,
                    "target_audience": brand.target_audience,
                    "requested_slot_count": requested_slot_count,
                    "constraint_infeasible": selection_result.constraint_infeasible,
                    "under_exploration_types": list(selection_result.under_exploration_types),
                    "quota_plan": selection_result.quota_plan,
                },
                policy_name=policy.policy_name,
                policy_version=policy.policy_version,
                candidate_count=len(ranked),
                chosen_count=len(selection_result.selected),
                requested_slot_count=requested_slot_count,
                created_at=now,
            )
        )

        selections: list[DecisionSelection] = []
        ranked_payload = [self._to_ranked_payload(item) for item in ranked]
        for rank_position, item in enumerate(selection_result.selected, start=1):
            slot_index = rank_position - 1
            decision_event_id = str(uuid.uuid4())
            extra_reason_codes: list[str] = []
            topic_type = str(item.evidence_summary.get("topic_type") or "core")
            if selection_result.constraint_infeasible:
                extra_reason_codes.append("constraint_infeasible")
            if topic_type in selection_result.under_exploration_types:
                extra_reason_codes.append("under_exploration")
            reason_codes = self._reason_codes_for(
                item=item,
                policy=policy,
                extra_reason_codes=extra_reason_codes,
            )
            decision_mode = "Exploration" if self._is_exploration(item, policy=policy) else "Exploitation"
            event = self._decision_store.save_decision_event(
                DecisionEventRecord(
                    id=decision_event_id,
                    workspace_id=workspace_id,
                    brand_id=brand_id,
                    decision_batch_id=batch.id,
                    brand_state_snapshot_id=snapshot.id,
                    brand_policy_config_id=policy.id,
                    slot_index=slot_index,
                    serving_policy_name=policy.policy_name,
                    serving_policy_version=policy.policy_version,
                    logging_policy_name=policy.policy_name,
                    logging_policy_version=policy.policy_version,
                    decision_mode=decision_mode,
                    exploration_mode=exploration_mode,
                    objective=objective,
                    context_features={
                        "topic_type": item.evidence_summary.get("topic_type"),
                        "boost": self._topic_type_boost(item, policy=policy),
                    },
                    candidate_set=ranked_payload,
                    ranked_list=ranked_payload,
                    chosen_action_id=item.id,
                    propensities=self._build_propensities(ranked),
                    created_at=now,
                )
            )
            self._decision_store.save_decision_batch_item(
                DecisionBatchItemRecord(
                    batch_id=batch.id,
                    topic_pool_item_id=item.id,
                    selected_slot_index=slot_index,
                    final_rank_position=rank_position,
                    source_decision_event_id=event.id,
                    review_status="pending",
                    score=item.final_score,
                    reason_codes=reason_codes,
                    metadata={
                        "title": item.title,
                        "angle": item.angle,
                        "hypothesis": item.hypothesis,
                        "topic_type": item.evidence_summary.get("topic_type"),
                        "decision_mode": decision_mode,
                    },
                )
            )
            self._decision_store.save_candidate_set_snapshot(
                CandidateSetSnapshotRecord(
                    id=str(uuid.uuid4()),
                    workspace_id=workspace_id,
                    brand_id=brand_id,
                    decision_batch_id=batch.id,
                    decision_event_id=event.id,
                    snapshot_scope="slot_candidate_set",
                    slot_index=slot_index,
                    candidate_count=len(ranked_payload),
                    candidate_set=ranked_payload,
                    metrics={
                        "requested_slot_count": requested_slot_count,
                        "selected_rank_position": rank_position,
                    },
                    created_at=now,
                )
            )
            selections.append(
                DecisionSelection(
                    slot_index=slot_index,
                    topic_pool_item_id=item.id,
                    decision_event_id=event.id,
                    title=item.title,
                    angle=item.angle,
                    hypothesis=item.hypothesis,
                    score=item.final_score,
                    topic_type=str(item.evidence_summary.get("topic_type") or "core"),
                    decision_mode=decision_mode,
                    review_status="pending",
                    reason_codes=reason_codes,
                )
            )

        return DecisionRunResult(
            batch_id=batch.id,
            workspace_id=batch.workspace_id,
            brand_id=batch.brand_id,
            brand_state_snapshot_id=batch.brand_state_snapshot_id,
            brand_policy_config_id=batch.brand_policy_config_id,
            objective=batch.objective,
            exploration_mode=batch.exploration_mode,
            requested_slot_count=batch.requested_slot_count,
            candidate_count=batch.candidate_count,
            chosen_count=batch.chosen_count,
            items=selections,
            created_at=batch.created_at,
        )

    def get_decision_batch(
        self,
        *,
        workspace_id: str,
        batch_id: str,
    ) -> DecisionBatchDetailResult:
        batch = self._decision_store.get_decision_batch(batch_id)
        if batch is None:
            raise DecisionNotFoundError(f"Decision batch not found: {batch_id}")
        if batch.workspace_id != workspace_id:
            raise MasterDataScopeError(f"Decision batch does not belong to workspace: {batch_id}")
        return self._build_batch_detail(batch)

    def get_latest_decision_batch(
        self,
        *,
        workspace_id: str,
        brand_id: str,
    ) -> DecisionBatchDetailResult:
        self._master_data_service.get_brand(workspace_id=workspace_id, brand_id=brand_id)
        batches = self._decision_store.list_decision_batches(brand_id)
        if not batches:
            raise DecisionNotFoundError(f"No decision batch found for brand: {brand_id}")
        latest = batches[0]
        if latest.workspace_id != workspace_id:
            raise MasterDataScopeError(f"Decision batch does not belong to workspace: {latest.id}")
        return self._build_batch_detail(latest)

    def review_batch_item(
        self,
        *,
        workspace_id: str,
        batch_id: str,
        slot_index: int,
        review_action: str,
        edited_title: str | None = None,
        edited_angle: str | None = None,
        edited_hypothesis: str | None = None,
        review_notes: str | None = None,
        reviewed_by_type: str = "operator",
        reviewed_by_id: str | None = None,
    ) -> DecisionReviewResult:
        batch = self._decision_store.get_decision_batch(batch_id)
        if batch is None:
            raise DecisionNotFoundError(f"Decision batch not found: {batch_id}")
        if batch.workspace_id != workspace_id:
            raise MasterDataScopeError(f"Decision batch does not belong to workspace: {batch_id}")
        item = self._decision_store.get_decision_batch_item_by_slot(batch_id=batch_id, slot_index=slot_index)
        if item is None:
            raise DecisionNotFoundError(f"Decision slot not found: {batch_id}:{slot_index}")
        if review_action not in {"accept", "reject", "edit_and_accept"}:
            raise DecisionValidationError("review_action must be one of accept/reject/edit_and_accept")
        if review_action == "edit_and_accept" and not any([edited_title, edited_angle, edited_hypothesis]):
            raise DecisionValidationError("edit_and_accept requires at least one edited field")

        metadata = dict(item.metadata)
        title = edited_title or str(metadata.get("title") or "")
        angle = edited_angle or str(metadata.get("angle") or "")
        hypothesis = edited_hypothesis or str(metadata.get("hypothesis") or "")
        reviewed_at = utcnow()
        updated = self._decision_store.save_decision_batch_item(
            replace(
                item,
                review_status=review_action,
                reviewed_at=reviewed_at,
                reviewed_by_type=reviewed_by_type,
                reviewed_by_id=reviewed_by_id,
                edited_title=edited_title,
                edited_angle=edited_angle,
                edited_hypothesis=edited_hypothesis,
                review_notes=review_notes,
                metadata={
                    **metadata,
                    "title": title,
                    "angle": angle,
                    "hypothesis": hypothesis,
                },
            )
        )
        return DecisionReviewResult(
            batch_id=updated.batch_id,
            slot_index=updated.selected_slot_index,
            topic_pool_item_id=updated.topic_pool_item_id,
            decision_event_id=updated.source_decision_event_id,
            review_status=updated.review_status,
            title=title,
            angle=angle,
            hypothesis=hypothesis,
            score=updated.score,
            reason_codes=updated.reason_codes,
            review_notes=updated.review_notes,
            reviewed_at=updated.reviewed_at,
        )

    def _build_batch_detail(self, batch: DecisionBatchRecord) -> DecisionBatchDetailResult:
        items = self._decision_store.list_decision_batch_items(batch.id)
        selections = [
            DecisionSelection(
                slot_index=item.selected_slot_index,
                topic_pool_item_id=item.topic_pool_item_id,
                decision_event_id=item.source_decision_event_id or "",
                title=str(item.metadata.get("title") or ""),
                angle=str(item.metadata.get("angle") or ""),
                hypothesis=str(item.metadata.get("hypothesis") or ""),
                score=item.score,
                topic_type=str(item.metadata.get("topic_type") or "core"),
                decision_mode=str(item.metadata.get("decision_mode") or "Exploitation"),
                review_status=item.review_status,
                reason_codes=item.reason_codes,
                edited_title=item.edited_title,
                edited_angle=item.edited_angle,
                edited_hypothesis=item.edited_hypothesis,
                review_notes=item.review_notes,
            )
            for item in items
        ]
        return DecisionBatchDetailResult(
            batch_id=batch.id,
            workspace_id=batch.workspace_id,
            brand_id=batch.brand_id,
            brand_state_snapshot_id=batch.brand_state_snapshot_id,
            brand_policy_config_id=batch.brand_policy_config_id,
            objective=batch.objective,
            exploration_mode=batch.exploration_mode,
            requested_slot_count=batch.requested_slot_count,
            candidate_count=batch.candidate_count,
            chosen_count=batch.chosen_count,
            items=selections,
            created_at=batch.created_at,
        )

    def _require_active_policy(self, *, workspace_id: str, brand_id: str) -> BrandPolicyConfigRecord:
        policy = self._master_data_service.get_active_brand_policy_config(
            workspace_id=workspace_id,
            brand_id=brand_id,
        )
        if policy is None:
            raise MasterDataNotFoundError(f"Active policy config not found: {brand_id}")
        return policy

    def _require_latest_state_snapshot(
        self,
        *,
        workspace_id: str,
        brand_id: str,
    ) -> BrandStateSnapshotRecord:
        snapshots = self._master_data_service.list_brand_state_snapshots(
            workspace_id=workspace_id,
            brand_id=brand_id,
        )
        if not snapshots:
            raise MasterDataNotFoundError(f"Brand state snapshot not found: {brand_id}")
        return snapshots[0]

    def _refresh_candidates(
        self,
        *,
        brand_id: str,
        policy: BrandPolicyConfigRecord,
        state_snapshot: BrandStateSnapshotRecord,
    ) -> list[TopicPoolItemRecord]:
        items = self._topic_pool_store.list_topic_pool_items(brand_id)
        refreshed = (
            self._scorer_service.ensure_fresh(
                workspace_id=policy.workspace_id,
                brand_id=brand_id,
                items=items,
            )
            if self._scorer_service is not None
            else items
        )
        refreshed.sort(key=lambda candidate: (-candidate.final_score, candidate.updated_at.isoformat(), candidate.id))
        return refreshed

    def _rank_candidates(
        self,
        items: list[TopicPoolItemRecord],
        *,
        policy: BrandPolicyConfigRecord,
    ) -> list[TopicPoolItemRecord]:
        return sorted(
            items,
            key=lambda item: (
                -(item.final_score + self._topic_type_boost(item, policy=policy)),
                -item.policy_score,
                item.title,
                item.id,
            ),
        )

    def _select_candidates(
        self,
        *,
        ranked: list[TopicPoolItemRecord],
        policy: BrandPolicyConfigRecord,
        requested_slot_count: int,
    ) -> _SelectionResult:
        if not ranked:
            return _SelectionResult(selected=[], constraint_infeasible=False, under_exploration_types=(), quota_plan={})
        targets = policy.topic_type_targets.get("targets") if isinstance(policy.topic_type_targets, dict) else None
        selected: list[TopicPoolItemRecord] = []
        selected_ids: set[str] = set()
        selected_counts: dict[str, int] = {}
        by_type: dict[str, list[TopicPoolItemRecord]] = {}
        for item in ranked:
            topic_type = str(item.evidence_summary.get("topic_type") or "core")
            by_type.setdefault(topic_type, []).append(item)

        constraint_plan = self._build_constraint_plan(
            targets=targets,
            requested_slot_count=requested_slot_count,
            by_type=by_type,
        )
        if isinstance(targets, list):
            for target in constraint_plan["ordered_targets"]:
                if len(selected) >= requested_slot_count:
                    break
                actual_min_slots = int(constraint_plan["actual_min_slots"].get(target.topic_type, 0))
                for item in by_type.get(target.topic_type, []):
                    if len(selected) >= requested_slot_count or actual_min_slots <= 0:
                        break
                    if item.id in selected_ids:
                        continue
                    selected.append(item)
                    selected_ids.add(item.id)
                    selected_counts[target.topic_type] = selected_counts.get(target.topic_type, 0) + 1
                    actual_min_slots -= 1

        for item in ranked:
            if len(selected) >= requested_slot_count:
                break
            if item.id in selected_ids:
                continue
            topic_type = str(item.evidence_summary.get("topic_type") or "core")
            max_slots = constraint_plan["max_slots"].get(topic_type)
            if max_slots is not None and selected_counts.get(topic_type, 0) >= int(max_slots):
                continue
            selected.append(item)
            selected_ids.add(item.id)
            selected_counts[topic_type] = selected_counts.get(topic_type, 0) + 1
        return _SelectionResult(
            selected=selected,
            constraint_infeasible=bool(constraint_plan["constraint_infeasible"]),
            under_exploration_types=tuple(sorted(constraint_plan["under_exploration_types"])),
            quota_plan=constraint_plan["quota_plan"],
        )

    def _build_constraint_plan(
        self,
        *,
        targets: Any,
        requested_slot_count: int,
        by_type: dict[str, list[TopicPoolItemRecord]],
    ) -> dict[str, Any]:
        ordered_targets: list[_TopicTypeConstraint] = []
        if not isinstance(targets, list):
            return {
                "ordered_targets": ordered_targets,
                "constraint_infeasible": False,
                "under_exploration_types": set(),
                "max_slots": {},
                "actual_min_slots": {},
                "quota_plan": {},
            }

        constraint_infeasible = False
        under_exploration_types: set[str] = set()
        mutable_min_slots: dict[str, int] = {}
        max_slots: dict[str, int] = {}
        ratio_lookup: dict[str, tuple[float, float]] = {}
        boost_lookup: dict[str, float] = {}

        for target in targets:
            if not isinstance(target, dict):
                continue
            topic_type = str(target.get("topic_type") or "")
            if not topic_type:
                continue
            min_ratio = float(target.get("min_ratio", 0.0))
            max_ratio = float(target.get("max_ratio", 1.0))
            priority_boost = float(target.get("priority_boost", 0.0))
            min_slots = math.ceil(min_ratio * requested_slot_count)
            if min_ratio > 0:
                min_slots = max(1, min_slots)
            max_slot_count = math.floor(max_ratio * requested_slot_count)
            if min_slots > max_slot_count:
                min_slots = max_slot_count
                constraint_infeasible = True
            constraint = _TopicTypeConstraint(
                topic_type=topic_type,
                min_ratio=min_ratio,
                max_ratio=max_ratio,
                priority_boost=priority_boost,
                min_slots=min_slots,
                max_slots=max_slot_count,
                available_candidates=len(by_type.get(topic_type, [])),
            )
            ordered_targets.append(constraint)
            mutable_min_slots[topic_type] = min_slots
            max_slots[topic_type] = max_slot_count
            ratio_lookup[topic_type] = (min_ratio, max_ratio)
            boost_lookup[topic_type] = priority_boost

        trim_order = sorted(
            ordered_targets,
            key=lambda item: (item.priority_boost, item.min_ratio, item.topic_type),
        )
        while sum(mutable_min_slots.values()) > requested_slot_count:
            trimmed = False
            constraint_infeasible = True
            for target in trim_order:
                if mutable_min_slots.get(target.topic_type, 0) <= 0:
                    continue
                mutable_min_slots[target.topic_type] = max(0, mutable_min_slots[target.topic_type] - 1)
                trimmed = True
                break
            if not trimmed:
                break

        actual_min_slots: dict[str, int] = {}
        for target in ordered_targets:
            actual = min(mutable_min_slots.get(target.topic_type, 0), target.available_candidates)
            actual_min_slots[target.topic_type] = actual
            if actual < mutable_min_slots.get(target.topic_type, 0):
                under_exploration_types.add(target.topic_type)

        quota_plan = {
            target.topic_type: {
                "min_ratio": ratio_lookup[target.topic_type][0],
                "max_ratio": ratio_lookup[target.topic_type][1],
                "priority_boost": boost_lookup[target.topic_type],
                "min_slots": mutable_min_slots.get(target.topic_type, 0),
                "actual_min_slots": actual_min_slots.get(target.topic_type, 0),
                "max_slots": max_slots[target.topic_type],
                "available_candidates": target.available_candidates,
            }
            for target in ordered_targets
        }
        return {
            "ordered_targets": ordered_targets,
            "constraint_infeasible": constraint_infeasible,
            "under_exploration_types": under_exploration_types,
            "max_slots": max_slots,
            "actual_min_slots": actual_min_slots,
            "quota_plan": quota_plan,
        }

    def _topic_type_boost(self, item: TopicPoolItemRecord, *, policy: BrandPolicyConfigRecord) -> float:
        topic_type = str(item.evidence_summary.get("topic_type") or "core")
        targets = policy.topic_type_targets.get("targets") if isinstance(policy.topic_type_targets, dict) else []
        for target in targets or []:
            if isinstance(target, dict) and str(target.get("topic_type")) == topic_type:
                return float(target.get("priority_boost", 0.0))
        return 0.0

    def _reason_codes_for(
        self,
        *,
        item: TopicPoolItemRecord,
        policy: BrandPolicyConfigRecord,
        extra_reason_codes: list[str] | None = None,
    ) -> list[str]:
        reason_codes = ["score_ranked"]
        boost = self._topic_type_boost(item, policy=policy)
        if boost > 0:
            reason_codes.append("priority_boost")
        if item.historical_reward_score > 0:
            reason_codes.append("historical_reward")
        for code in extra_reason_codes or []:
            if code not in reason_codes:
                reason_codes.append(code)
        return reason_codes

    def _is_exploration(self, item: TopicPoolItemRecord, *, policy: BrandPolicyConfigRecord) -> bool:
        return self._topic_type_boost(item, policy=policy) > 0 or item.novelty_score >= 0.4

    def _build_propensities(self, ranked: list[TopicPoolItemRecord]) -> list[dict[str, Any]]:
        total = sum(max(item.final_score, 0.01) for item in ranked)
        return [
            {
                "topic_pool_item_id": item.id,
                "probability": round(max(item.final_score, 0.01) / total, 6),
            }
            for item in ranked
        ]

    def _to_ranked_payload(self, item: TopicPoolItemRecord) -> dict[str, Any]:
        return {
            "topic_pool_item_id": item.id,
            "title": item.title,
            "topic_type": item.evidence_summary.get("topic_type"),
            "score": item.final_score,
            "novelty_score": item.novelty_score,
        }
