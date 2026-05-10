# Development Guide: phase_2_server_endpoints — Server endpoints + 状态注册

> Generated: 2026-05-10
> Architect: implementation skill (dev-helper Stage 2)
> Status: Draft → Ready for development
> Source: experiments/xhs_extension_mvp/improvements.md §「2026/05/09 Improvement」 Phase 2 + Phase 1 完成进度

## 1. Task Context

### Scope Boundary

- **Task ID**: phase_2_server_endpoints
- **Task Name**: Server endpoints + 状态注册
- **Phase**: 2026/05/09 Improvement Phase 2
- **Dependencies**: Phase 1 ✅ 已完成 — `scrape_search_feed` / `ScraperRuntime` / `ScrapeStateRegistry` / `is_logged_in` 全部可用
- **Task Goal**: 把 Phase 1 的 scraper 通过 HTTP 暴露出来，可由工作台触发，并通过现有 ingest 流水线写入 SQLite。**不接 V2 sink，不动 UI**。

### In Scope

- 修改 `experiments/xhs_extension_mvp/server/app.py`：
  - lifespan 注册 `ScraperRuntime` + `ScrapeStateRegistry` 到 `app.state`
  - 关停时调 `ScraperRuntime.shutdown()`
  - 注册 3 个新 endpoint（不是 4 个；登录 endpoint 已被「开发前置决策 5」明确删除）
- 修改 `experiments/xhs_extension_mvp/server/models.py`：新增 4 个 pydantic 模型
- 修改 `experiments/xhs_extension_mvp/server/storage.py`：新增 `ingest_scraper_items` 方法（瘦封装，复用 `ingest_capture`）
- 单测覆盖（详见 §4）

### Out Of Scope

- ❌ V2 ingestion sink（属 Stage B）
- ❌ 工作台 UI 改动（Phase 3）
- ❌ `POST /api/scraper/login`（决策 5 删除）
- ❌ Topic pool / decision 触发链（属 Stage B）
- ❌ 多 worker 支持（决策 6 — 单 worker 假设）

### Required Deliverables

**Production**:

| 路径 | 类型 | 用途 |
|---|---|---|
| `experiments/xhs_extension_mvp/server/app.py` | MODIFY | lifespan 改造 + 3 个 endpoint |
| `experiments/xhs_extension_mvp/server/models.py` | MODIFY | `AutoScrapeRequest` / `AutoScrapeResponse` / `ScrapeStatusResponse` / `ScraperReadinessResponse` |
| `experiments/xhs_extension_mvp/server/storage.py` | MODIFY | 新增 `ingest_scraper_items` 方法 |

**Tests**:

| 路径 | 类型 | 覆盖 |
|---|---|---|
| `tests/unit/test_scraper_endpoints.py` | NEW | 3 个 endpoint 的成功 / 失败路径 |
| `tests/unit/test_storage_scraper_ingest.py` | NEW | `ingest_scraper_items` 去重 + version++ |

### Acceptance Criteria

| AC ID | 描述 | 验证方式 |
|---|---|---|
| AC1 | `POST /api/tasks/{task_id}/auto-scrape` 立即返回 `202`，不阻塞响应 | 单测断言 `response.status_code == 202` |
| AC2 | 后台 task 跑完后 `mvp_capture_items` 表多出 N 条（N = scraper 返回去重数） | 集成测试：mock `scrape_search_feed` 返回 5 条，验证 SQLite 多 5 行 |
| AC3 | 写入后 `snapshot_version` += 1 | 集成测试：取前后 `snapshot_version` 对比 |
| AC4 | 并发触发同 task 第二次返回 `409` | 单测：mock `scrape_search_feed` 阻塞，连续两次触发 |
| AC5 | `GET /api/scraper/readiness` 能正确返回登录态；未登录时 scrape 触发返回 `LOGIN_REQUIRED`（不报 500） | 单测：分别 mock `is_logged_in` 返回 True/False |
| AC6 | 触发未存在的 `task_id` 返回 `404` | 单测断言 |
| AC7 | `GET /api/tasks/{task_id}/scrape-status` 对未触发过的任务返回 `404` | 单测断言 |
| AC8 | 后台 task 抛异常时进入 `ERROR` phase；registry 正确 release | 单测：mock scrape 抛异常，验证 status 显示 ERROR + busy=False |
| AC9 | `ingest_scraper_items` 跳过 token 校验直接走 `ingest_capture` | 单测：不传 token 也能成功 |
| AC10 | 所有新增单测全绿；不破坏既有 MVP 测试 | Stage 4 验证 |

