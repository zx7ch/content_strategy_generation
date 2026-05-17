# Development Guide: ALIGN-3 - SQLite Thread / Message Store

> Generated: 2026-05-16
> Source: docs/changes/2026-05-16-frontend-scope-v1-v2-alignment.md

## 1. Task Context

### Scope Boundary
- **Task ID**: ALIGN-3
- **Dependencies**: None
- **Goal**: 为 Creator Workbench 建立真实 conversation thread 层，作为 workflow session 之上的用户体验模型

### In Scope
- `app/memory/thread_store.py` — SQLite-backed ThreadStore（aiosqlite 模式）
- `app/models/schemas.py` — 8 个 Creator thread/message Pydantic 模型
- `app/main.py` — lifespan 中初始化 ThreadStore 并挂载到 `application.state`
- `app/api/routes/router.py` — 4 个新端点 + `_get_thread_store()` helper
- `tests/unit/test_thread_store.py` — 6 个 store 单测
- `tests/e2e/test_creator_thread_api.py` — 6 个 API e2e 测试

### Out Of Scope
- ALIGN-4/5/6/7/8（不实现 workflow、intent router、SSE、job control、publish candidate）
- thread 的重命名、删除、分享功能

### Acceptance Criteria
- [x] AC1: `POST /threads` 创建 thread，返回 thread_id/title/status
- [x] AC2: `GET /threads` 列出所有 threads
- [x] AC3: `GET /threads/{thread_id}` 返回 thread detail + messages（初始为空列表）
- [x] AC4: `POST /threads/{thread_id}/messages` 追加消息，intent 默认为 `free_chat`
- [x] AC5: `GET /threads/{nonexistent}` → 404
- [x] AC6: `POST /threads/{nonexistent}/messages` → 404
- [x] AC7: thread 可保存 active_workflow_session_id / active_job_id（`update_thread_active_job`）

---

## 2. Architecture Context

### System Position
```
FastAPI Router
  └─ POST/GET /threads, GET /threads/{id}, POST /threads/{id}/messages
       └─ ThreadStore (aiosqlite, creator_threads.db)
            ├─ creator_threads table
            └─ creator_messages table
```

### Tech Stack
- Python / aiosqlite（同 job_store.py 模式）
- FastAPI / Pydantic
- pytest + pytest-asyncio（asyncio_mode = "auto"）、httpx ASGITransport

### Key Behavioral Constraints
- thread_id / message_id 均为 uuid4 字符串
- 时间戳为 `datetime.utcnow().isoformat()` 格式
- ThreadStore 默认路径 `./data/creator_threads.db`
- intent 字段在 ALIGN-3 阶段默认返回 `"free_chat"`（ALIGN-5 实现真实路由）

---

## 3. Technical Design

### 3.1 Module Structure

| Path | NEW/MODIFY | Change Intent | AC |
|------|-----------|--------------|-----|
| `app/memory/thread_store.py` | **DONE** (已创建) | ThreadStore 类，aiosqlite 两张表 | AC7 |
| `app/models/schemas.py` | **DONE** (已追加) | 8 个 Pydantic 模型 | AC1-6 |
| `app/main.py` | **PARTIAL** (导入+connect 已加，state 赋值缺) | `application.state.thread_store = thread_store`；finally 加 close | AC1-6 |
| `app/api/routes/router.py` | **TODO** | `_get_thread_store()` + 4 端点 | AC1-6 |
| `tests/unit/test_thread_store.py` | **TODO** | 6 个 async 单测 | AC1-7 |
| `tests/e2e/test_creator_thread_api.py` | **TODO** | 6 个 e2e 测试 | AC1-6 |

### 3.2 main.py 缺失片段

在 `_worker_lifespan` 中需补全（当前 `application.state.thread_store` 赋值缺失，finally 也未 close）：

```python
# 已有（正确位置）:
thread_store = ThreadStore()
await thread_store.connect()

# 缺失 — 在 application.state.job_store = job_store 附近加：
application.state.thread_store = thread_store

# 缺失 — 在 finally 中 await job_store.close() 之后加：
await thread_store.close()
```

### 3.3 Router: `_get_thread_store()` helper

```python
def _get_thread_store(request: Request) -> ThreadStore:
    store = getattr(request.app.state, "thread_store", None)
    if store is None:
        raise APIError(
            status_code=500,
            error_code="THREAD_STORE_UNAVAILABLE",
            error_message="Thread store is not initialized",
            suggested_action="请通过应用 lifespan 初始化 thread store 后重试",
        )
    return store
```

### 3.4 Router: 4 个新端点

**POST /threads**
```python
@app.post("/threads", status_code=201)
async def create_thread(body: CreatorThreadCreateRequest, request: Request) -> CreatorThreadResponse:
    store = _get_thread_store(request)
    row = await store.create_thread(title=body.title)
    return CreatorThreadResponse(
        thread_id=row["id"], title=row["title"], status=row["status"],
        active_workflow_session_id=row["active_workflow_session_id"],
        active_job_id=row["active_job_id"],
    )
```

**GET /threads**
```python
@app.get("/threads")
async def list_threads(request: Request) -> CreatorThreadListResponse:
    store = _get_thread_store(request)
    rows = await store.list_threads()
    items = [CreatorThreadSummary(
        thread_id=r["id"], title=r["title"], status=r["status"],
        active_job_id=r["active_job_id"],
        created_at=r["created_at"], updated_at=r["updated_at"],
    ) for r in rows]
    return CreatorThreadListResponse(items=items)
```

