# Development Guide: ALIGN-4 - Creator Workflow API

> Generated: 2026-05-16
> Source: docs/changes/2026-05-16-frontend-scope-v1-v2-alignment.md

## 1. Task Context

### Scope Boundary
- **Task ID**: ALIGN-4
- **Dependencies**: ALIGN-3 (Done) — ThreadStore, thread endpoints
- **Goal**: 把 `/creator` 从前端 mock 变成真实 V1 Editing Mode 工作流入口

### In Scope
- 新增 `POST /threads/{thread_id}/workflow` 端点：创建 V1 session + 入队 strategy job + 回写 thread active session/job
- 新增 2 个 Pydantic 模型：`CreatorWorkflowRequest`、`CreatorWorkflowResponse`
- `frontend/src/lib/api.ts` 新增 4 个 thread/workflow API 函数
- `frontend/src/app/creator/page.tsx` 替换 mock state：线程列表从后端加载，消息和 workflow 调真实 API
- `tests/e2e/test_creator_workflow_api.py` 覆盖 workflow 端点的 API 契约

### Out Of Scope
- ALIGN-5（intent router）：message → workflow 的意图判断在 ALIGN-5 实现，ALIGN-4 继续用简单文本关键词检测
- ALIGN-6（SSE）：generation job 触发暂时由前端 polling 或在 strategy 成功后手动调用，不做 SSE 订阅
- Exploration Mode / candidate cards
- 线程重命名、删除、置顶（留后续）
- strategy → generation 完整 worker 链路的端到端 e2e 测试（需要 worker 进程运行）

### Acceptance Criteria
- [ ] AC1: `POST /threads/{thread_id}/workflow` → 201，返回 thread_id/session_id/job_id/stage
- [ ] AC2: workflow 端点不存在的 thread → 404
- [ ] AC3: 成功调用后 thread 的 `active_workflow_session_id` 和 `active_job_id` 被更新
- [ ] AC4: Creator 页面线程列表从后端加载（GET /threads），不再用 hardcoded mock
- [ ] AC5: 用户输入触发 workflow 时，前端调真实 `POST /threads/{id}/workflow` 并展示真实 session_id/job_id
- [ ] AC6: strategy job 完成信号到来后，前端能调 `POST /sessions/{session_id}/generate` 开始 generation
- [ ] AC7: 页面不展示 Exploration/candidate workflow 入口

### Residual Obligations
- **OPEN 残留**: 无（ALIGN-3 已全部关闭）
- **本任务产生的可预见残留**:
  - AC6 的 generation 触发依赖 strategy job 状态变更信号。ALIGN-6 实现 SSE 前，这部分在 ALIGN-4 用"轮询 job 状态"的临时机制承接，完整 SSE 集成留 ALIGN-6
  - Creator 线程切换时的历史消息加载（GET /threads/{id}）暂做 placeholder，完整 UI 留后续

---

## 2. Architecture Context

### System Position
```
Browser (Creator page)
  ├─ GET /threads → list sidebar threads
  ├─ POST /threads → create new thread
  ├─ POST /threads/{id}/messages → persist user message
  └─ POST /threads/{id}/workflow  [NEW]
       ├─ SessionManager → create_session (SQLITE_DB_PATH)
       ├─ JobStore (app.state.job_store) → enqueue strategy
       └─ ThreadStore (app.state.thread_store) → update active job
            ↓
       → returns { thread_id, session_id, job_id, stage:"strategy" }

Browser (after strategy succeeds, via polling GET /jobs/{job_id})
  └─ POST /sessions/{session_id}/generate  [existing endpoint]
```

### Tech Stack
- Backend: FastAPI / aiosqlite / Python async
- Frontend: Next.js (Client Component), React hooks
- Session: `app.agents.session_manager.SessionManager` per-request
- Jobs: `request.app.state.job_store` (JobStore, initialized in lifespan)
- Threads: `request.app.state.thread_store` (ThreadStore, initialized in lifespan)
- Tests: pytest-asyncio auto mode, httpx ASGITransport, tmp_path isolation

