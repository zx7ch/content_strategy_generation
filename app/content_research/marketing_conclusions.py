"""Pure, deterministic governance for Lite product-marketing conclusions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.content_research.contracts import admission_author_identity
from app.content_research.persistence_models import (
    DirectionalEvidencePacketRecord,
    MarketingConclusionCandidateRecord,
)

MARKETING_CONCLUSION_TRACKS = ("need", "value", "message")
_PROHIBITED_OUTCOME_TERMS = ("偏好", "转化", "购买", "因果", "效果提升", "表现更好")
_MAX_REPORT_TEXT_CHARS = 280


@dataclass(frozen=True)
class MarketingConclusionProposal:
    """One bounded conclusion proposal; it cannot itself admit supporting evidence."""

    id: str
    track: str
    statement: str
    supporting_claim_ids: tuple[str, ...]


@dataclass(frozen=True)
class AdmittedMarketingClaim:
    """The admitted, direction-scoped claim reference available to conclusion analysis."""

    claim_id: str
    research_direction_id: str
    evidence_packet_id: str
    admission_state: str = "admitted"
    quote_field_path: str = "content_text"


@dataclass(frozen=True)
class MarketingConclusionOutcome:
    """The evaluated outcome for one proposal, retaining no mutable inputs."""

    candidate_id: str
    track: str
    statement: str
    supporting_note_count: int
    independent_author_count: int
    body_quote_note_count: int
    reason_codes: tuple[str, ...]

    @property
    def ranking_key(self) -> tuple[int, int, int]:
        return (
            self.independent_author_count,
            self.supporting_note_count,
            self.body_quote_note_count,
        )

    @property
    def is_qualified(self) -> bool:
        return not self.reason_codes


@dataclass(frozen=True)
class MarketingConclusionTrackEvaluation:
    state: str
    candidate_id: str | None
    supporting_note_count: int
    independent_author_count: int
    body_quote_note_count: int
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class MarketingConclusionEvaluation:
    catalog: tuple[MarketingConclusionOutcome, ...]
    tracks: Mapping[str, MarketingConclusionTrackEvaluation]

    def safe_trace_payload(self) -> dict[str, dict[str, dict[str, object]]]:
        """Return counts and reason codes only; never source or conclusion content."""
        return {
            "tracks": {
                track: {
                    "state": outcome.state,
                    "supporting_note_count": outcome.supporting_note_count,
                    "independent_author_count": outcome.independent_author_count,
                    "body_quote_note_count": outcome.body_quote_note_count,
                    "reason_codes": outcome.reason_codes,
                }
                for track, outcome in self.tracks.items()
            }
        }


def _policy_value(policy: Mapping[str, object]) -> Mapping[str, object]:
    nested = policy.get("marketing_conclusion_policy")
    return nested if isinstance(nested, Mapping) else policy


def _index_by_id(records: Iterable[object], attribute: str) -> dict[str, object]:
    if isinstance(records, Mapping):
        return {str(key): value for key, value in records.items()}
    return {
        str(getattr(record, attribute)): record
        for record in records
        if getattr(record, attribute, None)
    }


def _proposal_reason(proposal: MarketingConclusionProposal, tracks: set[str]) -> str | None:
    if proposal.track not in tracks:
        return "conclusion_track_not_supported"
    if not proposal.statement.strip():
        return "conclusion_statement_empty"
    if len(proposal.statement) > _MAX_REPORT_TEXT_CHARS:
        return "conclusion_statement_too_long"
    if any(term in proposal.statement for term in _PROHIBITED_OUTCOME_TERMS):
        return "conclusion_statement_outcome_term_prohibited"
    if not proposal.supporting_claim_ids:
        return "conclusion_support_missing"
    return None


def _proposal_from(
    candidate: MarketingConclusionProposal | MarketingConclusionCandidateRecord,
) -> MarketingConclusionProposal:
    if isinstance(candidate, MarketingConclusionProposal):
        return candidate
    support = candidate.payload.get("supporting_claim_ids")
    support_ids = support if isinstance(support, list | tuple) else ()
    return MarketingConclusionProposal(
        id=candidate.id,
        track=candidate.track,
        statement=str(candidate.payload.get("statement") or ""),
        supporting_claim_ids=tuple(item for item in support_ids if isinstance(item, str)),
    )


def _evaluate_proposal(
    proposal: MarketingConclusionProposal,
    *,
    admitted_by_id: Mapping[str, object],
    packets_by_id: Mapping[str, object],
    tracks: set[str],
    minimum_notes: int,
    minimum_authors: int,
) -> MarketingConclusionOutcome:
    reason = _proposal_reason(proposal, tracks)
    if reason:
        return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, (reason,))

    note_ids: set[str] = set()
    author_ids: set[str] = set()
    body_quote_note_ids: set[str] = set()
    for claim_id in sorted(set(proposal.supporting_claim_ids)):
        claim = admitted_by_id.get(claim_id)
        if claim is None or getattr(claim, "admission_state", None) != "admitted":
            return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, ("conclusion_claim_not_admitted",))
        if getattr(claim, "research_direction_id", None) != "product_marketing":
            return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, ("conclusion_claim_direction_mismatch",))
        packet = packets_by_id.get(str(getattr(claim, "evidence_packet_id", "")))
        if not isinstance(packet, DirectionalEvidencePacketRecord):
            return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, ("conclusion_packet_not_found",))
        if packet.research_direction_id != "product_marketing":
            return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, ("conclusion_packet_direction_mismatch",))
        note_id = packet.canonical_source_id.strip()
        if not note_id:
            return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, ("conclusion_canonical_note_missing",))
        author_id = admission_author_identity(dict(packet.payload.get("field_projection") or {}))
        if not author_id:
            return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, ("conclusion_author_identity_missing",))
        note_ids.add(note_id)
        author_ids.add(author_id)
        if getattr(claim, "quote_field_path", "content_text") == "content_text":
            body_quote_note_ids.add(note_id)

    reasons: list[str] = []
    if len(note_ids) < minimum_notes:
        reasons.append("conclusion_note_count_unmet")
    if len(author_ids) < minimum_authors:
        reasons.append("conclusion_author_count_unmet")
    return MarketingConclusionOutcome(
        proposal.id,
        proposal.track,
        proposal.statement,
        len(note_ids),
        len(author_ids),
        len(body_quote_note_ids),
        tuple(reasons),
    )


def _deduplicated_outcomes(
    candidates: Iterable[MarketingConclusionProposal | MarketingConclusionCandidateRecord],
    **kwargs: object,
) -> tuple[MarketingConclusionOutcome, ...]:
    outcomes: list[MarketingConclusionOutcome] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for candidate in candidates:
        proposal = _proposal_from(candidate)
        support = tuple(sorted(set(proposal.supporting_claim_ids)))
        key = (proposal.track, proposal.statement, support)
        if key in seen:
            continue
        seen.add(key)
        outcomes.append(_evaluate_proposal(proposal, **kwargs))  # type: ignore[arg-type]
    return tuple(outcomes)


def _terminal_outcome(outcome: MarketingConclusionOutcome | None) -> MarketingConclusionTrackEvaluation:
    if outcome is None:
        return MarketingConclusionTrackEvaluation(
            "insufficient_evidence", None, 0, 0, 0, ("conclusion_no_qualified_candidate",)
        )
    return MarketingConclusionTrackEvaluation(
        "insufficient_evidence",
        None,
        outcome.supporting_note_count,
        outcome.independent_author_count,
        outcome.body_quote_note_count,
        outcome.reason_codes,
    )


def evaluate_marketing_conclusions(
    *,
    candidates: Iterable[MarketingConclusionProposal | MarketingConclusionCandidateRecord],
    admitted_claims: Iterable[AdmittedMarketingClaim] | Mapping[str, AdmittedMarketingClaim],
    packets: Iterable[DirectionalEvidencePacketRecord] | Mapping[str, DirectionalEvidencePacketRecord],
    policy: Mapping[str, object],
) -> MarketingConclusionEvaluation:
    """Evaluate admitted support without changing policy, proposals, or packet data."""
    conclusion_policy = _policy_value(policy)
    tracks_value = conclusion_policy.get("tracks")
    if tuple(tracks_value or ()) != MARKETING_CONCLUSION_TRACKS:
        raise ValueError("marketing conclusion policy tracks are invalid")
    minimum_notes = int(conclusion_policy.get("minimum_notes_per_conclusion", 0))
    minimum_authors = int(conclusion_policy.get("minimum_independent_authors_per_conclusion", 0))
    if minimum_notes < 1 or minimum_authors < 1:
        raise ValueError("marketing conclusion policy thresholds are invalid")

    outcomes = _deduplicated_outcomes(
        candidates,
        admitted_by_id=_index_by_id(admitted_claims, "claim_id"),
        packets_by_id=_index_by_id(packets, "id"),
        tracks=set(MARKETING_CONCLUSION_TRACKS),
        minimum_notes=minimum_notes,
        minimum_authors=minimum_authors,
    )
    track_evaluations: dict[str, MarketingConclusionTrackEvaluation] = {}
    for track in MARKETING_CONCLUSION_TRACKS:
        track_outcomes = [item for item in outcomes if item.track == track]
        qualified = [item for item in track_outcomes if item.is_qualified]
        if not qualified:
            track_evaluations[track] = _terminal_outcome(track_outcomes[0] if track_outcomes else None)
            continue
        maximum = max(item.ranking_key for item in qualified)
        winners = [item for item in qualified if item.ranking_key == maximum]
        if len(winners) != 1:
            track_evaluations[track] = MarketingConclusionTrackEvaluation(
                "no_single_primary_conclusion", None, maximum[1], maximum[0], maximum[2], ()
            )
            continue
        winner = winners[0]
        track_evaluations[track] = MarketingConclusionTrackEvaluation(
            "selected",
            winner.candidate_id,
            winner.supporting_note_count,
            winner.independent_author_count,
            winner.body_quote_note_count,
            (),
        )
    return MarketingConclusionEvaluation(outcomes, MappingProxyType(track_evaluations))
