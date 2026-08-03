# F003 Lite Model Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local Lite model-configuration card that saves an OpenAI-compatible Base URL, model ID, and API key, applies them without restart, and lets failed pre-research continue in the same workflow run.

**Architecture:** Persist one validated configuration per `(workspace_id, user_id)` in the Content Research SQLite database. Resolve that configuration inside the existing `app/services/llm` service before falling back to `.env`, and keep the OpenAI-compatible adapter request-scoped so a saved Base URL becomes effective immediately. Treat provider/configuration failures as stable safe errors, move pre-research to the existing durable `waiting_user` boundary, and resume the same brief/attempt/run after the user validates a replacement configuration.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic, SQLite, `openai.AsyncOpenAI`, pytest, Next.js 14, React 18, TypeScript, Node test runner, Playwright.

## Global Constraints

- Lite accepts only `base_url`, `model`, and `api_key` from the user.
- User-entered model IDs are free-form; only the OpenAI-compatible Chat Completions protocol is supported.
- `temperature`, output length, structured-output prompts, timeouts, and retry policy remain system-owned.
- API keys may be stored as plaintext in local SQLite, but no API response, log, Trace projection, usage event, or exception may expose the full key or Authorization header.
- A validated user configuration is selected atomically as `base_url + model + api_key`; provider failure must not silently fall back to another model or `.env`.
- `.env` remains the fallback only when the current Workspace/user has no validated configuration.
- Saving a configuration must not require a backend or frontend restart.
- Recovery reuses the same `attempt_id`, `brief_id`, and `workflow_run_id`; it must not replay completed Spider checkpoints or packets.
- The UI entry is a compact “模型服务” card directly below “本次研究摘要” in the Creator right sidebar.
- Do not add a settings center, avatar menu, model catalog sync, model-name allowlist, Anthropic-native adapter, user-tunable inference parameters, or automatic multi-model fallback.

---

## File Structure

### New backend modules

- `app/services/llm/configuration.py`: immutable configuration/candidate/summary types and the read-store protocol used by `LLMService`.
- `app/services/llm/configuration_store.py`: SQLite CRUD for the per-Workspace/user plaintext configuration.
- `app/services/llm/configuration_service.py`: URL normalization, candidate merging, live validation, save/delete orchestration, and safe summaries.
- `app/services/llm/failures.py`: stable provider failure type and OpenAI-compatible exception classification.

### Existing backend modules to modify

- `app/content_research/migrations.py`: append migration `0015` for `content_research_llm_configurations`.
- `app/services/llm/types.py`: add request-resolution metadata needed for a custom endpoint.
- `app/services/llm/providers/base.py`: accept an optional request-scoped Base URL.
- `app/services/llm/providers/openai_compatible.py`: use the request-scoped endpoint, retry once without unsupported optional parameters, and emit safe stable failures.
- `app/services/llm/service.py`: select the validated user configuration before static policy routing.
- `app/services/llm/tracked_client.py`: construct the store-aware shared service and register the custom OpenAI-compatible adapter.
- `app/services/llm/__init__.py`: export the new public types.
- `app/main.py`: construct and expose one configuration store/service alongside the shared LLM service.
- `app/content_research/api_schemas.py`: add safe configuration schemas and the `retry_presearch` action.
- `app/api/routes/router.py`: add Workspace-scoped configuration endpoints and pass the principal into pre-research.
- `app/content_research/presearch/service.py`: classify provider/JSON failures as model-configuration waits.
- `app/content_research/service.py`: persist Workspace identity in the brief, stop at `waiting_user`, and rerun the same pre-search attempt.
- `app/content_research/observation/trace_service.py`: project only stable LLM error codes and configuration source/model labels.

### New frontend module

- `frontend/src/components/content-research/ModelServiceCard.tsx`: isolated card/edit form with test, save, delete, and continue actions.

### Existing frontend modules to modify

- `frontend/src/lib/content-research-api.ts`: add configuration/retry contracts and attach Workspace/User headers to every Content Research request.
- `frontend/src/lib/content-research-api.test.ts`: verify endpoint shapes, request headers, and response typing.
- `frontend/src/app/creator/page.tsx`: render the card below the summary and connect same-run recovery.

### Tests

- `tests/unit/test_llm_configuration_store.py`
- `tests/unit/test_llm_configuration_service.py`
- `tests/unit/test_llm_service_abstraction.py`
- `tests/unit/test_llm_openai_compatible_adapter.py`
- `tests/unit/test_content_research_presearch.py`
- `tests/unit/test_content_research_trace_service.py`
- `tests/e2e/test_content_research_model_configuration_api.py`
- `tests/e2e/test_content_research_presearch_api.py`
- `tests/e2e/test_content_research_creator_browser.py`
- `tests/e2e/creator_browser_runtime.py`

---

### Task 1: Persist one validated user model configuration

**Files:**
- Create: `app/services/llm/configuration.py`
- Create: `app/services/llm/configuration_store.py`
- Modify: `app/content_research/migrations.py`
- Modify: `app/services/llm/__init__.py`
- Create: `tests/unit/test_llm_configuration_store.py`
- Modify: `tests/unit/test_content_research_migrations.py`

**Interfaces:**
- Produces: `UserLLMConfiguration`, `LLMConfigurationCandidate`, `LLMConfigurationSummary`, `LLMConfigurationReader`, and `SQLiteLLMConfigurationStore`.
- `SQLiteLLMConfigurationStore.get(workspace_id: str, user_id: str) -> UserLLMConfiguration | None`
- `SQLiteLLMConfigurationStore.upsert(configuration: UserLLMConfiguration) -> UserLLMConfiguration`
- `SQLiteLLMConfigurationStore.delete(workspace_id: str, user_id: str) -> bool`
- Consumed by: Tasks 2 and 3.

