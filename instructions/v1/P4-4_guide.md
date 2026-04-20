# Development Guide: P4-4 - Orchestrator 集成

> Generated: 2026-03-18
> Architect: implementation skill
> Status: Revised for spec-compliant SSE
> Source: `dev_spec.md` §4.3/§9.3, `docs/testing_strategy.md` `ts-p4-4`

## 1. Task Context

### Scope Boundary
- **Task ID**: `P4-4`
- **Task Name**: Orchestrator 集成
- **Phase**: Phase 4 工作流集成
- **Dependencies**:
  - `P4-3` router 契约已完成
  - `P4-2` workflow / checkpoint 已完成
- **本任务目标**:
  - 让后台 worker 能从 API 入队的 strategy/generate job 真正执行到 session 主链路
  - 补齐 `app/main.py`，在应用启动时集成 `JobStore + Orchestrator + JobWorker`
  - 修正 orchestrator 的 generation 分支，改为基于 `session_id` 执行 `ContentGenerationAgent.execute()`
  - 为 worker 执行结果写出 SSE 可消费的 `task_progress/task_completed/task_failed`
  - 让 SSE 严格遵循 spec：`Last-Event-ID` 历史补发后保持长连接、周期 heartbeat、持续实时推送
  - 新增覆盖 session / strategy / generation / SSE 主链路与长连接语义的验证

### Acceptance Criteria
- [ ] `main.py` 可稳定启动 FastAPI app，并在生命周期中启动/停止后台 worker
- [ ] strategy job 可经由 `router -> queue -> worker -> orchestrator -> StrategyAgent.execute(session_id)` 完成
- [ ] generation job 可经由 `router -> queue -> worker -> orchestrator -> ContentGenerationAgent.execute(session_id)` 完成
- [ ] worker 执行时会写入 `task_progress/task_completed/task_failed` 事件，供 SSE 使用
- [ ] `/sessions/{id}/events` 在 replay 完成后保持连接不断开，并持续推送后续事件
- [ ] heartbeat 周期性发送，不得通过 `return` 主动结束 SSE 连接
- [ ] heartbeat 不得破坏 `Last-Event-ID` 游标语义；客户端重连后仍能按持久化 `event_id` 正确补发
- [ ] 测试证明 create/get/strategy/generate/SSE 主链路与长连接语义可用

### Test Requirements
- **E2E Files**:
  - `tests/e2e/test_session_flow.py`
  - `tests/e2e/test_strategy_api.py`
  - `tests/e2e/test_generation_api.py`
  - `tests/e2e/test_sse_api.py`
- **Unit/Integration Adjustments**:
  - `tests/unit/test_orchestrator.py`
- **Core Scenarios**:
  1. create session / get session happy path
  2. strategy 入队后被 worker 消费并写回 strategy 数据
  3. generation 入队后被 worker 消费并写回 `generated_notes` / `similarity_report`
  4. SSE 能看到 `stage_changed/task_progress/task_completed`

## 2. Architecture Context

### System Position
- `router.py` 只负责入队；`main.py` 提供运行时装配
- `JobWorker` 负责消费 jobs 表
- `Orchestrator` 负责把 leased job 路由到 strategy / generation 主执行链
- `SessionManager` 仍是 session 真相来源

### Constraints
- 不接真实 Spider / LLM / RAG；E2E 默认用 fake/stub
- 不在本任务里做全量并发/错误矩阵，只做主链路和关键契约
- 不改 router 的 HTTP 契约

## 3. Technical Design

### 3.1 Files to Modify
```text
app/agents/orchestrator.py           MODIFY
app/workers/job_worker.py            MODIFY
app/main.py                          NEW
tests/unit/test_orchestrator.py      MODIFY
tests/e2e/test_session_flow.py       NEW
tests/e2e/test_strategy_api.py       NEW
tests/e2e/test_generation_api.py     NEW
tests/e2e/test_sse_api.py            NEW
tests/integration/test_sse_stream.py NEW
```

### 3.2 Required Interfaces
- `Orchestrator._run_generation_job(session_id, payload)` must call `ContentGenerationAgent.execute(session_id)`
- `create_app() -> FastAPI`
- app lifespan must manage:
  - `JobStore`
  - `Orchestrator`
  - `JobWorker`
  - worker stop event + task
- SSE stream semantics:
  - replay stored `session_events` with `event_id > Last-Event-ID`
  - after replay, continue polling/pushing without auto-closing
  - emit periodic `heartbeat`
  - heartbeat must not advance the reconnect cursor ahead of persisted events

### 3.3 Execution Flow
1. API enqueue strategy/generate job
2. Worker startup loop leases queued job
3. Worker emits `task_progress`
4. Orchestrator runs correct agent by `job_type`
5. Agent writes session data
6. Worker marks job succeeded/failed and emits SSE event

## 4. Testing Strategy

### E2E
- Use a real ASGI server/client pair for long-lived SSE behavior when needed; avoid reshaping production semantics to satisfy `TestClient`
- monkeypatch strategy/generation agent `execute()` to deterministic fake writes
- poll `GET /jobs/{id}` / `GET /sessions/{id}` until finished
- validate SSE history replay, long-lived heartbeat, and live events on the same connection

### Must Implement
1. session create/get success
2. strategy happy path with worker execution
3. generation happy path with worker execution and session completed
4. generation without strategy remains `409`
5. SSE replay contains worker-emitted progress/completion events
6. SSE connection survives idle periods and emits multiple heartbeats over time
7. A live event appended after replay is delivered on the same open connection
8. `Last-Event-ID` replay is based on persisted event ids, not synthetic heartbeat ids

## 5. Assumptions
- `router.app` can remain for unit tests; `main.py` becomes runtime assembly entry
- worker polling interval may be lowered in tests via settings monkeypatch
- E2E uses fake `execute()` implementations rather than external providers
- If framework-local test transports cannot faithfully expose streaming chunks, prefer a localhost `uvicorn` test harness over weakening SSE semantics