### Residual Obligations

来自 Phase 1 的 **Carry Into Phase 2** 残留项：
- **#1** `ScraperRuntime` 浏览器崩溃自动重启路径无单测覆盖 → **本任务通过 endpoint 集成测试间接覆盖**（mock runtime 模拟 closed context）

来自 Phase 1 的 **持续 carry-forward**：
- **#2** 反爬参数经验值 → 仍 carry into Phase 4
- **#5** pre-existing `test_xhs_extension_mvp.py` 5 个失败 → 不在本任务范围

### Contract Inventory

- **Upstream contracts** (Phase 2 消费):
  - Phase 1 `scrape_search_feed(keyword, *, runtime, scroll_count, on_progress) -> list[CaptureItemIn]`
  - Phase 1 `ScrapePhase` 字符串值（已锁定单测）
  - Phase 1 `ScrapeProgress` 字段
- **Downstream contracts** (Phase 2 提供):
  - `AutoScrapeResponse` 字段稳定，Phase 3 前端消费
  - `ScrapeStatusResponse` 字段稳定，Phase 3 状态轮询消费
  - `ScraperReadinessResponse` 字段稳定，Phase 3 banner 消费
  - HTTP 状态码语义稳定：`202`=已接受/`409`=并发拒绝/`404`=未知任务
- **Compatibility risks**:
  - `app.py` 的 `lifespan` 改动需要保留 `application.state.mvp_storage` 设置（既有路径依赖）
  - `storage.py` 不动既有 `ingest_extension_capture`（既有 `/api/extension/capture` endpoint 仍要工作）

### Test Requirements

- **Test layer**: unit only
- **Mock strategy**:
  - `ScraperRuntime` → 替换为 `FakeRuntime`（不启动真实浏览器）
  - `scrape_search_feed` → monkeypatch 替换为 fake 协程
  - `ScrapeStateRegistry` → 用真实实现（已通过 Phase 1 单测验证）
  - `MVPStorage` → 用真实实现 + tmp_path SQLite

---

## 2. Architecture Context

### System Position

```
┌──────────────────────────────────────────────────────────────┐
│ FastAPI App (server/app.py)  [Phase 2]                       │
│                                                              │
│  lifespan startup:                                           │
│    app.state.mvp_storage = MVPStorage(...)                   │
│    app.state.scraper_runtime = ScraperRuntime(...)  [NEW]    │
│    app.state.scrape_state_registry = ...()          [NEW]    │
│                                                              │
│  endpoints:                                                  │
│    GET  /api/scraper/readiness            [NEW]              │
│    POST /api/tasks/{tid}/auto-scrape      [NEW]   → 202      │
│    GET  /api/tasks/{tid}/scrape-status    [NEW]              │
│      ...existing endpoints unchanged...                      │
│                                                              │
│  lifespan shutdown:                                          │
│    await app.state.scraper_runtime.shutdown()      [NEW]     │
└────────────────────────────┬─────────────────────────────────┘
                             │ BackgroundTasks
                             ▼
                ┌────────────────────────────┐
                │ _run_scrape_background()   │
                │   1. on_progress callback  │
                │      → registry.update()   │
                │   2. scrape_search_feed()  │
                │      → list[CaptureItemIn] │
                │   3. storage.ingest_scraper│
                │      _items()              │
                │   4. registry.release()    │
                └────────────────────────────┘
```

### Tech Stack

- 复用 Phase 1 全部模块
- 新依赖：FastAPI `BackgroundTasks`（已在 fastapi 内）

### Constraints

- BackgroundTasks 在响应发送后运行；不能用于需要立即返回结果的场景（适合本任务）
- Storage 方法是同步 sqlite3；在 async 上下文里直接调（既有代码模式如此）
- 单进程单 registry，多 worker 部署会破坏并发控制（决策 6 已声明）