- [ ] **Step 1: Write failing persistence and migration tests**

```python
def test_configuration_round_trip_and_scope(tmp_path):
    store = SQLiteLLMConfigurationStore(str(tmp_path / "config.db"))
    saved = store.upsert(UserLLMConfiguration(
        workspace_id="ws_1",
        user_id="user_1",
        base_url="https://proxy.example/v1",
        model="custom-model-2026",
        api_key="sk-secret-1234",
        validation_status="validated",
        validated_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
    ))

    assert store.get("ws_1", "user_1") == saved
    assert store.get("ws_1", "user_2") is None
    assert store.delete("ws_1", "user_1") is True
    assert store.get("ws_1", "user_1") is None


def test_migration_0015_creates_scoped_configuration_table(tmp_path):
    db_path = str(tmp_path / "content_research.db")
    bootstrap_content_research_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(content_research_llm_configurations)"
        )}
        versions = {row[0] for row in conn.execute(
            "SELECT version FROM content_research_schema_migrations"
        )}
    assert {"workspace_id", "user_id", "base_url", "model", "api_key", "validation_status"} <= columns
    assert "0015" in versions
```

- [ ] **Step 2: Run the tests and verify the missing types/table failures**

Run:

```bash
pytest tests/unit/test_llm_configuration_store.py tests/unit/test_content_research_migrations.py -q
```

Expected: FAIL because the configuration types/store and migration `0015` do not exist.

- [ ] **Step 3: Add immutable configuration types and the store protocol**

Implement these exact public shapes in `app/services/llm/configuration.py`:

```python
@dataclass(frozen=True)
class UserLLMConfiguration:
    workspace_id: str
    user_id: str
    base_url: str
    model: str
    api_key: str
    validation_status: str
    validated_at: datetime
    last_validation_error_code: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class LLMConfigurationCandidate:
    base_url: str
    model: str
    api_key: str | None


@dataclass(frozen=True)
class LLMConfigurationSummary:
    source: str
    status: str
    base_url: str
    model: str
    api_key_configured: bool
    api_key_suffix: str | None
    validated_at: datetime | None
    error_code: str | None = None


class LLMConfigurationReader(Protocol):
    def get(self, workspace_id: str, user_id: str) -> UserLLMConfiguration | None: ...
```

Validate non-empty identity/configuration fields and require `validation_status == "validated"` for a stored record.

- [ ] **Step 4: Append immutable migration `0015` and implement SQLite CRUD**

Add a new checksum entry and migration application without editing versions `0001`–`0014`. The table must use:

```sql
CREATE TABLE content_research_llm_configurations (
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    base_url TEXT NOT NULL,
    model TEXT NOT NULL,
    api_key TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    validated_at TEXT NOT NULL,
    last_validation_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, user_id)
)
```

`SQLiteLLMConfigurationStore.__init__` must call `bootstrap_content_research_schema(db_path)`. `upsert` must preserve the original `created_at`, replace all three effective values atomically, and never log record contents.

- [ ] **Step 5: Run focused tests**

```bash
pytest tests/unit/test_llm_configuration_store.py tests/unit/test_content_research_migrations.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit the persistence boundary**

```bash
git add app/services/llm/configuration.py app/services/llm/configuration_store.py app/services/llm/__init__.py app/content_research/migrations.py tests/unit/test_llm_configuration_store.py tests/unit/test_content_research_migrations.py
git commit -m "feat(llm): persist Lite user model configuration"
```

---

### Task 2: Apply the saved endpoint through the shared LLM service

**Files:**
- Create: `app/services/llm/failures.py`
- Modify: `app/services/llm/types.py`
- Modify: `app/services/llm/providers/base.py`
- Modify: `app/services/llm/providers/openai_compatible.py`
- Modify: `app/services/llm/service.py`
- Modify: `app/services/llm/tracked_client.py`
- Modify: `app/services/llm/__init__.py`
- Modify: `tests/unit/test_llm_service_abstraction.py`
- Modify: `tests/unit/test_llm_openai_compatible_adapter.py`
- Modify: `tests/unit/test_llm_tracked_client.py`

**Interfaces:**
- Consumes: `LLMConfigurationReader.get(...)` from Task 1.
- Produces: `LLMProviderFailure(code: str, public_message: str, recoverable: bool, status_code: int | None, provider: str | None = None, model: str | None = None, configuration_source: str | None = None)`.
- Extends: `LLMResponse.configuration_source: str = "system_default"`; `LLMService` replaces it with `"user"` for scoped custom calls.
- Changes provider protocol to `generate(request, api_key, model, base_url=None) -> LLMResponse`.
- `LLMService.generate()` chooses user configuration only when both `context.tenant_id` and `context.user_id` exist and the stored record is validated.

- [ ] **Step 1: Write failing selection and adapter tests**

Extend the existing test doubles with these exact helpers:

```python
NOW = datetime(2026, 8, 3, tzinfo=timezone.utc)


class FakeConfigurationReader:
    def __init__(self, configuration: UserLLMConfiguration | None) -> None:
        self.configuration = configuration

    def get(self, workspace_id: str, user_id: str) -> UserLLMConfiguration | None:
        if self.configuration is None:
            return None
        if (workspace_id, user_id) != (
            self.configuration.workspace_id,
            self.configuration.user_id,
        ):
            return None
        return self.configuration


