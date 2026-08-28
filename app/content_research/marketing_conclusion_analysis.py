"""Bounded LLM proposals over already-admitted product-marketing claims."""

from __future__ import annotations

import asyncio
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
from app.services.llm.failures import LLMProviderFailure
from app.services.llm.types import LLMCallContext, LLMRequest, Message


class MarketingConclusionAnalysisError(ValueError):
    """The bounded model response cannot be admitted as a proposal catalog."""

    def __init__(self, message: str, *, detail_code: str) -> None:
        super().__init__(message)
        self.detail_code = detail_code


_MESSAGE_ANGLE_PERFORMANCE_TERMS = ("偏好", "转化", "购买", "因果", "效果提升", "表现更好")
_DEFAULT_MODEL_TIMEOUT_SECONDS = 90.0
_MAX_CANDIDATES_PER_TRACK = 5
_TRACK_CLAIM_TYPES = {
    "need": frozenset({"use_context", "target_audience_framing"}),
    "value": frozenset({"product_value_expression"}),
    "message": frozenset({"message_angle"}),
}
MARKETING_CONCLUSION_SYSTEM_PROMPT = (
    "You are a bounded product-marketing analyst. Return JSON only with exactly "
    "candidates, an array of objects containing exactly track, statement, and "
    "supporting_claim_ids. Return at most 5 candidates for each requested track; "
    "merge compatible claims instead of emitting one candidate per claim. Use only "
    "the supplied support-polarity claims whose eligible_tracks include the output "
    "track; counter claims are limitations, never supporting evidence. Never infer "
    "new evidence."
)


