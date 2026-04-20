# Development Guide: V2-P1-1 - Foundation and Master Data API Completion

> Generated: 2026-04-12
> Architect: dev-helper / implementation stage
> Status: Ready for development
> Source: `docs/v2/development_tasks.md` §2.1, `docs/v2/dev_spec.md` §2.3, §3.1-§3.3, §9 API Direction, §11

## 1. Task Context

### Scope Boundary
- **Task ID**: `V2-P1-1`
- **Task Name**: Foundation and Master Data API Completion
- **Phase**: Phase 1 - 基础与主数据
- **Dependencies**:
  - Existing code already provides partial P1-1 foundation: schema contract, migration manifest, workspace auth helper, in-memory master-data service
  - No V2 API layer exists yet for workspace/brand/channel/policy/state management
- **Task Goal**:
  - Complete the currently-started P1-1 slice by exposing workspace-scoped V2 master-data APIs
  - Enforce workspace isolation and active policy config semantics through the API layer
  - Keep the implementation deterministic and local-testable without introducing real Postgres runtime wiring yet

### In Scope
- Add V2 request/response schemas for:
  - `POST /workspaces`
  - `POST /brands`
  - `POST /brands/{id}/channels`
  - `PUT /brands/{id}/policy-configs/active`
  - `GET /brands/{id}/policy-configs/active`
  - `POST /brands/{id}/state-snapshots`
- Attach an in-memory V2 master-data store/service to the FastAPI app lifespan so requests share state inside one app runtime
- Enforce workspace-scoped access on brand routes through `app.v2.auth.resolve_workspace_principal(...)`
- Validate `topic_type_targets` write semantics from `docs/v2/dev_spec.md`:
  - reject `sum(min_ratio) > 1.0`
  - reject any `max_ratio < min_ratio`
- Add focused unit/API tests for auth, isolation, config read/write, and active-policy replacement

### Out Of Scope
- Do not implement Postgres repositories or SQL execution
- Do not implement `source-syncs` or `data-imports` in this task
- Do not implement topic pool, decision engine, publish, performance, or evaluation endpoints
- Do not introduce workspace membership persistence yet

### Required Deliverables
- Production:
  - V2 master-data API routes mounted on the existing FastAPI app
  - Workspace/brand/channel/policy/state operations backed by shared in-memory master-data service
  - Policy config validation aligned with `docs/v2/dev_spec.md`
- Tests:
  - API tests for workspace/brand/channel/policy/state endpoints and isolation failures
  - Service tests for policy target validation and active-policy behavior
- Spec/Docs:
  - none required for this turn beyond the generated guide

### Acceptance Criteria
- [ ] AC1 `POST /workspaces` creates a workspace and returns a stable id/slug/timezone payload
- [ ] AC2 `POST /brands` and `POST /brands/{id}/channels` enforce `workspace_id` scoping and reject cross-workspace access
- [ ] AC3 `PUT /brands/{id}/policy-configs/active` stores exactly one active policy config per brand
- [ ] AC4 policy config writes reject invalid `topic_type_targets` shapes defined by the V2 spec
- [ ] AC5 `GET /brands/{id}/policy-configs/active` and `POST /brands/{id}/state-snapshots` expose brand-level config read/write needed by P1-1
- [ ] AC6 V2 routes coexist with the legacy session API without regressing current tests

### Residual Obligations
- **Relevant OPEN Residuals**:
  - No explicit V2 residual tracker exists yet; treat the still-missing ingestion APIs as downstream work, not current-task scope
- **Current-Phase Carry-Forward Items To Re-check**:
  - Postgres-backed persistence still pending after this task
  - P1-2 ingestion endpoints depend on these master-data APIs and isolation rules
- **Resolved By This Task**:
  - Missing API surface for master-data read/write in the started P1-1 implementation
- **Deferred / Blocked**:
  - `workspace_members` persistence remains deferred because the current codebase has only header-based basic auth

### Contract Inventory
- Upstream contracts:
  - `docs/v2/dev_spec.md` workspace isolation and brand-policy rules
  - `docs/v2/development_tasks.md` P1-1 delivery and completion standard
- Downstream contracts:
  - future `source-syncs` / `data-imports` routes need brand/channel existence and scope validation
  - future decision engine needs active policy config and traceable state snapshot ids
- Files/interfaces with compatibility risk:
  - `app/api/routes/router.py` must keep legacy routes unchanged
  - `app/main.py` lifespan must continue starting the existing worker stack

### Test Requirements
- **Primary Test Files**:
  - `tests/unit/test_v2_master_data_service.py`
  - `tests/unit/test_v2_foundation_api.py`
  - `tests/unit/test_v2_auth.py`
- **Test Scenarios**:
  1. workspace creation succeeds without breaking legacy app startup
  2. workspace-scoped brand and channel creation succeed when headers match
  3. cross-workspace access to brand routes is rejected
  4. active policy config replacement keeps only the newest config active
  5. invalid `topic_type_targets` payloads are rejected
  6. state snapshot creation returns traceable ids within the correct brand/workspace scope
