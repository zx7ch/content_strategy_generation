# Development Guide: UI-ALIGN-1 - Data Intake Workspace Contract Hardening

> Generated: 2026-04-15
> Architect: dev-helper / implementation stage
> Status: Ready for development
> Source: `docs/v2/dev_spec.md` §9.0, §9.7.2, `docs/v2/development_tasks.md` §2.10, `docs/testing_rules.md`

## 1. Task Context

### Scope Boundary
- Task ID: `UI-ALIGN-1`
- Task Name: `Data Intake Workspace Contract Hardening`
- Phase: `Post-Phase-1 Alignment Backlog`
- Dependencies:
  - `/brands/[id]` route and brand detail shell already exist
  - `POST /brands/{id}/source-syncs` and `POST /brands/{id}/data-imports` already exist
  - current UI is still a developer-style editable JSON trigger panel and does not satisfy the formal operator workflow
- Task Goal:
  - original objective: turn `/brands/[id]` into the canonical operator-facing `Data Intake Workspace` with read-only preview, status card, latest receipt, automatic sync, and real error surfaces
  - reconciliation note: the current shipped runtime has since split this operator flow across `/data-sources` and `/data-processing`; this guide should be read as the source of the lane requirements, not as the current route-ownership definition

### In Scope
- add minimal backend workflow contracts required by the spec’s formal page flow:
  - extension capture session create/status
  - data import preview create/status
- preserve automatic progression from preview/session readiness into formal ingestion
- expose latest receipt and structured error state on the brand page
- replace editable runtime JSON textareas in the brand ingestion UI with formal lane actions and read-only previews
- support retry sync from the current preview/session state without forcing the operator to re-enter data
- add targeted unit/API tests for the new workflow endpoints

### Out Of Scope
- real browser-extension implementation outside the backend/page contract
- true spreadsheet file upload parsing from multipart input
- auth-system redesign
- broader no-fallback cleanup outside the touched brand-detail/data-intake path

### Required Deliverables
- Production:
  - backend capture-session and data-import-preview contract support
  - `/brands/[id]` data-intake UI using formal lane states instead of developer JSON editing
- Tests:
  - backend unit/API tests for session/preview workflow
  - keep existing ingestion tests green
- Spec/Docs:
  - task backlog status update in `docs/v2/development_tasks.md`

### Acceptance Criteria
- [ ] AC1 the shipped operator data-entry surface no longer depends on editable runtime JSON textareas
- [ ] AC2 both lanes expose entry action area, read-only canonical JSON preview, status card, and latest receipt area
- [ ] AC3 the default workflow is automatic fill + automatic sync once capture/preview succeeds
- [ ] AC4 retry sync reuses the current preview/session payload instead of requiring the operator to rebuild input
- [ ] AC5 structured validation or ingestion errors are shown in page state rather than hidden behind generic success/demo text

### Residual Obligations
- Relevant OPEN Residuals:
  - `UI-ALIGN-1`: formalize the brand-page data intake workflow
- Current-Phase Carry-Forward Items To Re-check:
  - keep route-driven interaction and real-error behavior aligned with `docs/v2/dev_spec.md`
- Resolved By This Task:
  - brand-page data-intake workflow gap
- Deferred / Blocked:
  - `UI-ALIGN-3` remains responsible for broader runtime no-fallback cleanup across untouched routes

### Contract Inventory
- Upstream contracts:
  - `IngestionService.create_source_sync(...)`
  - `IngestionService.create_data_import(...)`
  - brand-detail page SSR data loading
- New/expanded contracts:
  - `POST /brands/{id}/extension-capture-sessions`
  - `GET /brands/{id}/extension-capture-sessions/{session_id}`
  - `POST /brands/{id}/data-import-previews`
  - `GET /brands/{id}/data-import-previews/{preview_id}`
- Downstream contracts:
  - `frontend/src/lib/api.ts`
  - `frontend/src/components/brand/BrandIngestionPanel.tsx`
  - `frontend/src/app/brands/[id]/page.tsx`
- Files/interfaces with compatibility risk:
  - `app/models/schemas.py`
  - `app/api/routes/router.py`
  - `app/v2/ingestion/service.py`
  - `frontend/src/lib/api.ts`
  - `frontend/src/lib/types.ts`
  - `frontend/src/components/brand/BrandIngestionPanel.tsx`

### Test Requirements
- Primary test files:
  - `tests/unit/test_v2_ingestion_service.py`
  - `tests/unit/test_v2_ingestion_api.py`
- Required scenarios:
  1. extension capture session can be created and later returns waiting/accepted/failed style state
  2. data import preview returns canonical preview payload and auto-sync receipt
  3. retry sync reuses current preview/session payload
  4. structured validation errors surface in API responses for invalid import preview input
  5. existing direct ingestion paths remain compatible