class FakeProvider:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[LLMRequest, str, str, str | None]] = []

    async def generate(self, request, api_key, model, base_url=None):
        self.calls.append((request, api_key, model, base_url))
        if self.error is not None:
            raise self.error
        return LLMResponse(
            content="ok", provider="openai_compatible", model=model,
            usage=TokenUsage(), latency_ms=1,
        )
```

Add these behaviors:

```python
@pytest.mark.asyncio
async def test_user_configuration_overrides_policy_as_one_atomic_target():
    reader = FakeConfigurationReader(UserLLMConfiguration(
        workspace_id="ws_1", user_id="user_1",
        base_url="https://custom.example/v1", model="model-x",
        api_key="user-key", validation_status="validated", validated_at=NOW,
    ))
    provider = FakeProvider()
    service = LLMService(
        router=ModelRouter({"balanced": ResolvedModel("openai", "env-model")}),
        credential_resolver=CredentialResolver(FakeSettings()),
        providers={"openai_compatible": provider, "openai": FakeProvider()},
        configuration_reader=reader,
    )
    request = LLMRequest(
        messages=[Message(role="user", content="hi")],
        task_type="chat", model_policy="balanced",
        context=LLMCallContext(tenant_id="ws_1", user_id="user_1"),
    )

    await service.generate(request)

    assert provider.calls[0][1:] == ("user-key", "model-x", "https://custom.example/v1")


@pytest.mark.asyncio
async def test_user_configuration_failure_never_calls_env_provider():
    custom = FakeProvider(error=LLMProviderFailure("llm_auth_invalid", "API Key 无效", True, 401))
    env_provider = FakeProvider()
    reader = FakeConfigurationReader(UserLLMConfiguration(
        workspace_id="ws_1", user_id="user_1",
        base_url="https://custom.example/v1", model="model-x",
        api_key="user-key", validation_status="validated", validated_at=NOW,
    ))
    service = LLMService(
        router=ModelRouter({"balanced": ResolvedModel("openai", "env-model")}),
        credential_resolver=CredentialResolver(FakeSettings()),
        providers={"openai_compatible": custom, "openai": env_provider},
        configuration_reader=reader,
    )
    request = LLMRequest(
        messages=[Message(role="user", content="hi")],
        task_type="chat", model_policy="balanced",
        context=LLMCallContext(tenant_id="ws_1", user_id="user_1"),
    )

    with pytest.raises(LLMProviderFailure) as error:
        await service.generate(request)

    assert error.value.code == "llm_auth_invalid"
    assert env_provider.calls == []
```

For the adapter, test request-scoped Base URL and one compatibility retry:

```python
assert client_factory.calls == [{"api_key": "key", "base_url": "http://127.0.0.1:11434/v1"}]
assert completions.calls[1] == {
    "model": "local-model",
    "messages": [{"role": "user", "content": "hi"}],
}
```

- [ ] **Step 2: Run focused tests and verify failures**

```bash
pytest tests/unit/test_llm_service_abstraction.py tests/unit/test_llm_openai_compatible_adapter.py tests/unit/test_llm_tracked_client.py -q
```

Expected: FAIL because the service cannot resolve scoped configuration and adapters freeze Base URLs.

- [ ] **Step 3: Add stable provider failure classification**

In `failures.py`, map status/class without persisting the upstream body:

```python
STATUS_FAILURES = {
    401: ("llm_auth_invalid", "API Key 无效", True),
    402: ("llm_account_unavailable", "模型账户余额或套餐不可用", True),
    403: ("llm_auth_invalid", "API Key 无权调用该服务", True),
    404: ("llm_model_unavailable", "配置的模型不存在或不可用", True),
    429: ("llm_rate_limited", "模型服务请求过于频繁", True),
}
```

Classify timeout/connection/5xx as `llm_service_unavailable`, unsupported response shapes as `llm_protocol_incompatible`, and never use `str(exc)` as the public message. Preserve the original exception only as `__cause__` for server-side debugging.

`LLMService` must enrich a classified adapter failure with the selected safe target before re-raising: built-in calls use their provider/model and `configuration_source="system_default"`; scoped custom calls use `provider="openai_compatible"`, the user model ID, and `configuration_source="user"`. The exception must never carry Base URL, API Key, request headers, prompt, or raw response.

After a successful adapter call, return `dataclasses.replace(response, configuration_source=configuration_source)` so structured-output parsing failures can still be attributed to the safe selected target.

- [ ] **Step 4: Make the adapter Base URL request-scoped**

Change the protocol and adapter call to:

```python
async def generate(
    self,
    request: LLMRequest,
    api_key: str,
    model: str,
    base_url: str | None = None,
) -> LLMResponse:
    effective_base_url = base_url or self.base_url
    if not effective_base_url:
        raise LLMProviderFailure(
            "llm_protocol_incompatible",
            "模型服务 Base URL 未配置",
            True,
            None,
        )
```

On an HTTP 400 that explicitly names unsupported `temperature` or `max_tokens`, remove only those named optional fields and make one second call. Any second failure is classified normally; authentication, payment, model, rate-limit, and transport errors are never retried by this compatibility branch.

- [ ] **Step 5: Resolve scoped user configuration before the static policy**

Implement this selection order in `LLMService.generate`:

```python
context = request.context
user_configuration = (
    self._configuration_reader.get(context.tenant_id, context.user_id)
    if self._configuration_reader is not None
    and context is not None
    and context.tenant_id
    and context.user_id
    else None
)
if user_configuration is not None:
    provider_name = "openai_compatible"
    model = user_configuration.model
    api_key = user_configuration.api_key
    base_url = user_configuration.base_url
