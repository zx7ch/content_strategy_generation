# Scope Authority and SQLite Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make confirmed Scope Contract the sole collection authority, with an atomic and idempotent SQLite confirmation path.

**Architecture:** Scope Draft remains immutable. An append-only Draft-to-Contract link establishes one confirmation per Draft. The store owns a short `BEGIN IMMEDIATE` transaction; service code derives frozen fields from the Draft and gates formal collection.

**Tech Stack:** Python 3.10, FastAPI/Pydantic, SQLite, pytest.

## Global Constraints

- Scope Draft cannot authorize collection; collection requires a persisted Scope Contract.
- One Draft creates at most one Contract; a duplicate request returns that original Contract.
- No network, LLM, Spider, or runtime call occurs inside the SQLite write transaction.
- Preserve `busy_timeout=30000`; lock exhaustion is a retryable local persistence error, never a provider failure.
- Audit and state facts commit together; tests must re-read persisted records.
- Do not change Trace presentation.

---

### Task 1: Add atomic, idempotent Draft confirmation

**Files:**

- Modify: `app/content_research/migrations.py`, `app/content_research/scope_contract.py`
- Modify: `app/content_research/stores/base.py`, `app/content_research/stores/sqlite_store.py`
- Test: `tests/integration/test_content_research_scope_contract_store.py`

**Interfaces:**

- Produce `ScopeDraftConfirmation(draft_id, scope_contract_id, workflow_run_id, created_at)`.
- Produce `confirm_scope_atomically(draft_id, contract, event) -> tuple[ResearchScopeContract, bool]`; the Boolean means this call created the record.

- [ ] **Step 1: Write failing integration tests**

```python
first, created = store.confirm_scope_atomically(draft.id, contract_v1, event_v1)
second, repeated = store.confirm_scope_atomically(draft.id, contract_v1, event_v1)
assert created is True
assert repeated is False
assert second == first
assert store.list_scope_contracts(draft.workflow_run_id) == [first]
```

Also assert that a conflicting repeat leaves no v2 Contract.

- [ ] **Step 2: Verify RED**

Run `pytest tests/integration/test_content_research_scope_contract_store.py -v`; it must fail because the store operation and confirmation link are absent.

- [ ] **Step 3: Implement migration and transaction**

Add migration `0022` with:

