# Task 5 Creator Authority Contract Pack

## Mission and risk

**Mission:** a Creator user sees only actions that the server can execute for
the selected Content Research workflow, understands when recovery requires a
manual decision, and never has an older response overwrite the current Scope,
Trace, or report.

**Risk level:** L2. This slice crosses persisted Scope/Coverage state,
execution units and attempts, historical recovery, background workers, report
reads, and browser-side asynchronous state.

This Contract Pack supersedes the incomplete Task 5 planning text in
`2026-08-19-execution-facts-lineage-remediation.md`. It does not change the
meaning of a Scope Contract version: a new Scope version remains a user
semantic change only (for example, Relax), never a design or implementation
revision.

## Contract Pack

### User state

| ID | State / projection | Allowed action | Forbidden action / recovery |
|---|---|---|---|
| STATE-5-1 | `awaiting_confirmation`, with a persisted Draft | Only the exact available `confirm_scope` command projected for that Draft | A local Draft object, a stale prepare result, or an unavailable action never authorizes confirmation. Creator refreshes the persisted Scope projection before enabling confirm. |
| STATE-5-2 | `awaiting_scope_decision`, with an unresolved Coverage snapshot | Only available `resolve_coverage` rows and their declared `valid_constraint_ids` | Normal prepare/confirm/start and all legacy repair/retry/resume commands are unavailable. |
| STATE-5-3 | Execution Unit whose latest provider outcome is known and retryable | Only its exact `replay_coverage_decision` command | No browser-constructed retry; no other unit, attempt, or Coverage snapshot may be replayed. |
| STATE-5-4 | Execution Unit has `outcome_unknown` or manual recovery | No automatic mutation command | Creator renders truthful manual-recovery guidance; it does not schedule a provider call. |
| STATE-5-5 | Historical legacy run with no Scope-owned execution unit and no unresolved Coverage | Only exact legacy recovery actions projected from durable report/runtime facts | If a Scope decision or any Execution Unit owns the workflow, every legacy recovery action is absent. |
| STATE-5-6 | Completed/frozen report | Read-only report, Scope, Coverage, and Trace | A read never creates a command, dispatch, or publication. |
| STATE-5-7 | Creator thread with an explicitly selected or durable active Content Research run | Scope, Trace, report, cards, and recovery actions for that selected run only; historical artifacts remain read-only Timeline entries | A historical report, browser cache, or late response never changes the selected run. If the durable active run is absent, Creator may fall back to its valid local mapping and only then to a historical artifact. |

### Authority

| ID | Rule |
|---|---|
| AUTH-5-1 | `ResearchScopeContract` identifies research semantics. `CoverageSnapshot` identifies the decision target. `ScopeExecutionUnit` identifies one accepted user decision; `attempt_no` and lease token remain server-only execution mechanics. |
| AUTH-5-2 | `WorkflowMutationAuthority` is the sole policy for projecting and requiring mutating workflow actions. Its normalized projected action consists of the action name and the durable payload fields that its corresponding mutation validates. There is no browser-issued action ID or lease credential. |
| AUTH-5-3 | Every public mutation must be both projected by `WorkflowMutationAuthority` and required by it before the first domain write. Existing Scope/lease/store guards remain defense in depth, not a separate UI policy. |
| AUTH-5-4 | Legacy `execution_authorization` and continuation records are compatibility internals. Browser-facing responses expose the safe `execution_unit` projection only. |
| AUTH-5-5 | Legacy recovery compatibility is preserved: it is available only while no Scope-owned Execution Unit and no unresolved Coverage coexist, and only when the legacy runtime/report facts satisfy the exact existing recovery rule. This does not create a new Scope version. |
| AUTH-5-6 | Current-run selection precedence is: explicit valid user selection; otherwise durable `thread.active_run_id`; otherwise a valid thread-local cached run mapping; otherwise the newest readable historical artifact. Historical artifacts never outrank a durable active run. Successful Brief confirmation for Run B durably sets Run B as `thread.active_run_id` before Creator projects it as current. |

### Transitions and atomic boundaries

