# Development Guide: P1-2 - 数据入口与证据层

> Generated: 2026-04-12
> Architect: dev-helper adapted for `docs/v2/development_tasks.md`
> Status: Ready for development
> Source: `docs/v2/development_tasks.md` §2.2, `docs/v2/dev_spec.md` §3.4, §9.1, §9.2, §9.7

## 1. Task Context

### Scope Boundary
- **Task ID**: `P1-2`
- **Task Name**: 数据入口与证据层
- **Phase**: V2 Phase 1
- **Dependencies**:
  - `P1-1` foundation 已具备：workspace/brand/channel/master-data API、frontend console shell、V2 auth
- **Task Goal**:
  - 让市场/竞品证据和品牌历史内容通过统一 ingestion contract 进入正式数据模型，并在控制台里提供可触发、可见状态的入口

### In Scope
- V2 schema/migration 增加：
  - `ingestion_runs`
  - `authors`
  - `topics`
  - `content_items`
  - `content_metrics_snapshots`
  - `comments`
- 后端实现：
  - `POST /brands/{id}/source-syncs`
  - `POST /brands/{id}/data-imports`
  - 统一 ingestion service，负责 dedupe、author/content/metrics 落库与 ingestion_run 状态汇总
- 前端实现：
  - 品牌详情页里的 source sync 触发入口
  - historical import 触发入口
  - ingestion status 卡片或占位反馈
- 测试：
  - schema contract
  - service 行为
  - API contract

### Out Of Scope
- 不实现真实 Chrome extension 浏览器侧代码；本轮只把 `xhs_extension_capture` 作为正式 adapter contract 接入后端
- 不实现 Topic Pool refresh、Agent、Decision、Publish、Performance、Evaluation
- 不实现真实 Postgres ingestion store 的全量生产级适配；允许先以 in-memory/runtime 跑通 API 契约，同时补齐 schema/migration 契约
- 不把 `/topic-pool` 页面伪装成 live ingestion 数据页，避免越过 phase 边界

### Required Deliverables
- Production:
  - `app/v2/db/schema.py`
  - `app/v2/db/migrations.py`
  - `app/v2/db/runner.py`
  - `app/v2/ingestion/*`
  - `app/api/routes/router.py`
  - `app/models/schemas.py`
  - frontend 品牌详情入口相关页面/组件/API types
- Tests:
  - `tests/unit/test_v2_schema_contract.py`
  - `tests/unit/test_v2_ingestion_service.py`
  - `tests/unit/test_v2_ingestion_api.py`
  - frontend 如有纯函数可补最小单测，否则以类型与构建通过为准
- Spec/Docs:
  - `instructions/P1-2_v2_guide.md`
  - `frontend/README.md` phase boundary 说明更新

### Acceptance Criteria
- [ ] AC1 `POST /brands/{id}/source-syncs` 创建一个 `ingestion_run`，返回 `{ingestion_run_id, entry_type=source_sync, status}`
- [ ] AC2 `source-syncs` payload 经统一 normalize 后可写入 `authors/content_items/content_metrics_snapshots`
- [ ] AC3 `POST /brands/{id}/data-imports` 实现 `historical_note_import_v1`，并按 spec required fields 校验
- [ ] AC4 `data-imports` 采用规范 dedupe 优先级：`platform_content_id` > normalized `source_url` > `content_hash`
- [ ] AC5 重复导入时不会生成重复 `content_items`，而是更新既有记录并追加/覆盖 metrics snapshot
- [ ] AC6 所有 ingestion 数据都遵守 `workspace_id + brand_id` 作用域隔离
- [ ] AC7 前端能在品牌详情入口触发 source sync / historical import，并看到与 formal ingestion contract 对应的状态反馈

### Test Requirements
- **Primary Test Files**:
  - `tests/unit/test_v2_schema_contract.py`
  - `tests/unit/test_v2_ingestion_service.py`
  - `tests/unit/test_v2_ingestion_api.py`