```sql
CREATE TABLE content_research_scope_draft_confirmations (
  scope_draft_id TEXT PRIMARY KEY,
  scope_contract_id TEXT NOT NULL UNIQUE,
  workflow_run_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

In `confirm_scope_atomically`, use one store connection: `BEGIN IMMEDIATE`; read the confirmation link; return its Contract if it exists; otherwise validate persisted Draft workflow/plan against Contract, insert Contract, audit event, then link, then commit. Roll back on every exception. Do not change prior migration checksums.

- [ ] **Step 4: Verify GREEN**

Run `pytest tests/integration/test_content_research_scope_contract_store.py -v`; assert one Contract and one confirmation audit event.

- [ ] **Step 5: Commit**

Commit only the files named for this task with `fix(content-research): atomically confirm scope drafts`.

### Task 2: Derive confirmation from Draft and enforce the collection gate

**Files:**

- Modify: `app/content_research/api_schemas.py`, `app/content_research/service.py`
- Test: `tests/e2e/test_content_research_scope_api.py`, `tests/e2e/test_content_research_brief_confirm_api.py`

**Interfaces:**

- `ConfirmScopeRequest` accepts `scope_draft_id`, `structure_hash`, and ordered non-empty final-query edits only.
- `start_formal_research` raises `ContentResearchValidationError("scope_confirmation_required")` without a Scope Contract.

- [ ] **Step 1: Write failing API tests**

```python
assert (await start_formal_research(unconfirmed_workflow)).status_code == 422
assert response.json()["detail"]["message"] == "scope_confirmation_required"
contract = await confirm_scope(draft_id, structure_hash, ["白衬衫通勤穿搭", ...])
assert contract["constraints"] == persisted_draft["constraints"]
assert contract["query_groups"][0]["suggested_query"] == persisted_draft["query_groups"][0]["suggested_query"]
assert contract["query_groups"][0]["execution_role"] == "exploratory"
```

Also cover a stale structure hash and a client payload attempting to replace core/context fields.

- [ ] **Step 2: Verify RED**

Run `pytest tests/e2e/test_content_research_scope_api.py tests/e2e/test_content_research_brief_confirm_api.py -v`; tests must fail because formal research bypasses scope and the request accepts replacement data.

- [ ] **Step 3: Implement the authority boundary**

Preserve Draft constraints, suggested queries, and target terms in the service. Validate both Draft and current Brief hashes. Apply only final-query edits in Draft order, call `confirm_scope_atomically`, and make `scope_confirmed` contain Draft ID/hash, Contract ID/version, all group origins/roles, and per-query `{suggested_query, final_query, changed}` facts. Before runtime dispatch, require the latest Scope Contract.

- [ ] **Step 4: Verify GREEN**

Run `pytest tests/e2e/test_content_research_scope_api.py tests/e2e/test_content_research_brief_confirm_api.py tests/unit/test_content_research_scope_contract.py -v`.

- [ ] **Step 5: Commit**

Commit only task files with `fix(content-research): enforce confirmed scope authority`.

### Task 3: Expose the minimal durable Scope read projection

**Files:**

- Modify: `app/content_research/api_schemas.py`, `app/content_research/service.py`, `app/api/routes/router.py`
- Test: `tests/e2e/test_content_research_scope_api.py`

**Interfaces:**

- `GET /content-research/workflows/{workflow_run_id}/scope?version={version}` returns latest Draft, requested/latest Contract, and redacted Scope audits.

- [ ] **Step 1: Write a failing recovery test**

```python
body = (await client.get(f"/content-research/workflows/{workflow_run_id}/scope")).json()
assert body["draft"]["id"] == prepared_draft_id
assert body["scope_contract"]["id"] == confirmed_contract_id
assert [event["event_name"] for event in body["audit_events"]] == ["scope_suggested", "scope_confirmed"]
```

- [ ] **Step 2: Verify RED**

Run the new test; it must fail with 404 because the route is absent.

- [ ] **Step 3: Implement the narrow projection**

Add response schema, service read method, and route. Return only Draft/Contract/audit facts; do not add candidate matching or coverage projections before their owning Task 3 work exists.

- [ ] **Step 4: Verify GREEN and commit**

Run `pytest tests/e2e/test_content_research_scope_api.py tests/integration/test_content_research_scope_contract_store.py -v`, then commit task files with `feat(content-research): expose frozen scope projection`.

### Task 4: Establish the SQLite lock-error baseline

**Files:**

- Modify: `app/content_research/stores/sqlite_store.py`, `docs/bugfix/20260806_f003_sqlite_write_coordination.md`
- Create: `tests/integration/test_content_research_sqlite_write_coordination.py`

**Interfaces:**

- Produce `RetryableLocalPersistenceError("sqlite_write_locked")` from exhausted lock acquisition in `confirm_scope_atomically`.

- [ ] **Step 1: Write a failing contention test**

```python
with hold_immediate_transaction(db_path):
    with pytest.raises(RetryableLocalPersistenceError, match="sqlite_write_locked"):
        store.confirm_scope_atomically(draft.id, contract, event)
assert store.list_scope_contracts(draft.workflow_run_id) == []
```

- [ ] **Step 2: Verify RED**

Run `pytest tests/integration/test_content_research_sqlite_write_coordination.py -v`; it must expose the raw SQLite lock error.

- [ ] **Step 3: Add bounded classification**

Catch only `sqlite3.OperationalError` messages containing `locked` or `busy` around the short transaction and raise the stable retryable exception; re-raise all other errors. Do not alter async dispatch semantics.

- [ ] **Step 4: Document and verify**

Document that only Scope confirmation now has this bounded write coordination. Run `pytest tests/integration/test_content_research_sqlite_write_coordination.py tests/integration/test_content_research_scope_contract_store.py -v` and commit with `fix(content-research): classify scope sqlite lock contention`.
