# F003 Lite Presearch-to-Spider Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让产品营销只能从用户确认并原子冻结的结构生成 Spider 查询；删除废弃的营销 query 规格，并使结构确认写入不再与 workflow runtime 竞争 SQLite 锁。

**Architecture:** LLM 仅产生 `SubjectStructure` 候选。产品营销 brief 确认时，用户确认 core、first intent、context；该 structure 与用户确认字段列表通过 `WorkflowRunManager` 所有的异步事务写入。唯一 query compiler 从该 structure 生成并冻结 Q1/Q2，dispatch 在调用 Spider 前验证该冻结契约。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、aiosqlite、SQLite、Next.js、pytest / pytest-asyncio。

## Global Constraints

- Lite 是正式方案子集：不新增第二套 query 或确认链路。
- LLM 候选、方向默认问题、页面展示字符串和未冻结 brief 不得生成 Spider query。
- 产品营销必须在 dispatch 前确认 `core_entities[0]`、`research_intents[0]`、`context_modifiers`。
- payload 仅新增 `subject_structure_user_confirmed_fields`；不新增 `AnchoredTerm` 或 query token provenance。
- Q1=`core + first_intent`；Q2=`core + first_intent + facet`。facet 仅来自用户自定义焦点或受控营销目标映射。
- 删除产品营销废弃的默认 query 规格与代码；不保留兼容分支或旧 query fallback。
- 确认 brief、subject checkpoint、workflow transition 必须在单一 async transaction 中完成；锁冲突是可恢复本地冲突，不得变成 500。
- 未通过 dispatch 守卫不得调用 Spider；真实 canary 证据不足不得触发追加搜索。

---

### Task 1: 原子化结构确认写入

**Files:**
- Modify: `app/services/workflow_run_manager.py`
- Modify: `app/content_research/async_dispatch.py`
- Modify: `app/content_research/service.py:912-1014`
- Test: `tests/unit/test_content_research_presearch.py`

**Interfaces:**
- Produces: `WorkflowRunManagerRuntime.confirm_subject_structure_atomically(workflow_run_id, state_writer) -> dict`。
- Consumes: 一个由 `AsyncFormalResearchDispatchRepository` 在 manager connection 内执行的 brief/checkpoint writer。
- Invariant: brief 更新、subject_structure checkpoint、run 从 `waiting_user` 恢复并完成 presearch，要么全部提交，要么全部回滚。

- [ ] **Step 1: 写入失败测试**

在真实 `WorkflowRunManagerRuntime` 测试中确认结构后，断言：brief payload 已是 confirmed、含三个 `subject_structure_user_confirmed_fields`、恰有一个 confirmed checkpoint、run 为 running 且 presearch step 为 succeeded。注入 state writer 的 `sqlite3.OperationalError("database is locked")`，断言返回可恢复的 `ContentResearchConflictError`，brief/checkpoint/run 状态均保持确认前值。

- [ ] **Step 2: 验证 RED**

Run: `pytest -q tests/unit/test_content_research_presearch.py -k 'structured_subject_confirmation or subject_confirmation_conflict'`

Expected: FAIL；当前实现先同步 `save_brief()`，随后在独立 runtime transaction 写 checkpoint 与状态。

- [ ] **Step 3: 实现单一事务转换**

在 `WorkflowRunManager` 增加一个 public transaction method：它先执行 `state_writer(self._conn)`，再在同一 `_transaction()` 中恢复 `waiting_user` run、启动并完成 presearch、推进到 brief_confirm。`AsyncFormalResearchDispatchRepository` 增加只接受已有 `aiosqlite.Connection` 的 `persist_subject_structure_confirmation()`，写入 updated brief 与 `content_research_stage_checkpoints`。`confirm_subject_structure()` 删除同步 `self._store.save_brief()` 与 `self._store.save_stage_checkpoint()` 调用，改为调用 runtime 原子接口。

将 SQLite lock 映射为 `ContentResearchConflictError`（HTTP 409，`recoverable=true`）；不得自动重试该确认请求。

- [ ] **Step 4: 验证 GREEN**

Run: `pytest -q tests/unit/test_content_research_presearch.py -k 'structured_subject_confirmation or subject_confirmation_conflict'`

Expected: PASS，且没有同步 store 写入被该确认路径调用。

- [ ] **Step 5: Commit**

```bash
git add app/services/workflow_run_manager.py app/content_research/async_dispatch.py app/content_research/service.py tests/unit/test_content_research_presearch.py
git commit -m "fix(content-research): atomically confirm subject structure"
```

