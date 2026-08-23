# Lite Content Research Final Delivery Impact Manifest

## Frozen baseline

- Branch: `localwork`
- Baseline commit: `664aed4`
- Date: 2026-08-23 (Asia/Shanghai)
- Python target baseline: `100 passed in 8.73s`
- Frontend full baseline: `82 passed in 1.73s`
- Pre-existing Lane D failures: none in the planned target suites.
- Removed before baseline: two uncommitted, failing browser experiments for deferred unknown-outcome/replay behavior and their test-only runtime/seed helpers. They were not production behavior and are outside the approved final slices.

## Lane rules

| Lane | Meaning | Gate |
|---|---|---|
| A | Current approved Contract Pack behavior | Blocking in its owner Task. |
| B | Trace, LLM, XHS login, thread restore, or other foundation behavior | Blocking in every implementation Task. |
| C | Superseded old query/Brief/admission behavior | Rewrite or delete in the first owner Task; never preserve production behavior for the old assertion. |
| D | Unrelated failure present before work | Delta-only; baseline currently has none. |

## Required entrypoint matrix

| Entrypoint | Current authority/risk | Contract IDs | Owner | Lane |
|---|---|---|---|---|
| Presearch proposal | Still emits `custom_research_question`; proposal is not retrieval authority | `AUTH-QP-1` | Task 3 | C |
| `confirm_subject_structure` | Interpretation snapshot only; must not become a second confirmation stage | `AUTH-QP-1`, `INV-QP-1` | Task 3 | C |
| `confirm_brief` | Persists deprecated query-driving fields; active run update is not atomic with confirmation | `INV-5-6`, `AUTH-QP-1` | Task 2 for active run; Task 3 for old fields | A/C |
| Plan builder | Copies deprecated custom question/marketing goal into executable planning | `AUTH-QP-1` | Task 3 | C |
| Product query compiler | Uses goal facet/first intent and old two-query portfolio | `AUTH-QP-1`–`AUTH-QP-3`, `INV-QP-1` | Task 3 | C |
| `prepare_scope` | Creates v1 semantics and old required contexts/intents | `STATE-QP-1`, `INV-QP-1` | Task 3 | C |
| `confirm_scope` | Existing exact Draft identity/non-empty edit guards are retained | `INV-QP-2`, `FAIL-QP-3` | Task 3 foundation; Task 4 editing | A |
| Dispatch guard | Must consume frozen final queries only | `AUTH-QP-1`, `INV-QP-3` | Task 3 | A |
| Worker/provider | Must send each frozen final query unchanged | `INV-QP-3` | Task 3; Task 5 for Expand | A |
| Candidate admission | Still requires first intent; v2 must require only A | `AUTH-QP-2`, `FAIL-QP-4` | Task 3 | C |
| Coverage | Must ignore missing B/C and preserve sample/author thresholds | `FAIL-QP-4`, `ACC-QP-4` | Task 3; Task 5 actions | A |
| Report read model | Still projects internal marketing goal; must read frozen Scope without reconstructing retrieval meaning | `AUTH-QP-1`, `AUTH-QP-4` | Task 3; Task 6 history | A |
| Thread restore | Historical report currently outranks durable active run in one Creator branch | `AUTH-5-6`, `FAIL-5-8` | Task 2 | B |
| Scope read | Must dispatch by persisted schema version and exact selected run | `AUTH-QP-4`, `INV-5-5` | Task 2 for run; Task 6 for version | A |
| Trace read | Must remain selected-run execution truth and redact secrets | `INV-5-5`, `FAIL-5-4` | Task 2 plus every foundation gate | B |
| LLM configuration/scope | Must remain validated, workspace scoped, and redacted | Foundation invariant | Tasks 2–6 smoke | B |
| XHS Cookie/QR login | Must remain authenticated/recoverable and redact Cookie | Foundation invariant | Tasks 2–6 smoke | B |

## Production and frontend impact ownership

Every inventory match in the following files is covered by its row. A file with two rows has symbol-level ownership rather than shared implementation ownership.