### Key Behavioral Constraints
- stage 固定返回 "strategy"（工作流永远从策略阶段开始，这是正式设计，不是简化）
- 前端 strategy 完成检测：轮询 `GET /jobs/{job_id}` 直到 `status == "done"`，再调 generate

### ⚠ 技术债务（非正式方案，必须在 progress tracker 里记录为 OPEN 残留）

**TD-1 [ALIGN-5 实现] session 复用缺失**
- 当前行为：每次调 `POST /threads/{id}/workflow` 都新建 session，不检查 thread 是否已有活跃 session
- 正式方案：ALIGN-5 实现 intent router 后，应先判断当前消息意图（新建 workflow / 补充约束 / 查进度），再决定是复用 session 还是新建
- 遗留风险：用户在同一 thread 里多次触发 workflow 会产生多个孤立 session
- carry into: ALIGN-5

**TD-2 [UI 完善阶段实现] 线程切换时历史消息不加载**
- 当前行为：切换 thread 时，前端只更新 activeThreadId，不从后端拉取该 thread 的历史消息（GET /threads/{id}）
- 正式方案：切换 thread 时调 GET /threads/{thread_id}，渲染返回的 messages 列表
- 遗留风险：用户切换线程后看不到历史对话，体验不完整
- carry into: 后续 UI 完善任务（ALIGN-3 端点已就绪，仅缺前端调用）

**TD-3 [ALIGN-6 替换] job 状态检测用轮询而非 SSE**
- 当前行为：前端每 3s 轮询 GET /jobs/{job_id} 判断 strategy 是否完成，再触发 generate
- 正式方案：ALIGN-6 实现 SSE 后，订阅 GET /sessions/{id}/events，收到 stage_changed 事件即时触发
- 遗留风险：3s 延迟、无谓请求、tab 切换后定时器可能泄漏
- carry into: ALIGN-6

---

## 3. Technical Design

### 3.1 Module Structure

| Path | NEW/MODIFY | Required Change | Linked AC |
|------|-----------|-----------------|-----------|
| `app/api/routes/router.py` | MODIFY | 新增 `_get_job_store()` helper + `POST /threads/{id}/workflow` 端点 | AC1/2/3 |
| `app/models/schemas.py` | MODIFY | 追加 `CreatorWorkflowRequest`、`CreatorWorkflowResponse` | AC1 |
| `frontend/src/lib/api.ts` | MODIFY | 新增 `listThreads`、`createThread`、`appendThreadMessage`、`startThreadWorkflow` | AC4/5/6 |
| `frontend/src/app/creator/page.tsx` | MODIFY | 替换 mock 数据和 state 为真实 API 调用 | AC4/5/7 |
| `tests/e2e/test_creator_workflow_api.py` | NEW | 4 个 e2e 测试覆盖 workflow API 契约 | AC1/2/3 |

### 3.2 New Pydantic Models

```python
# app/models/schemas.py 追加到 Creator thread 模型组末尾

class CreatorWorkflowRequest(BaseModel):
    user_query: str
    platform: str = "xiaohongshu"
    mode: str = "editing"
    user_id: Optional[str] = None  # 默认用 DEFAULT_USER_ID

class CreatorWorkflowResponse(BaseModel):
    thread_id: str
    session_id: str
    job_id: str
    stage: str  # 固定 "strategy"
```

### 3.3 Router: `_get_job_store()` helper

```python
def _get_job_store(request: Request) -> JobStore:
    store = getattr(request.app.state, "job_store", None)
    if store is None:
        raise APIError(
            status_code=500,
            error_code="JOB_STORE_UNAVAILABLE",
            error_message="Job store is not initialized",
            suggested_action="请通过应用 lifespan 初始化 job store 后重试",
        )
    return store
```

### 3.4 Router: `POST /threads/{thread_id}/workflow`

