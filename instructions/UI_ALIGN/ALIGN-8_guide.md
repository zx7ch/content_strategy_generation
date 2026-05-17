# Development Guide: ALIGN-8 - Manual Complete / Publish Candidate

> Generated: 2026-05-16
> Architect: implementation skill (dev-helper Stage 2)
> Status: Ready for development
> Source: docs/changes/2026-05-16-frontend-scope-v1-v2-alignment.md §ALIGN-8

---

## 1. Task Context

### Scope Boundary

- **Task ID**: ALIGN-8
- **Task Name**: Manual Complete / Publish Candidate
- **Phase**: Creator Workbench MVP — final step
- **Dependencies**: ALIGN-7 Done (all ALIGN-1 through ALIGN-7 Done)
- **Task Goal**:
  - Let users click "完成" to manually end and accept the current Creator task.
  - Convert generated notes into publish candidates in the Workspace Console.
  - Show strategy + generated notes in `creator/page.tsx` after generation completes.

### In Scope

- `POST /threads/{thread_id}/complete` — mark thread `accepted`, idempotent, generate publish candidates from active session's generated notes.
- `GET /publish-candidates` — return all publish candidates (no brand filter, global Creator output pool).
- `GET /threads/{thread_id}/result` — return strategy + notes for the thread's active session (needed by frontend display).
- New `publish_candidates` SQLite table in `creator_threads.db` (managed by ThreadStore).
- `ThreadStore.complete_thread()`, `save_publish_candidates()`, `count_publish_candidates()`, `list_publish_candidates()`.
- New Pydantic schemas: `PublishCandidate`, `CompleteThreadResponse`, `PublishCandidatesResponse`, `GeneratedNoteItem`, `ThreadResultResponse`.
- `frontend/src/lib/api.ts` — `completeThread()`, `getPublishCandidates()`, `getThreadResult()` + matching types.
- `frontend/src/app/creator/page.tsx` — fetch thread result after generation completes; show strategy + notes in chat; show "完成" button; call `completeThread()` on click.
- `frontend/src/app/publish/page.tsx` — add Creator candidates section (fetch `getPublishCandidates`, render table).
- `tests/e2e/test_creator_publish_candidate.py` — 4 e2e tests.

### Out Of Scope

- Writing into Topic Pool or triggering decision/bandit loop.
- `POST /publish-records/from-candidate` (listed as optional in spec — defer).
- Per-brand filtering of candidates.
- Pagination for `/publish-candidates`.
- Auth/RBAC (TD-ALIGN7-2 still deferred, local-first single-user).

### Required Deliverables

- **Production**:
  - `app/models/schemas.py` — 5 new schema classes
  - `app/memory/thread_store.py` — `publish_candidates` table + 4 new methods
  - `app/api/routes/router.py` — 3 new endpoints + schema imports
  - `frontend/src/lib/api.ts` — 3 new functions + 4 new types
  - `frontend/src/app/creator/page.tsx` — result display + "完成" button
  - `frontend/src/app/publish/page.tsx` — Creator candidates section
- **Tests**:
  - `tests/e2e/test_creator_publish_candidate.py` — 4 tests

### Acceptance Criteria

- [ ] AC1: `POST /threads/{id}/complete` sets thread status to `"accepted"` and returns `publish_candidate_count`.
- [ ] AC2: Complete is idempotent — calling twice returns same result without duplicate candidates.
- [ ] AC3: `GET /publish-candidates` returns all candidates with full fields.
- [ ] AC4: Frontend "完成" button calls `completeThread()` and shows confirmation.
- [ ] AC5: Creator page shows strategy and generated notes after generation completes.
- [ ] AC6: `/publish` page has Creator candidates section.
- [ ] AC7: `POST /threads/nonexistent/complete` returns 404.

### Residual Obligations

No residual tracker section exists in the spec file. No open residuals from previous tasks directly block ALIGN-8.

**Tech debts still open from prior tasks (not ALIGN-8 scope)**:
- TD-ALIGN5-4 (`redirect_job` intent) — not blocking here.
- TD-ALIGN6-1 (running job pause) — not blocking here.
- TD-ALIGN7-1 (EventSource auto-rebuild on session switch) — not blocking here.
- TD-ALIGN7-2 (SSE auth) — local-first acceptable, not blocking.

