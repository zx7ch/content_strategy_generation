"""Competitor-discovery's explicit-name, sample-only candidate boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.content_research.admission.candidates import (
    ExtractedFact,
    build_claim_candidate,
    extract_facts,
)
from app.content_research.admission.quote_fields import quote_fields_for_direction
from app.content_research.admission.strategy import AdmissionStrategy
from app.content_research.persistence_models import (
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
)

COMPETITOR_DISCOVERY_CLAIM_INTENTS = {
    "named_competitor": "competitor_identification",
    "visible_content_expression": "brand_expression",
}

_ALLOWED_FIELDS = quote_fields_for_direction("competitor_discovery")
_PROHIBITED_TERMS = ("官方账号", "官方身份", "市场领导", "市占", "最受欢迎", "竞争表现")


def _candidate_context(packet: DirectionalEvidencePacketRecord) -> dict[str, Any] | None:
    projection = dict(packet.payload.get("field_projection") or {})
    names = projection.get("competitor_names")
    metrics = projection.get("metrics")
    observed_at = projection.get("metrics_observed_at")
    author = projection.get("author")
    body = projection.get("content_text")
    if (
        not isinstance(names, list)
        or not all(isinstance(name, str) and name.strip() for name in names)
        or not isinstance(metrics, Mapping)
        or not metrics
        or not isinstance(observed_at, str)
        or not observed_at
        or not isinstance(author, str)
        or not author
        or not isinstance(body, str)
        or not body
    ):
        return None
    return {
        "competitor_names": tuple(dict.fromkeys(name.strip() for name in names)),
        "author": author,
        "canonical_source_id": packet.canonical_source_id,
        "engagement_context": {"metrics": dict(metrics), "metrics_observed_at": observed_at},
    }


def build_competitor_discovery_candidate(
    *,
    workflow_run_id: str,
    direction_id: str,
    claim_type: str,
    competitor_name: str,
    fact: ExtractedFact,
    context: Mapping[str, Any],
) -> ClaimCandidateRecord:
    """Build an auditable candidate when its explicit name occurs in the quote."""
    if direction_id != "competitor_discovery":
        raise ValueError("competitor factory requires competitor_discovery direction")
    if claim_type not in COMPETITOR_DISCOVERY_CLAIM_INTENTS:
        raise ValueError("competitor claim type is not allowed")
    if fact.field_path not in _ALLOWED_FIELDS:
        raise ValueError("competitor claim must cite title, content text, or tags")
    if competitor_name not in context.get("competitor_names", ()) or competitor_name not in fact.text:
        raise ValueError("competitor name must occur in its source quote")
    if any(term in fact.text for term in _PROHIBITED_TERMS):
        raise ValueError("competitor observation cannot claim identity, market status, or performance")
    return build_claim_candidate(
        workflow_run_id=workflow_run_id,
        direction_id=direction_id,
        intent_id=COMPETITOR_DISCOVERY_CLAIM_INTENTS[claim_type],
        claim_type=claim_type,
        statement=fact.text,
        scope={"sample": "selected_packets", "competitor_name": competitor_name, **dict(context)},
        fact=fact,
        quote=fact.text,
        text_start=0,
        text_end=len(fact.text),
    )


def build_competitor_discovery_candidates(
    packet: DirectionalEvidencePacketRecord,
) -> list[ClaimCandidateRecord]:
    """Emit direct brand-name/expression observations from a qualified note."""
    context = _candidate_context(packet)
    if context is None:
        return []
    candidates: list[ClaimCandidateRecord] = []
    for fact in extract_facts(packet):
        if fact.field_path not in _ALLOWED_FIELDS:
            continue
        for name in context["competitor_names"]:
            if name not in fact.text:
                continue
            for claim_type in COMPETITOR_DISCOVERY_CLAIM_INTENTS:
                try:
                    candidates.append(
                        build_competitor_discovery_candidate(
                            workflow_run_id=packet.workflow_run_id,
                            direction_id=packet.research_direction_id,
                            claim_type=claim_type,
                            competitor_name=name,
                            fact=fact,
                            context=context,
                        )
                    )
                except ValueError:
                    continue
    return candidates


def competitor_discovery_boundary_reason(candidate: ClaimCandidateRecord) -> str | None:
    """Return a stable reason when a candidate reaches beyond sample evidence."""
    if candidate.claim_type not in COMPETITOR_DISCOVERY_CLAIM_INTENTS:
        return "competitor_discovery_evidence_boundary_violation"
    if any(term in candidate.statement for term in _PROHIBITED_TERMS):
        return "competitor_discovery_evidence_boundary_violation"
    refs = list(candidate.payload.get("quote_refs") or [])
    scope = dict(candidate.payload.get("scope") or {})
    name = scope.get("competitor_name")
    context = scope.get("engagement_context")
    if (
        len(refs) != 1
        or str(refs[0].get("field_path") or "") not in _ALLOWED_FIELDS
        or not isinstance(name, str)
        or name not in candidate.statement
        or not scope.get("author")
        or not scope.get("canonical_source_id")
        or not isinstance(context, Mapping)
        or not context.get("metrics")
        or not context.get("metrics_observed_at")
    ):
        return "competitor_discovery_evidence_boundary_violation"
    return None


class CompetitorDiscoveryAdmissionStrategy(AdmissionStrategy):
    def __init__(self) -> None:
        super().__init__("competitor_discovery")

    def build_candidates(self, packet: DirectionalEvidencePacketRecord) -> list[ClaimCandidateRecord]:
        return build_competitor_discovery_candidates(packet)

    def boundary_reason(self, candidate: ClaimCandidateRecord) -> str | None:
        return competitor_discovery_boundary_reason(candidate)


STRATEGY = CompetitorDiscoveryAdmissionStrategy()
