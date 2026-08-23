# Content Research Lite: Scope Contract Design

## Status

Approved design. This document defines the Lite implementation boundary for
user-confirmed retrieval scope, coverage decisions, and report limitations.

## Problem

The existing system correctly stores a structured interpretation of the user
input, but compiles product-marketing queries from only a core entity and first
intent. Context such as season can therefore disappear from all executed
queries. Later, a different literal relevance gate can reject all collected
evidence. The user sees only a late `insufficient_evidence` result.

The system must preserve the scope the user authorizes across retrieval,
selection, evidence admission, coverage assessment, and report presentation.

## Product Boundary

Lite supports:

- text constraints only;
- one required core object and up to two optional query aspects for product
  marketing;
- one to three user-editable query groups per direction;
- constraint modes `required` and `preferred`;
- three scope-insufficiency actions: expand, limited report, relax;
- a separate candidate-and-scope audit projection for developers and release
  acceptance; existing Trace UI remains unchanged.

Lite does not implement automatic unconstrained query expansion, semantic
ranking infrastructure, a historical run browser, or arbitrary workflow
editing.

## Interaction Reference

The approved interactive reference is kept with the project release design:
[`2026-08-16-lite-research-scope-contract-prototype.html`](../../release/2026-08-16-lite-research-scope-contract-prototype.html).

It contains the two user decision points:

1. Before collection: review the exact proposed search strings and directly
   edit every proposed query group. Missing optional product/experience or
   context/audience aspects are explained and may be supplied by the user, but
   do not block confirmation.
2. Before composing a conclusion: decide to expand required coverage, compose
   a limited report, or relax a condition with an explicit record.

The production UI should follow the Creator report/conversation visual theme,
not the Trace card styling.

The authority-object lifecycle reference is
[`2026-08-16-content-research-authority-lifecycle.html`](../../release/2026-08-16-content-research-authority-lifecycle.html). It describes the real Content Research stages, shared-use boundaries, concurrency protections, and the reason for every key rule.

## Domain Boundaries

### Subject structure: interpretation only

`SubjectStructure` remains a compact, immutable parsing snapshot of raw user
input. It supplies suggested values but never authorizes execution.

```text
canonical_subject, core_entities, research_intents, context_modifiers,
synonym_groups, ambiguities, resolution_state
```

It must not contain final query text, constraint modes, thresholds, user edits,
or coverage outcomes.

For product marketing, Scope preparation maps the first usable core entity to
core search object A, a concrete product/experience phrase to optional query
aspect B, and a concrete context/audience/occasion phrase to optional query
aspect C. These are query-composition roles, not three required evidence
conditions. Abstract analysis goals such as `上身感受` are not executable query
aspects and are not shown as if they will be sent to the source search box.

The deterministic suggested portfolio is `A`, `A B`, `A C`. If B or C is not
available, the corresponding group is omitted and Creator explains the missing
aspect and invites the user to add it. The user may still confirm the remaining
one or two non-empty groups.

Creator labels B as “产品／体验检索词” and explains that it is a concrete phrase
someone might search with the object, such as `凉感` or `显瘦`. It labels C as
“场景／人群检索词” and explains that it is a concrete occasion, audience, or
usage phrase, such as `夏季通勤`. Neither field is described as a research goal
or analysis question.

When either aspect is missing, Creator renders its optional inline input inside
the existing Scope card. Pressing Enter reissues `prepare_scope` with that
aspect value and replaces the persisted Draft; it does not introduce another
stage or action button. The existing confirm action remains available for the
current one or two non-empty groups even when the optional input is untouched.

### Scope contract: execution authority

`ResearchScopeContract` is the immutable versioned object frozen immediately
before source collection. It is derived from a subject structure proposal and
the user's confirmation. All later stages use it as their source of truth.