```python
@app.post("/threads/{thread_id}/workflow", status_code=201)
async def start_thread_workflow(
    thread_id: str, body: CreatorWorkflowRequest, request: Request
) -> CreatorWorkflowResponse:
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

    session_id = str(uuid.uuid4())
    user_id = body.user_id or DEFAULT_USER_ID

    # Step 1: create session, write task_progress event (matches POST /sessions)
    async with SessionManager(settings.SQLITE_DB_PATH) as session_manager:
        session = await session_manager.create_session(...)
    await job_store.append_session_event(event_name="task_progress", stage="init", ...)

    # Step 2: touch_user_activity → update_session(STRATEGY) → log_event (matches _enqueue_with_stage)
    async with SessionManager(settings.SQLITE_DB_PATH) as session_manager:
        await session_manager.touch_user_activity(session_id)
        await session_manager.update_session(session_id, stage=SessionStage.STRATEGY)
    log_event(...)

    job, created = await job_store.enqueue(
        session_id=session_id,
        job_type="strategy",
        payload=None,
        idempotency_key=None,
    )

    await thread_store.update_thread_active_job(
        thread_id=thread_id,
        session_id=session_id,
        job_id=job.id,
    )

    return CreatorWorkflowResponse(
        thread_id=thread_id,
        session_id=session_id,
        job_id=job.id,
        stage="strategy",
    )
```

**Required new imports in router.py:**
```python
from app.agents.session_manager import SessionManager
from app.models.schemas import (
    ...,  # existing
    CreatorWorkflowRequest,
    CreatorWorkflowResponse,
)
```

Check if `SessionManager` and `SessionStage` are already imported — grep before adding.

### 3.5 Frontend: api.ts additions

```typescript
// Thread & Workflow API types (add near top of api.ts)
export interface ThreadSummary {
  thread_id: string;
  title: string;
  status: string;
  active_job_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkflowStartResult {
  thread_id: string;
  session_id: string;
  job_id: string;
  stage: string;
}

// API functions
export async function listThreads(): Promise<ThreadSummary[]> {
  const res = await fetch(`${RUNTIME_BASE_URL}/threads`, { cache: "no-store" });
  if (!res.ok) throw new Error(`listThreads failed: ${res.status}`);
  const data = await res.json();
  return data.items as ThreadSummary[];
}

export async function createThread(title?: string): Promise<{ thread_id: string; title: string }> {
  const res = await fetch(`${RUNTIME_BASE_URL}/threads`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(title ? { title } : {}),
  });
  if (!res.ok) throw new Error(`createThread failed: ${res.status}`);
  return res.json();
}

export async function appendThreadMessage(
  threadId: string,
  text: string
): Promise<void> {
  const res = await fetch(`${RUNTIME_BASE_URL}/threads/${threadId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new Error(`appendThreadMessage failed: ${res.status}`);
}

export async function startThreadWorkflow(
  threadId: string,
  userQuery: string
): Promise<WorkflowStartResult> {
  const res = await fetch(`${RUNTIME_BASE_URL}/threads/${threadId}/workflow`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_query: userQuery }),
  });
  if (!res.ok) throw new Error(`startThreadWorkflow failed: ${res.status}`);
  return res.json();
}