**Newly introduced carry-forward risk**: If a thread has no active session or no generated notes, `complete_thread` must still succeed (0 candidates). Must not error.

### Contract Inventory

- **Upstream** (ALIGN-8 reads from):
  - `ThreadStore.get_thread()` → returns dict with `active_workflow_session_id`
  - `SessionDataStore.get_generated_notes(session_id, note_ids=None)` → `list[GeneratedNote]`
  - `SessionDataStore.get_strategy(session_id, None)` → `(ContentStrategy, pref, strategy_id)`
  - `SessionManager` owns the SQLite connection to `xhs_agent.db`; ALIGN-8 must open its own `aiosqlite` connection to that DB path.
- **Downstream** (ALIGN-8 writes to):
  - `publish_candidates` table (new, in `creator_threads.db`)
  - `creator_threads.status` and `creator_threads.accepted_at`

### Test Requirements

- **Test File**: `tests/e2e/test_creator_publish_candidate.py`
- **Test Scenarios**:
  1. `test_complete_thread_marks_accepted` — create thread, POST complete, assert status=accepted + publish_candidate_count is int.
  2. `test_complete_thread_idempotent` — POST complete twice, second call returns 200 with same status.
  3. `test_complete_nonexistent_thread_404` — POST complete on missing thread_id → 404.
  4. `test_list_publish_candidates_empty` — GET /publish-candidates on fresh store → 200 + empty items list.
- **Pattern**: Same `client` fixture as `test_creator_thread_api.py` (inject both `thread_store` and `job_store` onto `app.state`).

---

## 2. Architecture Context

### System Position

```
Browser (creator/page.tsx)
  ├── onCompleted (generation stage) → GET /threads/{id}/result → show notes/strategy
  ├── "完成" button click → POST /threads/{id}/complete
  └── (publish/page.tsx) → GET /publish-candidates

FastAPI Router
  ├── POST /threads/{id}/complete
  │     ├── ThreadStore.get_thread() [creator_threads.db]
  │     ├── aiosqlite → xhs_agent.db → SessionDataStore.get_generated_notes()
  │     ├── ThreadStore.complete_thread()
  │     └── ThreadStore.save_publish_candidates()
  ├── GET /publish-candidates → ThreadStore.list_publish_candidates()
  └── GET /threads/{id}/result
        ├── ThreadStore.get_thread()
        └── aiosqlite → xhs_agent.db → get_strategy + get_generated_notes

ThreadStore (creator_threads.db)
  └── publish_candidates table (NEW)

SessionDataStore (xhs_agent.db)
  └── generation_data table (existing, read-only from ALIGN-8)
```

### Tech Stack

- Language/runtime: Python 3.11 / FastAPI (async), Next.js 14 (React client component)
- Cross-DB access: open a fresh `aiosqlite.connect(settings.SQLITE_DB_PATH)` in the endpoint, create a `SessionDataStore` against that connection, read notes/strategy, close. No connection sharing with `SessionManager` (which owns its own connection in the background worker context).
- Idempotency key: `(thread_id, note_id)` uniqueness enforced via `INSERT OR IGNORE WHERE NOT EXISTS` in `publish_candidates`.

### Constraints

- `SessionDataStore.__init__` takes an `aiosqlite.Connection` object directly (not a path). Must `await aiosqlite.connect(...)` and pass the connection.
- The complete endpoint must not fail if there are no generated notes (0-candidate result is valid).
- `GET /threads/{id}/result` returns `{"strategy": null, "notes": []}` if session has no data — do not raise 500.

---

## 3. Technical Design

### 3.1 Module Structure

```
app/models/schemas.py        MODIFY — add 5 new schemas
app/memory/thread_store.py   MODIFY — add publish_candidates table + 4 methods
app/api/routes/router.py     MODIFY — add 3 endpoints + schema imports
frontend/src/lib/api.ts      MODIFY — add 3 functions + 4 types
frontend/src/app/creator/page.tsx   MODIFY — result display + 完成 button
frontend/src/app/publish/page.tsx   MODIFY — Creator candidates section
tests/e2e/test_creator_publish_candidate.py   NEW — 4 e2e tests
```

**Per-file Change Intent**:

| Path | NEW/MODIFY | Required Change | Linked AC |
|------|------------|-----------------|-----------|
| `app/models/schemas.py` | MODIFY | Add `PublishCandidate`, `CompleteThreadResponse`, `PublishCandidatesResponse`, `GeneratedNoteItem`, `ThreadResultResponse` | AC1–AC3 |
| `app/memory/thread_store.py` | MODIFY | `publish_candidates` table in `_init_tables`; add `complete_thread`, `save_publish_candidates`, `count_publish_candidates`, `list_publish_candidates` | AC1–AC3 |
| `app/api/routes/router.py` | MODIFY | Import 5 new schemas; add 3 endpoints | AC1–AC3, AC7 |
| `frontend/src/lib/api.ts` | MODIFY | `PublishCandidate`, `CompleteThreadResponse`, `GeneratedNoteItem`, `ThreadResult` types; `completeThread`, `getPublishCandidates`, `getThreadResult` functions | AC4–AC6 |
| `frontend/src/app/creator/page.tsx` | MODIFY | `generatedResult` state; fetch result on generation complete; render strategy/notes; "完成" button | AC4, AC5 |
| `frontend/src/app/publish/page.tsx` | MODIFY | Add Creator candidates section with `useEffect` + `getPublishCandidates` | AC6 |
| `tests/e2e/test_creator_publish_candidate.py` | NEW | 4 e2e tests | AC1–AC3, AC7 |

### 3.2 Class & Interface Design

#### `app/models/schemas.py` additions

```python
class PublishCandidate(BaseModel):
    candidate_id: str
    thread_id: str
    session_id: str
    note_id: str
    title: str
    content: str
    tags: List[str]
    created_at: str

class CompleteThreadResponse(BaseModel):
    thread_id: str
    status: str
    publish_candidate_count: int

class PublishCandidatesResponse(BaseModel):
    items: List[PublishCandidate]

class GeneratedNoteItem(BaseModel):
    note_id: str
    title: str
    content: str
    tags: List[str] = Field(default_factory=list)

class ThreadResultResponse(BaseModel):
    thread_id: str
    session_id: Optional[str]
    strategy: Optional[Dict[str, Any]]
    notes: List[GeneratedNoteItem]
```

#### `app/memory/thread_store.py` additions

```python
# In _init_tables — add after creator_messages index:
CREATE TABLE IF NOT EXISTS publish_candidates (
    candidate_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    note_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
)
CREATE INDEX IF NOT EXISTS idx_candidates_thread ON publish_candidates(thread_id)

# New methods:
async def complete_thread(self, thread_id: str) -> Optional[dict]:
    # UPDATE status='accepted', accepted_at=now, updated_at=now
    # Returns updated thread dict

async def save_publish_candidates(self, thread_id, session_id, candidates: list[dict]) -> list[str]:
    # INSERT OR IGNORE WHERE NOT EXISTS (thread_id, note_id)
    # Returns list of candidate_id

async def count_publish_candidates(self, thread_id: str) -> int:
    # SELECT COUNT(*) WHERE thread_id=?

async def list_publish_candidates(self) -> list[dict]:
    # SELECT * ORDER BY created_at DESC
```

#### `app/api/routes/router.py` new endpoints

