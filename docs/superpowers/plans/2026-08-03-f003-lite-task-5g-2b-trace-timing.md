# F003 Lite Task 5G-2B Recorded Trace Timing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Persist high-precision UTC queue, active execution, retry/backoff, and user-wait boundaries in the shared workflow runtime and safely project them through Lite Trace and its newest-first Creator timeline.

**Architecture:** Add an additive timing_json record to workflow steps and child tasks, populated only at actual shared-runtime state transitions using timezone-aware UTC timestamps. It stores eligibility, each active span, retry/backoff and waiting boundaries; the Trace service derives a safe timing projection with timing_source=recorded, while old rows without the record retain their legacy fields and get an explicitly estimated projection. The Creator consumes that projection, never manufactures server boundaries, and renders active, queue, waiting, sub-100ms, and approximate durations without changing reverse display order or stage numbers.

**Tech Stack:** Python 3, aiosqlite/SQLite additive migrations, Pydantic, pytest/httpx, TypeScript/Node test, Next.js build.

## Global Constraints

- Work only in codex/f003-lite-trace-timing; never merge, rebase, push, or touch another checkout.
- Persist boundary values in high-precision ISO-8601 UTC (+00:00) at the shared workflow write boundary. No frontend Date.now() and no polling timestamp may become a recorded boundary.
- Active time is the sum of closed and currently-open execution spans only. Queue, retry/backoff, pause, and user-wait intervals are separate and never included in active time.
- Existing SQLite databases are readable: columns are additive; rows without complete timing records project timing_source=estimated from existing event/step timestamps and never invent precision.
- Lite Trace is a safe projection: exclude raw provider messages, requests, responses, Cookies, tokens, payload/checkpoint contents, and model configuration data.
- Keep Task 5G-1/5G-2A behavior intact: current state transitions, bounded layered retries, candidate-failure isolation, provider counters, and recovery semantics must not regress.
- Keep Trace newest-first in the Creator. Display row number continues to mean original workflow order, not visual position.

## File Structure

- app/models/workflow.py: expose nullable durable timing metadata on WorkflowStep and WorkflowChildTask.
- app/memory/workflow_store.py: create/migrate timing_json columns and decode them for both old and new SQLite rows.
- app/services/workflow_run_manager.py: own recorded timestamps and active-span state transitions for steps and child tasks.
- app/content_research/observation/trace_service.py: normalize recorded timing, safely derive legacy estimates, and attach only safe timing projections to runtime steps/children.
- frontend/src/lib/content-research-trace.ts: format server-projected timing without calculating persisted boundaries locally.
- frontend/src/lib/content-research-trace.test.ts: cover recorded, waiting, queued, sub-100ms, UTC legacy, and estimated formatting.
- frontend/src/app/creator/page.tsx: feed timing payload into the existing timeline while retaining source-order numbering and newest-first reversal.
- tests/unit/test_workflow_run_manager.py and tests/unit/test_workflow_child_task_manager.py: assert transactional timing transitions and retry active-span semantics.
- tests/unit/test_content_research_trace_service.py and tests/e2e/test_content_research_trace_api.py: assert safe recorded projection, legacy estimate, no active growth while waiting, and API compatibility.
- tests/e2e/test_content_research_creator_browser.py: assert reversed visual ordering retains original stage labels and displays API timing wording.

---

### Task 1: Add durable, additive runtime timing records

**Files:**
- Modify: app/models/workflow.py (WorkflowStep and WorkflowChildTask)
- Modify: app/memory/workflow_store.py (schema creation/migration and row decoding)
- Modify: app/services/workflow_run_manager.py (all step and child state transitions)
- Test: tests/unit/test_workflow_run_manager.py
- Test: tests/unit/test_workflow_child_task_manager.py

**Interfaces:**
- Produces WorkflowStep.timing_json and WorkflowChildTask.timing_json, each nullable dict.
- Produces a persisted record shaped as {queued_at, execution_spans: [{started_at, finished_at}], retry_backoff_started_at, waiting_started_at}.
- Consumes existing start_step, complete_step, retry_step, wait_for_user_recovery, start_child_task, complete_child_task, retry_child_task, and fail_child_task commands.

