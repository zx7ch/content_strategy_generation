# Development Guide: P1-6 - SQLite 持久任务队列

> Generated: 2026-03-15
> Architect: implementation skill
> Status: Ready for development
> Source: `dev_spec.md` §9.2/§9.3, `docs/testing_strategy.md` `ts-p1-6`

## 1. Task Context

### Scope Boundary
- **Task ID**: `P1-6`
- **Task Name**: SQLite 持久任务队列
- **Phase**: Phase 1 基础设施
- **Dependencies**:
  - `P1-2` 已恢复到 `Done`，Session 生命周期字段以 `pause_requested` / `reindex_state` 为真源
  - `P1-4` 已完成，可直接使用配置中的 lease/retry 参数

### Acceptance Criteria
- [ ] 入队、抢占、重试、恢复和幂等执行语义正确
- [ ] 同 session 仅一个 `running` job
- [ ] API 能以任务形式触发 `strategy/generate`
- [ ] JobStore 自建的 `sessions` schema 与当前 Session 真源字段一致

### Test Requirements
- **Primary Test Files**:
  - `tests/unit/test_job_store.py`
  - `tests/integration/test_job_worker.py`
- **Scenarios**:
  1. `enqueue()` 支持 `Idempotency-Key` 去重
  2. `lease_one()` 只抢占一个可运行任务
  3. `lease_expires_at` / 恢复逻辑正确
  4. 同一 session 不出现多个 `running` job
  5. worker / API 基础协作链路保持可用
  6. JobStore 先初始化数据库时，`sessions` 表字段仍对齐当前生命周期规范

## 2. Architecture Context

### System Position
- `JobStore` 是 jobs 队列表与事件表的 SQLite 持久化入口
- 它会在队列模块先接触 DB 时兜底创建 `sessions` 表，因此这里的 schema 必须和 `SessionManager` 对齐
- `JobWorker`、`router.py`、`Orchestrator` 都依赖 `jobs` + `sessions.lifecycle_state`

### Constraints
- 不在本任务里改动 generation / orchestrator 主流程
- 优先做 schema 对齐和兼容，不做无关重构
- 若存在旧字段，允许保留；但新建库和 bootstrap 逻辑必须使用当前规范字段

## 3. Technical Design

### 3.1 Files to Modify
- `app/memory/job_store.py`
- `tests/unit/test_job_store.py`

### 3.2 Required Changes

1. `JobStore._init_tables()` 中 `sessions` 表定义改为与 `SessionManager` 当前字段一致：
   - 删除 bootstrap 中的 `freeze_until`
   - 使用 `pause_requested`, `pause_requested_at`
   - 使用 `reindex_state`, `reindex_attempts`
2. 保持 `jobs` / `session_events` 表逻辑不变
3. 不要求在 `JobStore` 中复制完整的 migration 逻辑；但 bootstrap 出来的新库不能再落旧字段名
4. 补测试锁定：
   - JobStore 先建库时，`sessions` 列包含新字段
   - 不包含旧字段 `freeze_until`

## 4. Testing Strategy

### 4.1 Test Layer

| Level | File | Focus |
|-------|------|-------|
| Unit | `tests/unit/test_job_store.py` | schema bootstrap、入队、抢占、恢复 |
| Integration | `tests/integration/test_job_worker.py` | worker + API 基础链路不回归 |

### 4.2 Must Implement
1. `JobStore` 初始化后 `sessions` 表列包含 `pause_requested`, `pause_requested_at`, `reindex_state`, `reindex_attempts`
2. `sessions` 表不再由 JobStore bootstrap 出 `freeze_until`
3. 现有 `enqueue/lease/recover/pause/resume` 语义不退化