```python
@app.post("/threads/{thread_id}/complete", status_code=200)
async def complete_thread(thread_id: str, request: Request) -> CompleteThreadResponse:
    thread_store = _get_thread_store(request)
    thread = await thread_store.get_thread(thread_id)
    if thread is None:
        raise APIError(status_code=404, error_code="THREAD_NOT_FOUND", ...)

    # Idempotent: already accepted
    if thread["status"] == "accepted":
        count = await thread_store.count_publish_candidates(thread_id)
        return CompleteThreadResponse(thread_id=thread_id, status="accepted", publish_candidate_count=count)

    session_id = thread.get("active_workflow_session_id")
    candidates = []
    if session_id:
        # Cross-DB: open xhs_agent.db independently
        import aiosqlite
        async with aiosqlite.connect(settings.SQLITE_DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            from app.memory.session_data_store import SessionDataStore
            data_store = SessionDataStore(conn)
            notes = await data_store.get_generated_notes(session_id, note_ids=None)
            candidates = [
                {"note_id": n.note_id, "title": n.title, "content": n.content, "tags": getattr(n, "tags", []) or []}
                for n in notes
            ]

    await thread_store.complete_thread(thread_id)
    if candidates:
        await thread_store.save_publish_candidates(thread_id, session_id, candidates)

    count = await thread_store.count_publish_candidates(thread_id)
    return CompleteThreadResponse(thread_id=thread_id, status="accepted", publish_candidate_count=count)

@app.get("/publish-candidates")
async def list_publish_candidates_route(request: Request) -> PublishCandidatesResponse:
    thread_store = _get_thread_store(request)
    rows = await thread_store.list_publish_candidates()
    items = [
        PublishCandidate(
            candidate_id=r["candidate_id"],
            thread_id=r["thread_id"],
            session_id=r["session_id"],
            note_id=r["note_id"],
            title=r["title"],
            content=r["content"],
            tags=r["tags"].split(",") if r["tags"] else [],
            created_at=r["created_at"],
        )
        for r in rows
    ]
    return PublishCandidatesResponse(items=items)

@app.get("/threads/{thread_id}/result")
async def get_thread_result(thread_id: str, request: Request) -> ThreadResultResponse:
    thread_store = _get_thread_store(request)
    thread = await thread_store.get_thread(thread_id)
    if thread is None:
        raise APIError(status_code=404, error_code="THREAD_NOT_FOUND", ...)

    session_id = thread.get("active_workflow_session_id")
    if not session_id:
        return ThreadResultResponse(thread_id=thread_id, session_id=None, strategy=None, notes=[])

    strategy_dict = None
    notes_list: list[GeneratedNoteItem] = []
    try:
        import aiosqlite
        async with aiosqlite.connect(settings.SQLITE_DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            from app.memory.session_data_store import SessionDataStore
            data_store = SessionDataStore(conn)
            try:
                strategy, _pref, _sid = await data_store.get_strategy(session_id, None)
                strategy_dict = strategy.model_dump() if strategy else None
            except Exception:
                pass
            notes = await data_store.get_generated_notes(session_id, note_ids=None)
            notes_list = [
                GeneratedNoteItem(
                    note_id=n.note_id,
                    title=n.title,
                    content=n.content,
                    tags=getattr(n, "tags", []) or [],
                )
                for n in notes
            ]
    except Exception:
        pass

    return ThreadResultResponse(thread_id=thread_id, session_id=session_id, strategy=strategy_dict, notes=notes_list)
```

### 3.3 Algorithm & Logic Flow

**`POST /threads/{id}/complete`**:
```
1. Get thread → 404 if missing
2. If status == 'accepted' → count existing candidates → return (idempotent)
3. Read active_workflow_session_id from thread
4. If session_id:
   a. Open aiosqlite to xhs_agent.db
   b. SessionDataStore.get_generated_notes(session_id, None)
   c. Build candidates list
5. ThreadStore.complete_thread(thread_id)
6. ThreadStore.save_publish_candidates(thread_id, session_id, candidates) if any
7. Count candidates → return CompleteThreadResponse
```

**Frontend onCompleted (generation stage)**:
```
1. SSE workflow_task_completed with stage="generate"
2. Close EventSource
3. Set task.status = "completed"
4. Call getThreadResult(activeThreadId) → set generatedResult state
5. Render strategy.positioning + notes (title + content) in chat area
6. Show "完成" button
```

**Frontend "完成" button click**:
```
1. Call completeThread(activeThreadId)
2. Show confirmation message "已采纳任务结果，笔记已进入发布候选列表。"
3. Hide "完成" button (set task.status stays "completed", mark accepted)
```

### 3.4 Implementation Checklist

- [ ] Add 5 schema classes to `schemas.py`
- [ ] Add `publish_candidates` table to `thread_store._init_tables`
- [ ] Add `complete_thread`, `save_publish_candidates`, `count_publish_candidates`, `list_publish_candidates` to `ThreadStore`
- [ ] Add 5 schema imports to `router.py`
- [ ] Add `POST /threads/{id}/complete` endpoint
- [ ] Add `GET /publish-candidates` endpoint
- [ ] Add `GET /threads/{id}/result` endpoint
- [ ] Add 4 types + 3 functions to `api.ts`
- [ ] Update `creator/page.tsx`: `generatedResult` state, fetch on generation complete, show results, "完成" button
- [ ] Update `publish/page.tsx`: Creator candidates section
- [ ] Write `test_creator_publish_candidate.py` with 4 tests