class MarketingConclusionAnalysisService:
    """Ask the configured expert model for proposals without exposing source identity."""

    def __init__(
        self,
        *,
        llm: DirectionalAnalysisLLM,
        llm_scope: Mapping[str, object] | None = None,
        timeout_seconds: float = _DEFAULT_MODEL_TIMEOUT_SECONDS,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("marketing conclusion model timeout must be positive")
        self._llm = llm
        self._llm_scope = llm_scope
        self._timeout_seconds = timeout_seconds

    async def generate(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str,
        policy: Mapping[str, object],
        admitted_claims: Iterable[
            tuple[ClaimAdmissionDecisionRecord, ClaimCandidateRecord]
        ],
        track: str | None = None,
    ) -> tuple[MarketingConclusionCandidateRecord, ...]:
        conclusion_policy = _policy_value(policy)
        if track is not None and track not in MARKETING_CONCLUSION_TRACKS:
            raise ValueError("marketing conclusion track is invalid")

        safe_claims: list[dict[str, object]] = []
        known_claim_ids: set[str] = set()
        claim_types_by_id: dict[str, str] = {}
        claim_polarities_by_id: dict[str, str] = {}
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
            claim_types_by_id[claim_id] = claim.claim_type
            scope = claim.payload.get("scope")
            scope = scope if isinstance(scope, dict) else {}
            qualifiers = scope.get("qualifiers")
            qualifiers = qualifiers if isinstance(qualifiers, dict) else {}
            polarity = scope.get("polarity")
            safe_polarity = (
                polarity if polarity in {"support", "counter"} else "support"
            )
            claim_polarities_by_id[claim_id] = safe_polarity
            eligible_tracks = [
                item
                for item in MARKETING_CONCLUSION_TRACKS
                if claim.claim_type in _TRACK_CLAIM_TYPES[item]
            ]
            if track is not None and track not in eligible_tracks:
                continue
            safe_claims.append(
                {
                    "claim_id": claim_id,
                    "claim_type": claim.claim_type,
                    "intent_id": claim.intent_id,
                    "quote": str(quote_ref["quote"]),
                    "field_path": str(quote_ref["field_path"]),
                    "eligible_tracks": eligible_tracks,
                    "polarity": safe_polarity,
                    "qualifiers": {
                        "scenes": _safe_qualifier_values(qualifiers.get("scenes")),
                        "audiences": _safe_qualifier_values(
                            qualifiers.get("audiences")
                        ),
                    },
                }
            )
        safe_claims.sort(key=lambda item: item["claim_id"])

        request = LLMRequest(
            messages=[
                Message(
                    role="system",
                    content=MARKETING_CONCLUSION_SYSTEM_PROMPT,
                ),
                Message(
                    role="user",
                    content=json.dumps(
                        {
                            "primary_marketing_goal": conclusion_policy[
                                "primary_marketing_goal"
                            ],
                            "tracks": [track] if track is not None else list(conclusion_policy["tracks"]),
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
            max_tokens=3200,
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
        try:
            response = await asyncio.wait_for(
                self._llm.generate(request), timeout=self._timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            raise LLMProviderFailure(
                "llm_service_unavailable",
                "模型服务响应超时",
                True,
                None,
            ) from exc
        if response.finish_reason == "length":
            raise MarketingConclusionAnalysisError(
                "marketing conclusion response was truncated by the model output limit",
                detail_code="response_truncated",
            )
        try:
            payload = json.loads(response.content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise MarketingConclusionAnalysisError(
                "marketing conclusion response must be valid JSON",
                detail_code="invalid_json",
            ) from exc
        candidates = self._parse_candidates(
            payload,
            workflow_run_id=workflow_run_id,
            research_plan_id=research_plan_id,
            known_claim_ids=known_claim_ids,
            claim_types_by_id=claim_types_by_id,
            claim_polarities_by_id=claim_polarities_by_id,
            requested_track=track,
        )
        if track is not None and any(candidate.track != track for candidate in candidates):
            raise MarketingConclusionAnalysisError(
                "marketing conclusion response crossed the requested track boundary",
                detail_code="unexpected_track",
            )
        return candidates

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
        claim_types_by_id: Mapping[str, str],
        claim_polarities_by_id: Mapping[str, str],
        requested_track: str | None = None,
    ) -> tuple[MarketingConclusionCandidateRecord, ...]:
        if not isinstance(payload, dict) or set(payload) != {"candidates"}:
            raise MarketingConclusionAnalysisError(
                "marketing conclusion response has invalid shape",
                detail_code="invalid_top_level_shape",
            )
        raw_candidates = payload["candidates"]
        if not isinstance(raw_candidates, list):
            raise MarketingConclusionAnalysisError(
                "marketing conclusion candidates must be an array",
                detail_code="invalid_candidates_shape",
            )
        records: list[MarketingConclusionCandidateRecord] = []
        candidate_counts: dict[str, int] = {}
        for raw in raw_candidates:
            if not isinstance(raw, dict) or set(raw) != {
                "track",
                "statement",
                "supporting_claim_ids",
            }:
                raise MarketingConclusionAnalysisError(
                    "marketing conclusion candidate has invalid shape",
                    detail_code="invalid_candidate_shape",
                )
            track = raw["track"]
            statement = raw["statement"]
            supporting_claim_ids = raw["supporting_claim_ids"]
            if not isinstance(track, str) or track not in MARKETING_CONCLUSION_TRACKS:
                raise MarketingConclusionAnalysisError(
                    "marketing conclusion candidate has unknown track",
                    detail_code="unknown_track",
                )
            candidate_counts[track] = candidate_counts.get(track, 0) + 1
            if candidate_counts[track] > _MAX_CANDIDATES_PER_TRACK:
                raise MarketingConclusionAnalysisError(
                    "marketing conclusion response has too many candidates for one track",
                    detail_code="too_many_candidates",
                )
            if requested_track is not None and track != requested_track:
                raise MarketingConclusionAnalysisError(
                    "marketing conclusion response crossed the requested track boundary",
                    detail_code="unexpected_track",
                )
            if not isinstance(statement, str) or not statement.strip():
                raise MarketingConclusionAnalysisError(
                    "marketing conclusion candidate has empty statement",
                    detail_code="empty_statement",
                )
            if (
                not isinstance(supporting_claim_ids, list)
                or not supporting_claim_ids
                or any(not isinstance(item, str) or not item for item in supporting_claim_ids)
            ):
                raise MarketingConclusionAnalysisError(
                    "marketing conclusion candidate has invalid supporting claims",
                    detail_code="invalid_supporting_claim_ids",
                )
            if len(set(supporting_claim_ids)) != len(supporting_claim_ids):
                raise MarketingConclusionAnalysisError(
                    "marketing conclusion candidate has duplicate supporting claim IDs",
                    detail_code="duplicate_supporting_claim_ids",
                )
            if not set(supporting_claim_ids).issubset(known_claim_ids):
                raise MarketingConclusionAnalysisError(
                    "marketing conclusion candidate references an unknown claim",
                    detail_code="unknown_supporting_claim_id",
                )
            if any(
                claim_types_by_id[claim_id] not in _TRACK_CLAIM_TYPES[track]
                for claim_id in supporting_claim_ids
            ):
                raise MarketingConclusionAnalysisError(
                    "marketing conclusion candidate uses evidence from another track",
                    detail_code="supporting_claim_track_mismatch",
                )
            if any(
                claim_polarities_by_id[claim_id] != "support"
                for claim_id in supporting_claim_ids
            ):
                raise MarketingConclusionAnalysisError(
                    "marketing conclusion candidate used counter evidence as support",
                    detail_code="supporting_claim_polarity_invalid",
                )
            if (
                track == "message"
                and any(claim_types_by_id[claim_id] == "message_angle" for claim_id in supporting_claim_ids)
                and any(term in statement for term in _MESSAGE_ANGLE_PERFORMANCE_TERMS)
            ):
                raise MarketingConclusionAnalysisError(
                    "message-angle candidate cannot state product-performance outcome",
                    detail_code="message_angle_performance_statement",
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


def _safe_qualifier_values(value: object) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return sorted(
        {
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip() and len(item) <= 32
        }
    )