| ID | From → event → to | Guard and writes | Atomic / external boundary |
|---|---|---|---|
| INV-5-1 | Persisted Draft → `confirm_scope` → confirmed Scope | Require exact projected Draft command; validate Draft ID, structure hash and normalized editable query payload before confirmation writes. | Existing atomic Draft/contract confirmation transaction. |
| INV-5-2 | Unresolved Coverage → Limited/Expand/Relax → authorized continuation or frozen limited-report path | Require exact projected resolution, snapshot, Scope and permitted constraint target. | Existing atomic decision/Execution Unit creation; worker remains external and lease-fenced. |
| INV-5-3 | Retryable failed Unit → exact replay → next attempt | Require exact projected replay and latest durable retryable provider outcome. | Existing replay transaction creates/requeues only the next attempt; provider call occurs after durable request fact. |
| INV-5-4 | Eligible historical state → repair/retry/resume → existing recovery path | Require the exact projected legacy action before **any** task, packet, relevance, admission, report, or runtime write. | Existing recovery transaction/worker boundaries apply. |
| INV-5-5 | Any selected run → Scope/Trace/Report read → Creator state | Attach a per-channel monotonic request ticket to every read; accept a response only if it matches the current selected run and latest ticket for that channel. | Reads have no writes or dispatch side effects. |
| INV-5-6 | Run B Brief awaiting confirmation → successful `confirm_brief` → Run B selected/current | Persist the confirmed Brief/plan and `thread.active_run_id = Run B` in the same SQLite transaction before returning the summary. Creator activates Run B and all subsequent Scope/Trace/report reads target it. A rejected, stale, or rolled-back confirmation leaves both the plan and previous durable active run unchanged. |

### Failures, coexistence, and history

| ID | Failure rule |
|---|---|
| FAIL-5-1 | A command that is absent/unavailable in the projection is rejected before writes, even if a raw browser/API client sends it. Zero task, packet, admission, checkpoint, runtime, report, and publication row deltas are required for the blocked path. |
| FAIL-5-2 | A stale Draft or stale Coverage projection cannot confirm or resolve a newer persisted state; server validation is authoritative. |
| FAIL-5-3 | An unknown provider outcome is never retried automatically. The resulting UI state is truthful even after reload. |
| FAIL-5-4 | Late same-run and cross-run responses cannot replace newer Scope, Trace, report, cards, or report messages. Test delays may delay a real owned-stack response but may not mock the Scope/action payload under test. |
| FAIL-5-5 | Historical legacy recovery may coexist with old runtime/report facts, but not with a pending Coverage or any Scope-owned Execution Unit. The read projection and mutation guard must make the same decision. |
| FAIL-5-6 | Report-publication crash integrity remains the explicitly deferred Task 4 concern. Task 5 neither changes publication lineage nor claims to resolve its finalizing-crash behavior; its legacy recovery projection must only expose actions the existing service can currently execute. |
| FAIL-5-7 | No Task 5 migration is required: browser response removal is a compatibility-edge change, and stored legacy authorization records remain readable server internals. |
| FAIL-5-8 | On reload with historical Run A artifacts and durable active Run B, Run B remains selected even if Run A has the newest readable report message. A failed/stale Brief confirmation, unreadable active-run projection, browser cache, or late Run A response cannot overwrite the durable selection. The failure is shown without silently selecting a different run. |

### Deployment assumption and deferred concurrency debt

| ID | Rule |
|---|---|
| ASSUMP-5-1 | Task 5 runs in the local single-user Creator model: one interactive user, one product action flow at a time, and no externally exposed parallel workflow-action client. Creator disables Scope confirmation and Coverage-decision controls while its action is pending. |
| DEBT-5-1 | A future multi-tab, multi-user, or externally callable workflow-action deployment needs a durable workflow-mutation claim/lease to arbitrate legacy recovery against a simultaneous Scope decision. This is out of the current local single-user product boundary. The worker can persist Coverage results but cannot itself create a Scope Execution Unit; only the user's `resolve_coverage` command does so. |

### Deferred legacy-recovery compatibility

| ID | Ruling |
|---|---|
| DEBT-5-2 | Historical runs with no confirmed Scope Contract are outside the Scope-interaction delivery. Creator must not claim their packet Repair is an accepted Scope recovery. Their future UX requires a separate decision between re-confirming Scope before re-evaluating saved evidence, issuing an explicitly unscoped limited historical report, or read-only access. This debt does not block new confirmed-Scope Coverage decisions. |

### Acceptance evidence

