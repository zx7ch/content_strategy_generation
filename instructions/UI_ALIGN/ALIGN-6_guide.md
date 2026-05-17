# Development Guide: ALIGN-6 - Job Control API

> Generated: 2026-05-16
> Source: docs/changes/2026-05-16-frontend-scope-v1-v2-alignment.md

## 1. Task Context

### Scope Boundary
- **Task ID**: ALIGN-6
- **Dependencies**: ALIGN-5 (Done) — intent router, job_store session-level pause/resume/cancel
- **Goal**: 新增 `POST /jobs/{job_id}/pause|resume|cancel` 三个 API 端点，把 job-level 控制从 message intent 解耦出来，并让 worker 在写成功结果前检查 cancel 状态

### In Scope
- 新增 `app/memory/job_store.py` — 3 个 job-level 方法：`pause_job`, `resume_job`, `cancel_job`
- 新增 `app/models/schemas.py` — `JobControlResponse` schema
- 新增 `app/api/routes/router.py` — 3 个端点：`POST /jobs/{id}/pause`, `POST /jobs/{id}/resume`, `POST /jobs/{id}/cancel`
- 修改 `app/workers/job_worker.py` — `_execute_job` 在 `mark_succeeded` 前检查 cancel 状态（stage boundary guard）
- 新增 `tests/unit/test_job_store.py` — 扩展：3 个 job-level 控制方法单测
- 新增 `tests/e2e/test_job_control_api.py` — 4 个 e2e 测试

### Out Of Scope
- 抢占式中断运行中 job（不实现）：`pause_job` 只暂停 `queued/retrying` 状态；`running` 状态返回当前 job，API 返回 409
- `pause_requested` flag on jobs table：jobs 表目前没有该字段（sessions 表有，但不适用于 jobs）；不做 schema migration，不实现软暂停信号
- SSE 事件推送（ALIGN-7 范围）：job-control 成功不主动推 SSE，只更新 DB 状态
- UI 任务条真实状态更新（前端消费 SSE 是 ALIGN-7 范围）

### Required Deliverables
- Production: `app/memory/job_store.py`（3 个新方法）、`app/models/schemas.py`（1 个新 schema）、`app/api/routes/router.py`（3 个端点）、`app/workers/job_worker.py`（cancel guard）
- Tests: `tests/unit/test_job_store.py`（扩展）、`tests/e2e/test_job_control_api.py`（新建）
- Spec: `instructions/ALIGN-6_guide.md`（本文件）、`docs/changes/2026-05-16-frontend-scope-v1-v2-alignment.md`（回填进展）

### Acceptance Criteria
- [ ] AC1: `POST /jobs/{id}/pause` — queued/retrying job → 200, status=paused
- [ ] AC2: `POST /jobs/{id}/pause` — running job → 409 "job is currently running; cannot pause"
- [ ] AC3: `POST /jobs/{id}/pause` — not found → 404
- [ ] AC4: `POST /jobs/{id}/resume` — paused job → 200, status=queued
- [ ] AC5: `POST /jobs/{id}/resume` — non-paused job → 409
- [ ] AC6: `POST /jobs/{id}/cancel` — queued/paused/retrying/running job → 200, status=cancelled
- [ ] AC7: `POST /jobs/{id}/cancel` — already cancelled/succeeded/failed job → 409
- [ ] AC8: `POST /jobs/{id}/cancel` — not found → 404
- [ ] AC9: worker does NOT call `mark_succeeded` if job was cancelled in DB while running

### Residual Obligations
- **TD-ALIGN5-1 [本任务部分关闭]**:
  - `cancel` 对 running job 支持（worker stage boundary check）→ **本任务关闭**
  - `pause` 对 running job（需要 jobs 表新增 pause_requested）→ **仍 OPEN，不在本任务范围**，新建 TD-ALIGN6-1 carry into 后续 worker 增强
- **TD-ALIGN4-3**: 轮询替换 SSE → ALIGN-7 处理，本任务不触碰

### ⚠ 技术债务（本任务产生）
- **TD-ALIGN6-1 [待排期]**: running job 的软暂停（pause_requested 信号）需要在 jobs 表新增 `pause_requested BOOLEAN` 字段，worker 在调用 orchestrator 前检查。当前 MVP：pause 对 running job 返回 409。carry into: 后续 worker 增强任务（无对应 ALIGN 任务，需单独排期）

---

## 2. Architecture Context

### System Position
```
POST /jobs/{job_id}/pause
POST /jobs/{job_id}/resume
POST /jobs/{job_id}/cancel
  └─ job_store.get_job(job_id)           → 404 if not found
  └─ validate state transition            → 409 if invalid
  └─ job_store.pause_job / resume_job / cancel_job
  └─ return JobControlResponse(job_id, session_id, status)

JobWorker._execute_job:
  result = await orchestrator.run_job(job)
  # NEW: stage boundary cancel check
  current = await job_store.get_job(job.id)
  if current and current.status == 'cancelled':
      emit task_cancelled event
      return JobExecutionResult(success=False, failed=False)
  await job_store.mark_succeeded(job.id)
```

