# F003 Lite Trace and Collection Correctness Design

**Status:** Approved design  
**Date:** 2026-08-02  
**Scope:** Task 5G urgent correctness repair for the Lite Creator workflow

## Problem Statement

A real Lite `product_marketing` run exposed three connected correctness defects:

1. A malformed Xiaohongshu search candidate with an ID shaped like a UUID plus
   timestamp reached `collect_note_detail`. The provider consistently returned
   `笔记不存在`, but the source adapter projected the permanent error as
   `transient_error`. The candidate-level failure then stopped the specialist
   even though the run had many successful sources.
2. Creator correctly hides QR authentication controls unless the safe Trace
   contains `auth_required` or `auth_expired`, but the incorrect provider
   classification made the recovery presentation misleading.
3. Workflow stages are shown newest-first, as intended, but their durations do
   not describe real execution. Brief and plan work happens before the runtime
   writes adjacent start/completion events, and SQLite event timestamps have
   only second precision. Formal-research duration currently measures the
   parent wall-clock window rather than separating queue, active execution, and
   user-wait time.

The repair must preserve the Lite boundary: the shared formal collection
kernel remains real, while `/trace` and `/lite-report` remain narrow, safe
projections with no Cookie, token, raw provider request/response, or raw source
payload.

## Goals

- Treat invalid or unavailable individual notes as candidate-level outcomes,
  then continue within the frozen sampling and provider-call budgets.
- Reserve run-level recovery for failures that can prevent the direction from
  meeting its frozen evidence contract.
- Replace fallback error coercion with a stable, exhaustive provider failure
  taxonomy.
- Preserve the existing newest-first Trace presentation and stage numbering.
- Record and project queue, active execution, and waiting time with explicit
  semantics and high-precision UTC timestamps.
- Make automatic provider retries, user-triggered recoveries, and workflow
  child attempts separate, bounded counters.
- Preserve same-run recovery, completed-operation reuse, and Lite-safe output.

## Non-Goals

- No standalone “check login status” entry in this urgent repair.
- No replacement Observation platform or generic distributed tracing system.
- No restoration of legacy `/report`, `/results`, EvidenceBundle, raw provider
  payloads, prose reports, aggregate recommendations, or action hypotheses.
- No change to the current newest-first Trace ordering.
- No increase to the frozen candidate, detail-fetch, or comment limits.

## Existing Frozen Limits

The observed run froze one `product_marketing` direction with two QueryGroups.
Each QueryGroup had `candidate_cap=20`, so the pre-deduplication discovery
ceiling was 40 candidates. Its `SamplePolicy` contained:

- `minimum_samples=3`
- `minimum_independent_authors=2`
- `author_cap=3`
- `detail_fetch_cap=30`
- `comment_limit=30`

The shared default policy also uses `detail_fetch_cap=30`. This design does not
change those values. A candidate rejected before a provider call does not
consume the detail-fetch budget. Once a detail request is sent, the attempt
does consume the budget even if the note is unavailable. Replacement
candidates may be tried only while the frozen cap remains.

## Shared Lite Formal-Execution Boundary

“Formal” in this design means the execution after the user confirms a Lite
Brief. Lite continues to use the shared production chain:

```text
Lite Brief confirmation
  -> frozen requested directions and policy
  -> Research Plan and specialist task specs
  -> real provider discovery/detail collection
  -> DirectionalPipeline selection and evidence admission
  -> governed snapshot
  -> /lite-report and safe /trace projection
```

Candidate validation, provider classification, replacement selection,
checkpointing, and retry accounting belong in the shared source/workflow
layers. Creator must not implement a Lite-only exception for malformed notes.

## Candidate Validation

The Xiaohongshu source boundary must validate a search candidate before a
detail operation is persisted or sent. A detail-eligible candidate must have:

- a Xiaohongshu note ID in the supported 24-character hexadecimal form;
- a note URL whose `/explore/{note_id}` path agrees with that ID;
- no URL fragment appended to the note ID;
- the security parameters required by the current detail endpoint; and
- a supported search-result shape rather than a separator, suggestion,
  promotion, or other non-note card.

ID and URL shape are hard eligibility rules. Missing display fields such as
author or title are not independently sufficient to reject a candidate,
because valid notes can omit optional presentation fields.

