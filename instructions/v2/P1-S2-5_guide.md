# Development Guide: P1-S2-5 - Frontend Contract And Guide Reconciliation

> Generated: 2026-04-19
> Architect: dev-helper / implementation stage
> Status: Ready for development
> Source: `docs/v2/development_tasks.md` §2.11.5, `docs/v2/dev_spec.md` §3.2, §5.4, §9.7

## 1. Task Context

### Scope Boundary
- Task ID: `P1-S2-5`
- Task Name: `Frontend Contract And Guide Reconciliation`
- Phase: `V2 Phase 1 Stage 2`
- Dependencies:
  - `P1-S2-1` through `P1-S2-4` are implemented
  - current frontend IA is already shipped in code and is the source of truth for operator-facing route ownership
- Task Goal:
  - reconcile the remaining spec/task-guide drift so current frontend pages, route ownership, and operator-facing naming are the canonical documented behavior

### In Scope
- update `docs/v2/dev_spec.md` so it matches the shipped frontend information architecture:
  - `/brands/[id]` = 品牌配置
  - `/data-sources` = 搜索观察台 + 数据入口工作区
  - `/data-processing` = 数据预览 / 校验结果 / 处理历史
- remove stale `account_handle` terminology from canonical brand-channel contracts and examples
- refresh `9.7.3` TypeScript examples so page-level contracts align with the current frontend implementation
- record the reconciliation decision in `docs/v2/development_tasks.md`
- update stale implementation guides that still instruct developers to use `/brands/[id]` as the canonical ingestion workspace

### Out Of Scope
- no new UI redesign
- no new backend route behavior beyond doc/guide contract reconciliation
- no renaming of already-shipped frontend user-facing copy unless required by spec-sync

### Required Deliverables
- Production docs:
  - `docs/v2/dev_spec.md`
  - `docs/v2/development_tasks.md`
  - `instructions/v2/P1-2_v2_guide.md`
  - `instructions/v2/UI-ALIGN-1_guide.md`
- Task guide:
  - `instructions/v2/P1-S2-5_guide.md`

### Acceptance Criteria
- [ ] AC1 spec section `9.7.2` documents the current shipped page ownership and no longer assigns ingestion workspace ownership to `/brands/[id]`
- [ ] AC2 spec section `9.7.3` route tree and TypeScript examples align with current frontend route and contract names
- [ ] AC3 canonical channel contracts no longer mention `account_handle`
- [ ] AC4 `development_tasks.md` records the reconciliation decisions so later work does not revert to the old IA
- [ ] AC5 Phase 1 guides no longer instruct developers to treat `/brands/[id]` as the canonical runtime ingestion workspace

## 2. Architecture Context

- Current frontend source of truth:
  - `frontend/src/components/brand/BrandDetailPanel.tsx`
  - `frontend/src/app/data-sources/page.tsx`
  - `frontend/src/components/brand/BrandIngestionPanel.tsx`
  - `frontend/src/app/data-processing/page.tsx`
  - `frontend/src/lib/api.ts`
  - `frontend/src/lib/types.ts`
- The shipped operator IA is:
  - `品牌配置` keeps brand profile editing and homepage link management
  - `数据源` combines search observation, browser capture, and historical upload
  - `数据处理` shows preview payloads, validation/error state, and processing history
- User-facing module names must stay productized and must not expose backend variable names such as `brand_voice`, `target_audience`, `goals`, `policy`, or `snapshot`

## 3. Technical Design

### 3.1 Files To Modify

| Path | Change | Intent |
|------|--------|--------|
| `docs/v2/dev_spec.md` | MODIFY | align canonical IA, route ownership, channel contract, and TS examples with shipped frontend |
| `docs/v2/development_tasks.md` | MODIFY | record reconciliation decision and mark old `/brands/[id]` ingestion ownership as superseded by split IA |
| `instructions/v2/P1-2_v2_guide.md` | MODIFY | annotate current route ownership for data entry surfaces |
| `instructions/v2/UI-ALIGN-1_guide.md` | MODIFY | mark original brand-page ingestion workspace framing as superseded by current `/data-sources` + `/data-processing` split |

### 3.2 Required Spec Updates

- `3.2 Brand and Channel Tables`
  - remove `account_handle`
  - keep `account_name` + `profile_url` as operator-facing homepage fields
- `5.x / input examples`
  - remove ad-hoc competitor `account_handle`
  - use `profile_url` as the operator-facing ad-hoc source locator
- `9.7.2 Functional Modules`
  - rewrite page ownership to match current frontend
- `9.7.3 Implementation Approach`
  - add `/data-sources` and `/data-processing`
  - replace legacy page-level examples like `BrandWorkspaceData` with current route/page data interfaces

### 3.3 Guide Reconciliation Rules

- historical guides may retain their original task scope, but any route ownership instruction that conflicts with shipped runtime behavior must be updated or explicitly marked as superseded
- when historical wording and current runtime differ, current runtime wins and spec/guides must say so plainly

## 4. Validation Checklist

- [ ] `rg "account_handle"` no longer returns live canonical docs/contracts for Phase 1 frontend/runtime behavior
- [ ] `docs/v2/dev_spec.md` mentions `/data-sources` and `/data-processing` in `9.7.3`
- [ ] no guide still tells developers that `/brands/[id]` is the canonical ingestion workspace in the shipped runtime
- [ ] current frontend implementation remains the reference for page ownership and user-facing naming

## 5. Testing Plan

- document consistency checks with `rg`
- optional `npm run build` only if a doc edit unexpectedly requires checking type names referenced by the docs

## 6. Notes

- This task is documentation and guide reconciliation, not a UI refactor.
- If the spec conflicts with the shipped frontend, update the spec to match the shipped frontend unless the shipped frontend is clearly broken; for this task, the user explicitly chose the shipped frontend as canonical.