### State Machine (jobs table)
```
queued ──pause──► paused ──resume──► queued
queued ──lease──► running ──mark_succeeded──► succeeded
                  running ──cancel (in DB)──► cancelled (worker skips mark_succeeded)
queued/retrying ──cancel──► cancelled
paused ──cancel──► cancelled
running: pause → 409 (not supported in MVP without jobs.pause_requested column)
succeeded/failed/cancelled: all control ops → 409
```

### Key Behavioral Constraints
- `pause_job`: only `queued` or `retrying` status jobs can be paused; `running` → 409
- `cancel_job`: all `queued/paused/retrying/running` jobs → cancelled immediately in DB
- `resume_job`: only `paused` jobs → back to `queued`
- `cancel_session_jobs` (existing) already handles `running` → cancelled for session-level; job-level cancel replicates this
- Worker guard: after `orchestrator.run_job` completes, re-fetch job status before `mark_succeeded`; if `cancelled`, skip success write and emit `task_cancelled` event

---

## 3. Technical Design

### 3.1 Module Structure

**Files to Create/Modify:**
```
app/memory/job_store.py     MODIFY  add pause_job, resume_job, cancel_job
app/models/schemas.py       MODIFY  add JobControlResponse
app/api/routes/router.py    MODIFY  add 3 job-control endpoints
app/workers/job_worker.py   MODIFY  cancel guard in _execute_job
tests/unit/test_job_store.py          MODIFY  add 3+ unit tests
tests/e2e/test_job_control_api.py     NEW     4 e2e tests
```

**Per-file Change Intent:**

| Path | NEW/MODIFY | Required Change | Linked AC |
|------|------------|-----------------|-----------|
| `app/memory/job_store.py` | MODIFY | 新增 `pause_job`, `resume_job`, `cancel_job` | AC1-9 |
| `app/models/schemas.py` | MODIFY | 新增 `JobControlResponse` | AC1-8 |
| `app/api/routes/router.py` | MODIFY | 新增 3 个端点 + 409 逻辑 | AC1-8 |
| `app/workers/job_worker.py` | MODIFY | `_execute_job` cancel guard | AC9 |
| `tests/unit/test_job_store.py` | MODIFY | 3 新单测 | AC1-9 |
| `tests/e2e/test_job_control_api.py` | NEW | 4 e2e 测试 | AC1-8 |

### 3.2 JobStore New Methods

```python
async def pause_job(self, job_id: str) -> Optional[JobRecord]:
    """
    Pause a single job.
    - queued/retrying → status='paused'
    - running/paused/succeeded/failed/cancelled → no DB change, return as-is
    - None → job not found
    Caller checks returned job.status to decide 409.
    """
    assert self._conn is not None
    async with self._conn.execute(
        """
        UPDATE jobs
        SET status = 'paused', updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status IN ('queued', 'retrying')
        """,
        (job_id,),
    ) as cursor:
        updated = cursor.rowcount > 0
    await self._conn.commit()
    # Fetch regardless — lets caller distinguish not-found vs invalid-transition
    return await self.get_job(job_id)


async def resume_job(self, job_id: str) -> Optional[JobRecord]:
    """
    Resume a paused job → queued.
    - paused → status='queued', not_before=CURRENT_TIMESTAMP
    - other statuses → no DB change, return as-is
    - None → job not found
    """
    assert self._conn is not None
    async with self._conn.execute(
        """
        UPDATE jobs
        SET status = 'queued',
            not_before = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'paused'
        """,
        (job_id,),
    ):
        pass
    await self._conn.commit()
    return await self.get_job(job_id)


async def cancel_job(
    self, job_id: str, reason: str = "user_cancelled"
) -> Optional[JobRecord]:
    """
    Cancel a single job.
    - queued/paused/retrying/running → status='cancelled', cancel_reason=reason
    - succeeded/failed/cancelled → no DB change, return as-is
    - None → job not found
    """
    assert self._conn is not None
    async with self._conn.execute(
        """
        UPDATE jobs
        SET status = 'cancelled',
            cancel_reason = ?,
            lease_expires_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND status IN ('queued', 'paused', 'retrying', 'running')
        """,
        (reason, job_id),
    ):
        pass
    await self._conn.commit()
    return await self.get_job(job_id)
```

### 3.3 Schema Addition

```python
# app/models/schemas.py — append after existing creator schemas
class JobControlResponse(BaseModel):
    job_id: str
    session_id: str
    status: str
```

