# F003 Lite Task 5G Trace Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` task-by-task.

**Goal:** Repair recoverable specialist failures so they reach an explicit waiting state and can be safely retried, while recording the remaining formal-Trace parity work as Task 5G.

**Architecture:** A recoverable failed specialist moves the parent from `running` to `waiting_user`. Retry requeues only the eligible child task and redispatches the same run. Creator derives status and elapsed time from the safe read-only `/trace` projection.

## Bug record — F003-LITE-TRACE-RECOVERY-001

- **Observed:** a retryable provider operation (for example `collect_note_detail · transient_error`) failed a child task but left the parent run and `formal_research` step as `running`; Creator therefore showed an indefinitely running workflow and `耗时未记录`.
- **Root cause:** `complete_formal_research()` persisted the child failure then returned `False`; `_execute_formal_research()` added a retry event but neither path performed a parent state transition. The existing resume route could wake an auth-required running job but did not restart a parent step already marked retryable.
- **Repair:** transition the run to backend state `waiting_user` and the parent step to `retrying`, record `run_waiting_user`, then restart that step before requeueing only eligible failed specialist work. In the UI, `waiting_user` is rendered as the user-facing Trace state `waiting_retry`/“等待恢复”; a running step uses `Date.now() - started_at` for elapsed time.
- **Companion contract repair:** Lite Brief confirmation had omitted `report_compose_mode`, so `build_default_snapshot()` used its intentional formal default, `prose`, while `/lite-report` correctly accepts only `template_only`. The Lite creation call now explicitly freezes `template_only`; the generic builder keeps its formal default for non-Lite callers.

**Tech Stack:** Python, FastAPI, SQLite, pytest, Next.js, TypeScript, Playwright.

## Global Constraints

- Preserve Lite data boundaries: no raw source content, provider request/response, Cookie, token, or ungoverned evidence in `/trace`.
- `auth_required` and `auth_expired` require QR authentication before retry; `transient_error`, `timeout`, `rate_limited`, and `unavailable` can requeue.
- `provider_access_rejected`, parser failures, terminal runs, and non-retryable operations must not become resumable.
- Retry keeps the same workflow run and child-task identity and does not duplicate completed provider operations.
- Task 5G parity is recorded, not silently bundled into this repair.

---

### Task 1: Recoverable failure state-machine repair

**Files:** `app/content_research/service.py`, `app/services/workflow_run_manager.py`, `frontend/src/app/creator/page.tsx`, `tests/e2e/test_content_research_trace_api.py`, `tests/e2e/test_content_research_creator_browser.py`.

- [ ] Write RED tests for a recoverable child failure moving its parent to `waiting_user`, for retry requeueing only that child, and for a running step rendering elapsed time from `started_at`.
- [ ] Run the targeted tests and observe the current `running` state and `耗时未记录` failures.
- [ ] Add a manager transition `wait_for_user_recovery(run_id, step_name)`; call it when `complete_formal_research()` receives recoverable failed outcomes; use existing recoverable-task requeueing before redispatch.
- [ ] Render elapsed time as `Date.now() - started_at` while a step has no `completed_at`.
- [ ] Run targeted API/browser tests, frontend typecheck, and `git diff --check`; commit with `fix(content-research): settle recoverable failures for retry`.

### Task 2: Task 5G formal Trace parity scope

**Files:** `docs/superpowers/plans/2026-07-30-f003-lite-task-5-report-quality.md`, `docs/features/f003/F003_content_research_lite_delivery_plan.md`.

- [ ] Record automatic refresh, real-time elapsed duration, explicit `running` / `waiting_retry` / `failed` / `completed` states, genuine retry/requeue, event timeline, checkpoint summaries, provider diagnostics, and report linkage.
- [ ] Record exclusions: raw source content, Cookies, tokens, provider requests/responses, legacy `/report`, `/results`, and EvidenceBundle remain unavailable to Lite.

### Task 5G-1: Fresh Trace state and basic elapsed-time compatibility — complete

- Trace dialog refreshes immediately when opened and polls the safe `/trace` projection every three seconds while the run is non-terminal. This allows an asynchronously persisted `auth_required` / `waiting_user` state to reveal the QR recovery controls without a manual refresh.
- SQLite timestamps that omit a timezone are parsed as UTC in Creator. For the
  legacy event model, Creator estimates the elapsed window from the latest
  `step_started` event to completion, or to `run_waiting_user` / retry
  scheduling when waiting. This prevents user-wait time from continuing to
  grow, but it is only a compatibility estimate: it does not yet record the
  real queue, execution, backoff, and waiting boundaries.