- [ ] **Step 1: Write the failing step timing test**

~~~python
@pytest.mark.asyncio
async def test_step_timing_records_queue_and_closed_active_span(manager):
    run = await manager.start_run(thread_id="thread-timing", user_id="user-timing")
    step = (await manager.initialize_steps(run.run_id, [{
        "step_name": "formal_research", "phase": "retrieval", "max_attempts": 3,
    }]))[0]
    started = await manager.start_step(run.run_id, step.step_name)
    completed = await manager.complete_step(run.run_id, step.step_name)

    assert started.timing_json["queued_at"].endswith("+00:00")
    assert completed.timing_json["execution_spans"][-1]["finished_at"].endswith("+00:00")
~~~

- [ ] **Step 2: Run it to verify RED**

Run: pytest tests/unit/test_workflow_run_manager.py -k timing -v

Expected: FAIL because WorkflowStep has no timing_json and transitions do not write it.

- [ ] **Step 3: Write failing recovery and child-span tests**

~~~python
@pytest.mark.asyncio
async def test_waiting_and_retry_do_not_extend_prior_active_span(manager):
    # Start formal research, wait_for_user_recovery, resume/start it.
    # Assert first span closes at the wait boundary, waiting is separate,
    # and resumed execution appends a second span.

@pytest.mark.asyncio
async def test_child_retry_records_separate_active_spans(seeded_manager):
    # Start, fail, retry, restart, complete a child. Assert two spans and no
    # retry/backoff interval included as active execution.
~~~

- [ ] **Step 4: Run them to verify RED**

Run: pytest tests/unit/test_workflow_run_manager.py -k "timing or waiting" -v && pytest tests/unit/test_workflow_child_task_manager.py -k timing -v

Expected: FAIL because the durable record and transition span handling do not exist.

- [ ] **Step 5: Write minimal durable model, migration, and transition logic**

~~~python
# workflow_store schema compatibility
await ensure_column(conn, table_name="workflow_steps",
                    column_name="timing_json", column_sql="timing_json TEXT")
await ensure_column(conn, table_name="workflow_child_tasks",
                    column_name="timing_json", column_sql="timing_json TEXT")

# workflow manager
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _start_execution(timing: dict[str, Any], at: str) -> dict[str, Any]:
    timing.setdefault("queued_at", at)
    timing.setdefault("execution_spans", []).append({"started_at": at, "finished_at": None})
    return timing
~~~

Use manager-local load/update helpers. Initialize queue eligibility when a step/child is created or requeued. Start an active span only in start_step/start_child_task. Close only the active span at actual complete/fail/retry/wait transition, set waiting_started_at only for user wait, and set retry_backoff_started_at only for retry. Preserve legacy started_at/completed_at columns.

- [ ] **Step 6: Run focused runtime suites**

Run: pytest tests/unit/test_workflow_run_manager.py -k "timing or waiting or recovery" -v && pytest tests/unit/test_workflow_child_task_manager.py -k timing -v && pytest tests/unit/test_workflow_transitions.py -v

Expected: PASS; existing workflow transition behavior stays green.

- [ ] **Step 7: Commit**

~~~bash
git add app/models/workflow.py app/memory/workflow_store.py app/services/workflow_run_manager.py tests/unit/test_workflow_run_manager.py tests/unit/test_workflow_child_task_manager.py tests/unit/test_workflow_transitions.py
git commit -m "feat: record workflow timing boundaries"
~~~

### Task 2: Safely project recorded and legacy timing through /trace

**Files:**
- Modify: app/content_research/observation/trace_service.py
- Modify: app/content_research/api_schemas.py if explicit response shape needs typing
- Test: tests/unit/test_content_research_trace_service.py
- Test: tests/e2e/test_content_research_trace_api.py

**Interfaces:**
- Consumes timing_json from runtime step/child records plus legacy started_at/completed_at/workflow events.
- Produces a timing dict: queued_at, execution_started_at, execution_finished_at, active_duration_ms, queue_duration_ms, waiting_started_at, retry_backoff_started_at, timing_source.
- Preserves existing top-level Trace fields and provider safe filtering.