### Task 2: 产品营销的显式结构确认与冻结字段

**Files:**
- Modify: `app/content_research/api_schemas.py`
- Modify: `app/content_research/service.py:1016-1160`
- Modify: `frontend/src/lib/content-research-api.ts`
- Modify: `frontend/src/app/creator/page.tsx`
- Test: `tests/e2e/test_content_research_brief_confirm_api.py`
- Test: `frontend/src/lib/content-research-api.test.ts`

**Interfaces:**
- Extends: `ContentResearchBriefConfirmRequest` with optional `subject_structure_confirmation` containing `core_object`, `research_intent`, and `context_modifiers`.
- Produces: product-marketing brief payload with `subject_structure_state="confirmed"` and `subject_structure_user_confirmed_fields=["core_entities[0]", "research_intents[0]", "context_modifiers"]`.
- Invariant: selecting `product_marketing` without this confirmation is a 422; non-product brief confirmation remains unchanged.

- [ ] **Step 1: 写入失败 API 和 UI 合约测试**

新增 API 用例：LLM 返回已解析但未用户确认的 `T恤 / 凉感 / 夏季` structure；选择 `product_marketing` 且不提交 `subject_structure_confirmation` 时返回 422。提交相同的三个字段后，frozen brief 记录完整 confirmed-fields 列表，且 locked plan 使用该确认值。新增前端 API payload 用例，断言产品营销确认请求传出这三个字段。

- [ ] **Step 2: 验证 RED**

Run: `pytest -q tests/e2e/test_content_research_brief_confirm_api.py -k product_marketing && npm test -- --runInBand frontend/src/lib/content-research-api.test.ts`

Expected: FAIL；当前 brief confirm 只传 structure hash，允许 LLM 直接成为产品营销 query 的事实来源。

- [ ] **Step 3: 实现单一确认入口**

Creator 在用户勾选产品营销时显示预填的 core、研究意图、使用场景字段；用户必须确认后才允许提交 brief。API 将确认值标准化为既有 `SubjectStructure`，并在 Task 1 的 async confirmation writer 内写入 brief。对于此前已通过 `confirm_subject_structure` 确认的 run，读取其 confirmed-fields 列表后允许 brief confirm，不再要求第二次输入。

`_build_and_persist_confirmed_plan()` 只读取该确认后的 frozen structure；不得使用 presearch LLM 候选的未确认值。

- [ ] **Step 4: 验证 GREEN**

Run: `pytest -q tests/e2e/test_content_research_brief_confirm_api.py && npm test -- --runInBand frontend/src/lib/content-research-api.test.ts`

Expected: PASS；非产品营销 API 测试不变。

- [ ] **Step 5: Commit**

```bash
git add app/content_research/api_schemas.py app/content_research/service.py frontend/src/lib/content-research-api.ts frontend/src/app/creator/page.tsx tests/e2e/test_content_research_brief_confirm_api.py frontend/src/lib/content-research-api.test.ts
git commit -m "feat(content-research): require marketing structure confirmation"
```

### Task 3: 删除旧营销话术 query 规格

**Files:**
- Modify: `app/content_research/workflow/direction_registry.py`
- Modify: `app/content_research/service.py`
- Modify: `tests/unit/test_content_research_query_planner.py`
- Modify: `tests/e2e/test_content_research_brief_confirm_api.py`
- Modify: active product-marketing specs and plans under `docs/superpowers/`

**Interfaces:**
- Produces: `product_marketing.default_questions == ["提炼小红书产品卖点表达"]`。
- Invariant: product-marketing compiler never receives a direction default facet; no active code/spec exposes the deleted query wording.

- [ ] **Step 1: 写入失败测试**

断言 product-marketing registry 只保留一个分析问题；确认 API 的 frozen query groups 精确等于 `T恤 凉感`、`T恤 凉感 上身感受`，不依赖任何旧文案的否定字符串断言。

- [ ] **Step 2: 验证 RED**

Run: `pytest -q tests/unit/test_content_research_query_planner.py tests/e2e/test_content_research_brief_confirm_api.py`

Expected: FAIL；registry 仍携带第二个泛化问题。

- [ ] **Step 3: 删除失效规格与分支**

从 `direction_registry.py` 删除第二个产品营销默认问题；删除 service 对产品营销 `second_facet` 的条件分支，而不是保留空字符串绕过。compiler 的非产品营销 `second_facet` 行为保留，但产品营销分支不接受或读取该参数。更新仍把废弃默认 query 描述为当前规格的文档与测试；历史 incident 文档仅保留事实记录，不作为 active specification。