- Test target:
  - task-scoped `unit` and router/API tests

## 2. Architecture Context

### System Position
`/data-sources`
-> browser capture / historical upload lanes
-> preview/session APIs
-> formal ingestion APIs
-> `ingestion_runs`

`/data-processing`
-> read-only preview / validation / processing history

### Tech Stack
- Language/runtime:
  - Python backend
  - TypeScript / Next.js frontend
- Primary libraries/services:
  - FastAPI + Pydantic schemas
  - existing `IngestionService`
  - React client-side lane state
- Execution pattern:
  - route-based operator pages + client lane actions/polling
- Key behavioral constraints:
  - keep real operator flow in the shipped data-source / data-processing pages
  - no runtime editable developer JSON
  - real errors only

### Constraints
- reuse existing ingestion normalization logic wherever possible
- keep new workflow state in-memory/store-local for now if durable preview/session tables are not present yet
- maintain explicit route-level status values and receipts compatible with the spec language

## 3. Technical Design

### 3.1 Module Structure

| Path | NEW/MODIFY | Required Change | Linked AC |
|------|------------|-----------------|-----------|
| `app/v2/ingestion/service.py` | MODIFY | add lightweight capture-session and import-preview workflow helpers plus retry support | AC2-AC5 |
| `app/models/schemas.py` | MODIFY | add request/response schemas for capture sessions and import previews | AC2-AC5 |
| `app/api/routes/router.py` | MODIFY | expose create/get preview/session endpoints and map structured errors | AC2-AC5 |
| `frontend/src/lib/types.ts` | MODIFY | add client types for lane status, preview payloads, receipts, and errors | AC2-AC5 |
| `frontend/src/lib/api.ts` | MODIFY | add API functions for create/get session/preview and retry actions | AC2-AC5 |
| `frontend/src/components/brand/BrandIngestionPanel.tsx` | MODIFY | replace editable trigger panel with formal `Data Intake Workspace` lanes | AC1-AC5 |
| `tests/unit/test_v2_ingestion_service.py` | MODIFY | cover preview/session and retry behavior | AC3-AC4 |
| `tests/unit/test_v2_ingestion_api.py` | MODIFY | cover new endpoints and structured errors | AC2-AC5 |

### 3.2 Interface Design

Prefer additive schemas like:

```python
class V2ExtensionCaptureSessionResponse(BaseModel):
    capture_session_id: str
    status: str
    expires_at: str
    preview_payload: Dict[str, Any] | None = None
    ingestion_receipt: V2IngestionAcceptedResponse | None = None
    error_summary: Dict[str, Any] | None = None
```

```python
class V2DataImportPreviewResponse(BaseModel):
    preview_id: str
    file_name: str
    status: str
    parsed_row_count: int
    preview_payload: Dict[str, Any] | None = None
    ingestion_receipt: V2IngestionAcceptedResponse | None = None
    field_errors: List[Dict[str, Any]] = Field(default_factory=list)
    error_summary: Dict[str, Any] | None = None
```

### 3.3 Logic Flow

Extension lane:

1. user clicks lane action on `/brands/[id]`
2. page requests a capture session
3. backend returns a waiting session with token/expires/status
4. for this turn’s minimal operator flow, page can use a canned/demo-like capture payload builder only as input to the formal session flow, not as an editable runtime textarea
5. once payload is attached, preview is read-only and sync runs automatically
6. page shows status card + latest receipt + retry sync

Historical import lane:

1. user clicks upload/import action
2. page submits a structured row payload to preview endpoint
3. backend validates and returns canonical read-only preview payload
4. backend auto-runs formal import
5. page shows status card + latest receipt + retry sync

### 3.4 Error Handling

- return structured validation errors for invalid preview input
- keep lane-level error summary in response and UI
- do not downgrade failures into generic “已提交” or mock placeholder copy

## 4. Implementation Checklist

- [ ] Add lightweight capture-session state handling in backend
- [ ] Add data-import-preview state handling in backend
- [ ] Add API schemas/routes for create/get session/preview
- [ ] Wire automatic sync after preview/session readiness
- [ ] Add retry sync using current stored preview payload
- [ ] Replace editable ingestion panel with formal lane UI
- [ ] Add/update task-scoped tests

## 5. Testing Plan

- `python3 -m pytest tests/unit/test_v2_ingestion_service.py tests/unit/test_v2_ingestion_api.py -q`
- `npm run build` in `frontend`

## 6. Notes

- This task may use in-memory preview/session state if no durable DB schema exists yet, but the API/UI contract must match the formal operator workflow.
- If a fully real extension handoff is still unavailable after implementation, keep the brand page honest about the lane state and error semantics rather than exposing editable developer JSON.