| ID | Contract IDs | Observable proof | Proof layer |
|---|---|---|---|
| ACC-5-1 | STATE-5-1, AUTH-5-2, INV-5-1, FAIL-5-2 | Restored Draft has no Confirm without an exact available command; an available command confirms via real Router/SQLite. | Browser-to-owned-stack plus real Router API. |
| ACC-5-2 | STATE-5-2, AUTH-5-1, INV-5-2 | In Creator, a user selects server-projected Expand, supplies a supplementary query, and sees its persisted decision, worker task, and refreshed execution/Coverage state. Limited and Relax keep existing backend route/integration evidence plus frontend payload tests. | One browser-to-owned-stack Expand golden path; provider adapter may be fake. |
| ACC-5-3 | STATE-5-3, STATE-5-4, AUTH-5-1, INV-5-3, FAIL-5-3 | Deferred recovery work; known retryable failure and unknown outcome are not prerequisites for successful Expand delivery. | Existing backend evidence retained; no new browser gate. |
| ACC-5-4 | STATE-5-5, AUTH-5-2, AUTH-5-5, INV-5-4, FAIL-5-1, FAIL-5-5 | An execution-unit-owned evidence-only report projects no legacy repair action; raw repair is rejected with zero row deltas. An eligible no-unit legacy run projects and executes its exact legacy recovery action. | Real Router/SQLite integration. |
| ACC-5-5 | AUTH-5-4 | Public Scope/coverage-action responses contain safe execution-unit data and omit legacy authorization fields. | Real Router schema test and TypeScript contract test. |
| ACC-5-6 | INV-5-5, FAIL-5-4 | Delayed Scope, Trace and report promises—within one run and after a run switch—cannot overwrite the latest selected projection. | Frontend state/component test; no real worker/browser gate. |
| ACC-5-7 | STATE-5-7, AUTH-5-6, INV-5-5, INV-5-6, FAIL-5-8 | A real Creator thread contains published historical Run A and newly confirmed Run B. The confirmation persists Run B as active; the right-side Scope/Trace/report target Run B immediately and after reload; Run A remains only in Timeline. Delayed Run A reads and a transient Run B read failure do not select Run A. | Browser-to-owned-stack with real Router/SQLite and delayed read control. |

## Ordered safe vertical slices

### Slice 5A — truthful mutation projection

**Outcome:** Creator and raw API callers receive one truthful answer to “what
may mutate this workflow now?”

**Contracts:** STATE-5-1, STATE-5-5; AUTH-5-2 through AUTH-5-5; INV-5-1,
INV-5-4; FAIL-5-1, FAIL-5-2, FAIL-5-5; ACC-5-1, ACC-5-4, ACC-5-5.

**Acceptance RED:** a Scope-owned report advertises legacy repair or performs
any repair write when called directly; a restored Draft can confirm with no
available projected command; an eligible no-unit legacy run cannot recover.

**Completion proof:** real Router/SQLite tests for allowed and rejected actions
plus one owned-stack browser Draft restore. Remove the public legacy
authorization field in this slice.

### Slice 5B — user-owned Coverage decision

**Outcome:** a user can use the server-approved Expand decision to supplement
the confirmed Scope with a query and see its real execution state.

**Contracts:** STATE-5-2; AUTH-5-1 through AUTH-5-3; INV-5-2; FAIL-5-2;
ACC-5-2.

**Acceptance RED:** browser submits the first unmet constraint rather than
Expand's declared target; mocks Scope/action data; fails to persist the
supplementary query; or cannot show the real queued/running/follow-up Coverage
projection.

**Completion proof:** one browser-to-owned-stack Expand path using a
deterministic provider adapter. Limited/Relax retain backend decision
identity/concurrency tests and frontend payload tests, but do not add browser
paths to this delivery gate.

### Slice 5C — recovery truth and exact replay

**Outcome:** deferred recovery work; known retryable failure and unknown
outcome must not be misrepresented as delivered Scope-interaction behavior.

**Contracts:** STATE-5-3, STATE-5-4; AUTH-5-1 through AUTH-5-3; INV-5-3;
FAIL-5-3; ACC-5-3.

**Acceptance RED:** a real provider timeout plus browser replay either calls
the provider zero/twice incorrectly, changes unit identity, or produces more
than one report; unknown outcome exposes replay.

**Completion proof:** not a Slice 5B gate. Existing backend evidence remains;
the future recovery slice requires its own fault-controlled acceptance proof.

### Slice 5D — truthful asynchronous reads

**Outcome:** Creator continues to show the selected run’s newest Scope, Trace,
and report despite late network responses.

**Contracts:** STATE-5-7; AUTH-5-6; INV-5-5, INV-5-6; FAIL-5-4,
FAIL-5-8; ACC-5-6, ACC-5-7.

**Acceptance RED:** a deliberately delayed real response replaces a newer
same-run or selected-new-run projection.

**Completion proof:** focused frontend state/component tests with delayed
Promises. This is a local UI ordering claim, not a worker-composition claim.

## Readiness verdict

**READY to complete the narrowed Slice 5B Expand golden path.** Slice 5A's historical no-Scope Repair
compatibility is explicitly deferred as `DEBT-5-2`; it is not part of the new
confirmed-Scope interaction. Slice 5B must not make that legacy path newly
reachable. Slices 5C–5D remain planned and do not block this delivery.

The Task 4 publication-integrity work remains a separately declared release
dependency for crash-safe report retry; it is not silently folded into Task 5.