- **Test Scenarios**:
  1. schema 包含 P1-2 所需表和索引
  2. source sync 可创建 ingestion_run 并写入内容证据
  3. historical import required fields 校验正确
  4. historical import dedupe 优先级正确
  5. cross-workspace brand 访问被拒绝
  6. frontend API loader 不把 P1-2 surface 错标为 mock-only

## 2. Architecture Context

- `P1-1` 现有 `MasterDataService` 负责品牌与配置主数据，不适合直接承载 ingestion evidence
- `P1-2` 新增 `IngestionService` 更合适，原因：
  - write path 包含 normalize/dedupe/run-status 汇总
  - evidence tables 与主数据职责不同
  - 后续可独立切换到 Postgres-backed ingestion store
- API 仍沿用现有 V2 workspace auth 和 router error contract
- 前端入口优先放在品牌详情页，避免提前把 `/topic-pool` 做成 live integration

## 3. Technical Design

### 3.1 Files to Create/Modify

| Path | NEW/MODIFY | Intent |
|------|------------|--------|
| `app/v2/db/schema.py` | MODIFY | 增加 P1-2 evidence tables SQL builder/manifest |
| `app/v2/db/migrations.py` | MODIFY | 暴露 P1-2 migration step |
| `app/v2/db/runner.py` | MODIFY | 支持运行到 P1-2 migrations |
| `app/v2/ingestion/models.py` | NEW | typed records for ingestion runs/authors/content/comments |
| `app/v2/ingestion/store.py` | NEW | store protocol + in-memory implementation |
| `app/v2/ingestion/service.py` | NEW | normalize + dedupe + run lifecycle |
| `app/v2/ingestion/__init__.py` | NEW | exports |
| `app/models/schemas.py` | MODIFY | V2 ingestion request/response models |
| `app/api/routes/router.py` | MODIFY | add two P1-2 endpoints and error mapping |
| `frontend/src/lib/types.ts` | MODIFY | ingestion status types |
| `frontend/src/lib/api.ts` | MODIFY | source sync / historical import client helpers |
| `frontend/src/components/brand/*` | MODIFY/NEW | brand detail ingestion cards/forms |
| `frontend/README.md` | MODIFY | note the current runtime data-entry path and ingestion entry surfaces |
| `tests/unit/test_v2_schema_contract.py` | MODIFY | schema assertions |
| `tests/unit/test_v2_ingestion_service.py` | NEW | service behavior |
| `tests/unit/test_v2_ingestion_api.py` | NEW | API contract |

### 3.2 Implementation Strategy

1. Add normative P1-2 table SQL and migration manifest
2. Implement in-memory ingestion store/service first so API and tests can close the contract quickly
3. Add FastAPI endpoints with workspace auth and unified error mapping
4. Expose ingestion trigger UI in brand detail panel, not later-phase route pages
5. Validate with targeted unit/API tests

### 3.3 Dedupe Rules

- For source sync:
  - prefer `platform + platform_content_id`
  - fallback to normalized `source_url`
  - fallback to `content_hash(title + body_text + published_at + author_handle)`
- For historical import:
  - exact same priority as `docs/v2/dev_spec.md` §historical_note_import_v1
- Store must preserve enough metadata to explain which dedupe key matched

### 3.4 Frontend Boundaries

- `P1-2` originally introduced live ingestion controls on `/brands/[id]`; in the current shipped IA, the operator-facing runtime path is split across `/data-sources` and `/data-processing`
- `/topic-pool` remains mock fallback until `P1-3`
- UI should describe status as `accepted`, imported counts, and latest run summary rather than pretending Topic Pool has already refreshed

## 4. Validation Notes

- This task is considered complete when the evidence ingestion contract is usable end-to-end for one brand in the existing V2 shell
- If Postgres-backed ingestion persistence is not implemented in this turn, the residual must be explicit; but schema/migration contract and in-memory API usability must still be delivered