export async function getJobStatus(jobId: string): Promise<{ job_id: string; status: string; result: unknown }> {
  const res = await fetch(`${RUNTIME_BASE_URL}/jobs/${jobId}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`getJobStatus failed: ${res.status}`);
  return res.json();
}

export async function enqueueGenerate(sessionId: string): Promise<{ session_id: string; job_id: string }> {
  const res = await fetch(`${RUNTIME_BASE_URL}/sessions/${sessionId}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!res.ok) throw new Error(`enqueueGenerate failed: ${res.status}`);
  return res.json();
}
```

### 3.6 Frontend: creator/page.tsx wiring

Replace mock data with real API calls. Key changes:

1. **Thread list** — on mount, `listThreads()` → replace `initialThreads`
2. **新建对话** — `createThread()` → backend-generated thread_id/title
3. **sendMessage** — `appendThreadMessage(activeThread.id, text)` — fire and forget (optimistic UI)
4. **startWorkflowTask** — `startThreadWorkflow(activeThread.id, text)` → store `sessionId`, `jobId` in state
5. **Task bar** — show real `stage`/`jobId`; add a polling `useEffect` that calls `getJobStatus(jobId)` every 3s while `status !== "done"/"failed"`. On strategy `done`, call `enqueueGenerate(sessionId)` and update stage to "generation".

**State additions to CreatorPage:**
```typescript
const [sessionId, setSessionId] = useState<string | null>(null);
const [jobId, setJobId] = useState<string | null>(null);
const [isLoading, setIsLoading] = useState(false);
```

**Polling useEffect:**
```typescript
useEffect(() => {
  if (!jobId || !task || task.status !== "running") return;
  const interval = setInterval(async () => {
    const status = await getJobStatus(jobId);
    if (status.status === "done" && task.stage === "strategy") {
      clearInterval(interval);
      const genResult = await enqueueGenerate(sessionId!);
      setJobId(genResult.job_id);
      setTask((t) => t ? { ...t, stage: "generation", progress: 50 } : t);
    } else if (status.status === "done" && task.stage === "generation") {
      clearInterval(interval);
      setTask((t) => t ? { ...t, status: "completed", stage: "completed", progress: 100 } : t);
    } else if (status.status === "failed") {
      clearInterval(interval);
      setTask((t) => t ? { ...t, status: "cancelled" } : t);
    }
  }, 3000);
  return () => clearInterval(interval);
}, [jobId, task?.status, task?.stage, sessionId]);
```

---

## 4. Testing Strategy

### 4.1 Test Pyramid Mapping

| Level | File | Count | Focus | Mock Strategy |
|-------|------|-------|-------|---------------|
| E2E | `tests/e2e/test_creator_workflow_api.py` | 4 | workflow API 契约 | 隔离 ThreadStore(tmp) + JobStore(tmp) 注入 app.state，SessionManager monkeypatch SQLITE_DB_PATH |

### 4.2 E2E Test Scenarios

**test fixture** — 在 `test_creator_workflow_api.py` 中定义，复用 `test_creator_thread_api.py` 的模式，但需要同时隔离 job_store:

```python
@pytest.fixture
async def client(tmp_path):
    thread_db = str(tmp_path / "threads.db")
    job_db = str(tmp_path / "jobs.db")

    thread_store = ThreadStore(thread_db)
    await thread_store.connect()
    job_store = JobStore(job_db)
    await job_store.connect()

    _orig_thread_store = getattr(app.state, "thread_store", None)
    _orig_job_store = getattr(app.state, "job_store", None)
    app.state.thread_store = thread_store
    app.state.job_store = job_store

    # SessionManager uses settings.SQLITE_DB_PATH — monkeypatch to tmp
    import app.api.routes.router as router_module
    original_db_path = router_module.settings.SQLITE_DB_PATH
    router_module.settings.SQLITE_DB_PATH = job_db  # reuse same db for session tables

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c

    router_module.settings.SQLITE_DB_PATH = original_db_path
    app.state.thread_store = _orig_thread_store
    app.state.job_store = _orig_job_store
    await thread_store.close()
    await job_store.close()
```

**Tests:**

| # | Test name | Scenario |
|---|-----------|---------|
| 1 | `test_start_workflow_creates_session_and_job` | create thread → POST /workflow → 201, has session_id/job_id/stage="strategy" |
| 2 | `test_start_workflow_updates_thread_active_job` | POST /workflow → GET /threads/{id} → active_workflow_session_id set |
| 3 | `test_start_workflow_nonexistent_thread_404` | POST /threads/bad-id/workflow → 404 |
| 4 | `test_start_workflow_updates_thread_active_job_readable` | workflow → GET /threads/{id} body contains job_id in thread detail |

### 4.3 SQLite 文件布局与 fixture 隔离方案

**生产文件布局（两个独立 SQLite 文件）：**
```
xhs_agent.db           ← settings.SQLITE_DB_PATH（默认 ./data/xhs_agent.db）
  ├─ sessions           ← SessionManager 管理
  ├─ jobs               ← JobStore 管理
  └─ session_events     ← JobStore 管理

creator_threads.db     ← ThreadStore 默认路径（./data/creator_threads.db）
  ├─ creator_threads
  └─ creator_messages