### 3.4 Router Endpoints

```python
# Shared validation helper (private)
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

@app.post("/jobs/{job_id}/pause", response_model=JobControlResponse)
async def pause_job(job_id: str, request: Request) -> JobControlResponse:
    job_store = _get_job_store(request)
    job = await job_store.pause_job(job_id)
    if job is None:
        raise APIError(status_code=404, error_code="JOB_NOT_FOUND",
                       error_message=f"Job {job_id} not found")
    if job.status == "running":
        raise APIError(status_code=409, error_code="JOB_RUNNING",
                       error_message="Job is currently running; only queued/retrying jobs can be paused")
    if job.status in _TERMINAL_STATUSES:
        raise APIError(status_code=409, error_code="JOB_TERMINAL",
                       error_message=f"Job is already in terminal state: {job.status}")
    return JobControlResponse(job_id=job.id, session_id=job.session_id, status=job.status)


@app.post("/jobs/{job_id}/resume", response_model=JobControlResponse)
async def resume_job(job_id: str, request: Request) -> JobControlResponse:
    job_store = _get_job_store(request)
    job = await job_store.resume_job(job_id)
    if job is None:
        raise APIError(status_code=404, error_code="JOB_NOT_FOUND",
                       error_message=f"Job {job_id} not found")
    # If resume had no effect (status was not paused), return 409
    if job.status != "queued":
        raise APIError(status_code=409, error_code="JOB_NOT_PAUSED",
                       error_message=f"Job cannot be resumed from status: {job.status}")
    return JobControlResponse(job_id=job.id, session_id=job.session_id, status=job.status)
```

**IMPORTANT for resume_job endpoint**: After `resume_job`, if the returned status is still `paused` (meaning no row was updated), it means it wasn't paused — BUT if it's `queued`, it could mean it was already queued OR we just resumed it. 

Better approach: pre-check status before calling the method:

```python
@app.post("/jobs/{job_id}/resume", response_model=JobControlResponse)
async def resume_job(job_id: str, request: Request) -> JobControlResponse:
    job_store = _get_job_store(request)
    # Pre-fetch to validate transition
    existing = await job_store.get_job(job_id)
    if existing is None:
        raise APIError(status_code=404, error_code="JOB_NOT_FOUND", ...)
    if existing.status != "paused":
        raise APIError(status_code=409, error_code="JOB_NOT_PAUSED",
                       error_message=f"Job cannot be resumed from status: {existing.status}")
    job = await job_store.resume_job(job_id)
    assert job is not None
    return JobControlResponse(job_id=job.id, session_id=job.session_id, status=job.status)


@app.post("/jobs/{job_id}/cancel", response_model=JobControlResponse)
async def cancel_job_endpoint(job_id: str, request: Request) -> JobControlResponse:
    job_store = _get_job_store(request)
    existing = await job_store.get_job(job_id)
    if existing is None:
        raise APIError(status_code=404, error_code="JOB_NOT_FOUND", ...)
    if existing.status in _TERMINAL_STATUSES:
        raise APIError(status_code=409, error_code="JOB_TERMINAL",
                       error_message=f"Job is already in terminal state: {existing.status}")
    job = await job_store.cancel_job(job_id, reason="user_cancelled")
    assert job is not None
    return JobControlResponse(job_id=job.id, session_id=job.session_id, status=job.status)
```

**Note on pause endpoint**: For running jobs, `pause_job` returns job with status=`running` (no DB update). The endpoint can use the same pre-check pattern:
```python
@app.post("/jobs/{job_id}/pause", response_model=JobControlResponse)
async def pause_job_endpoint(job_id: str, request: Request) -> JobControlResponse:
    job_store = _get_job_store(request)
    existing = await job_store.get_job(job_id)
    if existing is None:
        raise APIError(status_code=404, error_code="JOB_NOT_FOUND", ...)
    if existing.status == "running":
        raise APIError(status_code=409, error_code="JOB_RUNNING",
                       error_message="Job is currently running; only queued/retrying jobs can be paused")
    if existing.status in _TERMINAL_STATUSES:
        raise APIError(status_code=409, error_code="JOB_TERMINAL",
                       error_message=f"Job is already in terminal state: {existing.status}")
    job = await job_store.pause_job(job_id)
    assert job is not None
    return JobControlResponse(job_id=job.id, session_id=job.session_id, status=job.status)
```

### 3.5 Worker Cancel Guard