- Compatibility is explicit: old `running + auth_required` runs use the existing resume wake-up action; new `waiting_user` runs use retry/requeue. Both remain same-run recovery paths.
- Verification: `frontend/src/lib/content-research-trace.test.ts`; `tests/e2e/test_content_research_creator_browser.py::test_creator_trace_prioritizes_auth_required_child_and_resumes_once_after_qr`.

### Task 5G-2A: Shared collection correctness — implementation and live acceptance complete

- Validate Xiaohongshu candidates at the shared source boundary before a
  detail request is persisted or sent.
- Treat `invalid_candidate` and `note_unavailable` as non-retryable,
  candidate-level outcomes and continue replacement within the frozen detail
  budget.
- Replace the `transient_error` fallback with the stable provider failure
  taxonomy and keep authentication recovery exclusive to authentication
  failures.
- Enforce and expose separate provider-auto-retry, specialist-user-recovery,
  and workflow-child-attempt budgets.

Progress:

- [x] Candidate validation before detail scheduling/call, including safe
  `invalid_candidate` / eligible counts.
- [x] Stable candidate/provider classification and candidate-level replacement
  for `note_unavailable`.
- [x] Provider calls consume only the three automatic retries.
- [x] Enforce two specialist user recoveries and three total workflow-child
  executions at the same-run recovery boundary.
- [x] Project all three counters through safe Trace fields.
- [x] Stop the remaining provider calls after a provider-wide failure and map
  the terminal direction/run from the actual blocking failure taxonomy.
- [x] Replay only the failed discovery/detail/comment stage while retaining
  completed sibling evidence; require authentication readiness before an auth
  recovery consumes its budget.

Completion evidence:

- Same-run recovery consumes the durable child counter before dispatch: two
  user recoveries are allowed, the third is rejected before any provider call,
  and the child execution budget remains first execution plus two recoveries.
- Recovery can replace a stable-ID `superseded` async checkpoint. Fresh
  provider outcomes are therefore persisted and cannot be mistaken for a
  successful replay.
- Safe Trace exposes provider automatic retry, specialist user recovery, and
  workflow child execution as three separate bounded counters without raw
  provider data or credentials.
- The async dispatcher's empty-queue poll is read-only, and the formal E2E
  harness now uses the same commit-then-wake event seam as production.
- Provider-wide auth/access/parser/permanent failures short-circuit remaining
  detail or discovery calls. Candidate-local failures still isolate and backfill
  within the frozen budget, while compensated transient failures do not create
  a false run-level recovery card.
- Recovery excludes superseded failures, replays the failed stage instead of
  merely restarting workflow metadata, and performs the workflow restart plus
  child-attempt increments in one manager transaction. Concurrent same-run
  retry requests are serialized.
- Safe Trace keys provider operations by specialist plus fingerprint and emits
  an opaque operation ID, so identical provider calls from separate specialists
  cannot overwrite each other in the projection.

Live acceptance on 2026-08-03:

- Run `run_04a898dc71634c3fa7f49ddff3bc6a65` completed 32/32 safe
  Xiaohongshu operations (2 discovery and 30 detail), with no authentication
  failure and no automatic retry. This verifies that the updated Cookie and
  Spider interface are returning through the shared formal adapter.
- The initial `evidence_only_report` was traced to an admission-contract
  mismatch: Spider supplied provider author names but admission hard-required
  `author_id`. The shared contract now prefers stable ID and conservatively
  falls back to normalized author name without fabricating an ID.
- A packet-only downstream replay reused all 30 packets and all 64 existing
  operation checkpoints (zero ID differences), admitted 24 claim candidates,
  and published `complete_verified_report` with 24 citations. The latest
  direction state is `formal_directional_result`; the Creator run retains one
  timeline `artifact_result` message.
- Creator now preserves the HTTP status and treats 404 / `published report
  artifact is missing` as publication-pending. It keeps polling and does not
  append the transient window as a permanent chat failure.
- The acceptance run exposed an inactive Kimi Coding membership on the former
  `cheap_fast` presearch route. Presearch now uses the existing `balanced`
  OpenAI route; a minimal live request verified provider `openai`, model
  `gpt-4o-mini`, and a non-empty response. The admitted-evidence example still
  did not require another Spider run; the already persisted note packets were
  sufficient for downstream acceptance.

### Task 5G-2B: Recorded Trace timing semantics — pending

- Record real queue, active execution, retry/backoff, and waiting boundaries
  with high-precision UTC timestamps in the shared workflow runtime.
- Project recorded timing through the Lite-safe `/trace` adapter; label old
  event-derived durations as estimated.
- Preserve the existing newest-first presentation. Current/latest execution
  remains at the top and workflow stage numbers retain their original order.
