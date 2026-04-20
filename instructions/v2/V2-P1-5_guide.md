# Development Guide: V2-P1-5 - Publish Feedback And Evaluation

> Generated: 2026-04-13
> Architect: dev-helper / implementation stage
> Status: Ready for development
> Source: `docs/v2/development_tasks.md` §2.5, `docs/v2/dev_spec.md` §7.2-§7.6 and §9.5-§9.7, `docs/testing_rules.md`, `docs/testing_strategy.md` `ts-p1-5`

## 1. Task Context

### Scope Boundary
- Task ID: `V2-P1-5`
- Task Name: `P1-5 发布反馈与评估`
- This turn scope: complete the missing Phase 1 publish, performance, and evaluation loop on top of existing V2 foundation, ingestion, topic-pool, and decision slices
- Dependencies: `P1-1` foundation done, `P1-2` ingestion done, `P1-3` topic-pool done, `P1-4` decision batch flow done
- Goal: let one operator record publish lineage, import reward snapshots, run offline replay/SNIPS diagnostics, and view all three surfaces from the console

### In Scope
- Add a V2 feedback/evaluation backend slice for:
  - `POST /publish-records`
  - `GET /brands/{id}/publish-records`
  - `POST /performance/import`
  - `GET /brands/{id}/performance-snapshots`
  - `POST /evaluation-runs`
  - `GET /evaluation-runs/{id}`
  - `GET /brands/{id}/evaluation-runs/latest`
- Persist publish records, performance snapshots, feedback events, evaluation runs, and evaluation run slices in both in-memory and Postgres backends
- Enforce Phase 1 lineage rules:
  - non-manual publish with `decision_event_id` must trace to exactly one decision event
  - `decision_batch_id` must be coherent with the linked decision event when provided
  - performance import must generate canonical normalized rewards and replayable feedback lineage
  - evaluation must fail closed on missing candidate set, propensities, reward version, or publish/decion lineage
- Turn `/publish`, `/performance`, and `/evaluation` into live route behavior with explicit real loading/empty/error states
- Add task-scoped backend unit tests and frontend build validation

### Out Of Scope
- online experiments or rollout control
- Thompson Sampling / LinUCB serving logic
- asynchronous evaluation workers or long-running job orchestration
- advanced reward calibration beyond deterministic Phase 1 `reward_v1`
- a dedicated frontend modal/form system; pragmatic inline/browser-prompt flows are acceptable

### Required Deliverables
- Production:
  - new `app/v2/feedback` module with models, store, service, bootstrap
  - API schemas and routes for publish / performance / evaluation
  - frontend API integration and operator actions for `/publish`, `/performance`, `/evaluation`
- Tests:
  - unit tests for backend service and API contracts
  - frontend compile verification via `next build`
- Docs/spec sync:
  - generated guide only in this turn unless task is clean after validation

### Acceptance Criteria
- [ ] AC1 operator can create a manual publish record from `/publish`
- [ ] AC2 operator can create a publish record linked to one reviewed decision batch item and its `decision_event_id`
- [ ] AC3 `GET /brands/{id}/publish-records` exposes publish lineage fields needed by the console
- [ ] AC4 operator can import one canonical performance snapshot for a publish record, and the response returns normalized rewards
- [ ] AC5 performance import persists replayable feedback lineage for decision-linked posts without altering Phase 1 scorer boundaries
- [ ] AC6 operator can run one offline evaluation through `POST /evaluation-runs`
- [ ] AC7 evaluation fails closed when candidate set, propensities, reward version, or decision/publish lineage is missing
- [ ] AC8 evaluation output includes replay, SNIPS, ESS diagnostics, candidate quality metrics, and failure slices
- [ ] AC9 `/publish`, `/performance`, and `/evaluation` render live data when APIs are reachable and show explicit real loading/empty/error states otherwise
- [ ] AC10 task-scoped backend tests pass and frontend compiles successfully

### Contract Inventory
- Upstream:
  - `decision_events`, `decision_batches`, `decision_batch_items`, `candidate_set_snapshots`
  - `brand_channels`, `brands`, workspace auth headers
- Downstream:
  - publish management page
  - performance reward page
  - evaluation diagnostics page
- Compatibility risks:
  - schema file currently omits `evaluation_runs` tables from runtime DDL and must be brought back into parity with `docs/v2/dev_spec.md`
  - frontend `PublishRecord` / `PerformanceMetric` / `EvaluationSlice` types are still mock-era and need careful expansion without breaking fallback

### Test Requirements
- Backend layer: `unit`
- Frontend verification: build/type validation through `next build`
- Files:
  - `tests/unit/test_v2_feedback_service.py`
  - `tests/unit/test_v2_feedback_api.py`
- Scenario coverage:
  1. publish record creation for manual and decision-linked cases
  2. invalid publish lineage is rejected
  3. performance import computes deterministic normalized rewards and writes feedback lineage
  4. evaluation run succeeds on complete lineage and returns replay/SNIPS/ESS/candidate-quality diagnostics
  5. evaluation fails closed when replay-critical fields are missing
  6. latest publish/performance/evaluation read models drive frontend pages

## 2. Architecture Context