- [ ] **Step 4: 验证 GREEN**

Run: `pytest -q tests/unit/test_content_research_query_planner.py tests/e2e/test_content_research_brief_confirm_api.py && rg -n "废弃营销 query" app/content_research tests docs/superpowers`

Expected: 测试通过；搜索结果为空。

- [ ] **Step 5: Commit**

```bash
git add app/content_research/workflow/direction_registry.py app/content_research/service.py tests/unit/test_content_research_query_planner.py tests/e2e/test_content_research_brief_confirm_api.py docs/superpowers
git commit -m "refactor(content-research): remove legacy marketing query"
```

### Task 4: 冻结前 dispatch 守卫

**Files:**
- Modify: `app/content_research/service.py`
- Test: `tests/e2e/test_content_research_brief_confirm_api.py`
- Test: `tests/unit/test_content_research_presearch.py`

**Interfaces:**
- Produces: `ContentResearchValidationError` before any dispatch/job creation when product-marketing structure confirmation or locked query requirements are missing.
- Invariant: every primary product-marketing `QueryGroup` contains the frozen core and first intent.

- [ ] **Step 1: 写入失败测试**

构造缺 `subject_structure_user_confirmed_fields` 的 product-marketing brief，断言 confirm/dispatch 被拒绝、没有 dispatch job、没有 source collection 请求。构造 Q2 缺 first intent 的 frozen plan，断言相同拒绝。

- [ ] **Step 2: 验证 RED**

Run: `pytest -q tests/e2e/test_content_research_brief_confirm_api.py -k 'product_marketing and guard'`

Expected: FAIL；当前只校验 structure state，不校验确认字段和 frozen primary group 的组成。

- [ ] **Step 3: 实现守卫**

在正式 dispatch 的唯一入口读取 `RunPolicySnapshot.effective_policy["locked_query_plan"]`；对 product-marketing 验证 confirmed-fields、Q1/Q2 数量与 core/intent 包含关系。失败时返回 validation error，不创建或入队 dispatch job。

- [ ] **Step 4: 验证 GREEN**

Run: `pytest -q tests/e2e/test_content_research_brief_confirm_api.py -k 'product_marketing and guard'`

Expected: PASS，且 fake Spider/dispatch 调用计数为零。

- [ ] **Step 5: Commit**

```bash
git add app/content_research/service.py tests/e2e/test_content_research_brief_confirm_api.py tests/unit/test_content_research_presearch.py
git commit -m "fix(content-research): guard frozen marketing dispatch"
```

### Task 5: 真实 packet canary

**Files:**
- No production-code changes.
- Verify: Creator、policy snapshot、persisted packets/checkpoints、Lite report.

- [ ] **Step 1: 创建真实 run**

输入 `夏季凉感 T恤`；选择产品营销；在确认卡提交 `T恤 / 凉感 / 夏季` 和 `content_seeding`；不填自定义焦点。

- [ ] **Step 2: 在 Spider 前验证快照**

断言 frozen primary queries 精确为 `T恤 凉感` 和 `T恤 凉感 上身感受`；确认这两个查询之外没有产品营销 primary query。

- [ ] **Step 3: 验证真实数据和报告**

仅以该 run 的 persisted packet 聚合验证 author 与 availability 一致、读取 admission funnel 与 Lite report。若证据不足，记录真实原因并停止；不得追加 Spider 搜索。

- [ ] **Step 4: 提交安全自动化证据（如有）**

不提交真实笔记正文、作者、URL、cookie、Spider 响应或数据库文件。

## Plan Self-Review

- Spec coverage: Task 1 修复确认写入原子性；Task 2 使产品营销结构由用户确认；Task 3 删除旧 query 规格；Task 4 阻止无效状态 dispatch；Task 5 验证真实闭环。
- Scope: 不增加 LLM、embedding、二次 query 审计或历史 run 回写。
- Type consistency: Task 2 的 confirmation payload 写入 Task 1 的 async writer；Task 3 的 compiler 只消费 Task 2 冻结的 structure；Task 4 读取 Task 3 的 snapshot。
- Placeholder scan: 无 TBD/TODO 或未定义的恢复条件。

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-08-07-f003-presearch-to-spider-closure.md`. Execute tasks sequentially: atomic confirmation is required before product confirmation UI, and dispatch guard is required before the real canary.
