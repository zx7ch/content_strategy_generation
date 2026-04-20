# Development Guide: V2-P1-ACCEPT - Phase 1 End-To-End Acceptance Closure

> Generated: 2026-04-13
> Architect: dev-helper / implementation stage
> Status: Ready for development
> Source: `docs/v2/development_tasks.md` §2.6-§2.9, `docs/testing_strategy.md` §4-§5 and §11, existing V2 Phase 1 slices

## 1. Task Context

### Scope Boundary
- Task ID: `V2-P1-ACCEPT`
- Task Name: `Phase 1 API + Console end-to-end acceptance`
- This turn scope: close the final Phase 1 launch gate by adding and executing an end-to-end acceptance proof for the V2 closed loop
- Dependencies: `P1-1` foundation done, `P1-2` ingestion done, `P1-3` topic pool done, `P1-4` decision flow done, `P1-5` publish/performance/evaluation done
- Goal: prove that one workspace user can complete the full Phase 1 loop from brand setup to evaluation review without missing a step

### In Scope
- Add one acceptance-level test that runs the V2 Phase 1 loop through the shipped API surface:
  - workspace
  - brand
  - channel
  - state snapshot + policy
  - source sync or historical import
  - topic pool refresh
  - decisions run
  - decision review
  - publish record creation
  - performance import
  - evaluation run + latest evaluation read
- Record acceptance evidence artifact for the full loop
- Re-run targeted unit coverage already introduced for V2 P1 slices when needed
- Re-run frontend build to keep console route proof current

### Out Of Scope
- browser automation framework bootstrap
- real external dependency smoke for V2 ingestion sources
- updating `docs/v2/dev_spec.md` status tables unless explicitly requested later

### Required Deliverables
- Production/test:
  - `tests/acceptance/test_v2_phase1_full_loop.py`
  - acceptance artifact emission for the V2 loop
- Validation:
  - targeted V2 acceptance run
  - frontend build verification

### Acceptance Criteria
- [ ] AC1 one test covers the Phase 1 loop from brand setup to evaluation review
- [ ] AC2 the loop uses persisted V2 APIs instead of calling services directly
- [ ] AC3 non-manual publish lineage and reward import are exercised in the same chain
- [ ] AC4 evaluation output is asserted in the same chain
- [ ] AC5 acceptance evidence is written for later release-gate audit
- [ ] AC6 frontend still compiles after acceptance-oriented changes

### Contract Inventory
- Upstream: all V2 routes delivered across `P1-1` to `P1-5`
- Downstream: release gate / launch gate proof for Phase 1
- Compatibility risk: acceptance fixture state must not interfere with existing acceptance tests

### Test Requirements
- Primary layer: `acceptance`
- Supporting verification:
  - targeted `pytest -q tests/acceptance/test_v2_phase1_full_loop.py`
  - `cd frontend && npm run build`

## 2. Architecture Context

### System Position
- Acceptance test runs through `app.main` / FastAPI app runtime
- Uses V2 API endpoints only
- Reuses existing acceptance artifact writer in `tests/acceptance/conftest.py`

### Constraints
- Keep the test deterministic and low-cost
- No real network dependencies required for this V2 loop proof
- Prefer API-level loop proof over direct service calls to match launch-gate intent

## 3. Technical Design

### 3.1 Files To Create Or Modify
- `tests/acceptance/test_v2_phase1_full_loop.py`

### 3.2 Core Design Rules
- The test should create isolated workspace/brand data within its own app state
- It should assert operator-visible outcomes, not internal implementation details
- It should emit a compact artifact containing IDs and milestone outputs
- It should fail fast on the first missing loop step so release-gate diagnosis is easy

## 4. Implementation Checklist
- [ ] Add V2 full-loop acceptance test
- [ ] Write acceptance artifact
- [ ] Execute targeted acceptance run
- [ ] Execute frontend build

## 5. Testing Plan
- `pytest -q tests/acceptance/test_v2_phase1_full_loop.py`
- `cd frontend && npm run build`

## 6. Assumptions
- Acceptance mode for this V2 loop can run against the local in-process app because the launch-gate question here is closed-loop completeness rather than real provider availability