---

## 3. Technical Design

### 3.1 Module Structure

```
experiments/xhs_extension_mvp/server/
├── app.py            [MODIFY] lifespan + 3 endpoints + _run_scrape_background
├── models.py         [MODIFY] 4 个新模型
└── storage.py        [MODIFY] ingest_scraper_items
tests/unit/
├── test_scraper_endpoints.py         [NEW]
└── test_storage_scraper_ingest.py    [NEW]
```

### 3.2 Class & Interface Design

**3.2.1 `models.py` 新增**

```python
from experiments.xhs_extension_mvp.server.scraper_models import ScrapePhase

class AutoScrapeRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)
    scroll_count: int = Field(default=5, ge=1, le=10)


class AutoScrapeResponse(BaseModel):
    task_id: str
    accepted: bool
    started_at: datetime


class ScrapeStatusResponse(BaseModel):
    task_id: str
    keyword: str
    phase: ScrapePhase
    scroll_index: int
    scroll_total: int
    items_count: int
    error_message: str = ""
    started_at: datetime
    finished_at: Optional[datetime] = None


class ScraperReadinessResponse(BaseModel):
    profile_exists: bool
    logged_in: bool
    last_checked_at: datetime
    detail: str = ""
```

**3.2.2 `storage.py` 新增方法**

```python
def ingest_scraper_items(
    self,
    *,
    task_id: str,
    keyword: str,
    items: list[CaptureItemIn],
) -> tuple[int, int]:
    """Ingest scraper-collected items via the existing capture pipeline.

    No token validation (the scraper is server-authoritative; access control
    happens at endpoint registration). Returns (captured_count, new_count).
    """
    captured_count = len(items)
    if captured_count == 0:
        return 0, 0
    imported_count, _ = self.ingest_capture(
        task_id=task_id,
        page_type="search_result",
        query_text=keyword,
        items=items,
        capture_mode="scraper",
    )
    return captured_count, imported_count
```

**3.2.3 `app.py` 改造**

```python
# lifespan 改造（保持既有 mvp_storage 设置）
@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.mvp_storage = storage
    application.state.scraper_runtime = ScraperRuntime(
        profile_dir=Path("data/chrome-profile"),
    )
    application.state.scrape_state_registry = ScrapeStateRegistry()
    application.state.readiness_cache = _ReadinessCache(ttl_seconds=60)
    logger.info("Started XHS extension MVP app", extra={...})
    yield
    runtime = application.state.scraper_runtime
    if runtime is not None:
        await runtime.shutdown()
    logger.info("Stopped XHS extension MVP app", extra={...})
```

**3.2.4 Endpoints**

