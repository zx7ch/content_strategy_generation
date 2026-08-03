"""Deterministic, resumable direction evidence selection.

This module deliberately keeps only the small, replayable selection manifest in
checkpoints.  Full provider responses never become part of this pipeline's
durable read model.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any

from app.content_research.admission.candidates import build_claim_candidate, extract_facts
from app.content_research.admission.evaluator import (
    ALGORITHM_VERSION,
    ClaimAdmissionEvaluator,
)
from app.content_research.admission.registry import DEFAULT_ADMISSION_STRATEGIES
from app.content_research.admission.relevance import query_relevance_reason
from app.content_research.admission.results import (
    DIRECTION_RESULT_ALGORITHM_VERSION,
    build_direction_result,
)
from app.content_research.async_pipeline_store import AsyncDirectionalPersistenceSession
from app.content_research.contracts import (
    DirectionContract,
    RunPolicySnapshot,
    SamplePolicy,
    admission_author_identity,
    frozen_query_relevance,
)
from app.content_research.persistence_models import (
    ClaimAdmissionDecisionRecord,
    DirectionalEvidencePacketRecord,
    DirectionSourceProjectionRecord,
    StageCheckpointRecord,
)
from app.content_research.runtime import canonical_fingerprint
from app.content_research.sources.base import SourceOperationResult
from app.content_research.sources.canonical_registry import CanonicalSourceRegistry
from app.content_research.stores.base import ContentResearchStore

PACKET_FIELD_NAMES = frozenset(
    {
        "title",
        "content_text",
        "author_id",
        "author",
        "tags",
        "metrics",
        "media",
        "source_url",
        "source_published_at",
        "source_collected_at",
        "metrics_observed_at",
        "parent_note_id",
        "comment_text",
        "reply_depth",
        "quote",
        "competitor_names",
        "activity_signals",
        "keyword_patterns",
        "reference_window",
    }
)


class OperationOutcomeUnknownError(RuntimeError):
    """A prior external call may have completed, so it cannot be retried safely."""

    def __init__(self, *, operation: str, operation_fingerprint: str) -> None:
        self.operation = operation
        self.operation_fingerprint = operation_fingerprint
        super().__init__(f"collection outcome pending confirmation: {operation}")


@dataclass(frozen=True)
class QueryGroup:
    id: str
    direction_id: str
    query: str
    priority: int
    sort: str = "likes"
    candidate_limit: int = 20
    time_window: dict[str, str] | None = None
    cursor: str | None = None
    roles: tuple[str, ...] = ()
    activation: str = "primary"
    normalized_identity: str = ""


@dataclass(frozen=True)
class CandidateDecision:
    canonical_source_id: str
    selected: bool
    reasons: tuple[str, ...]
    query_group_ids: tuple[str, ...]
    query_hits: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class DirectionSelection:
    query_plan_hash: str
    candidate_manifest_hash: str
    decisions: tuple[CandidateDecision, ...]
    selected_source_count: int
    eligible_source_count: int
    independent_source_count: int
    status: str
    coverage_unmet_query_group_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DirectionEvidenceRun:
    selection: DirectionSelection
    packet_ids: tuple[str, ...]
    comment_packet_ids: tuple[str, ...]
    replayed_collect: bool
    replayed_selection: bool
    replayed_packet: bool
    blocking_failure_code: str | None = None


def compile_query_groups(
    *,
    direction_id: str,
    subject: str,
    questions: list[str],
    competitors: list[str],
    candidate_limit: int = 20,
    run_as_of_at: datetime | None = None,
) -> tuple[QueryGroup, ...]:
    terms = [
        subject.strip(),
        *sorted({item.strip() for item in competitors if item.strip()}),
    ]
    groups: list[QueryGroup] = []
    for index, question in enumerate(questions or [direction_id]):
        query = " ".join([*terms, question.strip()]).strip()
        normalized = " ".join(query.split())
        group_id = (
            f"qg_{canonical_fingerprint({'direction': direction_id, 'query': normalized})[:16]}"
        )
        groups.append(
            QueryGroup(
                group_id,
                direction_id,
                normalized,
                index,
                candidate_limit=candidate_limit,
                time_window={"end_at": run_as_of_at.isoformat()} if run_as_of_at else None,
            )
        )
    return tuple(groups)


def query_plan_hash(groups: tuple[QueryGroup, ...]) -> str:
    return canonical_fingerprint(
        {"query_groups": [_frozen_query_group_payload(item) for item in groups]}
    )


def _frozen_query_group_payload(group: QueryGroup) -> dict[str, Any]:
    payload = {
        "id": group.id,
        "direction_id": group.direction_id,
        "normalized_query": group.query,
        "priority": group.priority,
        "sort": group.sort,
        "time_window": dict(group.time_window or {}),
        "candidate_cap": group.candidate_limit,
    }
    if group.roles:
        payload["roles"] = list(group.roles)
    if group.activation != "primary":
        payload["activation"] = group.activation
    elif group.roles or group.normalized_identity:
        payload["activation"] = "primary"
    if group.normalized_identity:
        payload["normalized_identity"] = group.normalized_identity
    return payload


def _frozen_query_groups(
    snapshot: RunPolicySnapshot,
    direction_id: str,
) -> tuple[QueryGroup, ...] | None:
    locked = snapshot.effective_policy.get("locked_query_plan")
    if not isinstance(locked, dict):
        return None
    direction = dict((locked.get("directions") or {}).get(direction_id) or {})
    values = direction.get("query_groups")
    if not isinstance(values, list) or not values:
        raise ValueError("frozen query plan is missing the requested direction")
    all_groups = tuple(
        QueryGroup(
            id=str(value["id"]),
            direction_id=str(value["direction_id"]),
            query=str(value["normalized_query"]),
            priority=int(value["priority"]),
            sort=str(value["sort"]),
            candidate_limit=int(value["candidate_cap"]),
            time_window=dict(value["time_window"]),
            roles=tuple(value.get("roles") or ()),
            activation=str(value.get("activation") or "primary"),
            normalized_identity=str(value.get("normalized_identity") or ""),
        )
        for value in values
    )
    if query_plan_hash(all_groups) != direction.get("query_plan_hash"):
        raise ValueError("frozen query plan hash does not match its query groups")
    groups = tuple(group for group in all_groups if group.activation == "primary")
    if not groups:
        raise ValueError("frozen query plan has no active primary groups")
    return groups


def select_candidates(
    *,
    groups: tuple[QueryGroup, ...],
    candidates: list[dict[str, Any]],
    author_cap: int,
    minimum_samples: int = 1_000_000,
    minimum_independent_authors: int = 1_000_000,
    detail_fetch_cap: int | None = None,
    require_detail: bool = False,
    run_as_of_at: datetime | None = None,
    frozen_query_plan_hash: str | None = None,
) -> DirectionSelection:
    """Deduplicate before applying the author cap; never trade the cap for coverage."""
    by_id: dict[str, dict[str, Any]] = {}
    hits: dict[str, set[str]] = {}
    query_hits: dict[str, dict[str, int]] = {}
    for item in candidates:
        source_id = str(item.get("canonical_source_id") or item.get("canonical_id") or "")
        if not source_id:
            continue
        normalized = {**item, "canonical_source_id": source_id}
        if _is_after_as_of(normalized, run_as_of_at):
            normalized["out_of_time_window"] = True
        item_query_hits = list(normalized.get("query_hits") or ())
        query_group_id = str(normalized.get("query_group_id") or "")
        if query_group_id:
            item_query_hits.append(
                {
                    "query_group_id": query_group_id,
                    "rank": normalized.get("query_rank"),
                }
            )
        hits.setdefault(source_id, set())
        for hit in item_query_hits:
            if not isinstance(hit, dict):
                continue
            hit_group_id = str(hit.get("query_group_id") or "")
            if not hit_group_id:
                continue
            hits[source_id].add(hit_group_id)
            rank = int(hit.get("rank") or 0)
            source_hits = query_hits.setdefault(source_id, {})
            source_hits[hit_group_id] = min(
                rank,
                source_hits.get(hit_group_id, rank),
            )
        current = by_id.get(source_id)
        if current is None or _sort_key(normalized) < _sort_key(current):
            by_id[source_id] = normalized

    author_counts: dict[str, int] = {}
    decisions: list[CandidateDecision] = []
    selected = 0
    independent_authors: set[str] = set()
    incomplete = False
    selected_groups: set[str] = set()
    all_group_ids = {group.id for group in groups}
    detail_fetch_cap = detail_fetch_cap or len(by_id)
    for source_id, item in sorted(by_id.items(), key=lambda pair: _sort_key(pair[1])):
        reasons: list[str] = []
        if item.get("out_of_time_window"):
            reasons.append("out_of_time_window")
        if item.get("blocking_unavailable"):
            reasons.append("blocking_field_unavailable")
        if (
            require_detail
            and item.get("source_kind") in {"search_result", "search_result_minimal"}
            and not item.get("blocking_unavailable")
        ):
            reasons.append("detail_not_collected")
        author = str(item.get("author_id") or "")
        if author and author_counts.get(author, 0) >= author_cap:
            reasons.append("author_cap_reached")
        if selected >= detail_fetch_cap:
            reasons.append("detail_fetch_cap_reached")
        elif (
            selected >= minimum_samples
            and len(independent_authors) >= minimum_independent_authors
            and not (set(hits[source_id]) & (all_group_ids - selected_groups))
        ):
            # The global sample target cannot terminate retrieval while a
            # deterministic QueryGroup still has no selected source.  A
            # candidate that covers such a gap may extend the sample beyond
            # the minimum; author/detail caps above remain hard limits.
            reasons.append("retrieval_target_reached")
        is_selected = not reasons
        if is_selected:
            selected += 1
            if author:
                author_counts[author] = author_counts.get(author, 0) + 1
                independent_authors.add(author)
            selected_groups.update(filter(None, hits[source_id]))
            reasons.append("selected_deterministically")
        decisions.append(
            CandidateDecision(
                source_id,
                is_selected,
                tuple(reasons),
                tuple(sorted(filter(None, hits[source_id]))),
                tuple(
                    {
                        "query_group_id": group_id,
                        "rank": query_hits[source_id][group_id],
                    }
                    for group_id in sorted(query_hits.get(source_id, {}))
                ),
            )
        )

    coverage_unmet = tuple(group.id for group in groups if group.id not in selected_groups)
    if coverage_unmet:
        incomplete = True
    requirements_met = (
        selected >= minimum_samples and len(independent_authors) >= minimum_independent_authors
    )
    if not requirements_met:
        incomplete = True
    manifest = [
        {
            "id": key,
            "item": _manifest_value(value),
            "query_hits": [
                {
                    "query_group_id": group_id,
                    "rank": query_hits[key][group_id],
                }
                for group_id in sorted(query_hits.get(key, {}))
            ],
        }
        for key, value in sorted(by_id.items())
    ]
    status = (
        "insufficient_evidence" if not selected else ("incomplete" if incomplete else "complete")
    )
    return DirectionSelection(
        frozen_query_plan_hash or query_plan_hash(groups),
        canonical_fingerprint({"candidates": manifest}),
        tuple(decisions),
        selected,
        selected,
        len(independent_authors),
        status,
        coverage_unmet,
    )


def build_packet(
    *,
    direction_id: str,
    canonical_source_id: str,
    fields: dict[str, Any],
    availability: dict[str, str],
    retrieval_context: dict[str, Any],
) -> dict[str, Any]:
    """Return the minimal immutable packet projection, excluding raw/token data."""
    projection = {key: fields.get(key) for key in sorted(fields) if key in PACKET_FIELD_NAMES}
    safe_context = {
        key: value
        for key, value in retrieval_context.items()
        if key not in {"raw_payload", "access_token", "token", "cookie"}
    }
    value = {
        "direction_id": direction_id,
        "canonical_source_id": canonical_source_id,
        "field_projection": projection,
        "field_availability": dict(sorted(availability.items())),
        "retrieval_context": safe_context,
    }
    return {**value, "field_projection_hash": canonical_fingerprint(value)}


class DirectionalExecutionPipeline:
    """Persist the collect/selection/packet safe boundaries for one direction."""

    def __init__(self, store: ContentResearchStore) -> None:
        self._store = store
        self._canonical_sources = CanonicalSourceRegistry(store)
        self._checkpoint_started_at: dict[tuple[str, str], datetime] = {}

    @classmethod
    async def open_async(
        cls, db_path: str, *, workflow_run_id: str
    ) -> DirectionalExecutionPipeline:
        return cls(
            await AsyncDirectionalPersistenceSession.open(db_path, workflow_run_id=workflow_run_id)
        )  # type: ignore[arg-type]

    async def _flush(self) -> None:
        flush = getattr(self._store, "flush", None)
        if flush is not None:
            await flush()

    async def execute(
        self,
        *,
        workflow_run_id: str = "",
        subagent_task_id: str,
        direction_id: str,
        subject: str,
        questions: list[str],
        competitors: list[str],
        author_cap: int,
        minimum_samples: int = 1,
        minimum_independent_authors: int = 1,
        detail_fetch_cap: int | None = None,
        candidate_limit_per_query: int = 20,
        snapshot_id: str | None = None,
        discover: Callable[[QueryGroup], Awaitable[SourceOperationResult | list[dict[str, Any]]]],
        collect_detail: Callable[
            [dict[str, Any]], Awaitable[SourceOperationResult | dict[str, Any] | None]
        ]
        | None = None,
        collect_comments: Callable[[dict[str, Any]], Awaitable[SourceOperationResult]]
        | None = None,
        required_comment_fields: tuple[str, ...] = (),
        comment_limit: int = 30,
        comment_top_level_only: bool = True,
        comment_reply_depth_limit: int = 0,
        comment_policy_id: str | None = None,
        run_as_of_at: datetime | None = None,
        admission_contract: DirectionContract | None = None,
        admission_policy: SamplePolicy | None = None,
        policy_snapshot: RunPolicySnapshot | None = None,
    ) -> DirectionEvidenceRun:
        # Direct pipeline diagnostics have no workflow entity; keep them
        # isolated instead of allowing an unscoped persisted record.
        self._workflow_run_id = workflow_run_id or f"local_{subagent_task_id}"
        self._checkpoint_started_at = {}
        frozen_groups = (
            _frozen_query_groups(policy_snapshot, direction_id)
            if policy_snapshot is not None
            else None
        )
        if policy_snapshot is not None and frozen_groups is None:
            raise ValueError("formal collection requires a full locked query plan")
        groups = frozen_groups or compile_query_groups(
            direction_id=direction_id,
            subject=subject,
            questions=questions,
            competitors=competitors,
            candidate_limit=candidate_limit_per_query,
            run_as_of_at=run_as_of_at,
        )
        active_plan_hash = query_plan_hash(groups)
        locked_direction = (
            dict(
                (
                    policy_snapshot.effective_policy.get("locked_query_plan", {})
                    .get("directions", {})
                    .get(direction_id, {})
                )
                or {}
            )
            if policy_snapshot is not None
            else {}
        )
        plan_hash = str(locked_direction.get("query_plan_hash") or active_plan_hash)
        collect_fingerprint = (
            canonical_fingerprint(
                {"frozen_plan_hash": plan_hash, "active_plan_hash": active_plan_hash}
            )
            if plan_hash != active_plan_hash
            else plan_hash
        )
        collect_record = self._checkpoint(subagent_task_id, "collect", collect_fingerprint)
        replayed_collect = collect_record is not None
        if collect_record:
            candidates = list(collect_record.payload["candidates"])
        else:
            self._start_checkpoint(subagent_task_id, "collect")
            candidates, pagination = await self._collect_search_pages(
                subagent_task_id=subagent_task_id,
                plan_hash=plan_hash,
                groups=groups,
                discover=discover,
            )
            self._save_checkpoint(
                subagent_task_id,
                "collect",
                collect_fingerprint,
                {
                    "direction_id": direction_id,
                    "query_groups": [asdict(group) for group in groups],
                    "query_plan_hash": plan_hash,
                    "active_query_plan_hash": active_plan_hash,
                    "selection_policy": {
                        "snapshot_id": snapshot_id,
                        "author_cap": author_cap,
                        "minimum_samples": minimum_samples,
                        "minimum_independent_authors": minimum_independent_authors,
                        "detail_fetch_cap": detail_fetch_cap,
                        "run_as_of_at": run_as_of_at.isoformat() if run_as_of_at else None,
                    },
                    "candidates": candidates,
                    "pagination": pagination,
                },
            )

        self._start_checkpoint(subagent_task_id, "selection")
        selection = select_candidates(
            groups=groups,
            candidates=candidates,
            author_cap=author_cap,
            minimum_samples=minimum_samples,
            minimum_independent_authors=minimum_independent_authors,
            detail_fetch_cap=detail_fetch_cap,
            run_as_of_at=run_as_of_at,
            frozen_query_plan_hash=plan_hash,
        )
        selection_fp = canonical_fingerprint(
            {
                "plan": plan_hash,
                "manifest": selection.candidate_manifest_hash,
                "author_cap": author_cap,
                "minimum_samples": minimum_samples,
                "minimum_independent_authors": minimum_independent_authors,
                "detail_fetch_cap": detail_fetch_cap,
                "run_as_of_at": run_as_of_at.isoformat() if run_as_of_at else None,
            }
        )
        selection_record = self._checkpoint(subagent_task_id, "selection", selection_fp)
        replayed_selection = selection_record is not None
        if selection_record:
            selection = _selection_from_payload(selection_record.payload["selection"])
        else:
            self._save_checkpoint(
                subagent_task_id,
                "selection",
                selection_fp,
                {
                    "direction_id": direction_id,
                    "selection_policy": {
                        "snapshot_id": snapshot_id,
                        "author_cap": author_cap,
                        "minimum_samples": minimum_samples,
                        "minimum_independent_authors": minimum_independent_authors,
                        "detail_fetch_cap": detail_fetch_cap,
                        "run_as_of_at": run_as_of_at.isoformat() if run_as_of_at else None,
                    },
                    "selection": _selection_payload(selection),
                },
            )

        candidate_by_id = _candidate_map(candidates)
        detail_record = self._checkpoint(subagent_task_id, "detail", selection_fp)
        if detail_record:
            candidate_by_id = _candidate_map(detail_record.payload["candidates"])
            selection = _selection_from_payload(detail_record.payload["selection"])
        elif collect_detail is not None:
            self._start_checkpoint(subagent_task_id, "detail")
            revisions = self._selection_revisions(subagent_task_id, selection_fp)
            if revisions:
                latest = revisions[-1].payload
                candidate_by_id = _candidate_map(latest["candidates"])
                selection = _selection_from_payload(latest["selection"])
            revision_no = len(revisions)
            for candidate_id, candidate in sorted(
                candidate_by_id.items(), key=lambda pair: _sort_key(pair[1])
            ):
                if candidate.get("source_kind") not in {
                    "search_result",
                    "search_result_minimal",
                } or candidate.get("detail_attempted"):
                    continue
                if detail_fetch_cap is not None and revision_no >= detail_fetch_cap:
                    break
                operation_fingerprint = self._begin_operation(
                    subagent_task_id,
                    operation="detail",
                    request={"canonical_source_id": candidate_id},
                )
                await self._flush()
                try:
                    detail_result = await collect_detail(candidate)
                except Exception:
                    self._terminal_operation(
                        subagent_task_id,
                        "detail",
                        operation_fingerprint,
                        status="outcome_unknown",
                        failure_code="provider_call_interrupted",
                        recovery_action="确认外部调用结果后再恢复；系统不会自动重放。",
                    )
                    await self._flush()
                    raise
                if isinstance(detail_result, SourceOperationResult):
                    detail = (
                        dict(detail_result.items[0])
                        if detail_result.status in {"completed", "partial_completed"}
                        and detail_result.items
                        else None
                    )
                else:
                    detail = detail_result
                candidate["detail_attempted"] = True
                if detail is None:
                    candidate["blocking_unavailable"] = True
                else:
                    candidate_by_id[candidate_id] = {
                        **candidate,
                        **detail,
                        "canonical_source_id": candidate_id,
                        "detail_attempted": True,
                    }
                self._start_checkpoint(subagent_task_id, "selection_revision")
                selection = select_candidates(
                    groups=groups,
                    candidates=list(candidate_by_id.values()),
                    author_cap=author_cap,
                    minimum_samples=minimum_samples,
                    minimum_independent_authors=minimum_independent_authors,
                    detail_fetch_cap=detail_fetch_cap,
                    require_detail=True,
                    run_as_of_at=run_as_of_at,
                    frozen_query_plan_hash=plan_hash,
                )
                revision_no += 1
                decision = next(
                    item for item in selection.decisions if item.canonical_source_id == candidate_id
                )
                revision_fp = canonical_fingerprint(
                    {
                        "selection": selection_fp,
                        "revision": revision_no,
                        "candidate": candidate_id,
                        "manifest": selection.candidate_manifest_hash,
                    }
                )
                self._save_checkpoint(
                    subagent_task_id,
                    "selection_revision",
                    revision_fp,
                    {
                        "direction_id": direction_id,
                        "base_selection_fingerprint": selection_fp,
                        "revision": revision_no,
                        "trigger": {
                            "candidate_id": candidate_id,
                            "reasons": list(decision.reasons),
                        },
                        "candidates": list(candidate_by_id.values()),
                        "selection": _selection_payload(selection),
                    },
                )
                if isinstance(detail_result, SourceOperationResult):
                    self._terminal_operation_from_result(
                        subagent_task_id, "detail", operation_fingerprint, detail_result
                    )
                else:
                    self._complete_operation(subagent_task_id, "detail", operation_fingerprint)
                await self._flush()
                if isinstance(detail_result, SourceOperationResult) and _is_provider_wide_failure(
                    detail_result
                ):
                    break
                if selection.status == "complete":
                    break
            selection = select_candidates(
                groups=groups,
                candidates=list(candidate_by_id.values()),
                author_cap=author_cap,
                minimum_samples=minimum_samples,
                minimum_independent_authors=minimum_independent_authors,
                detail_fetch_cap=detail_fetch_cap,
                require_detail=True,
                run_as_of_at=run_as_of_at,
                frozen_query_plan_hash=plan_hash,
            )
            self._save_checkpoint(
                subagent_task_id,
                "detail",
                selection_fp,
                {
                    "direction_id": direction_id,
                    "candidates": list(candidate_by_id.values()),
                    "selection": _selection_payload(selection),
                },
            )

        comment_packet_ids: tuple[str, ...] = ()
        comment_required = bool(required_comment_fields)
        comments_fp = canonical_fingerprint(
            {
                "selection": selection_fp,
                "required_comment_fields": required_comment_fields,
                "limit": comment_limit,
                "top_level_only": comment_top_level_only,
                "reply_depth_limit": comment_reply_depth_limit,
                "sample_policy_id": comment_policy_id,
            }
        )
        comment_record = self._checkpoint(subagent_task_id, "comments", comments_fp)
        if comment_required and comment_record:
            comment_packet_ids = tuple(comment_record.payload.get("packet_ids") or [])
            if not comment_record.payload.get("usable", False):
                selection = replace(selection, status="incomplete")
        elif comment_required:
            self._start_checkpoint(subagent_task_id, "comments")
            if collect_comments is None:
                collection = {
                    "required": True,
                    "status": "unavailable",
                    "failure_reason": "comment_collector_unconfigured",
                    "usable": False,
                    "parents": [],
                }
            else:
                collection = await self._collect_required_comments(
                    subagent_task_id=subagent_task_id,
                    direction_id=direction_id,
                    comments_fingerprint=comments_fp,
                    selection=selection,
                    candidate_by_id=candidate_by_id,
                    collect_comments=collect_comments,
                    required_comment_fields=required_comment_fields,
                    comment_limit=comment_limit,
                    top_level_only=comment_top_level_only,
                    reply_depth_limit=comment_reply_depth_limit,
                    comment_policy_id=comment_policy_id,
                )
            comment_packet_ids = tuple(collection["packet_ids"])
            self._save_checkpoint(
                subagent_task_id,
                "comments",
                comments_fp,
                {"direction_id": direction_id, **collection},
            )
            if not collection["usable"]:
                selection = replace(
                    selection,
                    status="incomplete"
                    if selection.selected_source_count
                    else "insufficient_evidence",
                )

        packet_record = self._checkpoint(subagent_task_id, "packet", selection_fp)
        replayed_packet = packet_record is not None
        if packet_record:
            packet_ids = tuple(packet_record.payload["packet_ids"])
        else:
            self._start_checkpoint(subagent_task_id, "packet")
            packet_ids = tuple(self._persist_packets(direction_id, selection, candidate_by_id))
            self._save_checkpoint(
                subagent_task_id,
                "packet",
                selection_fp,
                {
                    "direction_id": direction_id,
                    "packet_ids": list(packet_ids),
                    "status": "incomplete"
                    if len(packet_ids) < selection.selected_source_count
                    else selection.status,
                },
            )
        if len(packet_ids) < selection.selected_source_count:
            selection = replace(selection, status="incomplete")
        if admission_contract and admission_policy and policy_snapshot:
            admission_packet_ids = (*packet_ids, *comment_packet_ids)
            self._run_admission(
                subagent_task_id,
                direction_id,
                selection,
                admission_packet_ids,
                admission_contract,
                admission_policy,
                policy_snapshot,
            )
        await self._flush()
        return DirectionEvidenceRun(
            selection,
            packet_ids,
            comment_packet_ids,
            replayed_collect,
            replayed_selection,
            replayed_packet,
            self._blocking_operation_failure(subagent_task_id, selection.status),
        )

    def replay_admission_from_persisted_packets(
        self,
        *,
        workflow_run_id: str,
        subagent_task_id: str,
        direction_id: str,
        contract: DirectionContract,
        policy: SamplePolicy,
        snapshot: RunPolicySnapshot,
    ) -> tuple[str, ...]:
        """Replay admission only; this boundary has no provider-call capability."""
        checkpoints = [
            item
            for item in self._store.list_typed_records(StageCheckpointRecord)
            if item.workflow_run_id == workflow_run_id
            and item.subagent_task_id == subagent_task_id
            and item.status == "completed"
        ]
        packet_checkpoint = next(
            (item for item in reversed(checkpoints) if item.stage_name == "packet"),
            None,
        )
        selection_checkpoint = next(
            (
                item
                for item in reversed(checkpoints)
                if item.stage_name == "detail" and item.payload.get("selection")
            ),
            None,
        ) or next(
            (
                item
                for item in reversed(checkpoints)
                if item.stage_name == "selection" and item.payload.get("selection")
            ),
            None,
        )
        if packet_checkpoint is None or selection_checkpoint is None:
            raise ValueError(
                "packet-only admission replay requires completed selection and packet checkpoints"
            )
        if str(packet_checkpoint.payload.get("direction_id") or "") != direction_id:
            raise ValueError("packet checkpoint direction does not match replay direction")
        comment_checkpoint = next(
            (item for item in reversed(checkpoints) if item.stage_name == "comments"),
            None,
        )
        packet_ids = tuple(packet_checkpoint.payload.get("packet_ids") or ())
        comment_packet_ids = (
            tuple(comment_checkpoint.payload.get("packet_ids") or ()) if comment_checkpoint else ()
        )
        if not packet_ids and not comment_packet_ids:
            raise ValueError("packet-only admission replay requires persisted packets")
        selection = _selection_from_payload(selection_checkpoint.payload["selection"])
        self._workflow_run_id = workflow_run_id
        self._checkpoint_started_at = {}
        self._run_admission(
            subagent_task_id,
            direction_id,
            selection,
            (*packet_ids, *comment_packet_ids),
            contract,
            policy,
            snapshot,
        )
        return packet_ids

    def _run_admission(
        self,
        task_id: str,
        direction_id: str,
        selection: DirectionSelection,
        packet_ids: tuple[str, ...],
        contract: DirectionContract,
        policy: SamplePolicy,
        snapshot: RunPolicySnapshot,
    ) -> None:
        relevance_contract = frozen_query_relevance(contract, snapshot)
        strategy = DEFAULT_ADMISSION_STRATEGIES.get(direction_id)
        packets = [
            packet
            for packet_id in packet_ids
            if (packet := self._store.get_typed_record(DirectionalEvidencePacketRecord, packet_id))
            is not None
        ]
        candidates_by_packet: list[tuple[DirectionalEvidencePacketRecord, list]] = []
        for packet in packets:
            if strategy is not None:
                candidates = strategy.build_candidates(packet)
            else:
                facts = extract_facts(packet)
                if not facts:
                    continue
                fact = facts[0]
                scope = {"sample": "selected_packets"}
                if fact.field_path == "comment_text":
                    scope["parent_note_canonical_source_id"] = packet.payload.get(
                        "retrieval_context", {}
                    ).get("parent_note_canonical_source_id")
                candidates = [
                    build_claim_candidate(
                        workflow_run_id=self._workflow_run_id,
                        direction_id=direction_id,
                        intent_id=contract.claim_rules[0],
                        claim_type=contract.claim_rules[0],
                        statement=fact.text,
                        scope=scope,
                        fact=fact,
                        quote=fact.text,
                        text_start=0,
                        text_end=len(fact.text),
                    )
                ]
            candidates_by_packet.append((packet, candidates))
        comment_packets = [
            packet
            for packet, _ in candidates_by_packet
            if packet.payload.get("retrieval_context", {}).get("source_kind") == "comment"
        ]
        candidate_packets = comment_packets or packets
        candidate_packet_ids = {packet.id for packet in candidate_packets}
        relevance_qualified_packet_ids = {
            packet.id
            for packet, candidates in candidates_by_packet
            if packet.id in candidate_packet_ids
            and any(
                query_relevance_reason(
                    candidate=candidate,
                    packet=packet,
                    contract=contract,
                    policy_snapshot=snapshot,
                )
                is None
                for candidate in candidates
            )
        }
        relevant_packets = [
            packet for packet in candidate_packets if packet.id in relevance_qualified_packet_ids
        ]
        eligible_packets = [
            packet
            for packet in relevant_packets
            if _packet_is_admission_eligible(
                packet=packet,
                contract=contract,
                snapshot=snapshot,
            )
        ]
        sample_authors = {
            identity
            for packet in eligible_packets
            if (identity := admission_author_identity(packet.payload.get("field_projection", {})))
        }
        selected_source_count = (
            len(candidate_packets) if comment_packets else selection.selected_source_count
        )
        relevance_qualified_source_count = len(relevant_packets)
        eligible_source_count = len(eligible_packets)
        independent_author_count = len(sample_authors)
        admission_packet_identities = tuple(
            sorted((packet.id, packet.field_projection_hash) for packet in packets)
        )
        checkpoint_identity = {
            "admission_packets": [
                {"id": packet_id, "field_projection_hash": packet_hash}
                for packet_id, packet_hash in admission_packet_identities
            ],
            "policy_snapshot_id": snapshot.id,
            "policy_snapshot_hash": snapshot.effective_policy_hash,
            "contract": {
                "id": contract.id,
                "schema_version": contract.schema_version,
            },
            "sample_policy": {
                "id": policy.id,
                "schema_version": policy.schema_version,
                "minimum_samples": policy.minimum_samples,
                "minimum_independent_authors": (policy.minimum_independent_authors),
                "author_cap": policy.author_cap,
                "metadata": policy.metadata,
            },
            "metrics": {
                "selected_source_count": selected_source_count,
                "relevance_qualified_source_count": (relevance_qualified_source_count),
                "eligible_source_count": eligible_source_count,
                "independent_author_count": independent_author_count,
            },
            "relevance_contract": relevance_contract,
            "relevance_algorithm_version": (
                relevance_contract.get("algorithm_version") if relevance_contract else None
            ),
            "admission_algorithm_version": ALGORITHM_VERSION,
            "direction_result_algorithm_version": DIRECTION_RESULT_ALGORITHM_VERSION,
        }
        fingerprint = canonical_fingerprint(checkpoint_identity)
        if self._checkpoint(task_id, "admission", fingerprint):
            return
        self._start_checkpoint(task_id, "facts")
        self._start_checkpoint(task_id, "admission")
        decisions: list[ClaimAdmissionDecisionRecord] = []
        for packet, candidates in candidates_by_packet:
            for candidate in candidates:
                if self._store.get_typed_record(type(candidate), candidate.id) is None:
                    self._store.save_claim_candidate(candidate)
                decision = (
                    ClaimAdmissionEvaluator()
                    .evaluate(
                        candidate=candidate,
                        packet=packet,
                        contract=contract,
                        sample_policy=policy,
                        policy_snapshot=snapshot,
                        selected_source_count=selected_source_count,
                        relevance_qualified_source_count=relevance_qualified_source_count,
                        eligible_source_count=eligible_source_count,
                        independent_author_count=independent_author_count,
                        admission_packet_identities=admission_packet_identities,
                    )
                    .record
                )
                if self._store.get_typed_record(ClaimAdmissionDecisionRecord, decision.id) is None:
                    self._store.save_claim_admission_decision(decision)
                decisions.append(decision)
        output = build_direction_result(
            direction_id=direction_id, policy_snapshot_id=snapshot.id, decisions=decisions
        )
        if (
            self._store.get_typed_record(type(output.direction_result), output.direction_result.id)
            is None
        ):
            self._store.save_direction_result_decision(output.direction_result)
        for weak in output.weak_signals:
            if self._store.get_typed_record(type(weak), weak.id) is None:
                self._store.save_weak_signal(weak)
        self._save_checkpoint(
            task_id,
            "facts",
            fingerprint,
            {"direction_id": direction_id, "packet_ids": list(packet_ids)},
        )
        self._save_checkpoint(
            task_id,
            "admission",
            fingerprint,
            {
                "direction_id": direction_id,
                "decision_ids": [item.id for item in decisions],
                "direction_result_id": output.direction_result.id,
                "policy_snapshot_id": snapshot.id,
                "policy_snapshot_hash": snapshot.effective_policy_hash,
                "relevance_contract": relevance_contract,
                "algorithm_version": ALGORITHM_VERSION,
                "direction_result_algorithm_version": DIRECTION_RESULT_ALGORITHM_VERSION,
                "sample_policy": checkpoint_identity["sample_policy"],
                "computed_metrics": checkpoint_identity["metrics"],
                "admission_packet_identities": checkpoint_identity["admission_packets"],
            },
        )

    async def _collect_search_pages(
        self,
        *,
        subagent_task_id: str,
        plan_hash: str,
        groups: tuple[QueryGroup, ...],
        discover: Callable[[QueryGroup], Awaitable[SourceOperationResult | list[dict[str, Any]]]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        candidates: list[dict[str, Any]] = []
        summaries: list[dict[str, Any]] = []
        stop_provider_calls = False
        for group in groups:
            if stop_provider_calls:
                break
            pages = self._page_records(subagent_task_id, "collect_page", plan_hash, group.id)
            group_candidates = [item for page in pages for item in page.payload["items"]]
            latest = pages[-1] if pages else None
            if latest is not None:
                self._complete_operation(
                    subagent_task_id,
                    "discover",
                    str(latest.payload["operation_fingerprint"]),
                )
            cursor = latest.payload.get("next_cursor") if latest else None
            terminal = bool(latest and latest.payload.get("terminal"))
            page_no = len(pages)
            while not terminal:
                request = {
                    "query_group_id": group.id,
                    "query": group.query,
                    "sort": group.sort,
                    "candidate_limit": group.candidate_limit,
                    "cursor": cursor,
                }
                operation_fingerprint = self._begin_operation(
                    subagent_task_id,
                    operation="discover",
                    request=request,
                )
                self._start_checkpoint(subagent_task_id, "collect_page")
                await self._flush()
                try:
                    result = _discover_page(await discover(replace(group, cursor=cursor)))
                except Exception:
                    self._terminal_operation(
                        subagent_task_id,
                        "discover",
                        operation_fingerprint,
                        status="outcome_unknown",
                        failure_code="provider_call_interrupted",
                        recovery_action="确认外部调用结果后再恢复；系统不会自动重放。",
                    )
                    await self._flush()
                    raise
                remaining = max(group.candidate_limit - len(group_candidates), 0)
                page_items = [
                    {
                        **_manifest_value(item),
                        "query_group_id": group.id,
                        "query_rank": len(group_candidates) + index,
                    }
                    for index, item in enumerate(
                        result.items[:remaining],
                        start=1,
                    )
                    if isinstance(item, dict)
                ]
                group_candidates.extend(page_items)
                reached_cap = len(group_candidates) >= group.candidate_limit
                truncated = bool(result.next_cursor and reached_cap)
                terminal = (
                    not result.next_cursor
                    or result.next_cursor == cursor
                    or reached_cap
                    or result.status not in {"completed", "partial_completed"}
                )
                page_no += 1
                page_payload = {
                    "base_fingerprint": plan_hash,
                    "query_group_id": group.id,
                    "page_no": page_no,
                    "cursor": cursor,
                    "next_cursor": result.next_cursor,
                    "items": page_items,
                    "actual_count": len(group_candidates),
                    "target_count": group.candidate_limit,
                    "sort": group.sort,
                    "status": result.status,
                    "completeness": "truncated_by_cap" if truncated else result.completeness,
                    "terminal": terminal,
                    "operation_fingerprint": operation_fingerprint,
                    "failure_reason": result.failure_reason,
                    "retryable": result.retryable,
                }
                page_fingerprint = canonical_fingerprint(
                    {"base": plan_hash, "group": group.id, "page": page_no, "cursor": cursor}
                )
                self._save_checkpoint(
                    subagent_task_id, "collect_page", page_fingerprint, page_payload
                )
                self._terminal_operation_from_result(
                    subagent_task_id, "discover", operation_fingerprint, result
                )
                await self._flush()
                cursor = result.next_cursor
                if _is_provider_wide_failure(result):
                    stop_provider_calls = True
                    break
            candidates.extend(group_candidates)
            final_pages = self._page_records(subagent_task_id, "collect_page", plan_hash, group.id)
            final_page = final_pages[-1] if final_pages else None
            summaries.append(
                {
                    "query_group_id": group.id,
                    "actual_count": len(group_candidates),
                    "target_count": group.candidate_limit,
                    "sort": group.sort,
                    "last_cursor": cursor,
                    "completeness": final_page.payload["completeness"]
                    if final_page
                    else "complete",
                }
            )
        return candidates, summaries

    async def _collect_required_comments(
        self,
        *,
        subagent_task_id: str,
        direction_id: str,
        comments_fingerprint: str,
        selection: DirectionSelection,
        candidate_by_id: Mapping[str, dict[str, Any]],
        collect_comments: Callable[[dict[str, Any]], Awaitable[SourceOperationResult]],
        required_comment_fields: tuple[str, ...],
        comment_limit: int,
        top_level_only: bool,
        reply_depth_limit: int,
        comment_policy_id: str | None,
    ) -> dict[str, Any]:
        parent_results: list[dict[str, Any]] = []
        collected_by_parent: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
        comments_fp = comments_fingerprint
        page_records = self._page_records(subagent_task_id, "comments_page", comments_fp)
        for page in page_records:
            parent_id = str(page.payload["parent_note_id"])
            candidate = candidate_by_id.get(parent_id)
            if candidate is None:
                continue
            collected_by_parent.setdefault(parent_id, (candidate, []))[1].extend(
                page.payload["items"]
            )
            self._complete_operation(
                subagent_task_id,
                "comments",
                str(page.payload["operation_fingerprint"]),
            )
        collected_comment_ids = {
            str(item.get("canonical_id") or "")
            for _, items in collected_by_parent.values()
            for item in items
        } - {""}
        stopped_by_direction_cap = False
        stop_provider_calls = False
        for decision in selection.decisions:
            if stop_provider_calls:
                break
            if len(collected_comment_ids) >= comment_limit:
                stopped_by_direction_cap = True
                break
            if not decision.selected:
                continue
            candidate = candidate_by_id.get(decision.canonical_source_id)
            if candidate is None or candidate.get("source_kind") in {
                "search_result",
                "search_result_minimal",
            }:
                continue
            parent_id = str(candidate.get("canonical_id") or decision.canonical_source_id)
            pages = [item for item in page_records if item.payload["parent_note_id"] == parent_id]
            latest = pages[-1] if pages else None
            cursor = latest.payload.get("next_cursor") if latest else None
            terminal = bool(latest and latest.payload.get("terminal"))
            page_no = len(pages)
            while not terminal and len(collected_comment_ids) < comment_limit:
                remaining = comment_limit - len(collected_comment_ids)
                request = {
                    "parent_note_canonical_source_id": decision.canonical_source_id,
                    "cursor": cursor,
                    "comment_limit": remaining,
                    "top_level_only": top_level_only,
                    "reply_depth_limit": reply_depth_limit,
                    "sample_policy_id": comment_policy_id,
                }
                operation_fingerprint = self._begin_operation(
                    subagent_task_id, operation="comments", request=request
                )
                self._start_checkpoint(subagent_task_id, "comments_page")
                await self._flush()
                try:
                    result = await collect_comments(
                        {
                            **candidate,
                            "_collection_cursor": cursor,
                            "_collection_limit": remaining,
                            "_collection_top_level_only": top_level_only,
                            "_collection_reply_depth_limit": reply_depth_limit,
                        }
                    )
                except Exception:
                    self._terminal_operation(
                        subagent_task_id,
                        "comments",
                        operation_fingerprint,
                        status="outcome_unknown",
                        failure_code="provider_call_interrupted",
                        recovery_action="确认外部调用结果后再恢复；系统不会自动重放。",
                    )
                    await self._flush()
                    raise
                page_items = [
                    _manifest_value(item) for item in result.items if isinstance(item, dict)
                ]
                page_items = page_items[:remaining]
                known_for_parent = collected_by_parent.setdefault(parent_id, (candidate, []))[1]
                known_for_parent.extend(page_items)
                collected_comment_ids.update(
                    str(item.get("canonical_id") or "") for item in page_items
                )
                collected_comment_ids.discard("")
                reached_cap = len(collected_comment_ids) >= comment_limit
                truncated = bool(result.next_cursor and reached_cap)
                terminal = (
                    not result.next_cursor
                    or result.next_cursor == cursor
                    or reached_cap
                    or result.status not in {"completed", "partial_completed"}
                )
                page_no += 1
                page_payload = {
                    "base_fingerprint": comments_fp,
                    "parent_note_id": parent_id,
                    "page_no": page_no,
                    "cursor": cursor,
                    "next_cursor": result.next_cursor,
                    "items": page_items,
                    "actual_count": len(collected_comment_ids),
                    "target_count": comment_limit,
                    "page_limit": remaining,
                    "top_level_only": top_level_only,
                    "reply_depth_limit": reply_depth_limit,
                    "sample_policy_id": comment_policy_id,
                    "sort": "provider_return_order",
                    "status": result.status,
                    "completeness": "truncated_by_cap" if truncated else result.completeness,
                    "terminal": terminal,
                    "operation_fingerprint": operation_fingerprint,
                    "failure_reason": result.failure_reason,
                    "retryable": result.retryable,
                }
                page_fingerprint = canonical_fingerprint(
                    {"base": comments_fp, "parent": parent_id, "page": page_no, "cursor": cursor}
                )
                self._save_checkpoint(
                    subagent_task_id, "comments_page", page_fingerprint, page_payload
                )
                self._terminal_operation_from_result(
                    subagent_task_id, "comments", operation_fingerprint, result
                )
                await self._flush()
                cursor = result.next_cursor
                if _is_provider_wide_failure(result):
                    stop_provider_calls = True
                    break
            if len(collected_comment_ids) >= comment_limit and cursor:
                stopped_by_direction_cap = True
                break

        packet_ids: list[str] = []
        for parent_id, (candidate, items) in collected_by_parent.items():
            parent_pages = [
                item
                for item in self._page_records(subagent_task_id, "comments_page", comments_fp)
                if item.payload["parent_note_id"] == parent_id
            ]
            final_page = parent_pages[-1] if parent_pages else None
            parent_packet_ids = self._persist_comment_packets(
                direction_id=direction_id,
                parent_candidate=candidate,
                parent_query_hits=next(
                    (
                        decision.query_hits
                        for decision in selection.decisions
                        if decision.canonical_source_id == parent_id
                    ),
                    (),
                ),
                parent_query_plan_hash=selection.query_plan_hash,
                items=items,
                required_comment_fields=required_comment_fields,
                collection_metadata={
                    "sort": "provider_return_order",
                    "target_comment_count": comment_limit,
                    "actual_comment_count": len(collected_comment_ids),
                    "next_cursor": final_page.payload.get("next_cursor") if final_page else None,
                    "top_level_only": top_level_only,
                    "reply_depth_limit": reply_depth_limit,
                    "sample_policy_id": comment_policy_id,
                    "completeness": "truncated_by_cap"
                    if stopped_by_direction_cap
                    else (final_page.payload["completeness"] if final_page else "unavailable"),
                    "status": final_page.payload["status"] if final_page else "failed",
                    "failure_reason": None,
                },
            )
            packet_ids.extend(parent_packet_ids)
            parent_results.append(
                {
                    "parent_note_id": parent_id,
                    "status": final_page.payload["status"] if final_page else "failed",
                    "failure_reason": None,
                    "completeness": "truncated_by_cap"
                    if stopped_by_direction_cap
                    else (final_page.payload["completeness"] if final_page else "unavailable"),
                    "next_cursor": final_page.payload.get("next_cursor") if final_page else None,
                    "actual_comment_count": len(items),
                    "deduplicated_comment_count": len(parent_packet_ids),
                    "deduplicated_author_count": len(
                        {str(item["author_id"]) for item in items if item.get("author_id")}
                    ),
                }
            )
        return {
            "required": True,
            "status": "completed" if packet_ids else "incomplete",
            "usable": bool(packet_ids),
            "packet_ids": packet_ids,
            "parents": parent_results,
        }

    def _blocking_operation_failure(self, task_id: str, selection_status: str) -> str | None:
        failures = [
            str((item.payload.get("completion") or {}).get("failure_code") or "")
            for item in self._store.list_typed_records(StageCheckpointRecord)
            if item.workflow_run_id == self._workflow_run_id
            and item.subagent_task_id == task_id
            and item.stage_name == "operation"
            and item.status != "superseded"
            and (item.payload.get("completion") or {}).get("failure_code")
        ]
        terminal_codes = {
            "parser_error",
            "provider_access_rejected",
            "provider_permanent_error",
        }
        for code in failures:
            if code in terminal_codes or code in {"auth_required", "auth_expired"}:
                return code
        if selection_status != "complete":
            for code in failures:
                if code in {
                    "timeout",
                    "transient_error",
                    "rate_limited",
                    "unavailable",
                }:
                    return code
        return None

    def _persist_packets(
        self,
        direction_id: str,
        selection: DirectionSelection,
        candidate_by_id: Mapping[str, dict[str, Any]],
    ) -> list[str]:
        packet_ids: list[str] = []
        for decision in selection.decisions:
            if not decision.selected:
                continue
            candidate = candidate_by_id[decision.canonical_source_id]
            if candidate.get("source_kind") in {"search_result", "search_result_minimal"}:
                continue
            provider = str(candidate.get("provider") or "xiaohongshu")
            source = self._canonical_sources.resolve_note(
                provider=provider,
                note_id=str(candidate.get("canonical_id") or decision.canonical_source_id),
                canonical_url=str(candidate.get("source_url") or ""),
            )
            packet = build_packet(
                direction_id=direction_id,
                canonical_source_id=source.id,
                fields=candidate,
                availability=dict(candidate.get("field_availability") or {}),
                retrieval_context={
                    "query_group_ids": list(decision.query_group_ids),
                    "query_hits": list(decision.query_hits),
                    "query_plan_hash": selection.query_plan_hash,
                    "source_kind": candidate.get("source_kind"),
                },
            )
            packet_id = f"dep_{canonical_fingerprint({'run': self._workflow_run_id, 'packet': packet['field_projection_hash']})[:24]}"
            if self._store.get_typed_record(DirectionalEvidencePacketRecord, packet_id) is None:
                self._store.save_directional_evidence_packet(
                    DirectionalEvidencePacketRecord(
                        packet_id,
                        "content_research_directional_packet_v1",
                        packet,
                        workflow_run_id=self._workflow_run_id,
                        research_direction_id=direction_id,
                        canonical_source_id=source.id,
                        field_projection_hash=packet["field_projection_hash"],
                    )
                )
            projection_id = f"dsp_{canonical_fingerprint({'run': self._workflow_run_id, 'direction': direction_id, 'source': source.id, 'packet': packet_id})[:24]}"
            if self._store.get_typed_record(DirectionSourceProjectionRecord, projection_id) is None:
                self._store.save_direction_source_projection(
                    DirectionSourceProjectionRecord(
                        projection_id,
                        "content_research_direction_projection_v1",
                        {
                            "selected": True,
                            "reasons": list(decision.reasons),
                            "query_group_ids": list(decision.query_group_ids),
                            "query_hits": list(decision.query_hits),
                        },
                        workflow_run_id=self._workflow_run_id,
                        research_direction_id=direction_id,
                        canonical_source_id=source.id,
                        evidence_packet_id=packet_id,
                    )
                )
            packet_ids.append(packet_id)
        return packet_ids

    def _persist_comment_packets(
        self,
        *,
        direction_id: str,
        parent_candidate: Mapping[str, Any],
        parent_query_hits: tuple[dict[str, Any], ...],
        parent_query_plan_hash: str,
        items: list[dict[str, Any]],
        required_comment_fields: tuple[str, ...],
        collection_metadata: dict[str, Any],
    ) -> list[str]:
        provider = str(parent_candidate.get("provider") or "xiaohongshu")
        parent = self._canonical_sources.resolve_note(
            provider=provider,
            note_id=str(parent_candidate.get("canonical_id") or ""),
            canonical_url=str(parent_candidate.get("source_url") or ""),
        )
        seen_comment_ids: set[str] = set()
        unique_items: list[dict[str, Any]] = []
        author_ids: set[str] = set()
        for item in items:
            comment_id = str(item.get("canonical_id") or "")
            if not comment_id or comment_id in seen_comment_ids:
                continue
            seen_comment_ids.add(comment_id)
            unique_items.append(item)
            author = str(item.get("author_id") or "")
            if author:
                author_ids.add(author)
        final_collection = {
            **collection_metadata,
            "deduplicated_comment_count": len(unique_items),
            "deduplicated_author_count": len(author_ids),
        }
        repeated_need_phrases: dict[str, dict[str, Any]] = {}
        for item in unique_items:
            text = str(item.get("comment_text") or item.get("content_text") or "").strip()
            if not any(term in text for term in ("需要", "希望", "想要", "能不能")):
                continue
            phrase = " ".join(text.split()).lower()
            entry = repeated_need_phrases.setdefault(phrase, {"comment_count": 0, "authors": set()})
            entry["comment_count"] += 1
            author = str(item.get("author_id") or "")
            if author:
                entry["authors"].add(author)
        final_collection["repeated_need_phrases"] = {
            phrase: {
                "comment_count": entry["comment_count"],
                "independent_author_count": len(entry["authors"]),
            }
            for phrase, entry in repeated_need_phrases.items()
        }
        packet_ids: list[str] = []
        for item in unique_items:
            comment_id = str(item.get("canonical_id") or "")
            source = self._canonical_sources.resolve_comment(
                provider=provider, comment_id=comment_id, parent_note_canonical_source_id=parent.id
            )
            context = {
                "source_kind": "comment",
                "parent_note_canonical_source_id": parent.id,
                "query_group_ids": [str(item["query_group_id"]) for item in parent_query_hits],
                "query_hits": list(parent_query_hits),
                "query_plan_hash": parent_query_plan_hash,
                "required_comment_fields": list(required_comment_fields),
                "collection": final_collection,
            }
            packet = build_packet(
                direction_id=direction_id,
                canonical_source_id=source.id,
                fields={
                    **item,
                    "comment_text": item.get("comment_text") or item.get("content_text"),
                    # Comments are rendered against their parent-note source when the
                    # provider does not supply a comment permalink; citation anchors
                    # must still retain a resolvable source URL.
                    "source_url": item.get("source_url") or parent_candidate.get("source_url"),
                    "parent_note_id": parent.id,
                },
                availability=dict(item.get("field_availability") or {}),
                retrieval_context=context,
            )
            packet_id = f"dep_{canonical_fingerprint({'run': self._workflow_run_id, 'packet': packet['field_projection_hash']})[:24]}"
            if self._store.get_typed_record(DirectionalEvidencePacketRecord, packet_id) is None:
                self._store.save_directional_evidence_packet(
                    DirectionalEvidencePacketRecord(
                        packet_id,
                        "content_research_directional_packet_v1",
                        packet,
                        workflow_run_id=self._workflow_run_id,
                        research_direction_id=direction_id,
                        canonical_source_id=source.id,
                        field_projection_hash=packet["field_projection_hash"],
                    )
                )
            projection_id = f"dsp_{canonical_fingerprint({'run': self._workflow_run_id, 'direction': direction_id, 'source': source.id, 'packet': packet_id})[:24]}"
            if self._store.get_typed_record(DirectionSourceProjectionRecord, projection_id) is None:
                self._store.save_direction_source_projection(
                    DirectionSourceProjectionRecord(
                        projection_id,
                        "content_research_direction_projection_v1",
                        {
                            "selected": True,
                            "parent_note_canonical_source_id": parent.id,
                            "query_group_ids": context["query_group_ids"],
                            "query_hits": context["query_hits"],
                            "collection": context["collection"],
                        },
                        workflow_run_id=self._workflow_run_id,
                        research_direction_id=direction_id,
                        canonical_source_id=source.id,
                        evidence_packet_id=packet_id,
                    )
                )
            packet_ids.append(packet_id)
        return packet_ids

    def _page_records(
        self,
        task_id: str,
        stage: str,
        base_fingerprint: str,
        scope_id: str | None = None,
    ) -> list[StageCheckpointRecord]:
        records = [
            item
            for item in self._store.list_typed_records(StageCheckpointRecord)
            if item.workflow_run_id == self._workflow_run_id
            and item.subagent_task_id == task_id
            and item.stage_name == stage
            and item.status == "completed"
            and item.payload.get("base_fingerprint") == base_fingerprint
            and (
                scope_id is None
                or item.payload.get("query_group_id") == scope_id
                or item.payload.get("parent_note_id") == scope_id
            )
        ]
        return sorted(records, key=lambda item: int(item.payload["page_no"]))

    def _checkpoint(
        self, task_id: str, stage: str, fingerprint: str
    ) -> StageCheckpointRecord | None:
        checkpoint = self._store.get_typed_record(
            StageCheckpointRecord,
            _checkpoint_id(self._workflow_run_id, task_id, stage, fingerprint),
        )
        return checkpoint if checkpoint is not None and checkpoint.status == "completed" else None

    def _begin_operation(self, task_id: str, *, operation: str, request: Mapping[str, Any]) -> str:
        """Durably claim an external call before invoking its adapter callable."""
        operation_fingerprint = self._operation_fingerprint(operation, request)
        lifecycle = self._operation_lifecycle(task_id, operation_fingerprint)
        if any(item.status == "outcome_unknown" for item in lifecycle):
            raise OperationOutcomeUnknownError(
                operation=operation, operation_fingerprint=operation_fingerprint
            )
        if any(item.status == "running" for item in lifecycle):
            self._save_operation_checkpoint(
                task_id, operation, operation_fingerprint, "outcome_unknown"
            )
            raise OperationOutcomeUnknownError(
                operation=operation, operation_fingerprint=operation_fingerprint
            )
        if any(item.status == "completed" for item in lifecycle):
            # The caller has no durable result to continue from, so reissuing the
            # request would still risk a duplicate provider-side operation.
            self._save_operation_checkpoint(
                task_id, operation, operation_fingerprint, "outcome_unknown"
            )
            raise OperationOutcomeUnknownError(
                operation=operation, operation_fingerprint=operation_fingerprint
            )
        self._save_operation_checkpoint(
            task_id, operation, operation_fingerprint, "running", request=request
        )
        return operation_fingerprint

    def _complete_operation(
        self,
        task_id: str,
        operation: str,
        operation_fingerprint: str,
        *,
        completion: Mapping[str, Any] | None = None,
    ) -> None:
        self._save_operation_checkpoint(
            task_id,
            operation,
            operation_fingerprint,
            "completed",
            completion=completion,
        )

    def _terminal_operation_from_result(
        self,
        task_id: str,
        operation: str,
        operation_fingerprint: str,
        result: SourceOperationResult,
    ) -> None:
        outcome = _safe_operation_outcome(result)
        failure_code = result.failure_reason
        if result.status in {"completed", "empty", "partial_completed"}:
            self._complete_operation(task_id, operation, operation_fingerprint, completion=outcome)
            return
        status = _operation_terminal_status(failure_code)
        self._terminal_operation(
            task_id,
            operation,
            operation_fingerprint,
            status=status,
            failure_code=failure_code,
            failure_reason=failure_code,
            retryable=result.retryable,
            recovery_action=_recovery_action(failure_code, result.retryable),
            outcome=outcome,
        )

    def _terminal_operation(
        self,
        task_id: str,
        operation: str,
        operation_fingerprint: str,
        *,
        status: str,
        failure_code: str | None = None,
        failure_reason: str | None = None,
        retryable: bool = False,
        recovery_action: str | None = None,
        outcome: Mapping[str, Any] | None = None,
    ) -> None:
        self._save_operation_checkpoint(
            task_id,
            operation,
            operation_fingerprint,
            status,
            completion={
                **dict(outcome or {}),
                "failure_code": failure_code,
                "failure_reason": failure_reason,
                "retryable": retryable,
                "recovery_action": recovery_action,
            },
        )

    def _operation_fingerprint(self, operation: str, request: Mapping[str, Any]) -> str:
        return canonical_fingerprint(
            {"operation": operation, "request": _safe_operation_request(request)}
        )

    def _completed_operation(
        self, task_id: str, operation_fingerprint: str
    ) -> StageCheckpointRecord | None:
        return next(
            (
                item
                for item in self._operation_lifecycle(task_id, operation_fingerprint)
                if item.status == "completed"
            ),
            None,
        )

    def _operation_lifecycle(
        self, task_id: str, operation_fingerprint: str
    ) -> list[StageCheckpointRecord]:
        return [
            item
            for item in self._store.list_typed_records(StageCheckpointRecord)
            if item.workflow_run_id == self._workflow_run_id
            and item.subagent_task_id == task_id
            and item.stage_name == "operation"
            and item.payload.get("operation_fingerprint") == operation_fingerprint
        ]

    def _save_operation_checkpoint(
        self,
        task_id: str,
        operation: str,
        operation_fingerprint: str,
        status: str,
        *,
        request: Mapping[str, Any] | None = None,
        completion: Mapping[str, Any] | None = None,
    ) -> None:
        record_id = _operation_checkpoint_id(
            self._workflow_run_id, task_id, operation_fingerprint, status
        )
        existing = self._store.get_typed_record(StageCheckpointRecord, record_id)
        if existing is not None and existing.status != "superseded":
            return
        payload: dict[str, Any] = {
            "workflow_run_id": self._workflow_run_id,
            "operation": operation,
            "operation_fingerprint": operation_fingerprint,
            "operation_state": status,
        }
        if request is not None:
            payload["request"] = _safe_operation_request(request)
        if completion is not None:
            payload["completion"] = _safe_operation_request(completion)
        running = next(
            (
                item
                for item in self._operation_lifecycle(task_id, operation_fingerprint)
                if item.status == "running"
            ),
            None,
        )
        started_at = (
            _utcnow()
            if status == "running"
            else (running.started_at if running is not None else None)
        )
        finished_at = _utcnow() if status != "running" and started_at is not None else None
        self._store.save_stage_checkpoint(
            StageCheckpointRecord(
                record_id,
                "content_research_stage_checkpoint_v1",
                payload,
                workflow_run_id=self._workflow_run_id,
                subagent_task_id=task_id,
                stage_name="operation",
                input_fingerprint=operation_fingerprint,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
            )
        )

    def _selection_revisions(
        self, task_id: str, selection_fingerprint: str
    ) -> list[StageCheckpointRecord]:
        revisions = [
            item
            for item in self._store.list_typed_records(StageCheckpointRecord)
            if item.workflow_run_id == self._workflow_run_id
            and item.subagent_task_id == task_id
            and item.stage_name == "selection_revision"
            and item.status == "completed"
            and item.payload.get("base_selection_fingerprint") == selection_fingerprint
        ]
        return sorted(revisions, key=lambda item: int(item.payload["revision"]))

    def _start_checkpoint(self, task_id: str, stage: str) -> None:
        self._checkpoint_started_at[(task_id, stage)] = _utcnow()

    def _save_checkpoint(
        self, task_id: str, stage: str, fingerprint: str, payload: dict[str, Any]
    ) -> None:
        stored_payload = {"workflow_run_id": self._workflow_run_id, **payload}
        started_at = self._checkpoint_started_at.pop((task_id, stage), None)
        finished_at = _utcnow() if started_at is not None else None
        self._store.save_stage_checkpoint(
            StageCheckpointRecord(
                _checkpoint_id(self._workflow_run_id, task_id, stage, fingerprint),
                "content_research_stage_checkpoint_v1",
                stored_payload,
                workflow_run_id=self._workflow_run_id,
                subagent_task_id=task_id,
                stage_name=stage,
                input_fingerprint=fingerprint,
                status="completed",
                started_at=started_at,
                finished_at=finished_at,
            )
        )


# Temporary import name for callers created during the earlier incomplete
# refactor. The formal router only uses DirectionalExecutionPipeline.
DirectionalEvidencePipeline = DirectionalExecutionPipeline


def _checkpoint_id(workflow_run_id: str, task_id: str, stage: str, fingerprint: str) -> str:
    return f"scp_{canonical_fingerprint({'run': workflow_run_id, 'task': task_id, 'stage': stage, 'input': fingerprint})[:24]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _operation_checkpoint_id(
    workflow_run_id: str, task_id: str, operation_fingerprint: str, status: str
) -> str:
    return f"scp_{canonical_fingerprint({'run': workflow_run_id, 'task': task_id, 'stage': 'operation', 'input': operation_fingerprint, 'status': status})[:24]}"


def _operation_terminal_status(failure_code: str | None) -> str:
    if failure_code == "auth_required":
        return "auth_required"
    if failure_code == "rate_limited":
        return "rate_limited"
    if failure_code == "timeout":
        return "timed_out"
    return "failed"


def _is_provider_wide_failure(result: SourceOperationResult) -> bool:
    if result.status in {"completed", "partial_completed", "empty"}:
        return False
    return result.failure_reason not in {
        "invalid_candidate",
        "note_unavailable",
        "empty_result",
    }


def _recovery_action(failure_code: str | None, retryable: bool) -> str | None:
    if failure_code == "auth_required":
        return "更新小红书登录态后继续。"
    if failure_code == "provider_access_rejected":
        # The detail payload can be valid while the provider rejects the
        # non-browser signing/fingerprint envelope. Do not misdirect the user
        # into repeatedly replacing a working login Cookie.
        return "笔记详情请求的浏览器安全上下文不兼容；请启用或更新兼容的浏览器会话详情采集提供者后重新发起调研。"
    if retryable or failure_code in {"timeout", "transient_error", "rate_limited", "unavailable"}:
        return "稍后重试本次采集。"
    return None


def _safe_operation_request(request: Mapping[str, Any]) -> dict[str, Any]:
    forbidden = {"raw_payload", "access_token", "token", "cookie", "credentials"}
    return {
        str(key): _safe_operation_value(value)
        for key, value in sorted(request.items())
        if str(key) not in forbidden
    }


def _safe_operation_outcome(result: SourceOperationResult) -> dict[str, Any]:
    """Persist the small provider outcome needed for diagnosis, never payloads."""
    outcome = {
        "provider": result.provider,
        "provider_operation": result.operation,
        "source_kind": result.source_kind,
        "result_status": result.status,
        "item_count": len(result.items),
        "completeness": result.completeness,
        "cookie_status": result.cookie_status,
        "has_next_cursor": bool(result.next_cursor),
    }
    dispositions = result.metadata.get("candidate_dispositions")
    if isinstance(dispositions, Mapping):
        outcome["candidate_dispositions"] = {
            key: int(dispositions[key])
            for key in ("invalid_candidate", "eligible")
            if isinstance(dispositions.get(key), int) and dispositions[key] >= 0
        }
    automatic_retry_count = result.metadata.get("automatic_retry_count")
    automatic_retry_limit = result.metadata.get("automatic_retry_limit")
    if isinstance(automatic_retry_count, int) and automatic_retry_count >= 0:
        outcome["automatic_retry_count"] = automatic_retry_count
    if isinstance(automatic_retry_limit, int) and automatic_retry_limit >= 0:
        outcome["automatic_retry_limit"] = automatic_retry_limit
    return outcome


def _safe_operation_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _safe_operation_request(value)
    if isinstance(value, (list, tuple)):
        return [_safe_operation_value(item) for item in value]
    return value


def _discover_page(value: SourceOperationResult | list[dict[str, Any]]) -> SourceOperationResult:
    if isinstance(value, SourceOperationResult):
        return value
    return SourceOperationResult(
        provider="pipeline_callback",
        operation="discover_candidates",
        source_kind="search_result_minimal",
        status="completed" if value else "empty",
        items=value,
        completeness="complete",
    )


def _selection_payload(selection: DirectionSelection) -> dict[str, Any]:
    return {**asdict(selection), "decisions": [asdict(item) for item in selection.decisions]}


def _selection_from_payload(payload: dict[str, Any]) -> DirectionSelection:
    return DirectionSelection(
        **{
            **payload,
            "decisions": tuple(
                CandidateDecision(
                    **{
                        **item,
                        "reasons": tuple(item["reasons"]),
                        "query_group_ids": tuple(item["query_group_ids"]),
                        "query_hits": tuple(item.get("query_hits", ())),
                    }
                )
                for item in payload["decisions"]
            ),
            "coverage_unmet_query_group_ids": tuple(
                payload.get("coverage_unmet_query_group_ids", ())
            ),
        }
    )


def _is_after_as_of(item: Mapping[str, Any], run_as_of_at: datetime | None) -> bool:
    if not run_as_of_at or not item.get("source_published_at"):
        return False
    try:
        published = datetime.fromisoformat(str(item["source_published_at"]).replace("Z", "+00:00"))
    except ValueError:
        return False
    return published > run_as_of_at


def _packet_is_admission_eligible(
    *,
    packet: DirectionalEvidencePacketRecord,
    contract: DirectionContract,
    snapshot: RunPolicySnapshot,
) -> bool:
    projection = dict(packet.payload.get("field_projection") or {})
    if admission_author_identity(projection) is None:
        return False
    source_kind = packet.payload.get("retrieval_context", {}).get("source_kind")
    required_fields = (
        contract.required_comment_fields
        if source_kind == "comment"
        else contract.required_note_fields
    )
    availability = dict(packet.payload.get("field_availability") or {})
    if any(availability.get(field) != "present" for field in required_fields):
        return False
    return not _is_after_as_of(projection, snapshot.run_as_of_at)


def _sort_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    published = str(item.get("source_published_at") or "")
    return (
        int(item.get("query_priority") or 0),
        -float(item.get("relevance") or 0),
        published,
        str(item.get("canonical_source_id") or item.get("canonical_id") or ""),
    )


def _manifest_value(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "provider",
            "canonical_source_id",
            "canonical_id",
            "source_url",
            "source_kind",
            "query_priority",
            "query_rank",
            "query_hits",
            "relevance",
            "source_published_at",
            "source_collected_at",
            "author_id",
            "author",
            "out_of_time_window",
            "blocking_unavailable",
            "detail_attempted",
            "field_availability",
            *PACKET_FIELD_NAMES,
        )
    }


def _candidate_map(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Deduplicate detail targets without discarding any frozen query/rank hit."""
    by_id: dict[str, dict[str, Any]] = {}
    hits_by_id: dict[str, dict[str, int]] = {}
    for item in candidates:
        source_id = str(item.get("canonical_source_id") or item.get("canonical_id") or "")
        if not source_id:
            continue
        current = by_id.get(source_id)
        if current is None or _sort_key(item) < _sort_key(current):
            by_id[source_id] = dict(item)
        source_hits = hits_by_id.setdefault(source_id, {})
        for hit in item.get("query_hits") or ():
            if not isinstance(hit, dict):
                continue
            group_id = str(hit.get("query_group_id") or "")
            rank = int(hit.get("rank") or 0)
            if group_id:
                source_hits[group_id] = min(rank, source_hits.get(group_id, rank))
        group_id = str(item.get("query_group_id") or "")
        if group_id:
            rank = int(item.get("query_rank") or 0)
            source_hits[group_id] = min(rank, source_hits.get(group_id, rank))
    for source_id, item in by_id.items():
        item["query_hits"] = [
            {"query_group_id": group_id, "rank": rank}
            for group_id, rank in sorted(hits_by_id[source_id].items())
        ]
    return by_id