```json
{
  "schema_version": "content_research_scope_contract_v2",
  "workflow_run_id": "run_…",
  "research_plan_id": "rp_…",
  "version": 1,
  "core_object": {"value": "长袖衬衫", "mode": "required", "aliases": ["衬衫"]},
  "constraints": [],
  "query_groups": [
    {
      "id": "qg_broad",
      "suggested_query": "长袖衬衫",
      "final_query": "长袖衬衫",
      "origin": "system_suggested",
      "execution_role": "coverage"
    },
    {
      "id": "qg_product_experience",
      "suggested_query": "长袖衬衫 凉感",
      "final_query": "长袖衬衫 凉感",
      "origin": "system_suggested",
      "execution_role": "coverage"
    },
    {
      "id": "qg_context_audience",
      "suggested_query": "长袖衬衫 夏季通勤",
      "final_query": "长袖衬衫 夏季通勤",
      "origin": "system_suggested",
      "execution_role": "coverage"
    }
  ],
  "coverage_policy": {"minimum_samples": 3, "minimum_independent_authors": 2},
  "created_by": "user_confirmation"
}
```

Contract versions are append-only. An expand or relax decision creates a new
version and preserves every earlier version and its outcome.

### Candidate constraint match: evidence-level facts

Each detailed candidate carries a `constraint_matches` projection keyed by
scope contract version. It records matched text/tag/metadata, unmatched
required conditions, and query provenance. It does not mutate the contract.

```json
{
  "scope_contract_version": 1,
  "query_group_hits": ["qg_…"],
  "constraint_matches": {
    "core_object": {"status": "matched", "evidence": ["衬衫"]}
  },
  "eligibility": "eligible",
  "exclusion_reasons": []
}
```

### Coverage snapshot: decision-ready aggregate

`CoverageSnapshot` summarizes the current contract version by constraint,
query group, candidate, and independent author. Its result is either
`satisfied` or `awaiting_scope_decision` with precise reason codes.

## Query Editing Policy

Users may enter any non-empty query text. The server must not reject a query
because it omits a required constraint. Instead it computes a pre-execution
coverage classification:

- `coverage`: query text directly contains all required conditions;
- `supplementary`: query targets a known missing required condition;
- `exploratory`: user-edited query is useful for recall but does not directly
  cover all required conditions.

An exploratory source can still contribute to the report only if its detailed
candidate independently matches every required constraint. Query provenance is
never itself evidence that a condition is true.

For product marketing, only A is a required candidate condition. B and C are
optional query aspects: their presence can be audited, but their absence never
excludes a candidate and never by itself triggers `awaiting_scope_decision`.
Suggested groups contain A, but a user may replace a group with any non-empty
text. A final query that omits A is classified `exploratory`; evidence returned
through that group is admissible only when the detailed candidate independently
matches A.

Source tracking remains query-group scoped. `suggested_query` retains the
system proposal, `final_query` retains the confirmed text, and `origin` is
`system_suggested` until the user changes the group, after which it is
`user_edited`. No slot-level A/B/C provenance is required.

When a user supplies a previously missing B or C, the server composes the
corresponding `A B` or `A C` group and persists a replacement Draft before
confirmation. That group's composed text initializes both `suggested_query`
and `final_query`; `origin` is `user_edited` because the user supplied the
aspect that caused the group to exist. The replaced Draft command becomes
stale.

## Execution States

```text
draft_scope
  -> scope_confirmed
  -> collecting
  -> evaluating_coverage
  -> report_ready | awaiting_scope_decision

awaiting_scope_decision
  -> scope_confirmed (expand or relax creates next version)
  -> report_ready (limited report)
```

No collection may begin from `draft_scope`. No formal report may be generated
from `awaiting_scope_decision` without the recorded `limited_report` decision.

## Product-Marketing Query Portfolio Contract Pack

This is one observable vertical slice from Scope preparation through frozen
execution and candidate admission. UI, API, persistence, collection, audit, and
acceptance must ship together.

This is an L2 lifecycle change because old and new frozen Scope semantics
coexist. Mutation projection, stale-response protection, retry, lease,
unknown-provider-outcome, and historical recovery continue to follow
[`2026-08-22-task-5-creator-authority-contract.md`](./2026-08-22-task-5-creator-authority-contract.md).
The rows below specialize that authority for query composition and admission;
they do not create a second mutation policy.

### User state

| ID | Projection | Allowed | Forbidden / recovery |
|---|---|---|---|
| `STATE-QP-1` | Persisted Draft with one to three non-empty proposed groups | Edit any group; optionally supply missing B/C; confirm the exact projected Draft command | Collection is forbidden. Missing B/C shows an explanation and prompt but does not disable confirmation. |
| `STATE-QP-2` | Confirmed immutable Scope | Read frozen queries and their group-level provenance; begin or observe collection | Query edits require a new Draft/version; the browser cannot mutate frozen text. |

