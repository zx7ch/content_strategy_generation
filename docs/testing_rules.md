
# Project Test Rules

High-frequency execution rules for project development and testing. This file is the default testing reference for `coder`, `testing`, and `dev-helper`.

Use `testing_strategy.md` only when a task requires deeper design detail, roadmap test matrix mapping, or higher-layer test policy not covered here.

## 1. Layer Boundaries

- `unit`: verify one module's logic, boundaries, error branches, logs, and observable state changes
- `integration`: verify collaboration between real internal components while keeping external providers fake by default
- `e2e`: verify user-visible API flows with deterministic fake external providers
- `acceptance`: verify real staging or pre-prod usability with real external dependencies

Default expectations:

- `coder` produces `unit` tests unless the guide explicitly requires higher-layer tests
- `testing` validates the requested test layer and escalates to deeper layers only when task scope requires it
- `dev-helper` uses these boundaries to decide which test layer must be delivered for the current task

## 2. Dependency Policy

### Unit

- Test one module or one tightly related pure-function group
- Mock or fake all cross-module and external dependencies
- Do not use real network calls
- Do not use real LLM or embedding calls
- Do not depend on real clock or nondeterministic time behavior

### Integration

- Real internal components are allowed when needed for collaboration verification
- External providers default to fake implementations
- Use temporary SQLite files or equivalent isolated local state
- Use temporary Chroma directories or lightweight vector stubs instead of real embedding providers

### E2E

- Run the real app stack and routers for user-visible flows
- Keep `Spider`, `LLM`, and `RAG` deterministic through fake implementations
- Assert user-visible behavior such as HTTP status, response schema, job state, session state, and SSE event sequence

### Acceptance

- Run only in staging or pre-prod
- Use real Spider, LLM, and RAG dependencies
- Control sample size, token budget, and concurrency
- Record request id, session id, provider or model, token usage, latency, and failure code

## 3. Fixtures and Utilities

- Prefer existing fixtures and helpers from `tests/conftest.py`
- Reuse project builders, factories, collectors, and fake clients before introducing new fixtures
- Fixtures must be deterministic and scoped to the smallest useful surface
- Use isolated temporary storage for DB and vector state
- Use controlled time utilities for cooldown, lease, lifecycle, retry, and purge behavior
- Do not depend on shared mutable state, real current time, or real external response formats

## 4. Naming, Placement, and Markers

- Put tests in the established directory for the intended layer
- Follow existing naming conventions such as `tests/unit/test_*.py`
- Use project pytest markers consistently:
  - `@pytest.mark.unit`
  - `@pytest.mark.integration`
  - `@pytest.mark.e2e`
  - `@pytest.mark.acceptance`
  - `@pytest.mark.slow`
  - `@pytest.mark.real_dependency`
- For async tests, use the project's existing async marker pattern consistently

## 5. Shift-Left Execution

- Write tests during implementation, not after the phase ends
- When a task introduces a new state, error code, threshold, SSE event, or persisted field, add automated tests in the same task
- Do not postpone `unit`, `integration`, or `e2e` coverage to the end of a phase when the task already made the behavior testable

## 6. Minimum Quality Rules

- Every acceptance criterion maps to at least one test or parameterized scenario
- Cover success, boundary, and failure paths
- Assert externally visible behavior and contract results before internal implementation details
- Use exact exception types, error codes, and status mappings when defined by the guide or source-of-truth documents
- If a guide lists N required scenarios, the delivered tests must cover those N scenarios unless an existing clearer equivalent such as parametrization is already used
- Unit-test coverage target for core logic is at least 80%
- For shipped frontend live routes, tests must lock the real runtime data path instead of only the mapper:
  - SSR pages must be verified with server-side workspace or auth resolution, not client-only context setup
  - Client pages must be verified with the actual runtime provider or equivalent initialized context
  - Live-route failures must surface explicit loading, empty, or error states; tests must not treat mock fallback as acceptable behavior unless the route is still formally phase-gated
- When a page depends on a runtime identity source such as workspace, brand, auth, or route params, at least one automated test must prove that the page's rendering mode (`SSR` or client) matches the source of that identity

## 7. When to Read `testing_strategy.md`

Read `testing_strategy.md` only when one of these applies:

- The guide explicitly references a chapter, section, or exact test design requirement from it
- The task requires roadmap test matrix mapping
- The task requires `integration`, `e2e`, or `acceptance` design beyond the rules in this file
- The task needs deeper release-gate, traceability, or cross-layer verification guidance