**GET /threads/{thread_id}**
```python
@app.get("/threads/{thread_id}")
async def get_thread(thread_id: str, request: Request) -> CreatorThreadDetailResponse:
    store = _get_thread_store(request)
    row = await store.get_thread(thread_id)
    if row is None:
        raise APIError(status_code=404, error_code="THREAD_NOT_FOUND",
                       error_message=f"Thread {thread_id} not found",
                       suggested_action="请检查 thread_id 是否正确")
    messages = await store.get_thread_messages(thread_id)
    thread_detail = CreatorThreadDetail(
        thread_id=row["id"], title=row["title"], status=row["status"],
        active_workflow_session_id=row["active_workflow_session_id"],
        active_job_id=row["active_job_id"], accepted_at=row["accepted_at"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )
    message_records = [CreatorMessageRecord(
        message_id=m["id"], thread_id=m["thread_id"], role=m["role"],
        text=m["text"], intent=m["intent"],
        linked_session_id=m["linked_session_id"], linked_job_id=m["linked_job_id"],
        created_at=m["created_at"],
    ) for m in messages]
    return CreatorThreadDetailResponse(thread=thread_detail, messages=message_records)
```

**POST /threads/{thread_id}/messages**
```python
@app.post("/threads/{thread_id}/messages", status_code=201)
async def append_thread_message(
    thread_id: str, body: CreatorMessageCreateRequest, request: Request
) -> CreatorMessageResponse:
    store = _get_thread_store(request)
    thread = await store.get_thread(thread_id)
    if thread is None:
        raise APIError(status_code=404, error_code="THREAD_NOT_FOUND",
                       error_message=f"Thread {thread_id} not found",
                       suggested_action="请检查 thread_id 是否正确")
    intent = "free_chat"  # ALIGN-5 will implement real routing
    msg_row = await store.append_message(thread_id=thread_id, role="user",
                                          text=body.text, intent=intent)
    message_record = CreatorMessageRecord(
        message_id=msg_row["id"], thread_id=msg_row["thread_id"],
        role=msg_row["role"], text=msg_row["text"], intent=msg_row["intent"],
        linked_session_id=msg_row["linked_session_id"],
        linked_job_id=msg_row["linked_job_id"], created_at=msg_row["created_at"],
    )
    return CreatorMessageResponse(message=message_record, intent=intent)
```

### 3.5 Router imports to add

```python
from app.memory.thread_store import ThreadStore
from app.models.schemas import (
    CreatorThreadCreateRequest, CreatorThreadSummary, CreatorThreadDetail,
    CreatorMessageRecord, CreatorThreadResponse, CreatorThreadListResponse,
    CreatorThreadDetailResponse, CreatorMessageCreateRequest, CreatorMessageResponse,
)
```

---

## 4. Testing Strategy

### 4.1 Unit tests — `tests/unit/test_thread_store.py`

Pattern: `async with ThreadStore(str(tmp_path / "test.db")) as store:`

| # | Test name | Scenario |
|---|-----------|---------|
| 1 | `test_create_thread_returns_id_and_title` | create → has id/title/status=active |
| 2 | `test_create_thread_default_title` | create with no title → title contains "对话" |
| 3 | `test_list_threads_returns_created` | create two → list returns both |
| 4 | `test_get_thread_returns_correct` | create → get by id returns same |
| 5 | `test_get_thread_none_for_missing` | get nonexistent → None |
| 6 | `test_append_message_persists` | create thread → append → get_thread_messages returns it |
| 7 | `test_update_thread_active_job` | create → update_thread_active_job → get shows session/job ids |

### 4.2 E2E tests — `tests/e2e/test_creator_thread_api.py`

Pattern: `httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")`

| # | Test name | Scenario |
|---|-----------|---------|
| 1 | `test_post_threads_creates_thread` | POST /threads → 201, has thread_id |
| 2 | `test_get_threads_lists_created` | create → GET /threads → items has thread |
| 3 | `test_get_thread_detail_empty_messages` | create → GET /threads/{id} → messages = [] |
| 4 | `test_post_message_to_thread` | create → POST /threads/{id}/messages → 201, intent=free_chat |
| 5 | `test_get_nonexistent_thread_404` | GET /threads/bad-id → 404 |
| 6 | `test_post_message_nonexistent_thread_404` | POST /threads/bad-id/messages → 404 |

**E2E 隔离方式**：monkeypatch `app.state.thread_store`（绕过 lifespan，直接注入 isolated ThreadStore）。

---

## 5. Implementation Checklist

1. [x] `app/memory/thread_store.py` — 已完成
2. [x] `app/models/schemas.py` — 已追加 8 个模型
3. [x] `app/main.py` — 补全 `application.state.thread_store` + `finally close`
4. [x] `app/api/routes/router.py` — 加 imports + `_get_thread_store()` + 4 端点
5. [x] `tests/unit/test_thread_store.py` — 7 个单测（全部通过）
6. [x] `tests/e2e/test_creator_thread_api.py` — 7 个 e2e 测试（全部通过；需 tests/e2e/conftest.py mock langgraph.checkpoint.sqlite）
