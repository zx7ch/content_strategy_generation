# Development Guide: P1-2 - Session 状态管理

> Generated: 2026-04-12
> Architect: implementation skill
> Status: Ready for validation
> Source: `dev_spec.md` §1.5.7, §5.2, §9.4.1, §10.4.5, §11.2, §11.3 `P1-2`, `docs/testing_strategy.md` `ts-p1-2`

## 1. Task Context

### Scope Boundary
- **Task ID**: `P1-2`
- **Task Name**: Session 状态管理
- **Phase**: Phase 1 基础设施
- **Dependencies**:
  - `P0-4` 已完成，pytest 基座与分层测试能力可直接复用
- **Task Goal**:
  - 在 `SessionManager` / `SessionDataStore` 上落地双存储分离、轻量 checkpoint、生命周期判定和 reindex 补偿状态，作为后续 `RAG`、`JobStore`、`Workflow`、`API/SSE` 的基础契约

### In Scope
- 对齐 `sessions` 表 schema 到当前 spec：`lifecycle_state`、`alive_until`、`purge_after`、`pause_requested`、`pause_requested_at`、`reindex_state`、`reindex_attempts`
- `SessionManager` 提供 create/get/update/delete/list、生命周期刷新、用户活动刷新、checkpoint 接入和 reindex 补偿状态管理
- `SessionDataStore` 负责把 `spider_notes` / `strategy` / `proposals` / `generated_notes` 外置到业务表，`sessions` 行仅保留引用
- 保留旧 `rag_*` 包装方法，兼容后续尚未完全迁移的调用点
- 通过 `tests/unit/test_session_state.py` 和 `tests/integration/test_checkpoint_recovery.py` 锁定核心行为

### Out Of Scope
- 不重构 `JobStore` 的完整实现，只消费其 `queued/retrying/running/paused/cancelled` 语义
- 不在本任务内扩展 API 路由、SSE streaming 或 worker 调度逻辑
- 不实现真实 Chroma 重建 worker，只负责写入 `pending/deadletter/ok` 补偿状态
- 不新增对外公开生命周期枚举；`pause_requested` 仍是内部控制位

### Required Deliverables
- Production:
  - `app/memory/session_state.py`
  - `app/memory/session_data_store.py`
  - `app/models/session.py`
  - `app/models/schemas.py` 中相关 response 字段与 session 契约保持一致
- Tests:
  - `tests/unit/test_session_state.py`
  - `tests/integration/test_checkpoint_recovery.py`
- Spec/Docs:
  - `instructions/P1-2_guide.md` 作为本轮实现与验证真源

### Acceptance Criteria
- [ ] AC1 create / update / get / delete / list 行为正确，`create_session()` 具备 UPSERT 幂等语义
- [ ] AC2 生命周期按 spec 判定：`queued/retrying/running` 使 session 保持 `alive`；`paused` 不算 active；`pause_requested + no active jobs => frozen`；无 active job 且超 10d => `purged`
- [ ] AC3 `purged` 转换时必须取消未完成 job，避免残留 `queued/retrying/running/paused`
- [ ] AC4 checkpoint 仅保存轻量引用，不落完整 `spider_notes/content_strategy/generated_notes`
- [ ] AC5 业务大对象外置到 `SessionDataStore`，双存储 round-trip 一致
- [ ] AC6 `reindex_state/reindex_attempts` 可追踪、可恢复，并保留旧 `rag_*` wrapper 兼容
- [ ] AC7 生命周期事件与 reindex 关键事件能被后续日志/SSE 层消费

### Residual Obligations
- **Relevant OPEN Residuals**:
  - 无。`dev_spec.md` §11.4 中 `P1-2` 唯一 residual `RES-P1-2-001` 已标记 `DONE`
- **Current-Phase Carry-Forward Items To Re-check**:
  - `purged` 后 job queue 与 lifecycle 一致性不能回归
  - `reindex_state` 不能参与 lifecycle 判定
  - `pause_requested` 恢复路径必须被 `update_activity()` 正确清理
- **Resolved By This Task**:
  - 轻量 checkpoint / 双存储 / 生命周期 / reindex 补偿主契约
- **Deferred / Blocked**:
  - 无新增 defer；若验证发现偏差，必须在 `dev_spec.md` §11.4 新登记 residual

### Contract Inventory
- **Upstream contracts**:
  - `dev_spec.md` §1.5.7 Session Manager
  - `dev_spec.md` §5.2 Session 生命周期与 API 调用关系
  - `dev_spec.md` §9.4.1 SQLite Schema
  - `dev_spec.md` §10.4.5 任务队列错误处理
- **Downstream contracts**:
  - `JobStore` 读取 `sessions.lifecycle_state` 和 jobs 状态来决定是否可执行
  - Workflow checkpoint 恢复依赖 `SessionManager.get_checkpointer()` 与轻量 state
  - API / SSE / logging 依赖 lifecycle/reindex 时间戳、状态和事件记录