### Authority

| ID | Rule |
|---|---|
| `AUTH-QP-1` | `SubjectStructure` and the `A / A B / A C` portfolio are suggestions only. The confirmed `ResearchScopeContract.query_groups[].final_query` values are the sole retrieval authority. |
| `AUTH-QP-2` | Product marketing has exactly one required candidate condition, A. B and C are optional query aspects and do not become required constraints. |
| `AUTH-QP-3` | A Draft contains one to three query groups. B/C absence may reduce cardinality; it cannot create an empty group or block confirmation. Provenance remains query-group scoped through `suggested_query`, `final_query`, and `origin`. |
| `AUTH-QP-4` | New product-marketing runs use `content_research_scope_contract_v2`. Existing v1 contracts retain their frozen required constraints and query semantics; reads, replay, and recovery never reinterpret or upgrade them in place. |

### Transitions

| ID | From → event → to | Guard, writes, and side effect |
|---|---|---|
| `INV-QP-1` | Interpreted subject or `STATE-QP-1` → `prepare_scope` → replacement `STATE-QP-1` | Persist A and every available concrete aspect suggestion; create `A`, then available `A B` and `A C` groups in deterministic order. Pressing Enter in a missing-aspect input reissues `prepare_scope`, creates a replacement Draft, and invalidates the earlier command without adding a stage or action button. Do not persist an abstract analysis goal as a query. No provider call occurs. |
| `INV-QP-2` | `STATE-QP-1` → `confirm_scope` → `STATE-QP-2` | Require the exact projected Draft identity and one to three non-empty final queries; atomically freeze group text/provenance before dispatch authorization. Do not require every final query to contain A. |
| `INV-QP-3` | `STATE-QP-2` → collection/detail admission → coverage or report state | Send each frozen `final_query` unchanged. A candidate may be admitted only after detailed evidence matches A; the presence or absence of B/C does not filter it. |

### Failure semantics

| ID | Rule |
|---|---|
| `FAIL-QP-1` | If B or C cannot be proposed, Creator explains the missing aspect and offers an optional input. Confirm remains available while at least one non-empty group exists. |
| `FAIL-QP-2` | A user-edited query that omits A is accepted and classified `exploratory`; its candidates still require an independent A match. |
| `FAIL-QP-3` | Stale Draft identity or an empty final group is rejected before Scope or dispatch writes. |
| `FAIL-QP-4` | Missing B/C aspects cannot exclude a candidate or trigger `awaiting_scope_decision`; only required A coverage and existing sample/author thresholds can do so. |
| `FAIL-QP-5` | Historical v1 contracts remain readable and executable under their frozen semantics. No migration rewrites constraints, query groups, Coverage snapshots, reports, or recovery authority. |

### Acceptance evidence

| ID | Contracts | Observable proof | Proof layer |
|---|---|---|---|
| `ACC-QP-1` | `STATE-QP-1`, `AUTH-QP-1` through `AUTH-QP-3`, `INV-QP-1` | `长袖衬衫` + `凉感` + `夏季通勤` renders exact suggestions `长袖衬衫`, `长袖衬衫 凉感`, `长袖衬衫 夏季通勤`, with no abstract research-goal field. | Browser-to-owned-stack |
| `ACC-QP-2` | `STATE-QP-1`, `AUTH-QP-3`, `FAIL-QP-1` | Missing B and/or C renders the correct explanation, allows optional user completion, and also permits confirmation with the remaining non-empty groups. | Browser-to-owned-stack plus real Router/SQLite |
| `ACC-QP-3` | `INV-QP-2`, `INV-QP-3`, `FAIL-QP-2`, `FAIL-QP-3` | A user edits one group to a non-empty query without A; the frozen Scope preserves suggestion/final/origin, Spider receives the exact final text, and only detailed candidates matching A are admitted. Empty or stale confirmation produces zero Scope/dispatch deltas. | Real Router/SQLite with controlled provider adapter |
| `ACC-QP-4` | `AUTH-QP-2`, `FAIL-QP-4` | Candidate admission and Coverage prove that absent B/C never excludes a candidate or creates a missing-required-condition decision. | Real owned-stack integration |
| `ACC-QP-5` | `AUTH-QP-4`, `FAIL-QP-5` | A historical v1 run retains its original required constraints and recovery projection while a new v2 run uses only A as required; neither read changes persisted rows. | Real Router/SQLite integration |