```

**测试 fixture 对应方案：**

```python
@pytest.fixture
async def client(tmp_path):
    thread_db = str(tmp_path / "threads.db")
    agent_db = str(tmp_path / "agent.db")   # sessions + jobs 同一文件，和生产一致

    thread_store = ThreadStore(thread_db)
    await thread_store.connect()
    job_store = JobStore(agent_db)
    await job_store.connect()

    # 初始化 sessions 表（workflow 端点用 SessionManager(settings.SQLITE_DB_PATH) 创建）
    from app.memory.session_state import SessionManager as SM
    async with SM(agent_db) as _:
        pass

    _orig_ts = getattr(app.state, "thread_store", None)
    _orig_js = getattr(app.state, "job_store", None)
    app.state.thread_store = thread_store
    app.state.job_store = job_store

    import app.api.routes.router as router_module
    _orig_db = router_module.settings.SQLITE_DB_PATH
    router_module.settings.SQLITE_DB_PATH = agent_db  # workflow 端点 SessionManager 用此路径

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c

    router_module.settings.SQLITE_DB_PATH = _orig_db
    app.state.thread_store = _orig_ts
    app.state.job_store = _orig_js
    await thread_store.close()
    await job_store.close()
```

---

## 5. Implementation Checklist

### Coding Sequence
1. [ ] `app/models/schemas.py` — 追加 `CreatorWorkflowRequest`、`CreatorWorkflowResponse`
2. [ ] `app/api/routes/router.py` — 检查 SessionManager / SessionStage import，补充缺失的；添加 `_get_job_store()`；插入 workflow 端点（在 `/threads/{thread_id}/messages` 之后）
3. [ ] `frontend/src/lib/api.ts` — 追加 6 个函数和 2 个 interface
4. [ ] `frontend/src/app/creator/page.tsx` — 接入真实 API：线程列表、消息持久化、workflow 启动、job 轮询
5. [ ] `tests/e2e/test_creator_workflow_api.py` — 新建，4 个 e2e 测试

### Imports to verify in router.py before adding
```bash
grep -n "SessionManager\|SessionStage\|JobStore" app/api/routes/router.py | head -10
```

---

## 6. Risk & Notes

**SessionManager + JobStore 共用同一 SQLite 文件的风险**:
- 生产中两者都用 `settings.SQLITE_DB_PATH`，表名不冲突（jobs/session_events vs sessions）
- 测试中需要用同一 tmp db 路径 init 两份 schema，顺序为：JobStore.connect() 先，SessionManager.connect() 后

**前端错误处理**:
- `listThreads()` 失败时：显示空列表 + 错误 toast，不阻塞页面加载
- `startThreadWorkflow()` 失败时：显示 assistant 错误消息，不改变 task 状态

**strategy stage 更新时序**:
- `create_session` 后 session stage 为 INIT
- 需要在 enqueue strategy 前调用 `session_manager.update_session(session_id, stage=SessionStage.STRATEGY)`，与现有 `_enqueue_with_stage` 保持一致
- 或者直接复用 `_enqueue_with_stage` 内部逻辑（但该函数需要 `session` 对象，需要先 load session）

**AC6 partial coverage**:
- ALIGN-4 仅验证 strategy job 入队成功；generation 触发由前端 polling + UI 实现
- 完整的 strategy → generation → result 链路需要 worker 运行，不纳入 ALIGN-4 e2e 测试

---

## 7. Spec Sync Expectations

- 完成后在 `docs/changes/2026-05-16-frontend-scope-v1-v2-alignment.md` ALIGN-4 `完成进展` 块更新为 checklist 格式，所有 AC 逐条打 `[x]` / `[-]`
- 以下三条技术债务在 progress tracker 阶段**必须**作为 OPEN 残留写回 spec，标记 `[-]`，不得忽略：

| 残留 ID | 问题 | carry into |
|---------|------|-----------|
| TD-ALIGN4-1 | session 复用逻辑缺失：每次 workflow 调用都新建 session | ALIGN-5 |
| TD-ALIGN4-2 | 线程切换时不加载历史消息：GET /threads/{id} 端点已就绪，前端调用缺失 | UI 完善阶段 |
| TD-ALIGN4-3 | job 状态检测用 3s 轮询而非 SSE：ALIGN-6 实现 SSE 后替换 | ALIGN-6 |