- [ ] **Step 1: Write the failing safe recorded projection test**

~~~python
@pytest.mark.asyncio
async def test_trace_projects_recorded_queue_active_and_waiting_without_secrets(service):
    # Seed a step with two recorded spans, queue/retry/wait boundaries.
    trace = await service.get_workflow_trace("run-timing")
    timing = trace.runtime_steps[-1]["timing"]
    assert timing["active_duration_ms"] == 800
    assert timing["queue_duration_ms"] == 100
    assert timing["waiting_started_at"] == "2026-08-03T01:00:01.100001+00:00"
    assert timing["timing_source"] == "recorded"
    assert "timing_json" not in trace.runtime_steps[-1]
    assert "payload_json" not in trace.runtime_steps[-1]
~~~

- [ ] **Step 2: Run it to verify RED**

Run: pytest tests/unit/test_content_research_trace_service.py -k recorded_queue -v

Expected: FAIL because safe runtime records lack timing.

- [ ] **Step 3: Write failing legacy and no-growth tests**

~~~python
@pytest.mark.asyncio
async def test_trace_marks_legacy_step_duration_estimated(service):
    # Insert legacy started_at/completed_at only. Assert timing_source=estimated
    # and no fabricated microsecond timestamp.

@pytest.mark.asyncio
async def test_waiting_trace_active_duration_is_stable_across_reads(service):
    # Read waiting run twice with time advancing; active_duration_ms is equal.
~~~

- [ ] **Step 4: Run them to verify RED**

Run: pytest tests/unit/test_content_research_trace_service.py -k "legacy or waiting_trace" -v

Expected: FAIL because the service uses a wall-clock event-derived duration without timing source.

- [ ] **Step 5: Implement the narrow timing adapter**

~~~python
def _safe_runtime_step_dict(step: Any) -> dict:
    value = _json_dict(step)
    safe = _select_safe_fields(value, SAFE_RUNTIME_STEP_FIELDS)
    safe["timing"] = _project_timing(value, workflow_events=[])
    return safe
~~~

Implement pure parser/aggregation helpers: treat offsetless legacy SQLite timestamps as UTC; sum non-negative closed spans; for a currently open span use only the backend read-time value passed to the service; omit unavailable boundaries rather than inventing them. Never expose timing_json raw. Keep provider-operation safety filtering and counters unchanged.

- [ ] **Step 6: Run unit and API Trace tests**

Run: pytest tests/unit/test_content_research_trace_service.py -v && pytest tests/e2e/test_content_research_trace_api.py -v

Expected: PASS; provider safe-projection tests remain green.

- [ ] **Step 7: Commit**

~~~bash
git add app/content_research/observation/trace_service.py app/content_research/api_schemas.py tests/unit/test_content_research_trace_service.py tests/e2e/test_content_research_trace_api.py
git commit -m "feat: project recorded trace timing safely"
~~~

### Task 3: Render server timing in Creator without changing ordering

**Files:**
- Modify: frontend/src/lib/content-research-trace.ts
- Modify: frontend/src/lib/content-research-trace.test.ts
- Modify: frontend/src/app/creator/page.tsx (timeline mapper and existing Trace card)
- Test: tests/e2e/test_content_research_creator_browser.py

**Interfaces:**
- Consumes timing from runtime steps returned by /trace and current step status.
- Produces traceExecutionDurationText(step, events) that prefers recorded timing and falls back to estimated legacy formatting.
- Preserves contentResearchTraceTimeline source-order number assignment before existing display reversal.

- [ ] **Step 1: Write failing TypeScript formatting tests**

~~~typescript
test("recorded timing separates active queue and waiting", () => {
  const value = traceExecutionDurationText({
    status: "retrying",
    timing: {
      active_duration_ms: 24407,
      queue_duration_ms: 570,
      waiting_started_at: "2026-08-03T01:00:25.000001+00:00",
      timing_source: "recorded",
    },
  }, []);
  assert.equal(value, "执行 24.4s · 排队 0.6s · 等待恢复中");
});

test("legacy timing displays approximate duration", () => {
  // Assert a timing_source=estimated projection has an approximate marker.
});