### System Position
- API layer: extend `app/api/routes/router.py`
- Service layer: add `app/v2/feedback/service.py`
- Persistence layer: add `app/v2/feedback/store.py` and `app/v2/feedback/postgres_store.py`
- Bootstrap/runtime: add `app/v2/feedback/bootstrap.py` and wire in `app/main.py`
- Shared contracts: extend `app/models/schemas.py`, `app/v2/db/schema.py`
- Frontend data layer: extend `frontend/src/lib/types.ts` and `frontend/src/lib/api.ts`
- Frontend routes: `frontend/src/app/publish/page.tsx`, `frontend/src/app/performance/page.tsx`, `frontend/src/app/evaluation/page.tsx`

### Technical Constraints
- Preserve workspace-scoped isolation on every read/write
- Reuse existing decision store as the source of truth for replay-critical decision records
- Keep evaluation deterministic and synchronous for Phase 1
- Do not mutate historical `decision_events` when importing performance or running evaluation
- Use the same live-route pattern as delivered pages: real data when reachable, explicit empty/error states when unavailable, no fabricated business-data fallback

## 3. Technical Design

### 3.1 Files To Create Or Modify
- `app/v2/db/schema.py`
- `app/models/schemas.py`
- `app/api/routes/router.py`
- `app/main.py`
- `app/v2/feedback/__init__.py`
- `app/v2/feedback/models.py`
- `app/v2/feedback/store.py`
- `app/v2/feedback/postgres_store.py`
- `app/v2/feedback/service.py`
- `app/v2/feedback/bootstrap.py`
- `frontend/src/lib/types.ts`
- `frontend/src/lib/api.ts`
- `frontend/src/app/publish/page.tsx`
- `frontend/src/app/performance/page.tsx`
- `frontend/src/app/evaluation/page.tsx`
- `tests/unit/test_v2_feedback_service.py`
- `tests/unit/test_v2_feedback_api.py`

### 3.2 Core Design Rules
- `POST /publish-records`
  - validates brand/channel scope
  - allows `decision_event_id = null` only for manual publish
  - if `decision_event_id` exists, the event must belong to the same workspace + brand
  - infer or validate `decision_batch_id` from the linked decision event
- `POST /performance/import`
  - validates the target publish record
  - computes deterministic normalized metrics and `short_term_reward`, `long_term_reward`, `composite_reward`
  - writes a `feedback_event` when the publish record is decision-linked
  - keeps Phase 1 scorer contract clean: evaluation may read feedback events, scorer still depends on canonical `performance_snapshots`
- `POST /evaluation-runs`
  - builds dataset from `decision_events` joined through `publish_records` and `performance_snapshots`
  - computes:
    - replay estimate
    - SNIPS estimate
    - sample count
    - coverage rate
    - ESS / ESS ratio
    - p95 / max importance weights
    - unsupported rate
    - candidate quality metrics from candidate-set snapshots
    - failure slices
  - must fail closed if any replay-critical record is incomplete
- Page loaders:
  - `/publish` reads real publish records and offers a pragmatic “手动发布” action
  - `/performance` reads performance snapshots and offers a pragmatic import action against the newest publish record
  - `/evaluation` reads latest evaluation run and offers a run action

### 3.3 Data Model Notes
- Restore runtime DDL parity by adding:
  - `evaluation_runs`
  - `evaluation_run_slices`
- Feedback slice record families:
  - `PublishRecordRecord`
  - `PerformanceSnapshotRecord`
  - `FeedbackEventRecord`
  - `EvaluationRunRecord`
  - `EvaluationRunSliceRecord`
- Service read models:
  - publish list row includes lineage, decision source label, and title fallback
  - performance list row includes reward summary, proxy label, and publish title
  - evaluation detail includes summary metrics and failure slices for the console

### 3.4 Error Handling
- lineage mismatch or missing decision references => `422 INVALID_FEEDBACK_PAYLOAD`
- cross-workspace access => existing `WORKSPACE_SCOPE_MISMATCH`
- missing publish record / evaluation run => `404 FEEDBACK_NOT_FOUND`
- incomplete replay dataset => `422 INVALID_EVALUATION_DATASET`
- backend unavailable => frontend shows explicit real error state instead of mock data

## 4. Implementation Checklist
- [ ] Add feedback/evaluation models, store protocol, in-memory store, and Postgres store
- [ ] Extend runtime DDL with evaluation tables
- [ ] Implement feedback service create/list/import/evaluation methods
- [ ] Wire feedback bootstrap into `app/main.py`
- [ ] Add API request/response schemas and routes
- [ ] Add backend unit tests for service and API flows
- [ ] Extend frontend types and API helpers for publish/performance/evaluation
- [ ] Replace mock-only page actions with live operators + safe fallback
- [ ] Run task-scoped pytest targets
- [ ] Run `cd frontend && npm run build`

## 5. Testing Plan
- Backend:
  - `pytest -q tests/unit/test_v2_feedback_service.py tests/unit/test_v2_feedback_api.py`
- Targeted regression:
  - `pytest -q tests/unit/test_v2_decision_service.py tests/unit/test_v2_decision_api.py`
- Frontend:
  - `cd frontend && npm run build`

## 6. Assumptions
- Phase 1 evaluation can be a synchronous API call because the target dataset is still single-brand and small-scale
- `reward_v1` can use a deterministic bounded normalization formula from imported raw metrics rather than a separate cohort service in this turn
- Candidate-quality metrics may be computed from persisted slot-level candidate snapshots instead of a separate session-level dataset as long as the result is reproducible
