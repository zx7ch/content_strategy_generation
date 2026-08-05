"""Bounded LLM proposals over already-admitted product-marketing claims."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

from app.content_research.admission.quote_fields import quote_fields_for_claim
from app.content_research.analysis import DirectionalAnalysisLLM
from app.content_research.llm_scope import content_research_llm_context
from app.content_research.marketing_conclusions import (
    MARKETING_CONCLUSION_TRACKS,
    _policy_value,
)
from app.content_research.persistence_models import (
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
    MarketingConclusionCandidateRecord,
)
from app.content_research.runtime import canonical_fingerprint
from app.services.llm.types import LLMCallContext, LLMRequest, Message


class MarketingConclusionAnalysisError(ValueError):
    """The bounded model response cannot be admitted as a proposal catalog."""


class MarketingConclusionAnalysisService:
    """Ask the configured expert model for proposals without exposing source identity."""

    def __init__(
        self,
        *,
        llm: DirectionalAnalysisLLM,
        llm_scope: Mapping[str, object] | None = None,
    ) -> None:
        self._llm = llm
        self._llm_scope = llm_scope

    async def generate(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str,
        policy: Mapping[str, object],
        admitted_claims: Iterable[
            tuple[ClaimAdmissionDecisionRecord, ClaimCandidateRecord]
        ],
    ) -> tuple[MarketingConclusionCandidateRecord, ...]:
        conclusion_policy = _policy_value(policy)

        safe_claims: list[dict[str, str]] = []
        known_claim_ids: set[str] = set()
        for decision, claim in admitted_claims:
            self._validate_admitted_claim(
                decision=decision,
                claim=claim,
                workflow_run_id=workflow_run_id,
            )
            quote_ref = claim.payload["quote_refs"][0]
            claim_id = claim.id
            if claim_id in known_claim_ids:
                raise ValueError("duplicate admitted claim")
            known_claim_ids.add(claim_id)
            safe_claims.append(
                {
                    "claim_id": claim_id,
                    "quote": str(quote_ref["quote"]),
                    "field_path": str(quote_ref["field_path"]),
                }
            )
        safe_claims.sort(key=lambda item: item["claim_id"])

        request = LLMRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You are a bounded product-marketing analyst. Return JSON only with exactly "
                        "candidates, an array of objects containing exactly track, statement, and "
                        "supporting_claim_ids. Use only the supplied claims; never infer new evidence."
                    ),
                ),
                Message(
                    role="user",
                    content=json.dumps(
                        {
                            "primary_marketing_goal": conclusion_policy[
                                "primary_marketing_goal"
                            ],
                            "tracks": list(conclusion_policy["tracks"]),
                            "claims": safe_claims,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            ],
            task_type="content_research.marketing_conclusion_analysis",
            model_policy="quality",
            temperature=0,
            max_tokens=900,
            response_format={"type": "json_object"},
            context=(
                content_research_llm_context(
                    self._llm_scope,
                    session_id=workflow_run_id,
                    workflow_run_id=workflow_run_id,
                    step_name="marketing_conclusion",
                    agent_name="marketing_conclusion_analyst",
                )
                if self._llm_scope is not None
                else LLMCallContext(
                    session_id=workflow_run_id,
                    job_id=workflow_run_id,
                    step_name="marketing_conclusion",
                    agent_name="marketing_conclusion_analyst",
                )
            ),
        )
        response = await self._llm.generate(request)
        try:
            payload = json.loads(response.content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise MarketingConclusionAnalysisError(
                "marketing conclusion response must be valid JSON"
            ) from exc
        return self._parse_candidates(
            payload,
            workflow_run_id=workflow_run_id,
            research_plan_id=research_plan_id,
            known_claim_ids=known_claim_ids,
        )

    @staticmethod
    def _validate_admitted_claim(
        *,
        decision: ClaimAdmissionDecisionRecord,
        claim: ClaimCandidateRecord,
        workflow_run_id: str,
    ) -> None:
        if (
            not isinstance(decision, ClaimAdmissionDecisionRecord)
            or not isinstance(claim, ClaimCandidateRecord)
            or decision.decision != "admitted"
            or decision.claim_candidate_id != claim.id
            or decision.research_direction_id != "product_marketing"
            or claim.research_direction_id != "product_marketing"
            or claim.workflow_run_id != workflow_run_id
            or decision.payload.get("reason_codes") not in ([], ())
            or not str(decision.payload.get("policy_snapshot_hash") or "")
        ):
            raise ValueError("marketing conclusion analysis requires admitted product-marketing claims")
        quote_refs = claim.payload.get("quote_refs")
        field_path = (
            str(quote_refs[0].get("field_path") or "")
            if isinstance(quote_refs, list)
            and len(quote_refs) == 1
            and isinstance(quote_refs[0], dict)
            else ""
        )
        if (
            not isinstance(quote_refs, list)
            or len(quote_refs) != 1
            or not isinstance(quote_refs[0], dict)
            or not str(quote_refs[0].get("quote") or "").strip()
            or field_path
            not in quote_fields_for_claim("product_marketing", claim.claim_type)
        ):
            raise ValueError("admitted claim requires one safe quote reference")

    @staticmethod
    def _parse_candidates(
        payload: object,
        *,
        workflow_run_id: str,
        research_plan_id: str,
        known_claim_ids: set[str],
    ) -> tuple[MarketingConclusionCandidateRecord, ...]:
        if not isinstance(payload, dict) or set(payload) != {"candidates"}:
            raise MarketingConclusionAnalysisError(
                "marketing conclusion response has invalid shape"
            )
        raw_candidates = payload["candidates"]
        if not isinstance(raw_candidates, list):
            raise MarketingConclusionAnalysisError(
                "marketing conclusion candidates must be an array"
            )
        records: list[MarketingConclusionCandidateRecord] = []
        for raw in raw_candidates:
            if not isinstance(raw, dict) or set(raw) != {
                "track",
                "statement",
                "supporting_claim_ids",
            }:
                raise MarketingConclusionAnalysisError(
                    "marketing conclusion candidate has invalid shape"
                )
            track = raw["track"]
            statement = raw["statement"]
            supporting_claim_ids = raw["supporting_claim_ids"]
            if not isinstance(track, str) or track not in MARKETING_CONCLUSION_TRACKS:
                raise MarketingConclusionAnalysisError(
                    "marketing conclusion candidate has unknown track"
                )
            if not isinstance(statement, str) or not statement.strip():
                raise MarketingConclusionAnalysisError(
                    "marketing conclusion candidate has empty statement"
                )
            if (
                not isinstance(supporting_claim_ids, list)
                or not supporting_claim_ids
                or any(not isinstance(item, str) or not item for item in supporting_claim_ids)
            ):
                raise MarketingConclusionAnalysisError(
                    "marketing conclusion candidate has invalid supporting claims"
                )
            if len(set(supporting_claim_ids)) != len(supporting_claim_ids):
                raise MarketingConclusionAnalysisError(
                    "marketing conclusion candidate has duplicate supporting claim IDs"
                )
            if not set(supporting_claim_ids).issubset(known_claim_ids):
                raise MarketingConclusionAnalysisError(
                    "marketing conclusion candidate references an unknown claim"
                )
            normalized_statement = statement.strip()
            normalized_claim_ids = sorted(supporting_claim_ids)
            identity = canonical_fingerprint(
                {
                    "workflow_run_id": workflow_run_id,
                    "research_plan_id": research_plan_id,
                    "track": track,
                    "statement": normalized_statement,
                    "supporting_claim_ids": normalized_claim_ids,
                }
            )
            records.append(
                MarketingConclusionCandidateRecord(
                    f"mc_{identity[:24]}",
                    "marketing_conclusion_candidate_v1",
                    {
                        "statement": normalized_statement,
                        "supporting_claim_ids": normalized_claim_ids,
                    },
                    workflow_run_id=workflow_run_id,
                    research_plan_id=research_plan_id,
                    track=track,
                )
            )
        return tuple(records)