- **Compatibility risks**:
  - 旧库缺列时必须通过补列迁移兼容
  - 旧 `rag_sync_status` / `rag_reindex_attempts` 调用点需由 wrapper 平滑过渡

### Test Requirements
- **Primary Test Files**:
  - `tests/unit/test_session_state.py`
  - `tests/integration/test_checkpoint_recovery.py`
- **Test Scenarios**:
  1. create / update / get / delete 正常
  2. UPSERT 幂等
  3. `alive/frozen/purged` 生命周期判定正确
  4. `active_jobs` / `paused_jobs` 对生命周期的影响正确
  5. checkpoint 仅保存轻量引用
  6. `SessionDataStore` 与主 session 存储一致
  7. Chroma 失败时补偿状态可追踪
  8. strategy 后可恢复到 generation
  9. checkpoint 不保存完整大对象
- **Test Target**:
  - 锁定 session 生命周期、双存储一致性、轻状态设计和 checkpoint 恢复协作

---

## 2. Architecture Context

### System Position

`SessionManager` 位于存储层中心，连接四个职责面：

1. `sessions` 表：保存轻量 checkpoint 元数据与生命周期字段
2. `SessionDataStore`：保存大对象业务数据并按需回填
3. `AsyncSqliteSaver`：作为 LangGraph checkpoint backend
4. `jobs` 表：通过 active job 统计和 purge cancel 语义影响生命周期

### Tech Stack
- Language/runtime: Python 3.11 + async / `aiosqlite`
- Primary libraries/services:
  - `langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver`
  - `pydantic` session models
  - SQLite WAL 模式
- Execution pattern:
  - async storage manager + idempotent UPSERT + lazy business-data hydration
- Key behavioral constraints:
  - `sessions` 行只存轻量引用
  - 先 SQLite、后 Chroma 的最终一致性
  - `retrying` 即使未到 `not_before` 也算 active
  - `paused` 不算 active

### Constraints
- 兼容已有 SQLite 数据库，禁止 destructive migration
- `purged` 后不允许恢复
- 生命周期只能由真实用户动作刷新 `last_user_activity_at`
- `reindex_state` 是补偿状态，不得阻止冻结/清理

---

## 3. Technical Design

### 3.1 Module Structure

**Files to Create/Modify**

```text
app/
├── memory/
│   ├── session_state.py
│   └── session_data_store.py
├── models/
│   ├── session.py
│   └── schemas.py
tests/
├── unit/test_session_state.py
└── integration/test_checkpoint_recovery.py
```

**Per-file Change Intent**

| Path | NEW/MODIFY | Required Change | Linked AC / Residual |
|------|------------|-----------------|----------------------|
| `app/memory/session_state.py` | MODIFY | 持久化 schema、生命周期判定、purge job cancel、reindex 状态机、checkpointer 接入 | AC1-AC7 |
| `app/memory/session_data_store.py` | MODIFY | 外置 spider/strategy/proposal/generation 数据并保持 UPSERT 幂等 | AC1, AC5 |
| `app/models/session.py` | MODIFY | 暴露 `pause_requested`、`reindex_state`、`reindex_attempts` 等运行时字段 | AC2, AC6 |
| `app/models/schemas.py` | MODIFY | 让 API response/session schema 与运行时字段命名对齐 | AC1, AC6 |
| `tests/unit/test_session_state.py` | MODIFY | 覆盖 CRUD、生命周期、purge cancel、reindex、wrapper、轻量 checkpoint | AC1-AC7 |
| `tests/integration/test_checkpoint_recovery.py` | MODIFY | 验证 checkpoint 恢复与轻量 state 协作 | AC4, AC5 |

### 3.2 Class & Interface Design

**Primary Class Or Entry Point**: `SessionManager`

```python
class SessionManager:
    async def create_session(...) -> Session: ...
    async def get_session(session_id: str) -> Session | None: ...
    async def update_session(session_id: str, **fields: Any) -> Session | None: ...
    async def refresh_lifecycle_state(session_id: str) -> SessionLifecycleState | None: ...
    async def update_activity(session_id: str) -> bool: ...
    async def touch_user_activity(session_id: str) -> bool: ...
    async def save_spider_results_with_consistency(...) -> list[str]: ...
    async def mark_reindex_pending(session_id: str) -> bool: ...
    async def mark_reindex_result(session_id: str, success: bool) -> bool: ...
    async def get_reindex_status(session_id: str) -> dict[str, Any] | None: ...
    def get_checkpointer(self) -> AsyncSqliteSaver: ...
```

**Supporting Store**: `SessionDataStore`

