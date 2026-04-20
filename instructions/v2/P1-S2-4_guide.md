# Development Guide: P1-S2-4 - Postgres Runtime Convergence

> Generated: 2026-04-19
> Architect: dev-helper / implementation stage
> Status: Ready for development
> Source: `docs/v2/development_tasks.md` §2.11.4, `docs/v2/dev_spec.md` §2.2 / §11, `docs/testing_strategy.md`

## 1. Task Context

### Scope Boundary
- **Task ID**: `P1-S2-4`
- **Task Name**: `Postgres Runtime Convergence`
- **Goal**: make Postgres-backed persistence the default shipped Phase 1 runtime path while keeping in-memory stores available only for test/local-only contexts.

### In Scope
- tighten V2 runtime selection so shipped/runtime bootstraps no longer silently fall back to in-memory when Postgres is required
- cover Postgres-backed bootstrap selection for:
  - foundation
  - ingestion
  - topic pool
  - decision
  - feedback / evaluation
- add migration coverage for the highest Phase 1 migration set used by feedback/evaluation runtime
- add task-scoped Postgres full-loop validation proving the Phase 1 core path works without in-memory-only behavior

### Out Of Scope
- rewriting service/store business logic that already works in Postgres-backed stores
- removing in-memory stores from unit tests or low-level direct service tests
- frontend work or guide reconciliation work owned by `P1-S2-5`

### Required Deliverables
- Production:
  - runtime selection contract that enforces Postgres for shipped environments
  - consistent bootstrap behavior across all Phase 1 V2 runtimes
- Tests:
  - unit tests for runtime selection and migration coverage
  - acceptance coverage for a Postgres-backed full loop
- Docs:
  - no broad spec rewrite required in this task; only code/test parity

### Acceptance Criteria
- [ ] shipped/runtime bootstraps do not silently default to in-memory when running in production-like environments without `POSTGRES_DSN`
- [ ] local/test paths can still opt into in-memory deterministically
- [ ] `topic_pool`, `decision`, and `feedback/evaluation` bootstrap selection is explicitly tested for Postgres-backed mode
- [ ] `run_p1_5_migrations(...)` is covered
- [ ] a Postgres-backed Phase 1 loop validates persistence across runtime re-instantiation for topic pool, decision, publish/performance, and evaluation

### Contract Inventory
- Upstream contracts:
  - `docs/v2/dev_spec.md`: `Postgres` is the only production source of truth
  - `docs/v2/development_tasks.md` §2.11.4 / §2.12
- Downstream contracts:
  - app lifespan runtime construction in [app/main.py](/Users/czx/Documents/agentic/content_strategy_generation/app/main.py)
  - bootstrap helpers in `app/v2/*/bootstrap.py`
  - acceptance runtime validation in `tests/acceptance/*`

### Relevant Files
- `app/config.py`
- `app/main.py`
- `app/v2/foundation/bootstrap.py`
- `app/v2/ingestion/bootstrap.py`
- `app/v2/topic_pool/bootstrap.py`
- `app/v2/decision/bootstrap.py`
- `app/v2/feedback/bootstrap.py`
- `app/v2/db/runner.py`
- `tests/unit/test_v2_postgres_runtime.py`
- `tests/acceptance/test_v2_p1_2_postgres_runtime.py`

## 2. Design Notes

### Runtime Convergence Rule
- `POSTGRES_DSN` present: always use Postgres-backed runtime and run the required migrations.
- `POSTGRES_DSN` absent:
  - allowed only in explicit local/test contexts
  - disallowed in shipped/production-like runtime contexts

### Recommended Runtime Gate
- introduce a single config-level decision point instead of duplicating “if DSN else in-memory” semantics ad hoc.
- keep default local developer ergonomics workable, but make “production without Postgres” fail closed rather than degrade silently.

### Migration Coverage
- current stores already exist for all target domains
- current gap is mainly verification and convergence, not missing storage classes
- `run_p1_5_migrations(...)` must be covered because feedback/evaluation runtime is the highest Phase 1 schema layer

### Postgres Full-Loop Validation
- reuse pglite-based acceptance style already present in `test_v2_p1_2_postgres_runtime.py`
- extend the validation beyond foundation+ingestion to include:
  1. create workspace / brand / channel
  2. seed policy + state snapshot
  3. ingest evidence
  4. refresh topic pool
  5. run decision batch
  6. create publish record
  7. import performance snapshot
  8. create evaluation run
  9. rebuild runtimes and confirm persisted outputs remain readable

## 3. Test Strategy

### Unit
- extend `tests/unit/test_v2_postgres_runtime.py`
- add coverage for:
  - `run_p1_5_migrations(...)`
  - Postgres bootstrap selection for topic pool / decision / feedback
  - fail-closed behavior when production-like runtime has no `POSTGRES_DSN`
  - explicit local/test fallback behavior

### Acceptance
- extend `tests/acceptance/test_v2_p1_2_postgres_runtime.py` or add a sibling acceptance test
- prove full-loop persistence survives runtime reconstruction on Postgres-backed services

## 4. Implementation Checklist

1. Introduce a shared runtime-selection rule in config/bootstrap layer.
2. Update all relevant V2 bootstraps to use the shared rule consistently.
3. Keep in-memory fallback explicit for tests/local-only contexts.
4. Extend unit tests for bootstrap and migration parity.
5. Add Postgres-backed full-loop acceptance coverage.
6. Run task-scoped pytest + acceptance validation.