else:
    resolved_model = self._router.resolve(request)
    provider_name = resolved_model.provider
    model = resolved_model.model
    api_key = self._credential_resolver.resolve(provider_name)
    base_url = None
```

Register one `OpenAICompatibleAdapter(provider="openai_compatible")` in `build_default_llm_service`, pass `SQLiteLLMConfigurationStore(db_path)` as the reader, and keep all existing built-in provider adapters for `.env` fallback.

- [ ] **Step 6: Ensure tracked failures persist only stable safe text**

When `TrackedLLMChatClient` catches `LLMProviderFailure`, record `error_message=f"{exc.code}: {exc.public_message}"`; do not record the upstream exception body. Preserve current behavior for non-provider programming errors.

- [ ] **Step 7: Run focused tests**

```bash
pytest tests/unit/test_llm_service_abstraction.py tests/unit/test_llm_openai_compatible_adapter.py tests/unit/test_llm_tracked_client.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit shared runtime resolution**

```bash
git add app/services/llm tests/unit/test_llm_service_abstraction.py tests/unit/test_llm_openai_compatible_adapter.py tests/unit/test_llm_tracked_client.py
git commit -m "feat(llm): apply request-scoped OpenAI-compatible configuration"
```

---

### Task 3: Expose safe Workspace-scoped configuration APIs

**Files:**
- Create: `app/services/llm/configuration_service.py`
- Modify: `app/content_research/api_schemas.py`
- Modify: `app/api/routes/router.py`
- Modify: `app/main.py`
- Create: `tests/unit/test_llm_configuration_service.py`
- Create: `tests/e2e/test_content_research_model_configuration_api.py`

**Interfaces:**
- Consumes: Task 1 store and Task 2 adapter/failure type.
- Produces: `LiteLLMConfigurationService.get_summary`, `.validate`, `.save`, and `.delete`.
- Produces endpoints `GET`, `POST /validate`, `PUT`, and `DELETE /content-research/llm-config`.

- [ ] **Step 1: Write failing service tests for URL validation, key reuse, and safe summaries**

Create the following deterministic adapter helper in the test file; it must not construct an SDK client or make a network request:

```python
class ProbeAdapter:
    def __init__(self, response: str) -> None:
        self.response = response
        self.failure: Exception | None = None
        self.calls: list[tuple[LLMRequest, str, str, str | None]] = []

    async def generate(self, request, api_key, model, base_url=None):
        self.calls.append((request, api_key, model, base_url))
        if self.failure is not None:
            raise self.failure
        return LLMResponse(
            content=self.response, provider="openai_compatible", model=model,
            usage=TokenUsage(), latency_ms=1, configuration_source="candidate",
        )


def make_configuration_service(tmp_path, response: str):
    store = SQLiteLLMConfigurationStore(str(tmp_path / "config.db"))
    adapter = ProbeAdapter(response)
    return LiteLLMConfigurationService(store=store, probe_adapter=adapter), adapter


def valid_candidate() -> LLMConfigurationCandidate:
    return LLMConfigurationCandidate(
        base_url="https://proxy.example/v1", model="model-x", api_key="secret-1234"
    )
```

```python
@pytest.mark.asyncio
async def test_save_validates_then_returns_redacted_summary(tmp_path):
    service, adapter = make_configuration_service(tmp_path, response='{"ok":true}')

    summary = await service.save(
        workspace_id="ws_1", user_id="user_1",
        candidate=LLMConfigurationCandidate(
            base_url="https://proxy.example/v1/",
            model="model-x", api_key="secret-1234",
        ),
    )

    assert summary.base_url == "https://proxy.example/v1"
    assert summary.api_key_configured is True
    assert summary.api_key_suffix == "1234"
    assert "secret-1234" not in repr(summary)
    assert adapter.calls[0][2] == "model-x"


@pytest.mark.asyncio
async def test_failed_candidate_does_not_replace_valid_configuration(tmp_path):
    service, adapter = make_configuration_service(tmp_path, response='{"ok":true}')
    await service.save(workspace_id="ws_1", user_id="user_1", candidate=valid_candidate())
    adapter.failure = LLMProviderFailure("llm_auth_invalid", "API Key 无效", True, 401)

    validation = await service.validate(
        workspace_id="ws_1", user_id="user_1",
        candidate=LLMConfigurationCandidate(
            base_url="https://bad.example/v1", model="bad-model", api_key="bad-key"
        ),
    )

    assert validation.status == "invalid"
    assert service.get_summary("ws_1", "user_1").model == "model-x"
```

Also reject URLs with schemes other than `http`/`https`, missing hosts, embedded username/password, query strings, or fragments. Allow localhost and path prefixes such as `/v1`.

- [ ] **Step 2: Run service tests and verify failure**

```bash
pytest tests/unit/test_llm_configuration_service.py -q
```

Expected: FAIL because the configuration service does not exist.

- [ ] **Step 3: Implement validation and atomic save semantics**

The live probe must use the same Task 2 adapter with this request:

```python
LLMRequest(
    messages=[
        Message(role="system", content='Return only {"ok":true}.'),
        Message(role="user", content="configuration probe"),
    ],
    task_type="content_research.configuration_probe",
    temperature=0.0,
    max_tokens=32,
)
```

Parse the response as a JSON object and require `payload.get("ok") is True`. Map a non-JSON/non-conforming response to `llm_protocol_incompatible`. If `candidate.api_key is None`, reuse the current stored key; reject it when no current key exists. `validate` never writes. `save` calls the same validator and writes only a valid result.

