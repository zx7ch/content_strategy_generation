# Development Guide: V2-P1-4 - Decision Console Completion

> Generated: 2026-04-13
> Architect: dev-helper / implementation stage
> Status: Ready for development
> Source: `docs/v2/development_tasks.md` §2.4, existing V2 P1-4 backend slice, `docs/testing_rules.md`

## 1. Task Context

### Scope Boundary
- Task ID: `V2-P1-4`
- Task Name: `P1-4 决策与评分`
- This turn scope: complete the remaining `P1-4` frontend-facing and route-driven obligations on top of the already-delivered backend slice
- Dependencies: `P1-1` foundation done, `P1-2` ingestion done, `P1-3` topic pool done, `P1-4` backend slice already implemented
- Goal: make `P1-4` operable end-to-end through the console, not just via backend APIs

### In Scope
- Add decision read APIs for frontend consumption
- Support route-driven navigation from `/topic-pool` to `/decisions`
- Turn `/decisions` from mock-only into live route behavior with explicit real empty/error states
- Implement operator action flows for `accept`, `reject`, `edit_and_accept`
- Surface `Exploitation` / `Exploration` clearly in operator workflow
- Add/extend tests for decision read APIs and backend contract continuity
- Validate frontend via `next build`

### Out Of Scope
- publish / performance / evaluation APIs
- frontend test framework bootstrap
- modal system or complex form infrastructure beyond a pragmatic in-page flow
- Thompson Sampling, LinUCB, online experiments

### Required Deliverables
- Production:
  - decision batch read endpoint(s)
  - frontend API client live decision loaders/actions
  - `/topic-pool` execute-decision route handoff
  - `/decisions` page live data rendering and operator actions
- Tests:
  - unit/API tests for new decision read path
  - regression for existing decision run/review APIs
  - frontend compile/build verification
- Docs/spec sync: guide only in this turn unless the whole task becomes clean

### Acceptance Criteria
- [ ] AC1 `/topic-pool` can trigger decision execution and navigate to `/decisions` using route state rather than hidden local state
- [ ] AC2 `/decisions` can load one real persisted batch by `batch_id` and fall back to latest batch for the selected brand
- [ ] AC3 `/decisions` shows real slot-level title, score, review status, and `Exploitation` / `Exploration`
- [ ] AC4 operator can run `accept`, `reject`, and `edit_and_accept` from `/decisions`
- [ ] AC5 operator actions call `PATCH /decision-batches/{id}/items/{slot_index}` and refresh visible state
- [ ] AC6 live decision pages show explicit real empty/error state when backend is unavailable or no batch exists
- [ ] AC7 backend read contract is covered by automated tests and frontend compiles successfully

### Contract Inventory
- Upstream:
  - existing `POST /brands/{id}/decisions/run`
  - existing `PATCH /decision-batches/{id}/items/{slot_index}`
  - selected brand context and route shells
- New downstream contracts:
  - `GET /decision-batches/{id}`
  - `GET /brands/{id}/decision-batches/latest`
- Compatibility risks:
  - frontend `DecisionItem` type is still mock-era and must be expanded carefully
  - route-driven navigation must not break direct `/decisions` entry

### Test Requirements
- Backend layer: `unit`
- Frontend verification: build/type-level validation through `next build`
- Files:
  - `tests/unit/test_v2_decision_api.py`
  - `tests/unit/test_v2_decision_service.py` only if read logic needs service coverage
- Scenarios:
  1. latest batch read returns the most recent persisted decision batch
  2. batch-by-id read returns reviewed fields after patch
  3. cross-workspace read is rejected
  4. existing run/review endpoints still pass
  5. frontend compiles with live decision integration

## 2. Architecture Context

### System Position
- API layer: extend `router.py`
- Service layer: extend `app/v2/decision/service.py`
- Persistence layer: extend `app/v2/decision/store.py`
- Frontend data layer: `frontend/src/lib/api.ts`
- Frontend pages: `frontend/src/app/topic-pool/page.tsx`, `frontend/src/app/decisions/page.tsx`

### Technical Constraints
- Keep route shell structure unchanged
- Use App Router primitives from `next/navigation`
- Preserve mock fallback behavior outside delivered live paths
- Delivered live paths themselves must not hide failures behind mock decision data
- Avoid adding a brand-new UI framework dependency just for editing/review flow

## 3. Technical Design

### 3.1 Files To Modify
- `app/v2/decision/models.py`
- `app/v2/decision/store.py`
- `app/v2/decision/postgres_store.py`
- `app/v2/decision/service.py`
- `app/models/schemas.py`
- `app/api/routes/router.py`
- `frontend/src/lib/types.ts`
- `frontend/src/lib/api.ts`
- `frontend/src/app/topic-pool/page.tsx`
- `frontend/src/app/decisions/page.tsx`
- `tests/unit/test_v2_decision_api.py`
- optional: `tests/unit/test_v2_decision_service.py`

### 3.2 Core Design Rules
- Decision read endpoints should reuse the persisted batch and batch-item state instead of recomputing ranking
- Read response should align closely with existing run response so frontend can share mapping logic
- Latest-batch route is brand-scoped; batch-by-id route is workspace-scoped
- Topic pool execute button should:
  - run decision API
  - navigate to `/decisions?batch_id=<id>`
- Decisions page should:
  - read `batch_id` from URL
  - if absent, request latest batch for the selected brand
  - expose inline action buttons
  - use a pragmatic edit flow, acceptable via `window.prompt` if necessary

### 3.3 API Design
- `GET /decision-batches/{batch_id}`
  - response: same batch summary shape as run response
- `GET /brands/{brand_id}/decision-batches/latest`
  - response: latest batch for selected brand
  - `404` if none exists yet

### 3.4 Frontend Data Design
- Expand `DecisionItem` with:
  - `slotIndex`
  - `reviewStatus`
  - `reviewNotes`
  - `decisionEventId`
  - `angle`
  - `hypothesis`
  - `topicType`
- Add API helpers:
  - `runDecisionBatch`
  - `getDecisionBatch`
  - `getLatestDecisionBatch`
  - `reviewDecisionBatchItem`

### 3.5 Error Handling
- read API missing batch/latest => backend `404 DECISION_NOT_FOUND`
- frontend latest-batch miss should degrade gracefully to empty-state guidance, not mock fallback
- operator action failures should show in-page error card and keep the page usable

## 4. Implementation Checklist
- [ ] Add decision read service methods and store queries
- [ ] Add batch-by-id and latest-batch API routes
- [ ] Extend decision API tests for read flows
- [ ] Add frontend decision live helpers and type mapping
- [ ] Wire `/topic-pool` execute button to run + navigate
- [ ] Wire `/decisions` page to live batch loading
- [ ] Add accept/reject/edit_and_accept flows with refresh
- [ ] Run task-scoped backend tests
- [ ] Run `frontend` build verification

## 5. Testing Plan
- Backend:
  - `pytest -q tests/unit/test_v2_decision_api.py tests/unit/test_v2_decision_service.py`
- Backend regressions:
  - `pytest -q tests/unit/test_v2_topic_pool_api.py tests/unit/test_v2_foundation_api.py`
- Frontend:
  - `cd frontend && npm run build`

## 6. Assumptions
- `window.prompt` is acceptable for `edit_and_accept` in this phase as a lightweight operator tool
- No separate audit log UI is required yet beyond persisted review fields and visible status updates
- `P1-4` can be considered clean after this turn if the console path is live and the backend/frontend validations above are green