| File / symbols | Target treatment | Owner | Lane |
|---|---|---|---|
| `app/content_research/async_dispatch.py` confirmation writer | Add active run to the existing atomic transaction | Task 2 | A |
| `app/content_research/service.py` Brief confirmation/current run | Persist Run B atomically; keep rejected/stale confirmation zero-write | Task 2 | A |
| `frontend/src/app/creator/page.tsx` `latestReport`, `restoredRunId`, durable selection | Replace with the single precedence and request-ticket path | Task 2 | B |
| `frontend/src/lib/api.ts` `active_run_id` thread projections | Retain as durable server authority | Task 2 | B |
| `app/content_research/api_schemas.py` old Brief fields and Scope request | Remove old new-run inputs; add explicit v2 optional B/C fields | Task 3 | C |
| `app/content_research/contracts.py` first-intent/custom-question/goal policy | Remove them from new retrieval authority; keep unrelated historical decoders readable | Task 3 | C |
| `app/content_research/persistence_models.py` stored old fields | Preserve v1 reads; stop writing them as v2 query authority | Task 3 | A |
| `app/content_research/presearch/{prompts.py,service.py,fallback_templates.py}` | Stop presenting/persisting custom research question as a retained executable input | Task 3 | C |
| `app/content_research/workflow/plan_builder.py` | Remove old query-driving fields from new product plans | Task 3 | C |
| `app/content_research/workflow/query_planner.py` | Replace goal facet/first-intent compiler with deterministic A/A B/A C compiler | Task 3 | C |
| `app/content_research/workflow/task_router.py` | Route product retrieval from confirmed v2 Scope only | Task 3 | C |
| `app/content_research/admission/{relevance.py,evaluator.py}` | Remove first-intent requirement for v2; require A independently | Task 3 | C |
| `app/content_research/service.py` `prepare_scope`/`confirm_scope`/dispatch | Build and execute persisted v2 authority | Task 3 | A |
| `app/content_research/stores/{base.py,sqlite_store.py}` Scope persistence | Add v2 compatibility while retaining atomic Draft/Contract guards | Task 3 | A |
| `frontend/src/lib/content-research-api.ts` old Brief and Scope types | Remove old fields; add server v2/replacement request fields | Task 3 | C |
| `frontend/src/app/creator/page.tsx` Brief/Scope cards | Remove old inputs; render exact portfolio and fenced missing-B/C replacement | Task 3 | C |
| `app/content_research/marketing_conclusions.py`, `marketing_conclusion_analysis.py` | Internal report track may remain but cannot feed query compilation/admission | Task 3 | A |
| `app/content_research/reporting/{contracts.py,composer.py,lite_read_model.py}` | Decouple internal goal from retrieval; project frozen v2 Scope | Task 3 | A |
| `app/content_research/advancement.py` custom question | Remove as new product retrieval input; retain unrelated advancement semantics only | Task 3 | C |
| `app/content_research/scope_contract.py` group edits/provenance | Preserve arbitrary non-empty final text and group-scoped origin | Task 4 | A |
| `app/content_research/service.py` edited confirmation | Freeze exact text; no post-confirm compilation/normalization | Task 4 | A |
| `frontend/src/app/creator/page.tsx` query editor | Submit exact server Draft command and render frozen result | Task 4 | A |
| `app/content_research/service.py` Coverage decisions | Exact run/Scope/snapshot mutation guard | Task 5 | A |
| `app/content_research/stores/sqlite_store.py` Coverage writes | Preserve atomic decision/execution-unit creation and idempotency | Task 5 | A |
| `frontend/src/app/creator/page.tsx` Coverage actions | Use only server-projected existing actions; refresh authoritative state | Task 5 | A |
| `app/content_research/reporting/lite_read_model.py` v1/v2 reads | Dispatch by persisted version; never reinterpret v1 | Task 6 | A |
| `app/content_research/scope_contract.py` v1/v2 decoder | Immutable dual-version read/replay/recovery | Task 6 | A |

## Test-file ownership

Every test file returned by the impact inventory has one first owner and one lane. Later Tasks may rerun it but may not silently change its assigned contract.

