# Development Guide: ALIGN-7 - Thread-scoped Events

> Generated: 2026-05-16
> Source: docs/changes/2026-05-16-frontend-scope-v1-v2-alignment.md

## 1. Task Context

### Scope Boundary
- **Task ID**: ALIGN-7
- **Dependencies**: ALIGN-6 (Done) — job-control API, cancel guard in worker
- **Goal**: 用 SSE 替换前端 3s 轮询；新增 `GET /threads/{id}/events` 端点，把 session/job events 映射为 thread-scoped event 名称并流式推送

### In Scope
- 新增 `app/api/routes/router.py` — `_SESSION_TO_THREAD_EVENT` 映射、`_format_sse_thread_event` helper、`_thread_event_stream` generator、`GET /threads/{id}/events` 端点
- 修改 `frontend/src/lib/api.ts` — 新增 `subscribeThreadEvents(threadId, handlers)` 函数
- 修改 `frontend/src/app/creator/page.tsx` — 替换 `setInterval` polling 为 `EventSource`，移除 `getJobStatus` 依赖
- 新增 `tests/integration/test_thread_events.py` — SSE replay 映射验证

### Out Of Scope
- 新建独立 thread_events 表：MVP 直接读 `session_events`，不重建事件系统
- `workflow_accepted` 事件：ALIGN-8 范围（complete 端点未实现）
- `message_created` 事件推送：messages 落库后没有对应 session_event，本任务不补充（ALIGN-8 可扩展）
- SSE 认证/权限：与现有 session SSE 对齐，无 token 要求

### Required Deliverables
- Production: router.py（新增 SSE 端点 + 3 个 helper）、api.ts（`subscribeThreadEvents`）、page.tsx（EventSource handler 替换 polling）
- Tests: `tests/integration/test_thread_events.py`（新建，SSE replay 验证）
- Spec: `instructions/ALIGN-7_guide.md`（本文件）、alignment.md 回填

### Acceptance Criteria
- [ ] AC1: `GET /threads/{thread_id}/events` — 404 if thread not found
- [ ] AC2: `GET /threads/{thread_id}/events` — 若 thread 无 active session，返回空 SSE 流（仅 heartbeat）后断开
- [ ] AC3: `GET /threads/{thread_id}/events` — 已持久化 session events 通过 `Last-Event-ID` replay
- [ ] AC4: session event `task_progress` → thread event `workflow_task_progress`
- [ ] AC5: session event `task_completed` → thread event `workflow_task_completed`
- [ ] AC6: session event `task_failed` → thread event `workflow_task_failed`
- [ ] AC7: session event `task_cancelled` → thread event `workflow_cancelled`
- [ ] AC8: session event `stage_changed` → thread event `workflow_stage_changed`
- [ ] AC9: 前端 EventSource 替换 `setInterval(3000)` 轮询
- [ ] AC10: `workflow_task_completed(stage=strategy)` → 前端调 `enqueueGenerate`，task 进入 generation 阶段
- [ ] AC11: `workflow_task_completed(stage=generate)` → 前端标记 completed

### Residual Obligations
- **TD-ALIGN4-3 [本任务关闭]**: job 状态轮询 → SSE 替换，此 TD 在本任务完成后关闭
- 无其他跨任务 carry-forward 项需在本任务处理

### ⚠ 技术债务（本任务产生）
- **TD-ALIGN7-1**: `message_created` 事件暂不推送（落库后无对应 session_event）；前端新消息仍走乐观更新，carry into: ALIGN-8 或独立消息事件任务，需排期
- **TD-ALIGN7-2**: thread 无 active session 时 EventSource 立即断开；重连后无重试逻辑。正式方案需前端指数退避重连，carry into: UI 完善阶段

---

## 2. Architecture Context

### System Position
```
GET /threads/{thread_id}/events
  └─ thread_store.get_thread(thread_id)     → 404 if not found
  └─ thread["active_workflow_session_id"]   → None → empty stream
  └─ job_store.list_session_events(session_id, after_event_id=last_event_id)
       ├─ replay persisted events (name-mapped)
       └─ live poll loop (yield mapped events + heartbeat)

Event mapping (session_event_name → thread_event_name):
  task_progress   → workflow_task_progress
  task_completed  → workflow_task_completed
  task_failed     → workflow_task_failed
  task_cancelled  → workflow_cancelled
  stage_changed   → workflow_stage_changed
  session_resumed → workflow_resumed
  session_paused  → workflow_paused
  heartbeat       → heartbeat
  (others)        → pass through unchanged

Frontend:
  EventSource("/threads/{id}/events")
    workflow_task_progress  → setTask progress
    workflow_task_completed → enqueueGenerate (strategy) or mark completed (generate)
    workflow_task_failed    → mark cancelled, appendMessage error
    workflow_cancelled      → mark cancelled, appendMessage cancelled
    workflow_stage_changed  → log (no UI change needed in MVP)
```

