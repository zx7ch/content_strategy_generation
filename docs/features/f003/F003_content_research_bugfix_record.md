# F003 Content Research Bugfix Record

## 2026-07-11: Fact-to-Evidence Closure and Failed-Specialist Recovery Barrier

### Symptom

- Directional agents emitted `supporting_fact_ids` and `evidence_refs`, but the persisted evidence bundle did not retain the fact-to-evidence mapping or calculate citation coverage. As a result, a bundle with collected samples could be classified as `signal` purely because its citation score defaulted to zero.
- A failed specialist was recorded as a terminal child outcome, but the parent `source_collect_minimal` step could still complete and mark the workflow successful. This exposed downstream states despite an incomplete specialist.

### Fix

- Persist the finding payload and a fact-to-evidence map in `EvidenceBundleRecord.metadata`; persist each bundle item's `fact_id` and fact claim alongside its evidence reference.
- Calculate and persist citation coverage at bundle construction: cited unique evidence / available unique evidence, including cited, available, and uncited IDs for auditability.
- Synchronize every child outcome before deciding parent completion. If any specialist fails, keep the parent step and run `running`, do not create a result snapshot, and emit `formal_research_needs_retry`.
- Reuse the same failed runtime child task on retry: transition it through `failed → retrying → running → succeeded`, while already-successful siblings are reused rather than rerun.
- Surface failed specialist names and failure reasons in Creator with an explicit retry / modify directions / end research recovery block. Brand and content decisions remain unavailable.

### Chain Audit

- `EvidenceRecord → EvidenceLineage`: shared evidence remains task/direction-aware; same-task retries are idempotent.
- `EvidenceLineage → EvidenceBundle`: Bundle items point to the evidence record, and bundle expansion reads all lineage records by evidence ID. The newly persisted fact map closes the former gap between summarization output and bundle data.
- `EvidenceBundle → Snapshot`: a snapshot is created only after all specialists have non-failed terminal outcomes; failed paths cannot cache an empty or partial snapshot as final results.
- `Snapshot → Creator decisions`: the existing terminal-run gate prevents decisions until the run succeeds; the new failed-specialist recovery block prevents a running-but-failed-child state from being presented as normal in-progress work.

### Verification

- `pytest -q tests/integration/test_content_research_subagent_task_router.py tests/integration/test_content_research_workflow_runtime.py tests/unit/test_content_research_evidence_boundary_v1.py tests/unit/test_content_research_evidence_bundle_service.py`
- `pytest -q tests/e2e/test_content_research_workflow_actions_api.py tests/e2e/test_content_research_source_collection_api.py tests/e2e/test_content_research_results_api.py tests/e2e/test_content_research_trace_api.py tests/e2e/test_content_research_evidence_bundle_api.py`
- `cd frontend && npm test && npm run build`

## 2026-07-11: Parallel Subagent Barrier and Decision-Set Completion

### Symptom

- A formal-research run could show one specialist as complete while another selected specialist never started, leaving the parent stage running.
- Refreshing results could expose a partial result before the selected specialists had all reached terminal states.
- Selecting or watchlisting one brand immediately removed the remaining brand decision cards and entered content selection.

### Root Cause

- The formal-research orchestrator awaited specialists in a `for` loop, making direction tasks serial instead of parallel.
- Parent workflow completion was based on the outcomes collected so far; it did not verify that every runtime child task had an outcome.
- The Creator treated any selected/watchlist brand (and any selected content item) as completion of the entire decision set.

### Fix

- Dispatch all queued formal-research specialists concurrently with `asyncio.gather`, reusing the same completed source collection.
- Require a terminal outcome for every workflow child task before synchronizing child states, completing `source_collect_minimal`, and completing the workflow.
- Keep result refresh and decision cards gated until the workflow has reached its terminal success state.
- Treat a decision stage as complete only when every displayed candidate has a current decision; one selected/watchlist choice no longer advances the UI.
- Add a two-direction orchestration test that verifies both specialist tasks are started and the runtime reaches a terminal state.
- Add decision-set and terminal-workflow UI view-model tests so a single choice or a running workflow cannot unlock the next screen.
- Make shared-source lineage direction/task-aware and idempotent on same-task retries, preventing one parallel specialist from failing on an existing lineage record.
- Replace raw source-note titles as finding summaries with a readable directional sample summary; raw titles remain available only in the evidence drawer.
- Render terminal subagent failures as `失败` with their recorded error, rather than `等待处理`.

### Verification

- `pytest -q tests/e2e/test_content_research_workflow_actions_api.py tests/integration/test_content_research_workflow_runtime.py`
- `cd frontend && npm test && npm run build`

## 2026-07-11: Formal Research Main-Chain Break

### Symptom

Real XHS collection returned items, but the workflow remained at source collection and no research result appeared.

### Root Cause

- The plan created runtime child tasks with deferred execution, but no application-service path invoked them after collection.
- Source collection only wrote observation events; it did not drive evidence, result snapshots, or runtime completion.
- The Creator requested results before formal evidence existed, allowing an empty snapshot to be cached.
- Browser and API journey tests seeded bundles directly, so they did not exercise the actual transition from collection to subagent execution.

### Fix

- Configure `SubagentTaskRouter` with the real store, source registry, and evidence services.
- Reuse the completed source collection in each selected direction task, then create evidence bundles and a result snapshot.
- Synchronize completed or failed subagent tasks into workflow child tasks, complete the source stage, and complete the workflow run.
- Do not fetch results immediately after checklist confirmation; wait for collection orchestration to finish.
- Editing directions now cancels the prior run and creates a new presearch run instead of reconfirming the old workflow.
- Ending research now removes the workflow, Content Research records, and its Creator thread; this differs from revision, which keeps the thread and starts a new run.

### Prevention

- Add a real orchestration test: confirm -> collect -> subagent tasks -> evidence bundles -> snapshot -> terminal workflow state.
- Keep fixture-seeded evidence tests separate from genuine workflow-transition tests.
- Treat every user-visible stage transition as requiring an explicit producer, completion condition, and recovery path.