- [ ] **Step 4: Define Pydantic request/response contracts**

Add:

```python
class ContentResearchLLMConfigurationRequest(BaseModel):
    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key: str | None = None


class ContentResearchLLMConfigurationResponse(BaseModel):
    source: str
    status: str
    base_url: str
    model: str
    api_key_configured: bool
    api_key_suffix: str | None = None
    validated_at: datetime | None = None
    error_code: str | None = None
```

Do not add `api_key` to any response schema.

- [ ] **Step 5: Write failing API tests**

Cover:

```python
headers = {"X-Workspace-Id": "ws_1", "X-User-Id": "user_1"}
saved = await client.put("/content-research/llm-config", headers=headers, json={
    "base_url": "https://proxy.example/v1",
    "model": "model-x",
    "api_key": "secret-1234",
})
assert saved.status_code == 200
assert "api_key" not in saved.json()
assert saved.json()["api_key_suffix"] == "1234"

other_user = await client.get(
    "/content-research/llm-config",
    headers={"X-Workspace-Id": "ws_1", "X-User-Id": "user_2"},
)
assert other_user.json()["source"] == "system_default"
```

Also assert missing Workspace/User headers return `401`, validation failure returns a safe body without the submitted key, and `DELETE` restores `source == "system_default"`.

- [ ] **Step 6: Wire routes and application state**

Resolve identity exclusively through `resolve_workspace_principal(request.headers)`. Add `_get_llm_configuration_service(request)` and construct a single service in `app/main.py` using the same SQLite configuration store and a dedicated injected OpenAI-compatible probe adapter. Do not accept Workspace or user IDs in request bodies.

`POST /validate` returns HTTP 200 with `status="validated"` or `status="invalid"`. `PUT` returns HTTP 422 with the stable error code when validation fails and leaves the existing configuration unchanged.

- [ ] **Step 7: Run service and API tests**

```bash
pytest tests/unit/test_llm_configuration_service.py tests/e2e/test_content_research_model_configuration_api.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit the API boundary**

```bash
git add app/services/llm/configuration_service.py app/content_research/api_schemas.py app/api/routes/router.py app/main.py tests/unit/test_llm_configuration_service.py tests/e2e/test_content_research_model_configuration_api.py
git commit -m "feat(content-research): expose Lite model configuration API"
```

---

### Task 4: Stop and resume pre-research on stable model failures

**Files:**
- Modify: `app/content_research/presearch/service.py`
- Modify: `app/content_research/service.py`
- Modify: `app/content_research/api_schemas.py`
- Modify: `app/api/routes/router.py`
- Modify: `app/content_research/observation/trace_service.py`
- Modify: `tests/unit/test_content_research_presearch.py`
- Modify: `tests/unit/test_content_research_api_contract.py`
- Modify: `tests/unit/test_content_research_trace_service.py`
- Modify: `tests/e2e/test_content_research_presearch_api.py`
- Modify: `tests/integration/test_workflow_step_recovery_e2e.py`

**Interfaces:**
- Consumes: `LLMProviderFailure` and Workspace-scoped resolution from Tasks 2–3.
- Adds: `PresearchInput.workspace_id`, `PresearchOutcome.recoverable`, and stable `error_code` projection.
- Adds workflow action: `retry_presearch`.
- Adds runtime methods `wait_for_presearch_recovery(workflow_run_id, reason)` and `restart_presearch_step(workflow_run_id)`.
- Produces: same-attempt `ContentResearchPresearchResponse` from `retry_presearch`.

- [ ] **Step 1: Replace fallback expectations with recoverable model-wait tests**

Add deterministic test doubles with these behaviors:

```python
class FailingLLM:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def generate(self, _request):
        raise self.error


class FailOnceThenSucceedLLM(FakeLLM):
    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    async def generate(self, request):
        self.call_count += 1
        if self.call_count == 1:
            raise LLMProviderFailure(
                "llm_auth_invalid", "API Key 无效", True, 401,
                provider="openai_compatible", model="model-x", configuration_source="user",
            )
        return await super().generate(request)


class AlwaysFailingLLM:
    async def generate(self, _request):
        raise LLMProviderFailure(
            "llm_auth_invalid", "API Key 无效", True, 401,
            provider="openai_compatible", model="model-x", configuration_source="user",
        )


async def create_waiting_presearch(service: ContentResearchService):
    return await service.submit_presearch(
        seed_text="夏季通勤短裤", user_note=None,
        thread_id="thread_1", workspace_id="ws_1", user_id="user_1",
    )
```

Extend the existing `_service` helper to accept `runtime: FakeRuntime | None` and pass `runtime or FakeRuntime()` to `ContentResearchService`.

```python
@pytest.mark.asyncio
async def test_provider_failure_waits_for_configuration_without_completing_presearch(store):
    runtime = FakeRuntime()
    service = _service(store, FailingLLM(
        LLMProviderFailure("llm_account_unavailable", "模型账户不可用", True, 402)
    ), runtime=runtime)

    response = await service.submit_presearch(
        seed_text="夏季通勤短裤", user_note=None,
        thread_id="thread_1", workspace_id="ws_1", user_id="user_1",
    )

    assert response.status == "waiting_model_config"
    assert response.error_code == "llm_account_unavailable"
    assert runtime.calls[-1]["event"] == "wait_for_presearch_recovery"
    assert all(call.get("event") != "presearch_ready" for call in runtime.calls)