```python
@app.get("/api/scraper/readiness", response_model=ScraperReadinessResponse)
async def get_scraper_readiness(request: Request) -> ScraperReadinessResponse:
    """Detect whether the persistent profile exists and is logged in.

    Caches result for 60 s to avoid spawning a probe page on every poll.
    """
    runtime: ScraperRuntime = request.app.state.scraper_runtime
    cache: _ReadinessCache = request.app.state.readiness_cache

    cached = cache.get_fresh()
    if cached is not None:
        return cached

    profile_exists = runtime._profile_dir.exists() and any(runtime._profile_dir.iterdir())
    if not profile_exists:
        result = ScraperReadinessResponse(
            profile_exists=False,
            logged_in=False,
            last_checked_at=utc_now(),
            detail="profile dir is empty; please complete first-time login per README",
        )
        cache.put(result)
        return result

    # Probe live login state via a throwaway page
    try:
        page = await runtime.acquire_page()
        try:
            await page.goto(
                "https://www.xiaohongshu.com/",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            logged_in = await is_logged_in(page)
        finally:
            await page.close()
    except Exception as exc:
        result = ScraperReadinessResponse(
            profile_exists=True,
            logged_in=False,
            last_checked_at=utc_now(),
            detail=f"login probe failed: {exc}",
        )
        cache.put(result)
        return result

    result = ScraperReadinessResponse(
        profile_exists=True,
        logged_in=logged_in,
        last_checked_at=utc_now(),
        detail="ok" if logged_in else "not logged in; please complete login per README",
    )
    cache.put(result)
    return result


@app.post(
    "/api/tasks/{task_id}/auto-scrape",
    status_code=202,
    response_model=AutoScrapeResponse,
)
async def trigger_auto_scrape(
    task_id: str,
    payload: AutoScrapeRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> AutoScrapeResponse:
    storage: MVPStorage = request.app.state.mvp_storage
    registry: ScrapeStateRegistry = request.app.state.scrape_state_registry
    runtime: ScraperRuntime = request.app.state.scraper_runtime

    if not storage.task_exists(task_id):
        raise HTTPException(status_code=404, detail="Task not found")

    state = await registry.try_acquire(
        task_id=task_id,
        keyword=payload.keyword,
        scroll_total=payload.scroll_count,
    )
    if state is None:
        raise HTTPException(
            status_code=409,
            detail=f"another scrape already running (active task: {registry.active_task_id})",
        )

    background_tasks.add_task(
        _run_scrape_background,
        runtime=runtime,
        registry=registry,
        storage=storage,
        task_id=task_id,
        keyword=payload.keyword,
        scroll_count=payload.scroll_count,
    )
    return AutoScrapeResponse(
        task_id=task_id,
        accepted=True,
        started_at=state.started_at,
    )


@app.get(
    "/api/tasks/{task_id}/scrape-status",
    response_model=ScrapeStatusResponse,
)
async def get_scrape_status(task_id: str, request: Request) -> ScrapeStatusResponse:
    registry: ScrapeStateRegistry = request.app.state.scrape_state_registry
    state = registry.get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="No scrape recorded for this task")
    return ScrapeStatusResponse(
        task_id=state.task_id,
        keyword=state.keyword,
        phase=state.progress.phase,
        scroll_index=state.progress.scroll_index,
        scroll_total=state.progress.scroll_total,
        items_count=state.progress.items_count,
        error_message=state.progress.error_message,
        started_at=state.started_at,
        finished_at=state.finished_at,
    )
```

**3.2.5 后台任务**

```python
async def _run_scrape_background(
    *,
    runtime: ScraperRuntime,
    registry: ScrapeStateRegistry,
    storage: MVPStorage,
    task_id: str,
    keyword: str,
    scroll_count: int,
) -> None:
    """Drive the scrape, update registry progress, and ingest results."""

    async def _on_progress(progress: ScrapeProgress) -> None:
        await registry.update(task_id, progress)

    try:
        items = await scrape_search_feed(
            keyword=keyword,
            runtime=runtime,
            scroll_count=scroll_count,
            on_progress=_on_progress,
        )
        if items:
            captured, imported = storage.ingest_scraper_items(
                task_id=task_id,
                keyword=keyword,
                items=items,
            )
            logger.info("Scraper ingested items", extra={
                "event_name": "mvp_scraper_ingested",
                "task_id": task_id,
                "captured": captured,
                "imported": imported,
            })
    except Exception as exc:
        logger.exception("Background scrape failed")
        current = registry.get(task_id)
        progress = current.progress if current else ScrapeProgress(scroll_total=scroll_count)
        await registry.update(
            task_id,
            progress.with_phase(ScrapePhase.ERROR, error_message=str(exc)),
        )
    finally:
        await registry.release(task_id)
```

**3.2.6 Readiness 缓存**

```python
@dataclass
class _ReadinessCache:
    ttl_seconds: int
    _value: ScraperReadinessResponse | None = None

    def get_fresh(self) -> ScraperReadinessResponse | None:
        if self._value is None:
            return None
        age = (utc_now() - self._value.last_checked_at).total_seconds()
        if age > self.ttl_seconds:
            return None
        return self._value

    def put(self, value: ScraperReadinessResponse) -> None:
        self._value = value
```

### 3.3 Algorithm & Logic Flow

**Auto-scrape 主流程**:

```
HTTP POST /api/tasks/{tid}/auto-scrape
  → validate task exists (else 404)
  → registry.try_acquire(tid, keyword, scroll_total)
      ├─ None → 409
      └─ state → register background_task + return 202

Background:
  scrape_search_feed(keyword, runtime, scroll_count, on_progress=registry.update)
    on each scroll → registry.update(tid, progress)
  → items
  if items:
    storage.ingest_scraper_items(tid, keyword, items)
  on exception:
    registry.update(tid, progress.with_phase(ERROR, error_message=...))
  finally:
    registry.release(tid)
```

**Status query**:

```
HTTP GET /api/tasks/{tid}/scrape-status
  → registry.get(tid)
      ├─ None → 404
      └─ state → ScrapeStatusResponse
```

### 3.4 Implementation Checklist

- [ ] models.py 新增 4 个 pydantic 模型
- [ ] storage.py 新增 `ingest_scraper_items` 方法
- [ ] app.py 改 lifespan：注册 runtime + registry + readiness_cache，关停时 shutdown
- [ ] app.py 加 3 个 endpoint + `_run_scrape_background` + `_ReadinessCache`
- [ ] 写 test_scraper_endpoints.py（mock scrape + runtime）
- [ ] 写 test_storage_scraper_ingest.py（真实 SQLite）
- [ ] 运行 Phase 1 + Phase 2 全部单测，确保零回归

### 3.5 Error Handling Strategy

```
HTTPException:
  404 - task_not_found / no_scrape_recorded
  409 - concurrent_scrape_active

后台异常:
  scrape_search_feed 抛异常 → ERROR phase + registry release
  ingest_scraper_items 抛异常 → ERROR phase + registry release（同一 try/except）

readiness 异常:
  profile 不存在 → profile_exists=False, logged_in=False
  probe page 失败 → logged_in=False + detail 含错误
  is_logged_in False → logged_in=False + detail 引导 README
```

**State / Persistence Notes**:
- `ScrapeStateRegistry` 是进程内 dict（单 worker 假设）
- `_ReadinessCache` 60s TTL，避免高频 banner 刷新引起反复 probe
- `ingest_capture` 已含去重 + snapshot_version++ 逻辑，本任务零修改

---

## 4. Testing Strategy

### 4.1 Test Pyramid Mapping

| Level | File | Count | Focus | Mock Strategy |
|---|---|---|---|---|
| Unit | `tests/unit/test_storage_scraper_ingest.py` | 3-4 | ingest_scraper_items 去重 / version++ | 真实 SQLite (tmp_path) |
| Unit | `tests/unit/test_scraper_endpoints.py` | 8-10 | 3 个 endpoint 的状态码与状态机 | mock `scrape_search_feed` + Fake `ScraperRuntime` |

### 4.2 Critical Test Scenarios

**`test_storage_scraper_ingest.py`**:
1. `test_ingest_writes_items_to_capture_table` — 写 5 条，验证 mvp_capture_items 多 5 行
2. `test_ingest_dedupes_by_note_id` — 写两次相同 note_id 的 item，验证只有 1 行
3. `test_ingest_increments_snapshot_version` — 取前后 snapshot_version 对比 +1
4. `test_ingest_empty_list_is_noop` — 空列表不写、不抛
5. `test_ingest_uses_capture_mode_scraper` — 验证 mvp_captures 表 `capture_mode = "scraper"`

**`test_scraper_endpoints.py`**:
1. `test_auto_scrape_returns_202_on_first_call` — 创建 task 后 POST 返回 202
2. `test_auto_scrape_returns_404_for_unknown_task`
3. `test_auto_scrape_returns_409_when_busy` — 第一次 POST 后 registry busy，第二次返回 409
4. `test_auto_scrape_completes_and_ingests_items` — mock scraper 返回 5 items，等 background 完成后查 storage 多 5 条
5. `test_auto_scrape_handles_login_required` — mock scraper 返回 []（LOGIN_REQUIRED 路径），不写 storage
6. `test_auto_scrape_handles_scrape_exception` — mock 抛异常，registry 进入 ERROR + release
7. `test_scrape_status_returns_404_for_unknown_task`
8. `test_scrape_status_returns_progress_after_trigger` — 触发后查 status 应返回 LAUNCHING 或 DONE
9. `test_readiness_returns_profile_missing` — profile dir 不存在
10. `test_readiness_caches_result_within_ttl` — 两次连续调，第二次不应再 probe（用计数器验证）

