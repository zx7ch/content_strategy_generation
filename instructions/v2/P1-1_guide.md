# Development Guide: P1-1 - 基础与主数据

> Generated: 2026-04-12
> Architect: dev-helper adaptation for V2
> Status: Ready for development
> Source: [docs/v2/dev_spec.md](/Users/czx/Documents/agentic/content_strategy_generation/docs/v2/dev_spec.md), [docs/v2/development_tasks.md](/Users/czx/Documents/agentic/content_strategy_generation/docs/v2/development_tasks.md)

## 1. Task Context

### Scope Boundary
- Task ID: `P1-1`
- Task Name: `基础与主数据`
- Phase: `Phase 1: 可上线闭环`
- Goal:
  - establish the V2 foundation layer for `workspace` / `brand`-scoped master data
  - add a normative Postgres schema contract for the P1-1 tables
  - add basic `workspace`-scoped request identity handling for future V2 APIs
  - provide a deterministic master-data service layer for brand config read/write and lineage tracking

### In Scope
- Extend runtime settings with V2 foundation config:
  - `POSTGRES_DSN`
  - V2 auth/scope headers and auth switch
- Create a new isolated V2 package under `app/v2/`
- Add normative P1-1 Postgres schema and migration manifest for:
  - `workspaces`
  - `workspace_members`
  - `brands`
  - `brand_channels`
  - `brand_state_snapshots`
  - `brand_policy_configs`
  - `topic_pool_items`
  - `decision_batches`
  - `decision_batch_items`
  - `candidate_set_snapshots`
  - `publish_records`
  - `performance_snapshots`
  - `feedback_events`
  - `scorer_configs`
- Add basic request identity / isolation helpers for:
  - `workspace_id`
  - `user_id`
  - optional auth token check
- Add master-data domain models and an in-memory store-backed service for:
  - workspace creation
  - brand creation
  - brand channel registration
  - active policy config read/write
  - brand state snapshot creation
- Add unit tests for settings, auth context, schema contract, and service lineage rules

### Out Of Scope
- Real Postgres connection pooling or repository adapter
- Alembic or external migration tooling integration
- Full V2 API route implementation
- Decision engine, ingestion, agent logic, scorer logic, evaluation logic
- Production-grade user auth, password auth, OAuth, or RBAC enforcement beyond basic scoped identity extraction

### Required Deliverables
- Production:
  - `app/v2/` foundation modules
  - P1-1 schema contract and migration manifest
  - master-data service layer with deterministic lineage checks
- Tests:
  - unit tests for new settings fields
  - unit tests for auth/scope handling
  - unit tests for schema contract presence
  - unit tests for master-data service behaviors
- Docs/Instructions:
  - this guide only; no spec rewrite in this task

### Acceptance Criteria
- [ ] AC1 V2 foundation settings support Postgres DSN plus `workspace`-scoped auth context configuration without breaking existing settings behavior.
- [ ] AC2 A normative P1-1 Postgres schema/migration contract exists for the required master-data and decision-loop foundation tables.
- [ ] AC3 `workspace_id + brand_id` isolation is enforced in the master-data service layer for brand-owned data operations.
- [ ] AC4 `brand_policy_config_id` and `brand_state_snapshot_id` are traceable through the new service APIs and domain records.
- [ ] AC5 Basic request identity extraction supports `workspace` and `user` context for future V2 APIs.
- [ ] AC6 Unit tests cover success, boundary, and failure paths for settings, auth, schema contract, and master-data service behavior.

### Residual Obligations
- Current-task carry-forward:
  - real Postgres adapter remains for a later task
  - V2 CRUD APIs remain for a later task
- Closure target for this task:
  - foundation contracts and service logic must be executable and testable without a live Postgres dependency

### Contract Inventory
- Upstream contracts:
  - [docs/v2/dev_spec.md](/Users/czx/Documents/agentic/content_strategy_generation/docs/v2/dev_spec.md) §2.3, §3.2, §3.3, §3.5, §5.4
  - [docs/v2/development_tasks.md](/Users/czx/Documents/agentic/content_strategy_generation/docs/v2/development_tasks.md) `P1-1`
- Downstream contracts:
  - `P1-2` ingestion tables and source sync flows
  - `P1-4` decision engine lineage references

### Test Requirements
- Layer: `unit`
- Test files:
  - `tests/unit/test_v2_settings.py`
  - `tests/unit/test_v2_auth.py`
  - `tests/unit/test_v2_schema_contract.py`
  - `tests/unit/test_v2_master_data_service.py`
- Required scenarios:
  1. V2 settings defaults and env override behavior
  2. request identity extraction success and failure paths
  3. schema contract contains all required P1-1 tables and key lineage columns
  4. master-data service enforces `workspace_id + brand_id` scope and active policy/state lineage

## 2. Architecture Context