test("recorded duration below 100ms renders below one tenth second", () => {
  // Assert active_duration_ms=23 renders "<0.1s".
});
~~~

- [ ] **Step 2: Run to verify RED**

Run: npm test -- content-research-trace.test.ts

Expected: FAIL because formatter ignores timing and derives current duration with Date.now().

- [ ] **Step 3: Implement timing-first formatting**

~~~typescript
const timing = objectValue(step, "timing");
if (numberValue(timing, "active_duration_ms") !== null) {
  return formatRecordedTiming(timing, stringValue(step, "status"));
}
return formatEstimatedLegacyTiming(step, events, now);
~~~

No TypeScript code creates persisted boundaries. The optional now stays solely for legacy fallback. Recorded waiting displays frozen active duration plus waiting wording; nonzero queue is separate; recorded sub-100ms displays <0.1s; estimated output visibly includes 约.

- [ ] **Step 4: Write the API-backed browser regression**

~~~python
def test_creator_trace_keeps_original_numbers_in_newest_first_display(page, seeded_trace_run):
    # Assert visual rows formal/plan/brief/presearch have badges 4/3/2/1
    # and waiting text contains active duration plus Chinese recovery wording.
~~~

- [ ] **Step 5: Run RED checks**

Run: npm test -- content-research-trace.test.ts && pytest tests/e2e/test_content_research_creator_browser.py -k trace -v

Expected: formatter fails before its implementation; browser fixture fails before timing is supplied.

- [ ] **Step 6: Wire the existing source-order timeline mapper**

~~~typescript
return recordList(trace?.runtime_steps).map((step, index) => ({
  number: index + 1,
  durationText: traceExecutionDurationText(step, workflowEvents),
  // Existing caller reverse() remains the sole display-order operation.
}));
~~~

Do not sort/reverse in the mapper and do not add polling-derived state.

- [ ] **Step 7: Run frontend and browser verification**

Run: npm test -- content-research-trace.test.ts && npm run build && pytest tests/e2e/test_content_research_creator_browser.py -k trace -v

Expected: PASS; build succeeds and reversed rows retain original stage numbers.

- [ ] **Step 8: Commit**

~~~bash
git add frontend/src/lib/content-research-trace.ts frontend/src/lib/content-research-trace.test.ts frontend/src/app/creator/page.tsx tests/e2e/test_content_research_creator_browser.py
git commit -m "feat: render recorded trace timing"
~~~

### Task 4: Regression sweep and delivery audit

**Files:**
- Modify: .superpowers/sdd/2026-08-02-f003-lite-task-5g-trace-parity/progress.md

**Interfaces:**
- Consumes prior units and frozen Task 5G-1/5G-2A suites.
- Produces ledger entries with exact commits, commands, results, and remaining concerns.

- [ ] **Step 1: Run focused backend suites**

Run: pytest tests/unit/test_workflow_run_manager.py tests/unit/test_workflow_child_task_manager.py tests/unit/test_content_research_trace_service.py tests/e2e/test_content_research_trace_api.py -v

Expected: PASS with recorded semantics and existing safety/retry assertions green.

- [ ] **Step 2: Run frontend test and build**

Run: npm test -- content-research-trace.test.ts && npm run build

Expected: PASS.

- [ ] **Step 3: Run browser Trace regression without external providers**

Run: pytest tests/e2e/test_content_research_creator_browser.py -k trace -v

Expected: PASS using fixtures only; no LLM or Spider invocation.

- [ ] **Step 4: Audit frozen-contract coverage**

Check timestamps originate only in manager; timing JSON is never returned raw; old rows are estimated; waiting/backoff are excluded from active; retry spans accumulate; provider safe fields/counters remain unchanged; newest-first display/original stage numbering remain; no Model Config file/API/UI changed.

- [ ] **Step 5: Remove only the untracked frontend dependency symlink and commit ledger**

~~~bash
rm frontend/node_modules
git add .superpowers/sdd/2026-08-02-f003-lite-task-5g-trace-parity/progress.md
git commit -m "docs: record trace timing verification"
~~~

Confirm frontend/node_modules is a symlink before removal; do not modify its target directory.