```python
async def _execute_job(self, job: JobRecord) -> JobExecutionResult:
    await self.job_store.append_session_event(...)  # existing task_progress event
    try:
        result = await self.orchestrator.run_job(job)

        # Stage boundary cancel check: job may have been cancelled while orchestrator was running
        current = await self.job_store.get_job(job.id)
        if current is not None and current.status == "cancelled":
            await self.job_store.append_session_event(
                session_id=job.session_id,
                job_id=job.id,
                event_name="task_cancelled",
                stage=job.job_type,
                payload={
                    "message": f"{job.job_type} job cancelled before result was written",
                    "progress": None,
                    "error_code": None,
                    "details": {"cancel_reason": current.cancel_reason},
                },
            )
            return JobExecutionResult(success=False, failed=False)

        await self.job_store.mark_succeeded(job.id)
        await self.job_store.append_session_event(...)  # existing task_completed event
        return JobExecutionResult(success=True)
    except Exception as exc:
        ...  # existing error handling unchanged
```

---

## 4. Testing Strategy

### 4.1 Test Pyramid Mapping

| Level | File | Count | Focus |
|-------|------|-------|-------|
| Unit | `tests/unit/test_job_store.py` | +4 | pause_job, resume_job, cancel_job state transitions |
| E2E | `tests/e2e/test_job_control_api.py` | 4 | API 404/409/200 paths |

### 4.2 Unit Test Scenarios (additions to test_job_store.py)

| # | Test name | Scenario |
|---|-----------|---------|
| 1 | `test_pause_job_queued_job` | 创建 queued job → pause_job → status=paused |
| 2 | `test_pause_job_running_job_no_change` | 模拟 running job → pause_job → status 仍为 running |
| 3 | `test_resume_job_paused_job` | paused job → resume_job → status=queued |
| 4 | `test_cancel_job_queued_job` | queued job → cancel_job → status=cancelled, cancel_reason="user_cancelled" |
| 5 | `test_cancel_job_running_job` | 强制设置 running → cancel_job → status=cancelled |
| 6 | `test_pause_job_not_found` | 不存在 job_id → pause_job → returns None |

### 4.3 E2E Test Scenarios (test_job_control_api.py)

| # | Test name | Scenario |
|---|-----------|---------|
| 1 | `test_pause_queued_job_returns_200` | 创建 workflow → job 在 queued → POST /jobs/{id}/pause → 200, status=paused |
| 2 | `test_resume_paused_job_returns_200` | pause 后 → POST /jobs/{id}/resume → 200, status=queued |
| 3 | `test_cancel_queued_job_returns_200` | queued job → POST /jobs/{id}/cancel → 200, status=cancelled |
| 4 | `test_cancel_nonexistent_job_returns_404` | POST /jobs/does-not-exist/cancel → 404 |

---

## 5. Implementation Checklist

1. [ ] `app/memory/job_store.py` — 新增 `pause_job`, `resume_job`, `cancel_job` 3 个方法
2. [ ] `app/models/schemas.py` — 新增 `JobControlResponse`
3. [ ] `app/api/routes/router.py` — 新增 `_TERMINAL_STATUSES` + 3 个端点（prefetch pattern）
4. [ ] `app/workers/job_worker.py` — `_execute_job` cancel guard（post-run_job, pre-mark_succeeded）
5. [ ] `tests/unit/test_job_store.py` — 新增 6 个 job-level 单测
6. [ ] `tests/e2e/test_job_control_api.py` — 新建，4 个 e2e 测试

---

## 6. Risk & Notes

**APIError 使用**: router.py 中已有 `APIError` 用法（参见 `/sessions/{id}` endpoints）。直接复用 `raise APIError(status_code=..., error_code=..., error_message=...)` 模式。

**函数名冲突**: 避免端点函数名与 job_store 方法同名。端点函数使用 `pause_job_endpoint`, `resume_job_endpoint`, `cancel_job_endpoint`，或者使用 FastAPI 的 `name=` 参数（建议用后缀方式更清晰）。

**pre-fetch + update 竞争条件**: 两步操作（先 get_job 再 update）之间有理论上的竞争窗口（job 状态可能被 worker 改变）。MVP 规模下 SQLite 单线程写入，风险极低，不实现分布式锁。

**cancel running job 实际效果**: 设置 DB 为 cancelled 后，worker 如果已经拿到 lease 正在跑 orchestrator，仍会跑完才检查。cancel guard 在 `run_job` 返回后才生效，不是实时中断。这是 MVP 的已知限制，在 spec 里明确为 TD-ALIGN6-1 关联 TD-ALIGN5-1 残留。

---

## 7. Spec Sync Expectations

- TD-ALIGN5-1: 
  - **cancel 对 running job 支持** → 本任务完成（worker cancel guard），在 spec 里更新为 `[-]` → `[x]` 对应部分
  - **pause 对 running job** → 仍 OPEN，新建 TD-ALIGN6-1，写入 ALIGN-6 进展的 `[-]` 项
- TD-ALIGN4-3: 仍 OPEN（ALIGN-7 处理）
- ALIGN-6 完成进展回填：使用 `[x]/[-]/[ ]` checklist 格式