@pytest.mark.asyncio
async def test_retry_presearch_reuses_attempt_brief_and_run(store):
    llm = FailOnceThenSucceedLLM()
    service = _service(store, llm)
    first = await service.submit_presearch(
        seed_text="夏季通勤短裤", user_note=None,
        thread_id="thread_1", workspace_id="ws_1", user_id="user_1",
    )

    retried = await service.retry_presearch(first.workflow_run_id)

    assert retried.attempt_id == first.attempt_id
    assert retried.brief_id == first.brief_id
    assert retried.workflow_run_id == first.workflow_run_id
    assert retried.status == "completed"


@pytest.mark.asyncio
async def test_retry_presearch_rejects_after_two_user_recoveries(store):
    service = _service(store, AlwaysFailingLLM())
    first = await create_waiting_presearch(service)
    await service.retry_presearch(first.workflow_run_id)
    await service.retry_presearch(first.workflow_run_id)

    with pytest.raises(ContentResearchValidationError, match="recovery budget exhausted"):
        await service.retry_presearch(first.workflow_run_id)
```

- [ ] **Step 2: Run focused tests and verify old fallback behavior fails them**

```bash
pytest tests/unit/test_content_research_presearch.py tests/unit/test_content_research_api_contract.py -q
```

Expected: FAIL because all LLM exceptions currently become fallback briefs and complete the pre-search step.

- [ ] **Step 3: Carry Workspace identity into every pre-search LLM request**

Resolve the principal in `create_content_research_presearch`, remove the standalone default `X-User-Id` parameter, and call:

```python
service.submit_presearch(
    seed_text=payload.seed_text,
    user_note=payload.user_note,
    thread_id=payload.thread_id,
    workspace_id=principal.workspace_id,
    user_id=principal.user_id,
)
```

Persist `workspace_id` and `user_id` in the brief payload. Set both `tenant_id=request.workspace_id` and `user_id=request.user_id` in `LLMCallContext` so Task 2 selects the saved configuration.

- [ ] **Step 4: Introduce stable pre-search failure outcomes**

Catch `LLMProviderFailure` separately and return:

```python
PresearchOutcome(
    status="waiting_model_config",
    checklist=build_fallback_checklist(request.seed_text, request.user_note),
    fallback_used=False,
    error_code=exc.code,
    error_message=exc.public_message,
    recoverable=exc.recoverable,
    provider=exc.provider,
    model=exc.model,
    configuration_source=exc.configuration_source,
)
```

For malformed structured output, retry the same bounded request once. If the second parse fails, use `llm_structured_output_invalid` and the same waiting state. Keep the existing first-feedback/hard-cutoff timing policy unchanged in Task 5H; this task changes provider/configuration and exhausted-parse failures, not Trace timing semantics owned by 5G-2B.

For the exhausted parse case, populate `provider=response.provider`, `model=response.model`, and `configuration_source=response.configuration_source` from the last successful transport response.

Extend `ContentResearchPresearchResponse` with exact optional fields so Creator never has to parse messages:

```python
error_code: str | None = None
error_message: str | None = None
recoverable: bool = False
configuration_source: str | None = None
model: str | None = None
```

- [ ] **Step 5: Persist a waiting brief and use the existing durable workflow boundary**

Initialize the `presearch` step with `max_attempts=3`. When the outcome is `waiting_model_config`, save the same draft brief with that status and call:

```python
await self._workflow_runtime.wait_for_presearch_recovery(
    workflow_run_id,
    reason={"code": outcome.error_code, "message": outcome.error_message},
)
```

Do not call `mark_presearch_ready`. `WorkflowRunManagerRuntime.wait_for_presearch_recovery` delegates to `manager.wait_for_user_recovery(..., step_name="presearch")`. `restart_presearch_step` atomically resumes the parent and starts the retrying pre-search step using `restart_step_and_retry_children(..., child_task_ids=[])`.

- [ ] **Step 6: Add the `retry_presearch` action and same-record update**

Add `retry_presearch` to `P0_WORKFLOW_ACTIONS`. In `run_workflow_action`, require `run.status == "waiting_user"`, `current_step == "presearch"`, and a waiting brief; reject succeeded/published/non-presearch runs. Before restarting, reject when `attempt_count >= max_attempts` so `max_attempts=3` means the initial attempt plus at most two user recoveries. Rerun from the persisted `seed_text`, `user_note`, `workspace_id`, and `user_id`; update the same brief rather than inserting a new one. On success, call `mark_presearch_ready`; on another safe failure, return to `waiting_user` and consume the next existing step attempt.

- [ ] **Step 7: Project safe recovery facts in Trace**

Add `error_code` to `_safe_runtime_step_dict` and allow only the stable `llm_*` code, never `error_message`. Persist `configuration_source`, `provider`, and `model` from `PresearchOutcome` in the brief payload; do not persist Base URL or API Key. Add a safe `llm_recovery` projection containing only:

```python
{
    "required": run_status == "waiting_user" and current_stage == "presearch",
    "error_code": presearch_step.error_code,
    "configuration_source": "user" or "system_default",
    "model": safe_model_name,
}
```

Add `llm_recovery: dict = Field(default_factory=dict)` to `ContentResearchTraceResponse` and the matching optional typed field to the frontend `ContentResearchTrace` interface in Task 5.

Add a recursive test that serializes configuration, pre-search, and Trace responses and asserts the submitted full API key, `Authorization`, and upstream response body are absent.

- [ ] **Step 8: Run workflow recovery tests**

```bash
pytest tests/unit/test_content_research_presearch.py tests/unit/test_content_research_api_contract.py tests/unit/test_content_research_trace_service.py tests/e2e/test_content_research_presearch_api.py tests/integration/test_workflow_step_recovery_e2e.py -q
```

Expected: PASS, with the same IDs before/after retry and no source collection invoked by pre-search recovery.

- [ ] **Step 9: Commit recoverable pre-search semantics**

```bash
git add app/content_research app/api/routes/router.py tests/unit/test_content_research_presearch.py tests/unit/test_content_research_api_contract.py tests/unit/test_content_research_trace_service.py tests/e2e/test_content_research_presearch_api.py tests/integration/test_workflow_step_recovery_e2e.py
git commit -m "feat(content-research): resume presearch after model configuration"
```

---

### Task 5: Add the Lite model-service card below the research summary

**Files:**
- Create: `frontend/src/components/content-research/ModelServiceCard.tsx`
- Modify: `frontend/src/lib/content-research-api.ts`
- Modify: `frontend/src/lib/content-research-api.test.ts`
- Modify: `frontend/src/app/creator/page.tsx`
- Modify: `tests/e2e/creator_browser_runtime.py`
- Modify: `tests/e2e/test_content_research_creator_browser.py`

**Interfaces:**
- Consumes: Task 3 configuration endpoints and Task 4 `retry_presearch` action.
- Produces frontend functions `getLLMConfiguration`, `validateLLMConfiguration`, `saveLLMConfiguration`, `deleteLLMConfiguration`, and `retryContentResearchPresearch`.
- Produces `ModelServiceCard` props `recoveryPending`, `onContinue`, and `onConfigurationChanged`.

- [ ] **Step 1: Write failing frontend API tests**

```typescript
test("model configuration requests are Workspace scoped and never expect a returned key", async () => {
  setWorkspaceContext("ws_1", "user_1");
  let requestHeaders = new Headers();
  globalThis.fetch = (async (_input, init) => {
    requestHeaders = new Headers(init?.headers);
    return jsonResponse({
      source: "user", status: "validated",
      base_url: "https://proxy.example/v1", model: "model-x",
      api_key_configured: true, api_key_suffix: "1234", validated_at: "2026-08-03T00:00:00Z",
      error_code: null,
    });
  }) as typeof fetch;

  const result = await saveLLMConfiguration({
    base_url: "https://proxy.example/v1", model: "model-x", api_key: "secret-1234",
  });

  assert.equal(requestHeaders.get("X-Workspace-Id"), "ws_1");
  assert.equal(requestHeaders.get("X-User-Id"), "user_1");
  assert.equal("api_key" in result, false);
});
```

Also test `retryContentResearchPresearch(runId)` sends action `retry_presearch` and returns the same attempt IDs.

- [ ] **Step 2: Run the frontend unit tests and verify failure**

```bash
npm --prefix frontend test
```

Expected: FAIL because the functions and Workspace headers are missing.

- [ ] **Step 3: Add typed API methods and Workspace headers**

Import `getWorkspaceContext` from `@/lib/api`. `contentResearchFetch` must reject an uninitialized Workspace and attach `X-Workspace-Id`, `X-User-Id`, and the existing optional auth token to every Content Research request. Define response types without an `api_key` field.

- [ ] **Step 4: Build the isolated card component**

The collapsed card must render:

```tsx
<section aria-label="模型服务">
  <h2>模型服务</h2>
  <p>{statusLabel(configuration.status)}</p>
  <p>模型：{configuration.model}</p>
  <p>来源：{configuration.source === "user" ? "用户配置" : "系统默认"}</p>
  {configuration.api_key_suffix && <p>API Key：••••{configuration.api_key_suffix}</p>}
  <button type="button" onClick={() => setEditing(true)}>配置模型</button>