```python
class SessionDataStore:
    async def save_spider_results(...) -> list[str]: ...
    async def get_spider_results(...) -> list[SpiderNote]: ...
    async def save_strategy(...) -> str: ...
    async def get_strategy(...) -> tuple[ContentStrategy | None, PlatformPreference | None, str | None]: ...
    async def save_proposals(...) -> list[str]: ...
    async def get_proposals(...) -> list[Proposal]: ...
    async def save_generated_notes(...) -> list[str]: ...
    async def get_generated_notes(...) -> list[GeneratedNote]: ...
```

### 3.3 Algorithm & Logic Flow

**Lifecycle Flow**

```text
create_session
  -> initialize lightweight session row
  -> set alive_until/purge_after/reindex defaults

get_session
  -> refresh_lifecycle_state
  -> read lightweight row
  -> hydrate heavy payloads from SessionDataStore using stored ids
  -> return Session model

refresh_lifecycle_state
  -> read last_user_activity_at + pause_requested + current lifecycle
  -> count active jobs (queued/retrying/running only)
  -> compute alive/frozen/purged
  -> if purged: cancel unfinished jobs
  -> update timestamps/events/logs
```

**Consistency Flow**

```text
save_spider_results_with_consistency
  -> write spider data to SQLite business table
  -> write note ids back into sessions row
  -> call rag_indexer if provided
  -> success => reindex_state=ok, attempts=0
  -> failure => keep SQLite data, set reindex_state=pending/deadletter
```

### 3.4 Implementation Checklist
- [ ] `create_session()` 初始化所有生命周期/补偿字段
- [ ] `_ensure_session_columns()` 兼容旧 DB 列缺失与旧 `rag_*` 字段迁移
- [ ] `_count_active_jobs()` 仅统计 `queued/retrying/running`
- [ ] `refresh_lifecycle_state()` 在 `purged` 时取消未完成 job，并写 lifecycle 事件
- [ ] `update_activity()` 恢复 `alive` 且清空 `pause_requested`
- [ ] `touch_user_activity()` 只刷新用户活动时间，不强制 lifecycle 迁移
- [ ] `SessionDataStore` 所有写入保持 UPSERT 幂等
- [ ] `mark_reindex_pending/result/status` 与 legacy wrapper 语义一致
- [ ] unit/integration tests 覆盖所有 AC 与测试矩阵场景

### 3.5 Error Handling Strategy

**Failure Mapping**

```text
SQLite write failure
  -> raise upstream

Chroma/reindex failure after SQLite success
  -> keep business data
  -> mark reindex pending / deadletter
  -> emit reindex log events

purged transition
  -> cancel unfinished jobs
  -> emit session_purged event/log
```

**Rules**
- 不吞掉主存储 SQLite 的失败
- 只在 Chroma 这类可补偿路径上使用最终一致性
- `PURGED` 为终态，后续 `refresh_lifecycle_state()` 直接返回 `PURGED`

---

## 4. Test Strategy

### 4.1 Layer Plan

| Layer | File | Why |
|-------|------|-----|
| `unit` | `tests/unit/test_session_state.py` | 锁定状态机、补偿语义、lightweight checkpoint、双存储 round-trip |
| `integration` | `tests/integration/test_checkpoint_recovery.py` | 验证 Workflow + checkpointer + SessionManager 的恢复协作 |

### 4.2 Required Scenarios

1. `create_session()` 和 `update_session()` 正确读写 session 字段
2. `create_session()` 重复调用只保留单行并以最新输入覆盖可覆盖字段
3. 24h/10d 生命周期边界判定正确
4. active jobs 让 session 继续 `alive`
5. paused jobs 不阻止 `purged`
6. `purged` 触发时 jobs 被统一 `cancelled`
7. checkpoint payload 不包含大对象
8. `SessionDataStore` 能从 ids 正确回填业务对象
9. reindex `pending -> deadletter -> ok` 状态机正确
10. legacy `rag_*` wrapper 返回新字段语义
11. checkpoint interrupt/resume 与一次性执行结果一致

### 4.3 Execution Notes
- 按 `docs/testing_rules.md`，本任务默认交付 `unit`，但因为 `ts-p1-2` 明确列出 `integration` 协作验证，所以需要一并执行对应 checkpoint integration
- 使用内存 SQLite 或 `tmp_path` 文件库，不依赖真实网络/真实 Chroma
- 断言优先落在状态、持久化结果和可观察行为，而不是内部实现细节

---

## 5. Validation Notes

- 当前 `dev_spec.md` §11.2 已将 `P1-2` 标记为 `✅ D`，本 guide 的主要用途是为重新验证或回归补强提供精确契约
- 若测试结果全部通过，则本轮不应强行重做 `P1-2` 生产代码；只需记录“实现已满足 guide”
- 若发现回归，优先最小化修复 `session_state.py` / `session_data_store.py` / tests，不扩展到无关模块