Rejected candidates receive an `invalid_candidate` disposition and never
reach `collect_note_detail`. They remain auditable through safe counts and
reason codes, not raw provider payloads or sensitive URLs.

## Stable Provider Failure Taxonomy

The source adapter must map every provider outcome into one stable code:

| Code | Meaning | Retryable | QR authentication |
|---|---|---:|---:|
| `invalid_candidate` | malformed/non-note discovery item | no | no |
| `note_unavailable` | deleted, nonexistent, or unavailable individual note | no | no |
| `auth_required` | missing, expired, or rejected authentication | only after authentication | yes |
| `rate_limited` | provider rate limit | yes | no |
| `timeout` | bounded operation timeout | yes | no |
| `transient_error` | connection or temporary transport failure | yes | no |
| `parser_error` | response cannot satisfy the governed schema | no | no |
| `provider_access_rejected` | policy/risk-control/access rejection | no | no |
| `provider_permanent_error` | recognized permanent failure without a narrower code | no | no |

`transient_error` is not an unknown-error fallback. In particular,
`笔记不存在` maps to `note_unavailable`. Original error messages may be logged
locally under existing redaction rules, but the Lite Trace returns only stable
codes, operation names, retryability, timing, and safe recovery guidance.

## Candidate-Level Continuation and Final Direction State

`DirectionalPipeline` must not promote one candidate failure directly to a
specialist failure. For each candidate it records a disposition, updates the
selection revision, and chooses the next eligible candidate while the frozen
budget remains.

The specialist outcome is decided only after collection reaches one of these
boundaries:

- **Succeeded:** frozen minimum samples and independent-author threshold are
  met. Invalid/unavailable candidates remain safe diagnostics only.
- **Insufficient evidence:** usable evidence exists but the frozen threshold
  cannot be met within the candidate/detail cap. This is a governed partial
  result, not an authentication or transient recovery state.
- **Unavailable:** no usable evidence can be produced and retrying cannot
  improve the outcome.
- **Waiting for recovery:** authentication, rate limiting, timeout, or
  transient transport failure prevents the direction from satisfying its
  contract after the automatic retry budget.
- **Failed:** a non-retryable system/provider failure prevents safe completion.

Authentication failures stop further calls immediately because they are
provider-wide rather than candidate-local. Completed provider operations and
completed sibling specialists remain reusable during same-run recovery.

## Retry Budgets and Counters

The configured values are three automatic retries and two user-triggered
recoveries. The current search implementation incorrectly merges them into
five retries inside one call, while detail collection has no equivalent
automatic retry and the Content Research requeue path does not consistently
increment the checkpoint counter.

The repaired contract is:

- A retryable provider operation gets at most three automatic retries after
  its initial attempt, using bounded exponential backoff.
- A Lite specialist gets at most two user-triggered same-run recoveries.
- A workflow child therefore has at most three workflow attempts: initial plus
  two user recoveries.
- `invalid_candidate`, `note_unavailable`, `parser_error`, and permanent access
  failures receive no automatic or user retry.
- `auth_required` receives no automatic retry. A successful authentication may
  start one user recovery attempt.

Trace exposes three distinct counters instead of one overloaded attempt value:

- provider automatic retry: `n / 3`;
- specialist user recovery: `n / 2`;
- workflow child attempt: `n / 3`.

The legacy `XHS_SPIDER_MAX_RETRIES` remains a compatibility fallback only when
the separate automatic/user settings are unavailable; it must not be added to
either configured budget.

## Trace Timing Contract

The safe `/trace` response should project explicit timing per runtime step:

```json
{
  "timing": {
    "queued_at": "2026-08-02T04:46:55.545217+00:00",
    "execution_started_at": "2026-08-02T04:46:56.115243+00:00",
    "execution_finished_at": "2026-08-02T04:47:20.522704+00:00",
    "active_duration_ms": 24407,
    "queue_duration_ms": 570,
    "waiting_started_at": "2026-08-02T04:47:21.000000+00:00",
    "timing_source": "recorded"
  }
}
```

Semantics:

- queue time runs from stage eligibility/enqueue to actual worker execution;
- active duration contains only execution spans and sums spans across retries;
- pause, retry backoff, and user-wait time do not increase active duration;
- waiting time is presented separately and never relabeled as execution;
- all new timestamps are explicit, high-precision UTC values;
- old runs without the new boundaries use `timing_source=estimated`, and the UI
  labels their duration as approximate.