| Test file | Intended change | First owner / lane |
|---|---|---|
| `frontend/src/app/creator/page.test.tsx` | Current-run tests stay foundation; old marketing-goal test is rewritten; Coverage payload tests remain current | Task 2 / B; named old-goal test Task 3 / C |
| `frontend/src/lib/content-research-api.test.ts` | Remove old Brief payload and add v2 Scope payload; retain header/redaction cases | Task 3 / A |
| `tests/e2e/test_content_research_creator_browser.py` | First owned-stack run test, followed by the named Tasks 3–6 journeys | Task 2 / B |
| `tests/e2e/test_content_research_brief_confirm_api.py` | Add atomic run test; rewrite named superseded Brief/query cases | Task 2 / A; old-query cases Task 3 / C |
| `tests/e2e/test_content_research_scope_api.py` | Add v2 path, exact edit/stale guards, Coverage and v1 replay cases | Task 3 / A |
| `tests/e2e/test_content_research_source_collection_api.py` | Prove frozen provider queries/A admission and Expand execution | Task 3 / A |
| `tests/integration/test_content_research_scope_contract_store.py` | Add dual-version round-trip and immutable recovery | Task 3 / A |
| `tests/integration/test_content_research_sqlite_write_coordination.py` | Retain confirmation concurrency and rerun after v2 cutover | Task 3 / A |
| `tests/unit/test_content_research_query_planner.py` | Replace old goal-facet/two-query expectations with literal A/A B/A C cases | Task 3 / C |
| `tests/unit/test_content_research_product_marketing_admission.py` | Replace first-intent-required v1-new-run assertions with v2 A-only admission | Task 3 / C |
| `tests/unit/test_content_research_contracts.py` | Remove old new-run locked custom question/goal assertions; retain historical decode | Task 3 / C |
| `tests/unit/test_content_research_plan_builder.py` | Remove custom question/goal routing from new product plan | Task 3 / C |
| `tests/unit/test_content_research_presearch.py` | Stop retaining old query-driving fields in the new Creator flow | Task 3 / C |
| `tests/unit/test_content_research_lite_read_model.py` | Project frozen v2 Scope; retain internal report compatibility | Task 3 / A |
| `tests/integration/test_content_research_lite_read_model.py` | Same owned read boundary with real persistence | Task 3 / A |
| `tests/unit/test_content_research_marketing_conclusions.py` | Prove internal track does not become retrieval authority | Task 3 / A |
| `tests/unit/test_content_research_marketing_conclusion_analysis.py` | Preserve internal conclusion semantics without query coupling | Task 3 / A |
| `tests/unit/test_content_research_report_faithfulness.py` | Preserve report audit independent of v2 query compiler | Task 3 / A |
| `tests/integration/test_content_research_report_execution.py` | Preserve frozen Scope/report execution boundary | Task 3 / A |
| `tests/integration/test_content_research_packet_replay.py` | Task 6 version-owned replay; remove new-run reliance on old goal fields | Task 6 / A |
| `tests/e2e/test_content_research_authorized_continuation_e2e.py` | Task 5 Coverage continuation under v2 Scope | Task 5 / A |
| `tests/acceptance/test_content_research_creator_ui_contract.py` | Replace source-text checks only where superseded; browser journeys remain authoritative | Task 3 / C |
| `tests/acceptance/test_creator_workflow_v2_full_loop.py` | Preserve active-run foundation and rerun after Task 2 | Task 2 / B |
| `tests/e2e/test_creator_message_rerun_workflow.py` | Preserve durable active-run behavior | Task 2 / B |
| `tests/unit/test_content_research_thread_lifecycle.py` | Preserve nullable/durable active-run lifecycle | Task 2 / B |
| `tests/unit/test_conversation_orchestrator.py` | Preserve server-owned active-run updates outside Brief path | Task 2 / B |
| `tests/unit/test_workflow_run_manager.py` | Preserve thread/run ownership | Task 2 / B |
| `tests/unit/test_workflow_schema.py` | Preserve existing `active_run_id` schema | Task 2 / B |
| `tests/e2e/test_content_research_trace_api.py` | Update superseded request fixtures in Task 3; Trace truth remains blocking | Task 3 / B |
| `tests/unit/test_content_research_trace_service.py` | Update old fixtures without changing Trace semantics | Task 3 / B |
| `tests/e2e/test_content_research_workflow_events_api.py` | Update old fixture fields; preserve event projection | Task 3 / B |
| `tests/e2e/test_content_research_formal_workflow_e2e.py` | Update old new-run fixtures; preserve worker/report lifecycle | Task 3 / A |
| `tests/e2e/creator_browser_runtime.py` | Update deterministic presearch response shape only when Task 3 changes it | Task 3 / B |
| `tests/e2e/test_content_research_presearch_api.py` | Update removed custom-question contract | Task 3 / C |
| `tests/e2e/test_content_research_human_decisions_api.py` | Update old fixtures; decision behavior unchanged | Task 3 / A |

## Foundation tests with no inventory hit

These rows are mandatory even though their symbols do not match the impact regex.

| Test file | Owner | Lane |
|---|---|---|
| `tests/e2e/test_content_research_model_configuration_api.py` | Tasks 2–6 smoke | B |
| `tests/e2e/test_xhs_qr_login_api.py` | Tasks 2–6 smoke | B |
| `tests/unit/test_content_research_llm_scope.py` | Tasks 2–6 smoke | B |
| `tests/unit/test_xhs_credentials.py` | Tasks 2–6 smoke | B |
| `tests/unit/test_xhs_qr_auth.py` | Tasks 2–6 smoke | B |

## Baseline commands

```bash
pytest -q \
  tests/unit/test_content_research_query_planner.py \
  tests/unit/test_content_research_scope_contract.py \
  tests/e2e/test_content_research_brief_confirm_api.py \
  tests/e2e/test_content_research_scope_api.py \
  tests/e2e/test_content_research_trace_api.py \
  tests/e2e/test_content_research_model_configuration_api.py \
  tests/e2e/test_xhs_qr_login_api.py
# 100 passed in 8.73s

cd frontend && npm test
# 82 passed in 1.73s
```

Task 1 exit condition is satisfied only when this manifest and the clean baseline are committed before Task 2 production code changes.
