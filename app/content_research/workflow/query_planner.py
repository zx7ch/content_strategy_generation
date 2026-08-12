"""Deterministic Lite query compilation from a confirmed subject structure."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime

from app.content_research.runtime import canonical_fingerprint
from app.content_research.subject_structure import SubjectStructure
from app.content_research.workflow.directional_pipeline import QueryGroup

QUERY_COMPILER_VERSION = "content_research_query_compiler_v2"
PRIMARY_QUERY_GROUP_CAP = 2
COVERAGE_FALLBACK_QUERY_GROUP_CAP = 1
QUERY_GROUP_CANDIDATE_CAP = 20
PRODUCT_MARKETING_GOAL_FACETS = {"content_seeding": "上身感受"}


@dataclass(frozen=True)
class PlannedQueryGroup:
    roles: tuple[str, ...]
    activation: str
    normalized_identity: str
    query_group: QueryGroup

    @property
    def role(self) -> str:
        return self.roles[0]


@dataclass(frozen=True)
class CompiledQueryPlan:
    primary_groups: tuple[PlannedQueryGroup, ...]
    fallback_group: PlannedQueryGroup | None
    plan_hash: str
    compiler_version: str = QUERY_COMPILER_VERSION


def compile_structured_query_plan(
    *,
    direction_id: str,
    subject_structure: SubjectStructure,
    explicit_focus: str = "",
    second_facet: str = "",
    primary_marketing_goal: str = "",
    run_as_of_at: datetime,
    provider: str = "xiaohongshu",
    sort: str = "likes",
) -> CompiledQueryPlan:
    if len(subject_structure.core_entities) != 1:
        raise ValueError("Lite query compilation requires one core entity")
    if run_as_of_at.tzinfo is None or run_as_of_at.utcoffset() is None:
        raise ValueError("run_as_of_at must be timezone-aware")

    core = _display_term(subject_structure.core_entities[0].canonical_name)
    primary_intent = _first_term(subject_structure.research_intents)
    if not core or not primary_intent:
        raise ValueError("structured query requires a core entity and primary intent")

    planned: list[PlannedQueryGroup] = []
    _append_or_merge(
        planned,
        _planned_group(
            direction_id=direction_id,
            role="core_intent",
            activation="primary",
            terms=(core, primary_intent),
            priority=0,
            provider=provider,
            sort=sort,
            run_as_of_at=run_as_of_at,
        ),
    )

    if direction_id == "product_marketing":
        focus = resolve_product_marketing_facet(
            primary_marketing_goal=primary_marketing_goal,
            custom_focus=explicit_focus,
        )
        role = "user_focus" if _display_term(explicit_focus) else "goal_facet"
        terms = (
            (core, primary_intent)
            if _identity_text(focus) == _identity_text(primary_intent)
            else (core, primary_intent, focus)
        )
        _append_or_merge(
            planned,
            _planned_group(
                direction_id=direction_id,
                role=role,
                activation="primary",
                terms=terms,
                priority=1,
                provider=provider,
                sort=sort,
                run_as_of_at=run_as_of_at,
            ),
        )
    else:
        focus = _display_term(explicit_focus)
        role = "user_focus"
        if not focus:
            focus = _display_term(second_facet)
            role = "direction_facet"
        if focus:
            _append_or_merge(
                planned,
                _planned_group(
                    direction_id=direction_id,
                    role=role,
                    activation="primary",
                    terms=(core, focus),
                    priority=1,
                    provider=provider,
                    sort=sort,
                    run_as_of_at=run_as_of_at,
                ),
            )
    primary_groups = tuple(planned[:PRIMARY_QUERY_GROUP_CAP])

    synonym_groups = dict(subject_structure.synonym_groups)
    aliases = tuple(
        _display_term(item)
        for item in synonym_groups.get(
            subject_structure.core_entities[0].canonical_name,
            (),
        )
        if _display_term(item)
    )
    fallback_group: PlannedQueryGroup | None = None
    if aliases and COVERAGE_FALLBACK_QUERY_GROUP_CAP:
        fallback_focus = _first_term(subject_structure.context_modifiers)
        if not fallback_focus:
            fallback_focus = (
                primary_intent
                if direction_id == "product_marketing"
                else focus or primary_intent
            )
        fallback_group = _planned_group(
            direction_id=direction_id,
            role="coverage_fallback",
            activation="coverage_fallback",
            terms=(aliases[0], fallback_focus),
            priority=PRIMARY_QUERY_GROUP_CAP,
            provider=provider,
            sort=sort,
            run_as_of_at=run_as_of_at,
        )

    all_groups = [*primary_groups]
    if fallback_group is not None:
        all_groups.append(fallback_group)
    plan_payload = {"query_groups": [_planned_payload(item)["query_group"] for item in all_groups]}
    return CompiledQueryPlan(
        primary_groups=primary_groups,
        fallback_group=fallback_group,
        plan_hash=canonical_fingerprint(plan_payload),
    )


def resolve_product_marketing_facet(
    *,
    primary_marketing_goal: str,
    custom_focus: str,
) -> str:
    focus = _display_term(custom_focus)
    if focus:
        return focus
    try:
        return PRODUCT_MARKETING_GOAL_FACETS[primary_marketing_goal]
    except KeyError as exc:
        raise ValueError("unknown product-marketing goal") from exc


def _planned_group(
    *,
    direction_id: str,
    role: str,
    activation: str,
    terms: tuple[str, ...],
    priority: int,
    provider: str,
    sort: str,
    run_as_of_at: datetime,
) -> PlannedQueryGroup:
    query = " ".join(item for item in (_display_term(term) for term in terms) if item)
    identity_payload = {
        "provider": provider.strip().casefold(),
        "query": _identity_text(query),
        "sort": sort.strip().casefold(),
        "time_window": {"end_at": run_as_of_at.isoformat()},
        "candidate_cap": QUERY_GROUP_CANDIDATE_CAP,
    }
    normalized_identity = canonical_fingerprint(identity_payload)
    group_id = f"qg_{canonical_fingerprint({'direction': direction_id, **identity_payload})[:16]}"
    return PlannedQueryGroup(
        roles=(role,),
        activation=activation,
        normalized_identity=normalized_identity,
        query_group=QueryGroup(
            id=group_id,
            direction_id=direction_id,
            query=query,
            priority=priority,
            sort=sort,
            candidate_limit=QUERY_GROUP_CANDIDATE_CAP,
            time_window={"end_at": run_as_of_at.isoformat()},
            roles=(role,),
            activation=activation,
            normalized_identity=normalized_identity,
        ),
    )


def _append_or_merge(
    values: list[PlannedQueryGroup],
    incoming: PlannedQueryGroup,
) -> None:
    for index, current in enumerate(values):
        if current.normalized_identity != incoming.normalized_identity:
            continue
        values[index] = replace(
            current,
            roles=tuple(dict.fromkeys((*current.roles, *incoming.roles))),
            query_group=replace(
                current.query_group,
                roles=tuple(
                    dict.fromkeys((*current.query_group.roles, *incoming.query_group.roles))
                ),
            ),
        )
        return
    values.append(incoming)


def _planned_payload(value: PlannedQueryGroup) -> dict[str, object]:
    group = value.query_group
    return {
        "roles": list(value.roles),
        "activation": value.activation,
        "normalized_identity": value.normalized_identity,
        "query_group": {
            "id": group.id,
            "direction_id": group.direction_id,
            "normalized_query": group.query,
            "priority": group.priority,
            "sort": group.sort,
            "time_window": dict(group.time_window or {}),
            "candidate_cap": group.candidate_limit,
            "roles": list(value.roles),
            "activation": value.activation,
            "normalized_identity": value.normalized_identity,
        },
    }


def _first_term(values: tuple[str, ...]) -> str:
    for value in values:
        normalized = _display_term(value)
        if normalized:
            return normalized
    return ""


def _display_term(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value))
    without_punctuation = "".join(
        " " if unicodedata.category(char).startswith("P") else char for char in normalized
    )
    return " ".join(without_punctuation.split())


def _identity_text(value: str) -> str:
    return _display_term(value).casefold()
