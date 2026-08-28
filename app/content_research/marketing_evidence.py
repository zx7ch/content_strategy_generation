"""Exact-span marketing evidence, qualifier-safe grouping, and grounded verification."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.content_research.admission.candidates import validate_candidate_packet
from app.content_research.contracts import admission_author_identity
from app.content_research.persistence_models import (
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
    MarketingConclusionCandidateRecord,
)

TRACK_CLAIM_TYPES = {
    "need": frozenset({"use_context", "target_audience_framing"}),
    "value": frozenset({"product_value_expression"}),
    "message": frozenset({"message_angle"}),
}
DEFAULT_CLUSTER_SIMILARITY = 0.55
_LEXICAL_DIMENSIONS = 256
_UNSUPPORTED_CAUSAL_TERMS = ("导致", "因此", "提升转化", "带来销量", "效果提升", "必然")


@dataclass(frozen=True)
class AtomicMarketingEvidence:
    atom_id: str
    claim_id: str
    track: str
    note_id: str
    account_id: str
    field_path: str
    quote: str
    text_start: int
    text_end: int
    polarity: str
    scenes: tuple[str, ...]
    audiences: tuple[str, ...]
    aspect: str = ""
    evidence_type: str = "direct_expression"


@dataclass(frozen=True)
class MarketingEvidenceCluster:
    cluster_id: str
    track: str
    atom_ids: tuple[str, ...]
    scenes: tuple[str, ...]
    audiences: tuple[str, ...]


@dataclass(frozen=True)
class MarketingGroundednessVerification:
    state: str
    reason_codes: tuple[str, ...]
    cluster_ids: tuple[str, ...]
    supporting_atom_ids: tuple[str, ...]
    counter_atom_ids: tuple[str, ...]
    counter_note_count: int
    counter_author_count: int


def build_atomic_marketing_evidence(
    admitted_claims: Iterable[
        tuple[ClaimAdmissionDecisionRecord, ClaimCandidateRecord]
    ],
    packets: Mapping[str, DirectionalEvidencePacketRecord],
) -> tuple[AtomicMarketingEvidence, ...]:
    atoms: list[AtomicMarketingEvidence] = []
    for decision, claim in admitted_claims:
        if decision.decision != "admitted" or decision.claim_candidate_id != claim.id:
            raise ValueError("atomic marketing evidence requires admitted claims")
        packet = packets.get(claim.evidence_packet_id)
        if packet is None:
            raise ValueError("atomic marketing evidence packet is missing")
        validate_candidate_packet(claim, packet)
        track = _track_for_claim_type(claim.claim_type)
        ref = claim.payload["quote_refs"][0]
        projection = dict(packet.payload.get("field_projection") or {})
        account_id = admission_author_identity(projection)
        if not account_id:
            raise ValueError("atomic marketing evidence account is missing")
        scope = claim.payload.get("scope")
        scope = scope if isinstance(scope, dict) else {}
        qualifiers = scope.get("qualifiers")
        qualifiers = qualifiers if isinstance(qualifiers, dict) else {}
        polarity = scope.get("polarity")
        polarity = polarity if polarity in {"support", "counter"} else "support"
        quote = str(ref["quote"])
        start, end = int(ref["text_start"]), int(ref["text_end"])
        atom_id = "mae_" + hashlib.sha256(
            repr((claim.workflow_run_id, claim.id, start, end, quote)).encode()
        ).hexdigest()[:24]
        atoms.append(
            AtomicMarketingEvidence(
                atom_id=atom_id,
                claim_id=claim.id,
                track=track,
                note_id=packet.canonical_source_id,
                account_id=account_id,
                field_path=str(ref["field_path"]),
                quote=quote,
                text_start=start,
                text_end=end,
                polarity=str(polarity),
                scenes=_qualifier_tuple(qualifiers.get("scenes")),
                audiences=_qualifier_tuple(qualifiers.get("audiences")),
            )
        )
    return tuple(sorted(atoms, key=lambda item: item.atom_id))


def lexical_evidence_vectors(
    atoms: Sequence[AtomicMarketingEvidence],
) -> dict[str, tuple[float, ...]]:
    return {atom.atom_id: _lexical_vector(atom.quote) for atom in atoms}


def cluster_atomic_marketing_evidence(
    atoms: Sequence[AtomicMarketingEvidence],
    vectors: Mapping[str, Sequence[float]],
    *,
    similarity_threshold: float = DEFAULT_CLUSTER_SIMILARITY,
) -> tuple[MarketingEvidenceCluster, ...]:
    if not 0 < similarity_threshold <= 1:
        raise ValueError("marketing evidence similarity threshold is invalid")
    parent = list(range(len(atoms)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for left in range(len(atoms)):
        left_vector = _validated_vector(vectors.get(atoms[left].atom_id))
        for right in range(left + 1, len(atoms)):
            if atoms[left].track != atoms[right].track:
                continue
            if not _qualifiers_compatible(atoms[left], atoms[right]):
                continue
            right_vector = _validated_vector(vectors.get(atoms[right].atom_id))
            if _cosine(left_vector, right_vector) >= similarity_threshold:
                union(left, right)

    grouped: dict[int, list[AtomicMarketingEvidence]] = defaultdict(list)
    for index, atom in enumerate(atoms):
        grouped[find(index)].append(atom)
    clusters: list[MarketingEvidenceCluster] = []
    for members in grouped.values():
        ordered = sorted(members, key=lambda item: item.atom_id)
        identity = hashlib.sha256(
            repr((ordered[0].track, tuple(item.atom_id for item in ordered))).encode()
        ).hexdigest()[:24]
        clusters.append(
            MarketingEvidenceCluster(
                cluster_id=f"mec_{identity}",
                track=ordered[0].track,
                atom_ids=tuple(item.atom_id for item in ordered),
                scenes=tuple(sorted({value for item in ordered for value in item.scenes})),
                audiences=tuple(
                    sorted({value for item in ordered for value in item.audiences})
                ),
            )
        )
    return tuple(sorted(clusters, key=lambda item: item.cluster_id))


def verify_marketing_candidate(
    candidate: MarketingConclusionCandidateRecord,
    *,
    atoms: Sequence[AtomicMarketingEvidence],
    clusters: Sequence[MarketingEvidenceCluster],
    contested_minimum_notes: int = 2,
    contested_minimum_authors: int = 2,
) -> MarketingGroundednessVerification:
    atoms_by_claim = {atom.claim_id: atom for atom in atoms}
    atoms_by_id = {atom.atom_id: atom for atom in atoms}
    support_claim_ids = candidate.payload.get("supporting_claim_ids")
    if not isinstance(support_claim_ids, list):
        return _failed_verification("verifier_support_shape_invalid")
    supporting = [atoms_by_claim.get(item) for item in support_claim_ids]
    if any(item is None for item in supporting):
        return _failed_verification("verifier_support_unknown")
    typed_support = [item for item in supporting if item is not None]
    if any(
        item.track != candidate.track or item.polarity != "support"
        for item in typed_support
    ):
        return _failed_verification("verifier_support_track_or_polarity_invalid")
    statement = str(candidate.payload.get("statement") or "")
    if not statement or any(term in statement for term in _UNSUPPORTED_CAUSAL_TERMS):
        return _failed_verification("verifier_statement_not_grounded")

    supporting_atom_ids = {item.atom_id for item in typed_support}
    related_clusters = [
        cluster
        for cluster in clusters
        if supporting_atom_ids & set(cluster.atom_ids)
    ]
    if not related_clusters:
        return _failed_verification("verifier_cluster_missing")
    counter = sorted(
        {
            atom
            for cluster in related_clusters
            for atom_id in cluster.atom_ids
            for atom in [atoms_by_id[atom_id]]
            if atom.polarity == "counter"
        },
        key=lambda item: item.atom_id,
    )
    counter_notes = {item.note_id for item in counter}
    counter_authors = {item.account_id for item in counter}
    state = (
        "contested"
        if len(counter_notes) >= contested_minimum_notes
        and len(counter_authors) >= contested_minimum_authors
        else "verified"
    )
    return MarketingGroundednessVerification(
        state=state,
        reason_codes=("counter_evidence_threshold_met",) if state == "contested" else (),
        cluster_ids=tuple(sorted(cluster.cluster_id for cluster in related_clusters)),
        supporting_atom_ids=tuple(sorted(supporting_atom_ids)),
        counter_atom_ids=tuple(item.atom_id for item in counter),
        counter_note_count=len(counter_notes),
        counter_author_count=len(counter_authors),
    )


def _track_for_claim_type(claim_type: str) -> str:
    matches = [track for track, types in TRACK_CLAIM_TYPES.items() if claim_type in types]
    if len(matches) != 1:
        raise ValueError("atomic marketing evidence claim type has no unique track")
    return matches[0]


def _qualifier_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(sorted({item for item in value if isinstance(item, str) and item}))


def _qualifiers_compatible(
    left: AtomicMarketingEvidence, right: AtomicMarketingEvidence
) -> bool:
    for left_values, right_values in (
        (set(left.scenes), set(right.scenes)),
        (set(left.audiences), set(right.audiences)),
    ):
        if left_values and right_values and left_values.isdisjoint(right_values):
            return False
    return True


def _lexical_vector(text: str) -> tuple[float, ...]:
    compact = "".join(text.lower().split())
    features = list(compact)
    features.extend(
        compact[index : index + 2] for index in range(max(0, len(compact) - 1))
    )
    values = [0.0] * _LEXICAL_DIMENSIONS
    for feature in features:
        digest = hashlib.sha256(feature.encode()).digest()
        values[int.from_bytes(digest[:2], "big") % _LEXICAL_DIMENSIONS] += 1.0
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        raise ValueError("marketing evidence text produced an empty vector")
    return tuple(value / norm for value in values)


def _validated_vector(value: Sequence[float] | None) -> tuple[float, ...]:
    if value is None or not value:
        raise ValueError("marketing evidence vector is missing")
    vector = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in vector):
        raise ValueError("marketing evidence vector is not finite")
    if math.sqrt(sum(item * item for item in vector)) == 0:
        raise ValueError("marketing evidence vector is zero")
    return vector


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("marketing evidence vector dimensions differ")
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def _failed_verification(reason: str) -> MarketingGroundednessVerification:
    return MarketingGroundednessVerification(
        state="failed",
        reason_codes=(reason,),
        cluster_ids=(),
        supporting_atom_ids=(),
        counter_atom_ids=(),
        counter_note_count=0,
        counter_author_count=0,
    )