**Readiness:** READY for vertical-slice planning. Product meaning, cardinality,
authority, stale Draft behavior, v1/v2 coexistence, failure semantics, and
observable acceptance are explicit; there are no unresolved query-portfolio
contract holes.

## Required APIs

The existing workflow action endpoint remains the command boundary. It gains
the actions below and returns the normalized/frozen contract in its `result`:

| Action | Input | Result |
|---|---|---|
| `prepare_scope` | direction, user-confirmed subject fields | suggestion with required A, optional B/C explanations, and one to three query groups |
| `confirm_scope` | contract draft, editable final queries, constraint modes | immutable scope-contract version; dispatch may begin |
| `resolve_coverage` | contract version, `expand_required_constraint` / `generate_limited_report` / `relax_constraint` | next contract version or authorized compose state |

Read APIs:

- `GET /content-research/workflows/{id}/scope`: latest and requested version;
- extend direction evidence response with `scope_contract`,
  `constraint_matches`, and `coverage_snapshot`;
- Lite report response exposes frozen scope and explicit conclusion boundaries.

## Evidence and Admission Rules

1. A detailed candidate is eligible only if every required constraint has a
   recorded match in allowed fields: title, body, tags, source metadata, or
   approved aliases for the core object.
2. Preferred constraints influence the audit and report scope description but
   do not exclude a candidate.
3. For product marketing, A is the only required candidate condition. B and C
   remain query-aspect observations and never exclude a candidate.
4. Claim admission consumes the candidate match projection. It must not impose
   a separate literal `core_entity + first_intent` rule that contradicts the
   scope contract.
5. Every admitted claim retains a reference to its candidate and contract
   version.

## Observability and Audit

Trace remains an execution timeline. The following new audit data is stored in
the content-research SQLite database and exposed through the evidence read
model, not rendered in Trace by default.

### Engineering rule

Scope observability is built at the same time as each state transition, never
as a post-feature logging task. The service operation that durably saves a
scope, collection checkpoint, candidate match, coverage decision, or report
projection must append the corresponding redacted audit event in the same
logical persistence boundary. A task is incomplete until its focused test
proves the audit event matches the persisted domain data exactly.

| Boundary | Persisted fields | Primary diagnostic |
|---|---|---|
| Interpretation → scope | subject structure hash, required A, optional B/C suggestions, user confirmation, query edit diff | Which concrete query aspects were available, and did the user change a proposed group? |
| Scope → Spider | contract version, final query, role, request ID, status, count, cursor, failure code | Which exact query did Spider receive and return? |
| Spider → detail | discovered, deduplicated, selected, detail success/failure, canonical source ID | Was recall or detail collection the bottleneck? |
| Detail → admission | per-constraint match status/evidence, allowed alias used, exclusion reasons | Which scope condition excluded a note? |
| Admission → report | threshold counts, author count, coverage decision, user action, report mode, limitations | Why was the report formal, limited, or withheld? |

Never persist raw Cookie values, LLM keys, full upstream request headers, or
unredacted provider error payloads in these observations.

## Acceptance Criteria

1. For core object `长袖衬衫`, product/experience aspect `凉感`, and
   context/audience aspect `夏季通勤`, the suggested groups are exactly
   `长袖衬衫`, `长袖衬衫 凉感`, and `长袖衬衫 夏季通勤` in that order.
2. If B or C is unavailable, Creator explains the corresponding optional field
   and invites user input without disabling confirmation of the remaining
   one or two non-empty groups.
3. A user can replace any query with arbitrary non-empty text; the saved group
   is marked `user_edited` and its suggestion is retained.
4. A user-edited query missing A is classified `exploratory`, not rejected.
5. A candidate returned by any query group is eligible only after its detailed
   evidence matches required A. Missing B or C never excludes it.
6. Abstract analysis goals such as `上身感受` are neither shown as executable
   search inputs nor compiled into a frozen query.
7. If required A coverage or an existing sample/author threshold is unmet, the run enters
   `awaiting_scope_decision`; it does not silently broaden scope.
8. A limited report clearly states its exact unmet constraints; a relaxed
   report states the versioned relaxation decision.
