# F003 Marketing Conclusion Aggregation and Three-State Presentation Design

## Goal

Make the existing product-marketing conclusion stage useful when it has
admitted evidence but no fully verified cross-source conclusion. The change
has two purposes:

1. construct a marketing candidate from multiple compatible admitted claims
   instead of encouraging the model to select one claim per track; and
2. present each marketing track as a verified conclusion, a directional
   hypothesis, or insufficient evidence.

This design does not add a user-operated follow-up collection flow, does not
change Spider execution, and does not weaken the existing verified-conclusion
threshold of three independent notes and two independent authors.

## Current State

`MarketingConclusionAnalysisService` receives only admitted product-marketing
claims and asks the analysis model for a `track`, `statement`, and
`supporting_claim_ids`. The parser ensures that IDs are known, non-empty, and
unique. `evaluate_marketing_conclusions` then verifies every supporting claim
against its decision, frozen policy, run, direction, evidence packet, safe
quote field, note identity, and author identity. It counts unique notes and
authors and selects a candidate only at the `3 notes / 2 authors` threshold.

The safety checks already exist. The gap is candidate construction: the prompt
does not prioritise combining compatible claims, so the model can supply one
claim per track. A valid one-claim proposal is then correctly classified as
`insufficient_evidence`; the evidence is real, but Lite has no way to expose
it as a non-publishable direction.

The current `selected`, `insufficient_evidence`,
`no_single_primary_conclusion`, and `analysis_unavailable` states also make no
distinction between "no usable direction" and "one usable but unverified
direction".

## Scope and Terminology

### User-visible track states

The three normal track results are:

| Display state | Durable decision state | Meaning |
| --- | --- | --- |
| 已验证结论 | `selected` | The candidate has at least three independent notes and two authors. |
| 待验证方向 | `directional` | A valid candidate exists, but it has not met the verified threshold. |
| 证据不足 | `insufficient_evidence` | No valid candidate can be formed. |

`no_single_primary_conclusion` and `analysis_unavailable` remain exceptional
or ambiguity states. They are not evidence-strength outcomes.

The existing report publication states are a separate axis. They remain
`complete_verified_report`, `partial_verified_report`, and
`evidence_only_report`. Add `directional_report` for a completed report with
at least one `directional` track and zero `selected` tracks. This prevents a
directional result from being silently presented as evidence-only or partially
verified.

### Evidence cluster

An evidence cluster is not a new persisted entity. It is one existing
`MarketingConclusionCandidateRecord` with more than one
`supporting_claim_ids` entry. Its identity is already a stable fingerprint of
the run, plan, track, statement, and sorted supporting claim IDs.

## Candidate Aggregation

### Safe model input

The analysis model continues to receive only admitted claims, without author
or provider identity. Each input claim includes its existing safe quote and
quote field, plus its `claim_id`, `claim_type`, and `intent_id`. The extra
type and intent fields let the model distinguish value evidence from message
evidence while retaining the existing source-identity boundary.

### Prompt contract

The model must produce no more than one candidate per track. For each track it
must:

- combine all mutually supporting admitted claims that fit one narrow
  statement;
- preserve material qualifiers already present in the supporting claims, such
  as `儿童`, in the statement or its visible evidence detail;
- use a `message_angle` claim only to support a content-expression statement,
  never a product-performance statement;
- return no candidate when no coherent statement is possible; and
- list every claim used in `supporting_claim_ids`.

The prompt may guide semantic grouping, but it does not grant authority to
publish. Model output remains a proposal only.

### Hard backend boundary

Existing backend verification stays mandatory and happens after aggregation:

- every support ID must identify an admitted `product_marketing` claim from the
  same run and policy;
- each claim must retain a valid packet relationship, safe quote field, quote
  span, canonical note ID, and author ID;
- unique note and author counts are calculated from packets, not accepted from
  model output; and
- a candidate may not become `selected` unless it satisfies `3 notes / 2
  authors` with no validation reason code.

This change deliberately permits evidence from different people or scenarios
to appear in one candidate. The report keeps each source claim and citation
visible, including qualifiers such as `儿童`, so the user can decide whether the
combined direction applies to their product. Current claim `scope` is only
`selected_packets`; no new audience or scenario gate is introduced.

## Decision Algorithm

For each track, the evaluator processes its one proposed candidate as follows:

1. Invalid candidate structure or invalid supporting evidence is rejected as
   today and cannot produce a report conclusion.
2. A valid candidate with at least three independent notes and two authors is
   `selected`.
3. A valid candidate with at least one admitted claim but below either
   verified threshold is `directional`. It retains its candidate ID, statement,
   supporting claim IDs, citations, unique counts, reason codes, and computed
   note/author gaps.
4. A track with no valid candidate is `insufficient_evidence`.

The LLM does not produce counts or a state. The service computes both from
persisted evidence. A marketing-conclusion checkpoint is successful when it
has generated and evaluated a candidate catalogue; a directional outcome is
not a failed model call.

## Report Projection and UI

Every `directional` section displays:

