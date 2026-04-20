# Development Guide: UI-ALIGN-3 - Runtime Error Honesty and No-Fallback Cleanup

> Generated: 2026-04-15
> Architect: dev-helper / implementation stage
> Status: Ready for development
> Source: `docs/v2/development_tasks.md` §2.10, `docs/testing_strategy.md` §2.1, `docs/testing_rules.md` §6

## 1. Task Context

### Scope Boundary
- Task ID: `UI-ALIGN-3`
- Task Name: `Runtime Error Honesty and No-Fallback Cleanup`
- Phase: `Post-Phase-1 Alignment Backlog`
- Dependencies:
  - `UI-ALIGN-1` completed the `/brands/[id]` operator workflow contract
  - `UI-ALIGN-2` completed `/topic-pool` explainability on the real API path
  - current frontend still ships runtime mock fallback branches for several live routes
- Task Goal:
  - remove remaining runtime mock/demo fallback behavior from shipped frontend routes and replace it with explicit live `loading`, `empty`, `error`, and `retry` handling

### In Scope
- audit the shipped live routes that currently fall back to sample data in runtime
- remove mock fallback returns from client-side page-data loaders used by shipped routes
- make workspace/bootstrap failure surface as a real operator-visible error instead of silently setting mock workspace identity
- preserve or improve explicit route-level error summaries and retry actions where live requests fail
- keep empty states for legitimate `404`/no-data cases when the backend contract already defines that as a normal response
- add deterministic frontend/unit-style coverage for the no-fallback API-loader behavior if local tooling supports it

### Out Of Scope
- redesigning page layout or navigation
- introducing new backend endpoints purely for this task
- changing SSR pages that already fail honestly through `server-api.ts`
- reworking isolated test fixtures/mock data used only in automated tests

### Required Deliverables
- Production:
  - runtime client data loaders no longer return mock/demo data for shipped routes
  - workspace initialization no longer synthesizes mock identity in runtime
  - affected routes show honest loading/error/empty states with retry entry points
- Tests:
  - deterministic coverage for client-side loader error behavior and/or workspace bootstrap error handling
  - build verification for the frontend bundle
- Spec/Docs:
  - update `docs/v2/development_tasks.md` with implementation status/evidence for `UI-ALIGN-3`

### Acceptance Criteria
- [ ] AC1 shipped runtime routes no longer replace backend failures with mock rows, sample metrics, or fabricated success state
- [ ] AC2 workspace/bootstrap failure is shown as an operator-visible blocking error rather than silently switching to mock workspace context
- [ ] AC3 `/topic-pool`, `/decisions`, `/publish`, `/performance`, and `/evaluation` preserve real backend error summaries in the UI and keep explicit retry affordances for failed actions
- [ ] AC4 legitimate no-data states still render as `empty` when the backend communicates “not found yet / not created yet”, without being conflated with transport or server failures
- [ ] AC5 mock/sample data remains limited to tests, fixtures, and isolated verification helpers instead of shipped runtime loaders

### Residual Obligations
- Relevant OPEN Residuals:
  - `UI-ALIGN-1` carry-forward: broader runtime no-fallback cleanup across untouched routes
  - `UI-ALIGN-2` carry-forward: explainability page must continue honoring real API failure semantics
- Current-Phase Carry-Forward Items To Re-check:
  - SSR routes already use honest live reads and should remain unchanged
  - client routes that depend on `WorkspaceProvider` must not request data before runtime identity is ready
- Resolved By This Task:
  - shipped runtime mock fallback on client pages
  - workspace mock identity bootstrap
- Deferred / Blocked:
  - none expected; if a route still cannot operate without demo payload, it must be written back as a residual instead of silently kept

### Contract Inventory
- Upstream contracts:
  - `GET /workspaces/default`
  - existing V2 read endpoints for brands, topic pool, decisions, publish records, performance snapshots, and evaluation runs
- Downstream contracts:
  - `frontend/src/components/providers/WorkspaceProvider.tsx`
  - `frontend/src/lib/api.ts`
  - client pages under `frontend/src/app/{topic-pool,decisions,publish,performance,evaluation}`
- Files/interfaces with compatibility risk:
  - `frontend/src/lib/api.ts`
  - `frontend/src/components/providers/WorkspaceProvider.tsx`
  - `frontend/src/components/ui/LiveApiErrorState.tsx`
  - `frontend/src/app/topic-pool/page.tsx`
  - `frontend/src/app/decisions/page.tsx`
  - `frontend/src/app/publish/page.tsx`
  - `frontend/src/app/performance/page.tsx`
  - `frontend/src/app/evaluation/page.tsx`
  - `docs/v2/development_tasks.md`

### Test Requirements
- Primary test files:
  - `frontend/src/lib/server-api.test.ts`
  - `frontend/src/lib/api.test.ts`
- Required scenarios:
  1. client page-data loaders throw on transport/server failure instead of returning mock fallback payloads
  2. decision/evaluation routes still treat documented `404` no-data as empty live state rather than error
  3. workspace bootstrap failure no longer installs mock identity
  4. frontend build remains green after route state changes
