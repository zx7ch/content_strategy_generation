"""Pure, deterministic governance for Lite product-marketing conclusions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.content_research.admission.candidates import validate_candidate_packet
from app.content_research.admission.quote_fields import quote_fields_for_claim
from app.content_research.contracts import admission_author_identity
from app.content_research.persistence_models import (
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
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
    if not isinstance(nested, Mapping):
        raise ValueError("marketing_conclusion_policy is required")
    frozen_contract = {
        "primary_marketing_goal": "content_seeding",
        "tracks": ["need", "value", "message"],
        "minimum_notes_per_conclusion": 3,
        "minimum_independent_authors_per_conclusion": 2,
        "require_core_and_first_intent_support": True,
        "maximum_primary_conclusions_per_track": 1,
    }
    if dict(nested) != frozen_contract:
        raise ValueError("marketing conclusion frozen contract is invalid")
    return nested


def _index_by_id(records: Iterable[DirectionalEvidencePacketRecord], attribute: str) -> dict[str, DirectionalEvidencePacketRecord]:
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


def _proposal_from(candidate: MarketingConclusionCandidateRecord) -> MarketingConclusionProposal:
    support = candidate.payload.get("supporting_claim_ids")
    support_ids = support if isinstance(support, list | tuple) else ()
    return MarketingConclusionProposal(
        id=candidate.id,
        track=candidate.track,
        statement=str(candidate.payload.get("statement") or ""),
        supporting_claim_ids=tuple(item for item in support_ids if isinstance(item, str)),
    )


def _admitted_by_claim_id(
    admitted_claims: Iterable[tuple[ClaimAdmissionDecisionRecord, ClaimCandidateRecord]],
) -> dict[str, tuple[ClaimAdmissionDecisionRecord, ClaimCandidateRecord]]:
    return {
        claim.id: (decision, claim)
        for decision, claim in admitted_claims
    }


def _evaluate_proposal(
    proposal: MarketingConclusionProposal,
    *,
    workflow_run_id: str,
    admitted_by_id: Mapping[str, tuple[ClaimAdmissionDecisionRecord, ClaimCandidateRecord]],
    packets_by_id: Mapping[str, DirectionalEvidencePacketRecord],
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
        admitted = admitted_by_id.get(claim_id)
        if admitted is None:
            return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, ("conclusion_claim_not_admitted",))
        decision, claim = admitted
        if (
            decision.decision != "admitted"
            or decision.claim_candidate_id != claim.id
            or not isinstance(decision.payload.get("policy_snapshot_hash"), str)
            or not decision.payload["policy_snapshot_hash"]
            or decision.payload.get("reason_codes") not in ([], ())
        ):
            return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, ("conclusion_claim_not_admitted",))
        if (
            decision.research_direction_id != "product_marketing"
            or claim.research_direction_id != "product_marketing"
        ):
            return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, ("conclusion_claim_direction_mismatch",))
        if claim.workflow_run_id != workflow_run_id:
            return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, ("conclusion_claim_run_mismatch",))
        packet = packets_by_id.get(claim.evidence_packet_id)
        if packet is None:
            return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, ("conclusion_packet_not_found",))
        if packet.workflow_run_id != workflow_run_id:
            return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, ("conclusion_packet_run_mismatch",))
        if packet.research_direction_id != claim.research_direction_id:
            return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, ("conclusion_packet_direction_mismatch",))
        refs = claim.payload.get("quote_refs")
        if not isinstance(refs, list) or len(refs) != 1 or not isinstance(refs[0], dict):
            return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, ("conclusion_quote_metadata_invalid",))
        field_path = str(refs[0].get("field_path") or "")
        if field_path not in quote_fields_for_claim("product_marketing", claim.claim_type):
            return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, ("conclusion_quote_field_not_allowed",))
        try:
            validate_candidate_packet(claim, packet)
        except ValueError:
            return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, ("conclusion_quote_metadata_invalid",))
        note_id = packet.canonical_source_id.strip()
        if not note_id:
            return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, ("conclusion_canonical_note_missing",))
        author_id = admission_author_identity(dict(packet.payload.get("field_projection") or {}))
        if not author_id:
            return MarketingConclusionOutcome(proposal.id, proposal.track, proposal.statement, 0, 0, 0, ("conclusion_author_identity_missing",))
        note_ids.add(note_id)
        author_ids.add(author_id)
        if field_path == "content_text":
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
    candidates: Iterable[MarketingConclusionCandidateRecord],
    **kwargs: object,
) -> tuple[MarketingConclusionOutcome, ...]:
    outcomes: list[MarketingConclusionOutcome] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for record in candidates:
        proposal = _proposal_from(record)
        support = tuple(sorted(set(proposal.supporting_claim_ids)))
        key = (proposal.track, proposal.statement, support)
        if key in seen:
            continue
        seen.add(key)
        outcomes.append(
            _evaluate_proposal(proposal, workflow_run_id=record.workflow_run_id, **kwargs)  # type: ignore[arg-type]
        )
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
    candidates: Iterable[MarketingConclusionCandidateRecord],
    admitted_claims: Iterable[tuple[ClaimAdmissionDecisionRecord, ClaimCandidateRecord]],
    packets: Iterable[DirectionalEvidencePacketRecord] | Mapping[str, DirectionalEvidencePacketRecord],
    policy: Mapping[str, object],
) -> MarketingConclusionEvaluation:
    """Evaluate admitted support without changing policy, proposals, or packet data."""
    conclusion_policy = _policy_value(policy)
    tracks_value = conclusion_policy.get("tracks")
    if tuple(tracks_value or ()) != MARKETING_CONCLUSION_TRACKS:
        raise ValueError("marketing conclusion policy tracks are invalid")
    minimum_notes = 3
    minimum_authors = 2

    outcomes = _deduplicated_outcomes(
        candidates,
        admitted_by_id=_admitted_by_claim_id(admitted_claims),
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
    return MarketingConclusionEvaluation(
        tuple(outcome for outcome in outcomes if outcome.is_qualified),
        MappingProxyType(track_evaluations),
    )