### System Position
- This task creates the V2 foundation layer in parallel to the existing session/generation runtime.
- New code must live under `app/v2/` to avoid coupling the legacy system to unfinished V2 contracts.
- The service layer must be deterministic and storage-adapter-friendly so later tasks can swap in a real Postgres repository.

### Constraints
- Do not break existing `app.main` startup or legacy session APIs.
- Do not introduce mandatory runtime dependencies on packages that are not already available for unit tests.
- Keep public interfaces narrow and typed.
- Prefer storage abstraction + in-memory implementation over fake direct Postgres code that cannot be tested.

## 3. Technical Design

### 3.1 Module Structure

Files to create/modify:

| Path | Action | Intent | Linked AC |
| --- | --- | --- | --- |
| `app/config.py` | MODIFY | Add V2 foundation settings fields | AC1 |
| `app/v2/__init__.py` | NEW | V2 package root | AC2 |
| `app/v2/auth.py` | NEW | Request identity extraction and `workspace` scope auth helpers | AC5 |
| `app/v2/db/__init__.py` | NEW | DB package export | AC2 |
| `app/v2/db/migrations.py` | NEW | Migration manifest types and ordered migration list | AC2 |
| `app/v2/db/schema.py` | NEW | Normative Postgres DDL for P1-1 foundation tables | AC2 |
| `app/v2/foundation/__init__.py` | NEW | Foundation package export | AC3 |
| `app/v2/foundation/models.py` | NEW | Typed records for workspace/brand/policy/state domain | AC3, AC4 |
| `app/v2/foundation/store.py` | NEW | Store protocol + in-memory implementation | AC3, AC4 |
| `app/v2/foundation/service.py` | NEW | Deterministic master-data service layer | AC3, AC4 |
| `tests/unit/test_v2_settings.py` | NEW | Settings tests | AC1 |
| `tests/unit/test_v2_auth.py` | NEW | Auth/scope tests | AC5 |
| `tests/unit/test_v2_schema_contract.py` | NEW | Schema contract tests | AC2 |
| `tests/unit/test_v2_master_data_service.py` | NEW | Service behavior tests | AC3, AC4 |

### 3.2 Interface Design

Primary public interfaces:

- `resolve_workspace_principal(...)` in `app/v2/auth.py`
- `build_p1_1_schema_sql()` and migration manifest accessors in `app/v2/db/`
- `MasterDataService` in `app/v2/foundation/service.py`

Expected behaviors:

- `resolve_workspace_principal(...)`
  - extracts `workspace_id` and `user_id` from configured headers
  - optionally validates an auth token when auth is enabled
  - raises a typed auth error on missing scope headers or invalid token
- `MasterDataService`
  - creates workspaces/brands/channels in a deterministic store
  - ensures brand-owned records always resolve to the same `workspace_id`
  - upserts one active policy per brand
  - records brand state snapshots with lineage ids

### 3.3 Algorithm & Logic Flow

Master-data flow:

1. validate request/workspace identity
2. create or load workspace-scoped entities
3. create brand under one `workspace`
4. register brand channel under the same `workspace` and `brand`
5. upsert active policy config for that brand
6. create brand state snapshot for that brand
7. return typed records with lineage ids

Policy upsert rules:

1. a new policy config is created with a generated `brand_policy_config_id`
2. existing active policy for the same brand becomes inactive
3. only one active policy remains per brand in the in-memory store

State snapshot rules:

1. every snapshot gets a generated `brand_state_snapshot_id`
2. snapshots are appended, not overwritten
3. snapshots are tied to the same `workspace_id + brand_id`

### 3.4 Error Handling

Implement typed errors for:

- missing workspace scope
- missing user scope when required
- invalid auth token
- cross-workspace brand access
- missing brand for policy/state writes

Tests must assert these errors as contract behavior.

## 4. Test Strategy

### Required Scenarios

`tests/unit/test_v2_settings.py`
- defaults include disabled auth and empty Postgres DSN
- env overrides set V2 settings without breaking legacy settings

`tests/unit/test_v2_auth.py`
- success: workspace and user context extracted
- failure: missing workspace header
- failure: token required but absent or invalid

`tests/unit/test_v2_schema_contract.py`
- migration manifest is ordered and non-empty
- DDL includes every required P1-1 table
- DDL includes `workspace_id`, `brand_id`, `brand_policy_config_id`, `brand_state_snapshot_id` where required

`tests/unit/test_v2_master_data_service.py`
- workspace creation
- brand creation under workspace
- channel registration under matching workspace/brand
- policy upsert replaces previous active policy
- state snapshots append with lineage ids
- cross-workspace access raises an error

### Quality Gate
- unit-test coverage target for new core logic: `>= 80%`

## 5. Implementation Notes

- Keep this task isolated under `app/v2/`
- Do not wire new V2 routes into `app.main` yet
- Do not mutate legacy session/generation storage paths
- If a design choice is needed, prefer deterministic pure Python logic over DB-specific behavior that cannot be tested in this task