- Test target:
  - task-scoped deterministic loader tests plus `npm run build`

## 2. Architecture Context

### System Position
`WorkspaceProvider`
-> `setWorkspaceContext(...)`
-> `frontend/src/lib/api.ts` request layer
-> client `usePageData(...)`
-> shipped runtime pages

SSR routes:

`frontend/src/lib/server-api.ts`
-> SSR page render

### Tech Stack
- Language/runtime:
  - TypeScript / Next.js App Router
- Primary libraries/services:
  - SWR for client fetch state
  - shared page-data loaders in `frontend/src/lib/api.ts`
  - shared live-error UI
- Execution pattern:
  - runtime workspace bootstrap on client, then per-route live fetch
- Key behavioral constraints:
  - do not fabricate successful page payloads when live fetch fails
  - keep page-level no-data states only for backend-defined empty/live conditions
  - keep the operator-facing UI actionable with explicit retry or troubleshooting copy

### Constraints
- existing sample data utilities in `frontend/src/lib/data.ts` may remain for tests or isolated verification, but must not be used by shipped runtime page-data loaders
- pages should keep their current layout structure as much as possible; this task is about runtime honesty, not redesign
- route loaders must preserve meaningful 404-as-empty behavior where the backend contract already uses it

## 3. Technical Design

### 3.1 Module Structure

| Path | NEW/MODIFY | Required Change | Linked AC |
|------|------------|-----------------|-----------|
| `frontend/src/lib/api.ts` | MODIFY | remove runtime mock fallback from shipped page-data loaders; keep only explicit live success or thrown/empty states | AC1, AC4, AC5 |
| `frontend/src/components/providers/WorkspaceProvider.tsx` | MODIFY | replace mock workspace bootstrap with blocking live-error state and retry action | AC2, AC5 |
| `frontend/src/components/ui/LiveApiErrorState.tsx` | MODIFY | optionally support retry action / blocking runtime wording reuse | AC2, AC3 |
| `frontend/src/app/topic-pool/page.tsx` | MODIFY | stop labeling runtime source as mock; preserve explicit error card from real loader failures | AC1, AC3 |
| `frontend/src/app/decisions/page.tsx` | MODIFY | same cleanup for decisions route while preserving empty state on 404 latest-batch | AC1, AC3, AC4 |
| `frontend/src/app/publish/page.tsx` | MODIFY | same cleanup for publish route | AC1, AC3 |
| `frontend/src/app/performance/page.tsx` | MODIFY | same cleanup for performance route | AC1, AC3 |
| `frontend/src/app/evaluation/page.tsx` | MODIFY | same cleanup for evaluation route while preserving empty state on 404 latest-run | AC1, AC3, AC4 |
| `frontend/src/lib/api.test.ts` | NEW | add deterministic tests for no-fallback loader semantics | AC1, AC4, AC5 |
| `docs/v2/development_tasks.md` | MODIFY | record implementation status/evidence for `UI-ALIGN-3` | AC5 |

### 3.2 Interface Design

Keep page-data interfaces additive/minimal. Prefer changing source semantics from `"live" | "mock"` to always runtime-live for shipped loaders rather than adding a new demo mode.

Workspace bootstrap behavior should move from:

```ts
catch(() => {
  setWorkspaceContext("mock-workspace", "mock-user");
  setReady(true);
})
```

to:

```ts
catch((error) => {
  setBootstrapError(getReadableMessage(error));
  setReady(false);
})
```

with a retry action that re-runs the bootstrap fetch instead of installing fake credentials.

### 3.3 Algorithm & Logic Flow

Client live route flow:

1. `WorkspaceProvider` fetches `/workspaces/default`
2. if success, store real workspace/user context and render children
3. if failure, render blocking live-error state with retry; do not set fake context
4. route `usePageData(...)` loaders call live API
5. on success, render real data
6. on documented no-data responses (for example latest decision batch/evaluation run `404`), return empty live state
7. on other failures, throw and let the page render explicit error state

### 3.4 Error Handling

- transport/server failures must bubble to the page/UI rather than being converted into demo payloads
- action mutations (`run`, `refresh`, `import`, `create`) should keep current inline error cards and button loading states
- blocking workspace bootstrap error should include the original backend failure summary plus retry guidance

## 4. Implementation Checklist

- [ ] Remove runtime mock fallback returns from shipped client page-data loaders
- [ ] Preserve explicit 404-as-empty behavior only where already part of route contract
- [ ] Replace workspace mock bootstrap with blocking error + retry
- [ ] Update affected pages to stop presenting `Mock fallback` as a valid runtime source
- [ ] Add deterministic tests for no-fallback client loader behavior
- [ ] Update `docs/v2/development_tasks.md` with task evidence

## 5. Testing Plan

- `node --test frontend/src/lib/server-api.test.ts frontend/src/lib/api.test.ts`
- `npm run build`

Expected coverage focus:
- runtime loaders do not mask backend failure with sample data
- empty live states remain intact for documented no-data responses
- workspace bootstrap failure is blocking and recoverable via retry