Brief and plan timing must bracket their real work. Plan execution begins
before task-spec and ResearchPlan construction and ends after its atomic
persistence. The system must not build the plan first and then append adjacent
start/completion events.

Formal research keeps stage activation/enqueue separate from the worker's
execution start. Provider-operation timings remain operation-specific; the
formal stage duration is the specialist execution window, not the duration of
one provider call.

## Creator Trace Presentation

The current newest-first ordering is retained. The active/latest stage stays
at the top and the earliest stage stays at the bottom:

```text
4 Execute specialist research   newest/current
3 Build research plan
2 Confirm Brief
1 Identify subject              earliest
```

Stage numbers continue to describe workflow order even though rows are
displayed in reverse chronological order. Multiple attempts within a stage are
also newest-first.

Each row displays status and active duration. Queue duration is shown
separately when nonzero. Waiting rows use wording such as
`执行 24.4s · 等待恢复中`; the execution number does not grow while waiting.
Recorded positive durations below 100ms render as `<0.1s`, while historical
fallbacks carry an approximate label.

Provider diagnostics distinguish completed operations, filtered invalid
candidates, unavailable notes, and failures requiring recovery. A compensated
candidate-level failure must not produce a run-level recovery banner. Existing
QR controls remain conditional on `auth_required` or `auth_expired`; this
repair does not add a standalone login-management surface.

## API and Compatibility

- `/trace` remains query-only and Lite-safe.
- Existing fields remain readable during migration; new timing and retry
  fields are additive.
- Historic runs do not receive invented precision. Their timing is explicitly
  estimated from the legacy event/step timestamps.
- Runtime child-task errors must carry the same stable domain failure code as
  the blocking provider outcome instead of a generic `WORKFLOW_ERROR`.
- Successful reports remain terminal and reject further retry/requeue actions.

## Verification Strategy

### Source and adapter tests

- Reject UUID/fragment/non-note candidates before detail collection.
- Accept valid 24-character note IDs and matching URLs.
- Map `笔记不存在` to `note_unavailable`.
- Distinguish authentication, rate-limit, timeout, transport, parser, access,
  and unknown permanent failures.
- Prove that safe Trace output contains none of the raw message, Cookie, token,
  or sensitive URL.

### Pipeline and state-machine tests

- Replace an invalid seventh candidate with the next eligible candidate.
- Succeed when one note is unavailable but frozen thresholds are still met.
- Produce governed insufficient evidence when the cap is exhausted.
- Stop provider calls immediately on authentication failure.
- Reuse completed operations and replay only eligible failed work.
- Enforce `detail_fetch_cap=30`, automatic retries `3`, user recoveries `2`,
  and total child attempts `3` without off-by-one behavior.

### Trace API tests

- Plan timing brackets actual construction and persistence.
- Queue, active, and waiting intervals do not overlap semantically.
- Waiting does not increase active duration.
- Retried attempts accumulate only active execution spans.
- Legacy runs are marked estimated.
- Provider and workflow counters agree.

### Creator tests

- Preserve newest-first stage ordering (`4 -> 3 -> 2 -> 1`).
- Render recorded, queued, waiting, sub-100ms, and estimated durations.
- Hide QR controls for invalid/unavailable notes.
- Show QR controls for authentication failures.
- Suppress the run-level warning when candidate replacement succeeds.
- Keep manual refresh and three-second polling read-only.

### Bounded real acceptance

Run one authenticated Lite `product_marketing` canary. It must demonstrate
real discovery/detail success, a controlled invalid or unavailable candidate,
continued collection within the frozen cap, a published or governed
insufficient-evidence outcome, and parity between provider-operation counts
and Creator Trace. It must not present the candidate failure as a login or
network-retry problem.

## Delivery Units

1. Candidate validation and stable provider classification.
2. Candidate replacement, evidence-threshold aggregation, and aligned child
   state.
3. Enforced automatic/user retry budgets and distinct counters.
4. High-precision real execution boundaries and safe timing projection.
5. Creator timing/recovery presentation while preserving newest-first order.
6. Focused API/browser verification plus one bounded real canary and Task 5G
   acceptance record.