### 3.5 Error Handling Strategy

- Thread not found → `APIError(404, "THREAD_NOT_FOUND")`
- Cross-DB read failure (xhs_agent.db inaccessible, no data) → silently return 0 candidates (complete still succeeds)
- `get_strategy` failure → `strategy_dict = None`, do not propagate
- Frontend `getThreadResult` failure → show fallback "任务完成" text (no crash)

---

## 4. Testing Strategy

### 4.1 Test Pyramid Mapping

| Level | File | Count | Focus | Mock Strategy |
|-------|------|-------|-------|---------------|
| E2E | `tests/e2e/test_creator_publish_candidate.py` | 4 | complete endpoint + publish candidates API | Inject `thread_store` + `job_store` directly onto `app.state`; no real `xhs_agent.db` (0-candidate path) |

No unit tests for `ThreadStore` new methods needed (methods are simple SQLite CRUD, already covered by pattern from ALIGN-3 `test_thread_store.py`). If time permits, add 2 unit tests.

### 4.2 Critical Test Scenarios

1. `test_complete_thread_marks_accepted` — POST complete on active thread → 200, status="accepted", publish_candidate_count is int (0, since no xhs_agent.db in test).
2. `test_complete_thread_idempotent` — POST complete twice → both return 200 status="accepted", count same.
3. `test_complete_nonexistent_thread_404` — POST complete on bad id → 404.
4. `test_list_publish_candidates_empty` — GET /publish-candidates on fresh store → 200, items=[].

### 4.3 Test Data Fixtures

Same `client` fixture pattern as `test_creator_thread_api.py`:
```python
@pytest.fixture
async def client(tmp_path):
    db_path = str(tmp_path / "align8.db")
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    job_store = JobStore(db_path)
    await job_store.connect()
    # inject both onto app.state
    ...
```

### 4.4 Shift-left Cadence

- Write tests immediately after backend implementation.
- All 4 tests must pass before marking task complete.

---

## 5. Implementation Checklist

### Coding Sequence (Order Matters)

1. [ ] `app/models/schemas.py` — add 5 schema classes
2. [ ] `app/memory/thread_store.py` — add table + 4 methods
3. [ ] `app/api/routes/router.py` — import schemas, add 3 endpoints
4. [ ] `tests/e2e/test_creator_publish_candidate.py` — write 4 tests; run to verify green
5. [ ] `frontend/src/lib/api.ts` — add types + functions
6. [ ] `frontend/src/app/creator/page.tsx` — result display + 完成 button
7. [ ] `frontend/src/app/publish/page.tsx` — Creator candidates section

### Dependencies to Install/Verify

```
aiosqlite (already installed — used by ThreadStore and SessionManager)
```

No new dependencies.

### Notes on Cross-DB Access

The endpoint opens a fresh `aiosqlite.connect(settings.SQLITE_DB_PATH)` inline. This is acceptable for a single-user local-first deployment. It does not interfere with `SessionManager`'s own connection, which lives in the background worker process context.

---

## 6. Risk & Notes

**Technical Debt**:
- TD-ALIGN8-1: `GET /threads/{id}/result` is not in the spec's API contract section but is required for the frontend display. If a spec-strict review happens, this endpoint needs to be added to the contract table.
- TD-ALIGN8-2: `GET /publish-candidates` has no pagination — acceptable for MVP where candidate count will be small.

**Architecture Decision**:
- Cross-DB read is done inline in the endpoint (not via a shared service) to avoid coupling `ThreadStore` to `SessionDataStore`. This keeps the two DB files independent and easy to reason about in a local-first context.

**Spec Alignment**:
- Spec says "可选：`POST /publish-records/from-candidate`" — deferred, not implemented.
- `GET /threads/{id}/result` is an ALIGN-8-introduced endpoint not in the spec contract section — must be noted in spec sync.

---

## 7. Spec Sync Expectations

After coding and testing:
- Update ALIGN-8 checklist in spec file (mark each AC with `[x]`).
- Add `GET /threads/{thread_id}/result` to the Backend API Contract Additions section of the spec.
- Note TD-ALIGN8-1 and TD-ALIGN8-2 as `[-]` items in the ALIGN-8 checklist.
- Mark ALIGN-8 Progress: Done.
