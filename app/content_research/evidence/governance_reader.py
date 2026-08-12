"""Run-and-plan scoped public projections for governance records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.content_research.persistence_models import AggregateClaimRecord, CrossDirectionRecord

if TYPE_CHECKING:
    from app.content_research.stores.base import ContentResearchStore


_FORBIDDEN_PUBLIC_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "prompt",
    "raw_content",
    "raw_payload",
    "token",
}


@dataclass(frozen=True)
class GovernanceReadModel:
    """A paged, safe projection of one workflow plan's governance output."""

    workflow_run_id: str
    research_plan_id: str
    cross_direction_records: list[dict[str, Any]]
    aggregate_claims: list[dict[str, Any]]
    cross_direction_total: int
    aggregate_total: int
    offset: int
    limit: int


class GovernanceReadModelReader:
    """The only typed-record read seam for cross-direction governance."""

    def __init__(self, store: ContentResearchStore) -> None:
        self._store = store

    def read(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> GovernanceReadModel:
        if offset < 0 or not 1 <= limit <= 50:
            raise ValueError("offset must be non-negative and limit must be 1..50")
        cross_direction_records, aggregate_claims = self._records(
            workflow_run_id=workflow_run_id,
            research_plan_id=research_plan_id,
        )
        return GovernanceReadModel(
            workflow_run_id=workflow_run_id,
            research_plan_id=research_plan_id,
            cross_direction_records=cross_direction_records[offset:offset + limit],
            aggregate_claims=aggregate_claims[offset:offset + limit],
            cross_direction_total=len(cross_direction_records),
            aggregate_total=len(aggregate_claims),
            offset=offset,
            limit=limit,
        )

    def read_all(self, *, workflow_run_id: str, research_plan_id: str) -> GovernanceReadModel:
        """Return the complete stable set for an immutable governed snapshot."""
        cross_direction_records, aggregate_claims = self._records(
            workflow_run_id=workflow_run_id,
            research_plan_id=research_plan_id,
        )
        return GovernanceReadModel(
            workflow_run_id=workflow_run_id,
            research_plan_id=research_plan_id,
            cross_direction_records=cross_direction_records,
            aggregate_claims=aggregate_claims,
            cross_direction_total=len(cross_direction_records),
            aggregate_total=len(aggregate_claims),
            offset=0,
            limit=max(len(cross_direction_records), len(aggregate_claims), 1),
        )

    def _records(
        self, *, workflow_run_id: str, research_plan_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        cross_direction_records = sorted(
            (
                _public_record(
                    item.payload,
                    record_id=item.id,
                    identity_key="cross_direction_record_id",
                    kind_key="record_type",
                    kind_value=item.record_type,
                )
                for item in self._store.list_typed_records(CrossDirectionRecord)
                if item.research_plan_id == research_plan_id
                and item.payload.get("workflow_run_id") == workflow_run_id
            ),
            key=lambda item: str(item["cross_direction_record_id"]),
        )
        aggregate_claims = sorted(
            (
                _public_record(
                    item.payload,
                    record_id=item.id,
                    identity_key="aggregate_claim_id",
                    kind_key="aggregate_type",
                    kind_value=item.aggregate_type,
                )
                for item in self._store.list_typed_records(AggregateClaimRecord)
                if item.research_plan_id == research_plan_id
                and item.payload.get("workflow_run_id") == workflow_run_id
            ),
            key=lambda item: str(item["aggregate_claim_id"]),
        )
        return cross_direction_records, aggregate_claims


def _public_record(
    payload: dict[str, Any],
    *,
    record_id: str,
    identity_key: str,
    kind_key: str,
    kind_value: str,
) -> dict[str, Any]:
    return {
        identity_key: record_id,
        kind_key: kind_value,
        **safe_public_projection(payload),
    }


def safe_public_projection(value: Any) -> Any:
    """Remove provider and model secrets recursively from a public read model."""
    if isinstance(value, dict):
        return {
            key: safe_public_projection(item)
            for key, item in value.items()
            if not _is_forbidden_public_key(key)
        }
    if isinstance(value, list):
        return [safe_public_projection(item) for item in value]
    return value


def _is_forbidden_public_key(key: str) -> bool:
    normalized = key.lower()
    return (
        normalized in _FORBIDDEN_PUBLIC_KEYS
        or "prompt" in normalized
        or "token" in normalized
        or "cookie" in normalized
        or "credential" in normalized
        or "secret" in normalized
        or "provider_payload" in normalized
        or "response_body" in normalized
        or normalized.startswith("raw_")
        or "authorization" in normalized
    )
