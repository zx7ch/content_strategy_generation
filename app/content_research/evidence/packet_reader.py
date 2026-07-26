"""Run-scoped read model for directional evidence packets."""

from __future__ import annotations

from dataclasses import dataclass

from app.content_research.persistence_models import (
    DirectionalEvidencePacketRecord,
    DirectionSourceProjectionRecord,
    StageCheckpointRecord,
)
from app.content_research.stores.base import ContentResearchStore


@dataclass(frozen=True)
class DirectionPacketReadModel:
    packets: list[DirectionalEvidencePacketRecord]
    projections: list[DirectionSourceProjectionRecord]
    checkpoints: list[StageCheckpointRecord]


class PacketEvidenceReader:
    """The only packet read seam for a workflow direction.

    It deliberately has no EvidenceBundle dependency. Claim admission and
    result building consume this model in later Foundation tasks.
    """

    def __init__(self, store: ContentResearchStore) -> None:
        self._store = store

    def read_direction(
        self, *, workflow_run_id: str, direction_id: str, offset: int = 0, limit: int = 50,
    ) -> DirectionPacketReadModel:
        if offset < 0 or limit < 1:
            raise ValueError("offset must be non-negative and limit must be positive")
        checkpoints = [
            item for item in self._store.list_typed_records(StageCheckpointRecord)
            if item.workflow_run_id == workflow_run_id
            and item.status == "completed"
            and item.payload.get("direction_id") == direction_id
        ]
        return DirectionPacketReadModel(
            packets=self._store.list_directional_evidence_packets(
                workflow_run_id, direction_id, offset=offset, limit=limit,
            ),
            projections=self._store.list_direction_source_projections(
                workflow_run_id, direction_id, offset=offset, limit=limit,
            ),
            checkpoints=checkpoints,
        )