### Reuse Existing Patterns
- `_parse_last_event_id` — reuse for `Last-Event-ID` header parsing
- `settings.SSE_HEARTBEAT_SECONDS`, `settings.SSE_REPLAY_LIMIT` — same config
- `JobStore.list_session_events(session_id, after_event_id, limit)` — already exists
- `_get_job_store(request)` helper — already exists
- `_get_thread_store(request)` helper — already exists

---

## 3. Technical Design

### 3.1 Module Structure

| Path | NEW/MODIFY | Required Change | Linked AC |
|------|------------|-----------------|-----------|
| `app/api/routes/router.py` | MODIFY | mapping dict + format helper + stream generator + endpoint | AC1-8 |
| `frontend/src/lib/api.ts` | MODIFY | `subscribeThreadEvents` function | AC9-11 |
| `frontend/src/app/creator/page.tsx` | MODIFY | replace polling useEffect with EventSource | AC9-11 |
| `tests/integration/test_thread_events.py` | NEW | SSE replay mapping test | AC3-8 |

### 3.2 Backend: router.py additions

**Event name mapping (module-level constant):**
```python
_SESSION_TO_THREAD_EVENT: dict[str, str] = {
    "task_progress":   "workflow_task_progress",
    "task_completed":  "workflow_task_completed",
    "task_failed":     "workflow_task_failed",
    "task_cancelled":  "workflow_cancelled",
    "stage_changed":   "workflow_stage_changed",
    "session_resumed": "workflow_resumed",
    "session_paused":  "workflow_paused",
}
```

**SSE format helper (no Pydantic schema — raw dict serialization):**
```python
import json as _json  # already imported as json

def _format_sse_thread_event(
    record: SessionEventRecord,
    thread_event_name: str,
    thread_id: str,
    *,
    include_id: bool = True,
) -> str:
    lines: list[str] = []
    if include_id:
        lines.append(f"id: {record.event_id}")
    lines.append(f"event: {thread_event_name}")
    data = {
        "event_id": record.event_id,
        "thread_id": thread_id,
        "session_id": record.session_id,
        "job_id": record.job_id,
        "stage": record.stage,
        "event_name": thread_event_name,
        "payload": record.payload,  # dict via @property
    }
    lines.append(f"data: {json.dumps(data, ensure_ascii=False, default=str)}")
    return "\n".join(lines) + "\n\n"
```

**Thread event stream generator:**
```python
async def _thread_event_stream(
    request: Request,
    *,
    thread_id: str,
    session_id: str,
    job_store: JobStore,
    last_event_id: Optional[int],
) -> AsyncIterator[str]:
    # Replay persisted events
    replay_events = await job_store.list_session_events(
        session_id,
        after_event_id=last_event_id,
        limit=settings.SSE_REPLAY_LIMIT,
    )
    last_sent_event_id = last_event_id or 0
    for record in replay_events:
        thread_event_name = _SESSION_TO_THREAD_EVENT.get(record.event_name, record.event_name)
        last_sent_event_id = record.event_id
        yield _format_sse_thread_event(record, thread_event_name, thread_id)

    # Live polling loop
    heartbeat_deadline = monotonic() + settings.SSE_HEARTBEAT_SECONDS
    poll_interval = min(0.2, settings.SSE_HEARTBEAT_SECONDS)
    while not await request.is_disconnected():
        live_events = await job_store.list_session_events(
            session_id,
            after_event_id=last_sent_event_id,
            limit=settings.SSE_REPLAY_LIMIT,
        )
        if live_events:
            for record in live_events:
                thread_event_name = _SESSION_TO_THREAD_EVENT.get(record.event_name, record.event_name)
                last_sent_event_id = record.event_id
                yield _format_sse_thread_event(record, thread_event_name, thread_id)
            heartbeat_deadline = monotonic() + settings.SSE_HEARTBEAT_SECONDS
            continue

        now = monotonic()
        if now >= heartbeat_deadline:
            yield ": heartbeat\n\n"  # SSE comment — keeps connection alive, no id advance
            heartbeat_deadline = monotonic() + settings.SSE_HEARTBEAT_SECONDS
            continue

        await asyncio.sleep(min(poll_interval, heartbeat_deadline - now))
```