</section>
```

The edit form owns `base_url`, `model`, and an initially blank `api_key`. “测试连接” calls validate only; “保存” calls save; “删除配置，恢复系统默认” calls delete. Disable duplicate submissions, keep the current valid summary if validation fails, and show the stable user-facing error adjacent to the form. Never write the key to `localStorage`, URL parameters, console output, or React-visible summary state after save.

- [ ] **Step 5: Place the card below the existing summary**

Render order inside the right sidebar must remain:

```tsx
<ResearchRunSection />
<TraceSection />
<ResearchSummarySection />
<ModelServiceCard
  recoveryPending={presearchWaitingForModel}
  onContinue={continuePresearch}
  onConfigurationChanged={setLLMConfiguration}
/>
```

Allow the right sidebar to render when F003 Lite is enabled and either a run, a pre-search intent, or the idle Creator page is present. Run-specific sections render their existing empty state when there is no run. When the current pre-search response has `status === "waiting_model_config"`, do not show the confirm checklist; show the card warning and enable “继续调研” only after a successful validation/save. `onContinue` invokes `retry_presearch`, replaces the existing intent response, and shows the checklist only after `status === "completed"`.

- [ ] **Step 6: Add deterministic browser coverage**

Inject a deterministic configuration service in `creator_browser_runtime.py`; it must validate locally without a network call and return only a masked suffix. Add a browser test that:

```python
page.get_by_role("region", name="模型服务").get_by_role("button", name="配置模型").click()
page.get_by_label("Base URL").fill("https://proxy.example/v1")
page.get_by_label("模型").fill("model-x")
page.get_by_label("API Key").fill("secret-1234")
page.get_by_role("button", name="测试连接").click()
expect(page.get_by_text("连接验证成功")).to_be_visible()
page.get_by_role("button", name="保存").click()
expect(page.get_by_text("API Key：••••1234")).to_be_visible()
expect(page.get_by_text("secret-1234", exact=True)).not_to_be_visible()
```

Add a second scenario where pre-search initially returns `llm_account_unavailable`; saving the replacement enables “继续调研”, and clicking it produces the checklist without changing the run/brief/attempt IDs.

- [ ] **Step 7: Run frontend and browser tests**

```bash
npm --prefix frontend test
npm --prefix frontend run build
pytest tests/e2e/test_content_research_creator_browser.py -k "model_service or presearch_model_recovery" -q
```

Expected: PASS.

- [ ] **Step 8: Commit the Creator experience**

```bash
git add frontend/src/components/content-research/ModelServiceCard.tsx frontend/src/lib/content-research-api.ts frontend/src/lib/content-research-api.test.ts frontend/src/app/creator/page.tsx tests/e2e/creator_browser_runtime.py tests/e2e/test_content_research_creator_browser.py
git commit -m "feat(creator): add Lite model service card"
```

---

### Task 6: Verify the complete Lite recovery contract and close delivery records

**Files:**
- Modify: `tests/e2e/test_content_research_model_configuration_api.py`
- Modify: `tests/e2e/test_content_research_presearch_api.py`
- Modify: `tests/e2e/test_content_research_creator_browser.py`
- Modify: `docs/features/f003/F003_content_research_lite_delivery_plan.md`
- Modify: `docs/superpowers/specs/2026-08-03-f003-lite-model-configuration-design.md`

**Interfaces:**
- Consumes all prior task outputs.
- Produces the final acceptance evidence and delivery status for Task 5H.

- [ ] **Step 1: Add a cross-layer API-key redaction test**

Use the literal sentinel `sk-task5h-must-never-leak-9876`, then recursively serialize:

- configuration GET/validate/save/delete responses;
- pre-search failure/retry responses;
- workflow snapshot and `/trace`;
- rows returned by the public usage API.

The assertion must be:

```python
for payload in public_payloads:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert "sk-task5h-must-never-leak-9876" not in encoded
    assert "Authorization" not in encoded