### 4.3 Test Data Fixtures

```python
# tests/unit/test_scraper_endpoints.py
class FakeScraperRuntime:
    def __init__(self):
        self.shutdown_called = False
        self.acquire_count = 0

    async def ensure_started(self):
        return object()

    async def acquire_page(self):
        self.acquire_count += 1
        return _FakePage()  # see test_scraper_login.py for shape

    async def shutdown(self):
        self.shutdown_called = True


@pytest.fixture
def app_with_fakes(tmp_path, monkeypatch):
    """Build a test app with FakeScraperRuntime and a controllable scrape."""
    from experiments.xhs_extension_mvp.server.app import create_app
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    # Override runtime in lifespan via app.state after startup
    ...
    return app
```

### 4.4 Background Task Synchronization

FastAPI `BackgroundTasks` runs **after the response is sent**. To assert
post-completion state in tests, use `TestClient`'s context manager — it waits
for background tasks to complete before exiting:

```python
with TestClient(app) as client:
    resp = client.post(f"/api/tasks/{tid}/auto-scrape", json={...})
    # background task runs here on context exit
# now assert post-completion state
```

If that doesn't work for our setup, await directly using a synchronous adapter
in the mock scrape function.

---

## 5. Implementation Checklist

### Coding Sequence (Order Matters)

1. [ ] models.py — 加 4 个 pydantic 模型
2. [ ] storage.py — 加 `ingest_scraper_items`
3. [ ] storage 单测 — 验证 ingest + dedup + version++
4. [ ] app.py — 改 lifespan + 加 3 个 endpoint + 后台任务函数 + readiness cache
5. [ ] endpoint 单测 — 验证 3 个 endpoint 与后台流转
6. [ ] 跑 Phase 1 + Phase 2 全部单测 + 既有 MVP 测试，确认零额外回归

### Dependencies to Install/Verify

无新依赖。FastAPI 已包含 `BackgroundTasks`。

### Configuration Required

无（profile_dir 在 lifespan 里硬编码 `data/chrome-profile`，与 Phase 1 CLI 一致）。

---

## 6. Risk & Notes

**Technical Debt Warning**:
- `_ReadinessCache` 是简单 in-process dataclass；多 worker 会各自维护一份缓存（决策 6 已说明）
- BackgroundTasks 与请求响应同一 event loop，长时间 scrape（30s+）会占用 worker；可接受（单用户单 task）

**Architecture Decision**:
- 选 `BackgroundTasks` 而非独立 task queue（Celery / RQ）：打通阶段不引入新基础设施
- readiness probe 走真实 page.goto，不读 cookies file：通用 + 不依赖具体 cookie 格式

**Spec Alignment**:
- Phase 2 的 endpoints 形态与 improvements.md §3.2 已锁的 sudo code 完全一致
- 3 个 endpoint（不是 4 个）— 决策 5 删除了 login endpoint
- Phase 2 验收标准里 6-7 项（"V2 topic_pool 自动出现新候选"）属 Stage B 范围，不在本任务

**Cross-task Dependencies**:
- Phase 3（前端 UI）依赖 `ScraperReadinessResponse` / `AutoScrapeResponse` / `ScrapeStatusResponse` 字段稳定
- Stage B（V2 sink）依赖 `_run_scrape_background` 内部可注入 sink 列表（本任务先不实现，但要把 ingest 写在一个清晰隔离的位置便于扩展）

## 7. Spec Sync Expectations

- Stage 5 由 progress-tracker 在 improvements.md Phase 2「✅ 完成进度」回填
- Phase 1 残留 #1（runtime 自动重启）通过本任务 endpoint 集成测试间接覆盖；若仍未充分覆盖，标 carry-forward 到 Phase 4
- 任何未关闭项写入 Phase 2「⚠️ 遗留问题」
- 既有 `test_xhs_extension_mvp.py` 5 个失败保持 carry-forward（独立 issue）