- the label `待验证方向`;
- the candidate statement;
- source citations and their normal evidence-detail affordance;
- `当前 N 篇 / M 位作者`;
- exact remaining gaps: `还缺 X 篇独立笔记` and/or `还缺 Y 位独立作者`;
- a fixed boundary: `该方向不可作为功效或投放定论`.

`selected` continues to display as an evidence-backed conclusion. An
`insufficient_evidence` section continues to show the verification direction
without a fabricated statement. Message-angle evidence can support a selected
or directional content-expression statement, but the UI must not render it as
a verified product effect.

The priority action is derived from the three track results:

- when one or more tracks are `selected`, use only those selected conclusions
  for the first content recommendation;
- when no track is selected but one or more are `directional`, instruct the
  user to validate those directions before making a seeding judgment;
- when all tracks are insufficient, request more qualified evidence.

No button starts collection in this change. A future user-feedback collection
feature must create a new confirmed run and cannot resume or mutate the frozen
query plan of the old run.

## Expected Historical Replay Result

Packet-only replay of `run_75d42250492f4139a8f7b9927e08cfd3` should make no
provider call and create no evidence packet. Given its seven admitted claims:

- need is `directional`: the child high-activity, sweaty-back scenario has one
  note and one author;
- value is `directional`: light/drapey feel plus cooling-material language has
  two notes and two authors, with title-level support explicitly
  bounded; and
- message is `selected`: `夏日清凉感` may be tested as a content-expression
  theme with three independent title-level notes and authors. This verifies
  repeated expression, not cooling efficacy.

The resulting report is `partial_verified_report`: it contains one selected
track and two directional tracks.

## Compatibility and Observability

The read API adds `directional` to the marketing-track union with the same
provenance fields as `selected`, plus reason codes and evidence gaps. Existing
clients can continue to render `selected` and terminal states; Creator must be
updated atomically with the API contract.

Trace adds `directional` as a safe marketing-conclusion track state and shows
counts and reason codes only. It never exposes raw provider payloads, prompts,
or author identities.

Existing historical `insufficient_evidence` decisions remain readable. They
are not rewritten merely by deployment. Packet-only replay is the explicit,
idempotent way to evaluate historical packet sets under the new algorithm.

## Tests

### 1. Deterministic decision matrix

The evaluator test suite must use persisted claim/packet fixtures and cover
every threshold outcome:

| Valid candidate | Unique notes | Unique authors | Required state |
| --- | ---: | ---: | --- |
| none | 0 | 0 | `insufficient_evidence` |
| one claim | 1 | 1 | `directional` |
| two notes from one author | 2 | 1 | `directional` |
| two notes from two authors | 2 | 2 | `directional` |
| three notes from two authors | 3 | 2 | `selected` |
| three claims from one note | 1 | 1 | `directional` |
| three notes from three authors | 3 | 3 | `selected` |

The fixtures must also prove that removing one valid quote, author identity,
or packet/run relationship invalidates the affected candidate rather than
preserving stale counts.

### 2. LLM proposal protocol tests

`MarketingConclusionAnalysisService` tests must inject a deterministic fake
LLM. They must verify that the safe prompt contains only admitted claim IDs,
claim type, intent, quote, and quote field; it must not expose author or
provider identity. Fixed responses cover a one-claim proposal, a multi-claim
proposal, an empty catalogue, unknown IDs, duplicate IDs, and more than one
candidate for the same track. The parser must reject the invalid responses;
the deterministic evaluator, not the fake LLM, supplies states and counts.

A live-model run is an interoperability smoke test only. It must never be the
stable oracle for semantic wording or decision state.

### 3. Projection, publication, and UI tests

Focused API, read-model, and browser coverage must prove:

- the parser accepts multiple unique known support claim IDs and rejects
  unknown or duplicate IDs;
- backend counting deduplicates two claims from one note and does not accept
  model-provided counts;
- a valid below-threshold candidate becomes `directional`, retains citations,
  and carries accurate gaps;
- a qualified multi-claim candidate remains `selected` only at `3 / 2`;
- title-only `message_angle` evidence cannot produce a product-performance
  statement;
- API and Creator render all three normal track states, including the
  directional boundary and citation controls;
- publication state is `directional_report` only when no selected track exists
  and at least one directional track exists;
- `partial_verified_report` has at least one selected track; and
- `evidence_only_report` has neither selected nor directional tracks.

The Creator browser test must assert that a `directional` card is not labelled
`已验证结论`, does not receive a direct-investment recommendation, exposes its
note and author gaps, and opens its evidence detail. A mixed-source card must
retain visible qualifier text from its supporting claims, including `儿童` when
that is present in a claim.

### 4. Replay and decision-audit invariants

Packet-only replay of the old run must create neither provider operation nor
new packet and must produce the expected partial report. Running the same
replay twice must preserve the report decision IDs, candidate IDs, counts,
reason codes, and citation identities. Trace and Lite read-model projections
must agree on every track's state, counts, and reason codes. Existing stored
`insufficient_evidence` reports remain readable until that explicit replay;
deployment does not silently rewrite them.