**Endpoint:**
```python
@app.get("/threads/{thread_id}/events")
async def stream_thread_events(
    thread_id: str,
    request: Request,
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    thread_store = _get_thread_store(request)
    job_store = _get_job_store(request)

    thread = await thread_store.get_thread(thread_id)
    if thread is None:
        raise APIError(
            status_code=404,
            error_code="THREAD_NOT_FOUND",
            error_message=f"Thread {thread_id} not found",
            suggested_action="请检查 thread_id 是否正确",
        )

    parsed_last_event_id = _parse_last_event_id(last_event_id)
    session_id = thread["active_workflow_session_id"]

    if session_id is None:
        # No active session: return empty SSE stream (client will close immediately)
        async def _empty_stream() -> AsyncIterator[str]:
            yield ": no active session\n\n"
        return StreamingResponse(
            _empty_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    return StreamingResponse(
        _thread_event_stream(
            request,
            thread_id=thread_id,
            session_id=session_id,
            job_store=job_store,
            last_event_id=parsed_last_event_id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
```

### 3.3 Frontend: api.ts addition

```typescript
export function subscribeThreadEvents(
  threadId: string,
  handlers: {
    onProgress?: (data: { stage?: string; payload: unknown }) => void;
    onCompleted?: (data: { stage?: string; payload: unknown }) => void;
    onFailed?: (data: { payload: unknown }) => void;
    onCancelled?: (data: { payload: unknown }) => void;
    onStageChanged?: (data: { stage?: string; payload: unknown }) => void;
  }
): EventSource {
  const es = new EventSource(`${RUNTIME_BASE_URL}/threads/${threadId}/events`);

  es.addEventListener("workflow_task_progress", (e) => {
    const data = JSON.parse((e as MessageEvent).data);
    handlers.onProgress?.(data);
  });
  es.addEventListener("workflow_task_completed", (e) => {
    const data = JSON.parse((e as MessageEvent).data);
    handlers.onCompleted?.(data);
  });
  es.addEventListener("workflow_task_failed", (e) => {
    const data = JSON.parse((e as MessageEvent).data);
    handlers.onFailed?.(data);
  });
  es.addEventListener("workflow_cancelled", (e) => {
    const data = JSON.parse((e as MessageEvent).data);
    handlers.onCancelled?.(data);
  });
  es.addEventListener("workflow_stage_changed", (e) => {
    const data = JSON.parse((e as MessageEvent).data);
    handlers.onStageChanged?.(data);
  });

  return es;
}
```

### 3.4 Frontend: page.tsx — replace polling with EventSource

Replace the polling `useEffect` block entirely. Use a `taskRef` to avoid stale closure in EventSource handler:

```typescript
const taskRef = useRef<WorkflowTask | null>(null);
// Keep taskRef in sync with task state
useEffect(() => { taskRef.current = task; }, [task]);

// EventSource subscription — replaces polling
useEffect(() => {
  if (!activeThreadId || !task || task.status !== "running") return;

  const es = subscribeThreadEvents(activeThreadId, {
    onProgress: (data) => {
      const progress = (data.payload as { progress?: number })?.progress;
      if (typeof progress === "number") {
        setTask((t) => t ? { ...t, progress } : t);
      }
    },
    onCompleted: (data) => {
      const current = taskRef.current;
      if (!current) return;
      if (data.stage === "strategy" && current.sessionId) {
        enqueueGenerate(current.sessionId)
          .then((genResult) => {
            setTask((t) =>
              t ? { ...t, stage: "generation", jobId: genResult.job_id, progress: 50 } : t
            );
          })
          .catch(() => {});
      } else if (data.stage === "generate") {
        setTask((t) => t ? { ...t, status: "completed", stage: "completed", progress: 100 } : t);
        appendMessage({ role: "assistant", text: "任务完成。这里将展示策略摘要和生成笔记。" });
      }
    },
    onFailed: () => {
      setTask((t) => t ? { ...t, status: "cancelled" } : t);
      appendMessage({ role: "system", text: "任务执行失败，请重试。" });
    },
    onCancelled: () => {
      setTask((t) => t ? { ...t, status: "cancelled" } : t);
      appendMessage({ role: "system", text: "任务已取消。" });
    },
  });

  return () => es.close();
}, [activeThreadId, task?.status, task?.sessionId]);
```

