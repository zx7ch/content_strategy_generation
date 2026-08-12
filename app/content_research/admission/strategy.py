"""Stable extension seam for direction-specific admission behavior."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.content_research.persistence_models import (
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
)


class AdmissionStrategy(ABC):
    """Own candidate construction and evidence-boundary validation for one direction."""

    def __init__(self, direction_id: str) -> None:
        self.direction_id = direction_id.strip()
        if not self.direction_id:
            raise ValueError("admission strategy direction_id cannot be empty")

    @abstractmethod
    def build_candidates(
        self, packet: DirectionalEvidencePacketRecord,
    ) -> list[ClaimCandidateRecord]:
        """Build direction-qualified candidates from one evidence packet."""

    @abstractmethod
    def boundary_reason(self, candidate: ClaimCandidateRecord) -> str | None:
        """Return a stable reason when the candidate exceeds this direction's scope."""