- **Test Target**:
  - unit + API-level tests only

---

## 2. Architecture Context

### System Position
- V2 foundation APIs are a thin HTTP layer over the existing in-memory master-data service
- Workspace isolation is enforced at request entry using header-derived principal context
- The API layer must prepare P1-2 ingestion and later decision-time lineage without requiring Postgres runtime yet

### Tech Stack
- Language/runtime: Python 3 + FastAPI + Pydantic
- Primary libraries/services: existing app router, `app.v2.auth`, `app.v2.foundation.*`
- Execution pattern: synchronous service methods called from async FastAPI handlers
- Key behavioral constraints:
  - every business row must be scoped by `workspace_id`
  - policy runtime semantics come only from `brand_policy_configs`
  - master-data APIs must not mutate legacy session workflow behavior

### Constraints
- Reuse current `MasterDataService` rather than building a second domain layer
- Preserve existing test and router conventions
- Keep persistence abstraction compatible with future Postgres store replacement

---

## 3. Technical Design

### 3.1 Module Structure

**Files to Create/Modify:**

| Path | NEW/MODIFY | Required Change | Linked AC |
|------|------------|-----------------|-----------|
| `app/v2/foundation/store.py` | MODIFY | add lookup/list helpers and slug uniqueness support needed by API | AC1-AC5 |
| `app/v2/foundation/service.py` | MODIFY | add policy target validation and lookup helpers | AC2-AC5 |
| `app/api/routes/router.py` | MODIFY | add V2 master-data routes and shared service accessors | AC1-AC6 |
| `app/main.py` | MODIFY | initialize shared V2 master-data store/service during app lifespan | AC1-AC6 |
| `app/models/schemas.py` | MODIFY | add V2 request/response schemas | AC1-AC5 |
| `tests/unit/test_v2_master_data_service.py` | MODIFY | extend service coverage for policy validation | AC3-AC4 |
| `tests/unit/test_v2_foundation_api.py` | NEW | add API tests for V2 master-data routes | AC1-AC6 |

### 3.2 Class & Interface Design

**Primary Service Extensions**:

- `MasterDataService.get_brand(...) -> BrandRecord`
- `MasterDataService.get_workspace(...) -> WorkspaceRecord`
- `MasterDataService.validate_policy_targets(topic_type_targets: dict[str, Any]) -> None`

**API Access Helpers**:

- `_get_v2_master_data_service(request: Request) -> MasterDataService`
- `_get_workspace_principal(request: Request) -> WorkspacePrincipal`
- `_ensure_brand_scope(principal, brand_id, service) -> BrandRecord`

### 3.3 Algorithm & Logic Flow

**Workspace bootstrap**

`POST /workspaces`
-> validate request body
-> create workspace
-> return canonical workspace payload

**Workspace-scoped brand write**

request headers
-> `resolve_workspace_principal(...)`
-> confirm brand/workspace match
-> execute service write
-> return persisted payload

**Policy config write**

request body
-> validate `topic_type_targets`
-> deactivate previous active config for same brand
-> persist new active config
-> return active config payload

### 3.4 Implementation Checklist
- [ ] Extend store/service for lookup helpers needed by API handlers
- [ ] Enforce `workspace_id` isolation on every brand-scoped route
- [ ] Validate `topic_type_targets` min/max ratio rules before saving
- [ ] Expose master-data routes on the existing router without changing legacy route behavior
- [ ] Add tests covering success, rejection, and active-policy replacement flows

**Error Classification Rules**:
- workspace or brand not found -> `404`
- missing or mismatched workspace auth headers -> `401/403` style API error payload
- invalid policy target ratios -> `422`

### 3.5 Error Handling Strategy

- Reuse existing `APIError` and `ErrorResponse` envelope for V2 route failures
- Map `WorkspaceAuthError` to a uniform client-facing auth error
- Map `MasterDataError` not-found and scope violations to deterministic API errors

---

## 4. Testing Strategy

### Required Commands

```bash
python3 -m pytest tests/unit/test_v2_master_data_service.py tests/unit/test_v2_foundation_api.py tests/unit/test_v2_auth.py tests/unit/test_v2_schema_contract.py tests/unit/test_v2_settings.py -q
```

### Required Scenario Mapping

- `AC1`: workspace API test
- `AC2`: brand/channel scope API tests
- `AC3`: active policy replacement service + API tests
- `AC4`: invalid `topic_type_targets` validation tests
- `AC5`: policy read + state snapshot API tests
- `AC6`: targeted regression signal from the existing V2 unit suite staying green

---

## 5. Progress Tracking Requirements

- Treat this task as a focused completion slice of V2 `P1-1`
- Next recommended task after completion: `V2 P1-2 数据入口与证据层`
