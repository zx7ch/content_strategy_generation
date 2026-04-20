# Development Guide: P5-2 - 集成测试

> Generated: 2026-03-21
> Architect: implementation skill
> Status: Ready for development
> Source: `dev_spec.md` §9.2/§9.3/§9.4, `docs/testing_strategy.md` `ts-p5-2`

## 1. Task Context

### Scope Boundary
- **Task ID**: `P5-2`
- **Task Name**: 集成测试
- **Phase**: Phase 5 测试交付
- **Dependencies**:
  - `P5-1` 已完成，unit 回归与 residual 清单已收口
  - `P4-4` 已完成，可直接基于现有 queue / session / SSE / workflow 协作链路补 integration 证据
- **Task Goal**:
  - 用真实内部组件协作补齐 unit 难以证明的状态一致性、补偿链路和 SSE 生命周期事件，优先关闭当前唯一相关 `OPEN` residual

### In Scope
- 为 `session_frozen/session_purged` 生命周期变化补齐可写入并被 SSE replay/live 消费的真实事件路径
- 新增 `tests/integration/test_sse_replay.py`，锁定 replay、`Last-Event-ID`、live 衔接和 lifecycle 事件可观察性
- 新增 `tests/integration/test_reindex_compensation.py`，锁定 pending / success / deadletter / 主流程降级返回
- 扩充 `tests/integration/test_job_worker.py`，验证 `purged` 与 job queue 的跨模块一致性
- 视需要补强 `tests/integration/test_checkpoint_recovery.py` 的额外中断点恢复场景

### Out Of Scope
- 不承担真实 HTTP 入口和端到端并发验证，这属于 `P5-3`
- 不接真实 Spider / LLM / RAG 外部依赖
- 不做与 integration 证据无关的大规模重构

### Required Deliverables
- Production: `session_state.py` 中生命周期事件持久化的最小修复
- Tests: `tests/integration/` 新增或补强 `test_sse_replay.py`、`test_reindex_compensation.py`、`test_job_worker.py`、必要时 `test_checkpoint_recovery.py`
- Spec/Docs: `dev_spec.md` §9.2 / §9.4 进度与 residual 同步

### Acceptance Criteria (from dev_spec.md §9.3 + relevant residuals)
- [ ] AC1 `session_frozen/session_purged` 生命周期变化会写入 `session_events`，并能被 `_event_stream()` replay/live 消费
- [ ] AC2 `purged` 触发后，跨 `SessionManager` / `JobStore` 的未完成 jobs 统一 `cancelled`，无残留 active jobs
- [ ] AC3 `reindex` 补偿链路具备 pending / success / deadletter 的 integration 证据，且主流程在 pending 状态下仍返回业务结果
- [ ] AC4 checkpoint recovery 至少有一条真实内部组件协作链路被锁定

### Residual Obligations (from dev_spec.md §9.4)
- **Relevant OPEN Residuals**:
  - `RES-P4-3-001`: SSE 缺少 `session_frozen` / `session_purged` 生命周期事件发出路径
- **Current-Phase Carry-Forward Items To Re-check**:
  - 若 SSE lifecycle 事件仍无法在 replay/live 中观察，必须保留 `RES-P4-3-001`
  - 若 reindex compensation 只在 unit 层成立、integration 仍有缺口，需登记新 residual
- **Resolved By This Task**:
  - 预期关闭 `RES-P4-3-001`
- **Deferred / Blocked**:
  - API 入口级 lifecycle 可见性属于 `P5-3` 的 E2E 证据，不在本轮关闭

### Contract Inventory
- Upstream contracts: `dev_spec.md` §4.2 lifecycle、§4.3.7 SSE、§7.4.1 schema、§8.4.4 queue handling、§10 观测/告警窗口定义
- Downstream contracts: `P5-3` 的真实 API / SSE E2E 验证
- Files/interfaces with compatibility risk: `SessionManager.refresh_lifecycle_state()`, `router._event_stream()`, `JobStore.session_events`, checkpoint resume behavior

### Test Requirements (from docs/testing_strategy.md §12.x)
- **Primary Test Files**:
  - `tests/integration/test_sse_replay.py`
  - `tests/integration/test_reindex_compensation.py`
  - `tests/integration/test_job_worker.py`
  - `tests/integration/test_checkpoint_recovery.py`
- **Test Scenarios**:
  1. 事件落库顺序正确，`Last-Event-ID` replay 正确，replay 后无重连即可进入 live 流
  2. `session_frozen/session_purged` 生命周期事件可被 SSE 客户端观察
  3. Chroma 写失败后 session 标记 `pending`，补偿成功恢复为 `ok`，连续失败进入 `deadletter`
  4. `purged` 触发后 jobs 统一 `cancelled`
  5. checkpoint 在不同 interrupt 点恢复后结果与一次性执行一致
- **Test Target**: 锁定跨模块状态一致性、事件序列与故障降级能力，而不是单模块内部细节

---

## 2. Architecture Context

### System Position
```text
SessionManager <-> SQLite sessions/session_events/jobs
            \-> lifecycle transitions + reindex compensation
router._event_stream() -> session_events replay/live SSE
JobStore / JobWorker -> queue state transitions and cancellation semantics
LangGraph workflow -> checkpoint interrupt/resume
```