```

- [ ] **Step 2: Add checkpoint-preservation acceptance coverage**

Seed a waiting run with completed Spider operation checkpoints and packet IDs, save a replacement model configuration, then invoke the applicable same-run resume. Assert before/after sets are identical:

```python
assert after_provider_operation_ids == before_provider_operation_ids
assert after_packet_ids == before_packet_ids
assert after_workflow_run_id == before_workflow_run_id
```

For a failure before first collection, assert the retry completes pre-search and the first subsequent formal-research start performs collection exactly once.

- [ ] **Step 3: Run the complete focused verification suite**

```bash
pytest tests/unit/test_llm_configuration_store.py tests/unit/test_llm_configuration_service.py tests/unit/test_llm_service_abstraction.py tests/unit/test_llm_openai_compatible_adapter.py tests/unit/test_llm_tracked_client.py tests/unit/test_content_research_presearch.py tests/unit/test_content_research_trace_service.py tests/e2e/test_content_research_model_configuration_api.py tests/e2e/test_content_research_presearch_api.py tests/integration/test_workflow_step_recovery_e2e.py -q
npm --prefix frontend test
npm --prefix frontend run build
pytest tests/e2e/test_content_research_creator_browser.py -k "model_service or presearch_model_recovery" -q
```

Expected: every command exits `0`; no test uses a live external model or Spider endpoint.

- [ ] **Step 4: Run broader regression tests proportional to the shared LLM change**

```bash
pytest tests/unit/test_llm_*.py tests/unit/test_content_research_*.py tests/integration/test_content_research_*.py -q
```

Expected: PASS. Any unrelated pre-existing failure must be recorded with its exact test name and reproduced on the pre-task commit before it can be classified as unrelated.

- [ ] **Step 5: Update delivery evidence**

Change Task 5H from `P0，待开始` to `P0，实现与验收完成` only after all Step 3 commands pass. Record:

- migration version `0015`;
- configuration source precedence;
- stable error codes exercised;
- same run/attempt/brief identities from the recovery test;
- before/after Provider operation and packet counts;
- frontend build and browser-test results;
- recursive redaction sentinel result.

Change the design status to `已实现并验收` with the completion date only at that point.

- [ ] **Step 6: Commit acceptance evidence**

```bash
git add tests/e2e/test_content_research_model_configuration_api.py tests/e2e/test_content_research_presearch_api.py tests/e2e/test_content_research_creator_browser.py docs/features/f003/F003_content_research_lite_delivery_plan.md docs/superpowers/specs/2026-08-03-f003-lite-model-configuration-design.md
git commit -m "test(content-research): accept Lite model configuration recovery"
```

---

## Final Verification Checklist

- [x] The right sidebar order is Trace, evidence/Trace, research summary, model service.
- [x] Only `base_url`, `model`, and `api_key` are editable.
- [x] A free-form model ID works when the endpoint passes the live probe.
- [x] A saved configuration is effective on the next call without process restart.
- [x] A selected user configuration never silently falls back after failure.
- [x] `.env` is used only when no validated user configuration exists.
- [x] Full API keys and Authorization headers are absent from every public/logged projection.
- [x] Pre-search failure enters `waiting_user`; successful retry reuses attempt, brief, and run IDs.
- [x] Completed Spider operations and packets are unchanged by model configuration recovery.
- [x] Existing successful/published runs still reject replay.
- [x] Focused backend, frontend build, and browser tests pass.