**Remove from page.tsx:**
- `pollingRef` state and its `useEffect` (the `setInterval` block)
- `getJobStatus` and `enqueueGenerate` from the polling effect (keep `enqueueGenerate` import — still used in EventSource handler)
- Remove `getJobStatus` from import list

**Add to page.tsx:**
- `taskRef` and its sync effect
- EventSource `useEffect` above

---

## 4. Testing Strategy

### 4.1 Test Pyramid Mapping

| Level | File | Count | Focus |
|-------|------|-------|-------|
| Integration | `tests/integration/test_thread_events.py` | 4 | SSE replay + event name mapping |

### 4.2 Integration Test Scenarios

Tests use fake-disconnect pattern (same as `test_sse_api.py`): create a `_FakeRequest` that disconnects immediately, so only the replay path is exercised.

| # | Test | Scenario |
|---|------|---------|
| 1 | `test_thread_events_replay_maps_event_names` | 注入 task_progress + task_completed session events → GET /threads/{id}/events → 验证 SSE event names 变为 workflow_task_progress / workflow_task_completed |
| 2 | `test_thread_events_404_for_nonexistent_thread` | GET /threads/bad-id/events → 404 |
| 3 | `test_thread_events_empty_stream_when_no_active_session` | thread 无 active session → 200, body 含 ": no active session" |
| 4 | `test_thread_events_last_event_id_replay` | 注入 3 个 events → Last-Event-ID=event_1_id → 只收到 event_2 和 event_3 |

### 4.3 Test Fixture

```python
# Reuse pattern from test_creator_workflow_api.py:
# tmp ThreadStore + JobStore + SessionManager schema in isolated tmp_path
# monkeypatch app.state.thread_store / job_store / settings.SQLITE_DB_PATH
```

---

## 5. Implementation Checklist

1. [ ] `app/api/routes/router.py` — 新增 `_SESSION_TO_THREAD_EVENT` 映射常量
2. [ ] `app/api/routes/router.py` — 新增 `_format_sse_thread_event` helper
3. [ ] `app/api/routes/router.py` — 新增 `_thread_event_stream` generator
4. [ ] `app/api/routes/router.py` — 新增 `GET /threads/{id}/events` 端点
5. [ ] `frontend/src/lib/api.ts` — 新增 `subscribeThreadEvents`
6. [ ] `frontend/src/app/creator/page.tsx` — 新增 `taskRef` + sync effect
7. [ ] `frontend/src/app/creator/page.tsx` — 替换 polling `useEffect` 为 EventSource `useEffect`
8. [ ] `frontend/src/app/creator/page.tsx` — 移除 `getJobStatus` import（`enqueueGenerate` 保留）
9. [ ] `tests/integration/test_thread_events.py` — 4 个集成测试

---

## 6. Risk & Notes

**`_thread_event_stream` 需要访问 job_store 而非 `app.state.job_store` 内的隐式连接**：使用 `_get_job_store(request)` 返回的 `job_store` 实例（已持久化连接）直接调用 `list_session_events`，与现有 `_event_stream` 不同（它每次用 `async with JobStore(...)` 新开连接）。测试 fixture 直接赋值 `app.state.job_store` 所以没问题。

**EventSource 的 `appendMessage` 调用**: `appendMessage` 定义在组件函数体内，EventSource handler 里调用不存在 stale closure 问题，因为它通过 `setMessages` state updater 操作，而 setter 引用是稳定的。只有 `task` 状态在 handler 里有 stale closure 风险，用 `taskRef` 解决。

**`enqueueGenerate` 触发时机**: 当收到 `workflow_task_completed(stage=strategy)` 时触发。注意这个事件对应 `task_completed` with `stage=strategy`（job_type）。worker 的 `mark_succeeded` 会发 `task_completed` event，stage 字段设为 `job.job_type`（"strategy" 或 "generate"）。确认 `_format_sse_thread_event` 里 `data.stage` 是 `record.stage`（= job_type）。

---

## 7. Spec Sync Expectations

- TD-ALIGN4-3: SSE 替换轮询 → **本任务关闭**，在 alignment.md ALIGN-4 进展将 `[-]` 更新为 `[x]`（实际 SSE 在 ALIGN-7 实现，但 TD 指向的承诺在此兑现）
- TD-ALIGN7-1 / TD-ALIGN7-2: 新增 `[-]` 进 ALIGN-7 进展，carry into 明确