### Tech Stack
- Language/runtime: Python 3.10 + FastAPI + aiosqlite + pytest + LangGraph
- Primary libraries/services: `fastapi`, `aiosqlite`, `langgraph`, project SQLite stores
- Execution pattern: real internal components with fake external dependencies
- Key behavioral constraints:
  - integration 层允许真实 SQLite 和内部组件协作
  - 不得使用真实网络或真实 LLM/Spider/RAG
  - 关键断言必须落在持久化状态、事件序列或恢复结果上

### Constraints
- 生命周期事件若要进入 SSE replay/live，必须落到 `session_events` 持久层，而不是只打日志
- reindex compensation 要基于现有 `SessionManager` 语义实现，不单独发明新的补偿状态机
- checkpoint tests 必须保持轻量，不把大量业务 JSON 塞进 checkpoint

---

## 3. Technical Design

### 3.1 Module Structure

**Files to Create/Modify:**
```text
app/memory/session_state.py                 MODIFY
tests/integration/test_sse_replay.py        NEW
tests/integration/test_reindex_compensation.py NEW
tests/integration/test_job_worker.py        MODIFY
tests/integration/test_checkpoint_recovery.py MODIFY
```

**Per-file Change Intent**:
| Path | NEW/MODIFY | Required Change | Linked AC / Residual |
|------|------------|-----------------|----------------------|
| `app/memory/session_state.py` | `MODIFY` | lifecycle 状态变化时写入 `session_events`；保持 `purged` job cleanup 事务语义 | `AC1`, `AC2`, `RES-P4-3-001` |
| `tests/integration/test_sse_replay.py` | `NEW` | 锁定 replay/live 衔接、`Last-Event-ID`、lifecycle 事件可观察性 | `AC1`, `RES-P4-3-001` |
| `tests/integration/test_reindex_compensation.py` | `NEW` | 锁定 pending / success / deadletter / 主流程降级返回 | `AC3` |
| `tests/integration/test_job_worker.py` | `MODIFY` | 增加 `purged` 后 jobs 统一 `cancelled`、worker 不再消费的集成场景 | `AC2` |
| `tests/integration/test_checkpoint_recovery.py` | `MODIFY` | 补一个额外 interrupt 点恢复场景 | `AC4` |

### 3.2 Class & Interface Design

**Primary Entry Points**:
- `SessionManager.refresh_lifecycle_state()`
- `router._event_stream()`
- `SessionManager.save_spider_results_with_consistency()`
- `JobWorker.run_once()`
- `create_workflow(...).ainvoke(...)`

Expected public behavior:
- lifecycle 状态从 `alive -> frozen/purged` 时，会写出同名 `session_events`
- `_event_stream()` 能 replay 这些生命周期事件，并在连接保持打开时收到 live 事件
- reindex 失败不回滚 SQLite 主数据写入，只把 session 标记为 `pending`
- `purged` 后 worker 不会继续消费该 session 的未完成 jobs

### 3.3 Algorithm & Logic Flow

**Core Flow**
```text
refresh_lifecycle_state()
  -> compute next lifecycle
  -> if state changed: persist lifecycle fields
  -> append session_frozen/session_purged event into session_events
  -> for purged: cancel unfinished jobs

_event_stream()
  -> replay persisted events after Last-Event-ID
  -> keep polling same table for new lifecycle/task events

save_spider_results_with_consistency()
  -> persist spider data
  -> if rag index fails: mark reindex pending, but still return note_ids
  -> later compensation marks ok or deadletter
```

### 3.4 Implementation Checklist
- [ ] 在 `SessionManager` 中补 lifecycle event 持久化 helper
- [ ] 新增 SSE replay/live integration tests，覆盖 lifecycle 事件
- [ ] 新增 reindex compensation integration tests
- [ ] 扩充 purge/job cancellation integration test
- [ ] 补 checkpoint 第二个 interrupt 点恢复场景
- [ ] 根据测试结果更新 `dev_spec.md` §9.2 / §9.4

**Error Classification Rules**
- integration 失败若源于真实状态不一致，归为 `contract_violation`
- 若测试本身依赖过强或断言错误，归为 `test_defect`
- 若需要真实 HTTP/SSE 行为才能证明，保留到 `P5-3`

---

## 4. Testing Strategy

### 4.1 Test Pyramid Mapping

| Level | File | Focus | Dependency Policy |
|-------|------|-------|-------------------|
| Integration | `tests/integration/test_sse_replay.py` | SSE replay/live + lifecycle events | real SQLite + real `_event_stream()`, fake request |
| Integration | `tests/integration/test_reindex_compensation.py` | reindex pending/success/deadletter | real `SessionManager`, fake rag indexer |
| Integration | `tests/integration/test_job_worker.py` | purge/job cancellation consistency | real `SessionManager` + `JobStore` + `JobWorker` |
| Integration | `tests/integration/test_checkpoint_recovery.py` | interrupt/resume consistency | real workflow + stub agents |

### 4.2 Critical Test Scenarios

1. `Last-Event-ID` replay后仍能收到后续 live lifecycle 事件
2. `session_frozen` / `session_purged` 事件顺序写入并可观察
3. reindex 失败后 `pending` 不阻断主流程结果返回
4. repeated reindex failure 达到上限进入 `deadletter`
5. session `purged` 后 queued/paused jobs 统一 `cancelled`
6. checkpoint 在另一个 interrupt 点恢复后结果仍与一次性执行一致

### 4.3 Execution Plan

- 先新增/补强 integration tests，让缺口直接暴露
- 再做最小生产修复以满足测试
- 最后跑任务相关 integration targets，必要时再跑全量 integration

