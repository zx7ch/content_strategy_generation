# F003 Lite 报告就绪与相关性诊断 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不再次调用 Spider 的前提下，使报告只在可读时成为 Creator 的终态结果，并把产品营销 claim 的高淘汰率归因到互斥、可复现的规则类别。

**Architecture:** 正式调研完成后，workflow 从 `running` 进入 `finalizing_report`。该专用状态执行治理、快照、审计、publication 与 artifact materialization；仅在 artifact 和唯一 Creator timeline message 已就绪后转为 `succeeded`。Lite reader 在 `finalizing_report` 期间不暴露报告。相关性规则返回稳定的 rejection code，而不是复用 `query_subject_not_supported`。author 保持 packet projection 的事实字段，不把不需要的 availability 标记升级为采集门槛。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、SQLite、pytest / pytest-asyncio、Next.js / TypeScript。

## Global Constraints

- 不调用 Spider，不新增搜索、LLM、embedding 或真实笔记 fixture。
- 当前真实 run 只用于只读离线 replay / 聚合验证；不得修改其历史记录。
- Lite report 的内容合同不变；publication 未可读不能伪装成“证据不足”。
- SQLite 写入协调是 P1，本计划不改动该路径。
- 产品营销的 `author` 保持为 projection 和独立作者计数的输入；不加入 `required_note_fields`，不额外淘汰笔记。

---

### Task 1: 用 `finalizing_report` 建立报告就绪终态边界

**Files:**

- Modify: `app/models/workflow.py`
- Modify: `app/services/workflow_run_manager.py`
- Modify: `app/content_research/service.py:3430-3530,3714-3785`
- Modify: `app/content_research/reporting/read_model.py`
- Modify: `app/content_research/reporting/publication_materializer.py`
- Modify: `tests/e2e/test_content_research_report_publication_timeline_api.py`
- Modify: `tests/unit/test_workflow_run_manager.py`

**Interfaces:**

- Produces: `running → finalizing_report → succeeded` 的单向状态转换；`succeeded` 保证 Lite report 已可读。
- Consumes: 已完成的方向任务、治理记录及 frozen policy。
- Preserves: `_publish_report_after_workflow_completion()` 只在 `finalizing_report` 中 materialize；若 publication 出错，run 可以失败或取消而不会卡住。

- [ ] **Step 1: Write the failing lifecycle test**

在现有 report-publication timeline API 测试中，先 materialize 再断言 Lite endpoint 为 404；只有 `complete_report_finalization()` 后才返回 200。再为 manager 加状态机测试：普通 `complete_run()` 不能跳过 finalization，finalization 可以成功、失败或取消。

- [ ] **Step 2: Verify RED**

Run: `pytest -q tests/e2e/test_content_research_report_publication_timeline_api.py -k publication`

Expected: FAIL，因为当前 materialized artifact 会在 `finalizing_report` 中被 Lite reader 提前读取。

- [ ] **Step 3: Implement the minimal lifecycle split**

新增 `FINALIZING_REPORT`，由 formal research runtime 在方向任务完成后进入该状态。publication materializer 只接受该状态；reader 只接受 `succeeded`。publication、artifact 和唯一 timeline message 完成后再调用 `complete_report_finalization()`；错误路径允许把 finalizing run 失败或取消。删除 publication 阶段的重复 marketing governance。

- [ ] **Step 4: Verify GREEN**

Run: `pytest -q tests/e2e/test_content_research_report_publication_timeline_api.py tests/integration/test_content_research_lite_read_model.py`

Expected: PASS；`succeeded` 的 Lite report 一直具有 publication、artifact 与一致 lineage，finalizing artifact 不对用户可读。

- [ ] **Step 5: Commit**

```bash
git add app/models/workflow.py app/services/workflow_run_manager.py app/content_research/service.py app/content_research/reporting/read_model.py app/content_research/reporting/publication_materializer.py tests/e2e/test_content_research_report_publication_timeline_api.py tests/unit/test_workflow_run_manager.py
git commit -m "fix(content-research): finalize reports before success"
```

### Task 2: 将产品营销相关性拒绝分类为互斥原因

**Files:**

- Modify: `app/content_research/admission/relevance.py`
- Modify: `tests/unit/test_content_research_admission_relevance.py`
- Modify: `app/content_research/workflow/directional_pipeline.py`（仅当 trace 聚合需要读取新 code）
- Modify: `tests/unit/test_content_research_directional_pipeline.py`（仅当上一文件有改动）

**Interfaces:**

- Produces: `query_relevance_reason(...) -> str | None` 返回一个稳定 rejection code。
- Contract: frozen query provenance、quote shape / field、core anchor、first-intent anchor 依次检查，且不再以 `query_subject_not_supported` 覆盖不同失败原因。
- Consumes: 已持久化 claim、quote ref 与 policy snapshot；不请求 provider。

- [ ] **Step 1: Write failing unit tests for each rejection boundary**

添加彼此独立的用例，分别断言：无有效 query group 返回 `invalid_query_provenance`；缺 quote 返回 `invalid_quote_reference`；quote 不含 core 返回 `core_entity_not_supported`；quote 含 core 但不含 first intent 返回 `first_intent_not_supported`。保留合法 quote 返回 `None` 的现有行为。

- [ ] **Step 2: Verify RED**

Run: `pytest -q tests/unit/test_content_research_admission_relevance.py`

Expected: FAIL，因为当前多个边界均返回 `query_subject_not_supported`。

- [ ] **Step 3: Implement only the new deterministic codes**

保持原先的判断顺序和 admission 门槛不变，仅在每个 return 点返回上述专用 code；将 trace / decision 已保存的 `reason_codes` 直接保留为新值，不引入重跑或二次搜索。

- [ ] **Step 4: Verify GREEN and offline replay**

Run: `pytest -q tests/unit/test_content_research_admission_relevance.py tests/unit/test_content_research_directional_pipeline.py`

然后以只读脚本或已有 service replay 统计本次 run 的新分类输入；不得写入 DB、不得触发 Spider。

- [ ] **Step 5: Commit**

```bash
git add app/content_research/admission/relevance.py tests/unit/test_content_research_admission_relevance.py app/content_research/workflow/directional_pipeline.py tests/unit/test_content_research_directional_pipeline.py
git commit -m "fix(content-research): classify marketing relevance rejections"
```

### Task 3: 关闭 author availability 的错误缺陷定义

**Files:**

- Modify: `tests/unit/test_content_research_source_payloads.py`
- Modify: `docs/bugfix/20260807_f003_presearch_to_spider_closure.md`

**Interfaces:**

- Verifies: `normalize_note_detail()` 在 `author` 被请求时才写入 `field_availability.author`，且 author projection 始终不被 availability 的字段集过滤。
- Preserves: `product_marketing.required_note_fields` 不含 `author`。

- [ ] **Step 1: Write a regression test for the contract distinction**

构造有 author 的 detail payload，使用不包含 `author` 的 `required_fields`；断言 `payload["author"]` 保留，同时 `field_availability` 不需要含 `author`。再使用包含 `author` 的 fields 断言 availability 为 `present`。

- [ ] **Step 2: Verify the test describes the current contract**

Run: `pytest -q tests/unit/test_content_research_source_payloads.py -k author`

Expected: PASS；若失败，只修 normalizer 的 projection/availability 边界，不改 `product_marketing` 采集门槛。

- [ ] **Step 3: Correct the closure record**

把“packet 缺少 author”改为“availability 只声明合同请求字段”；记录真实 run 的 author projection 与独立作者计数已有效，移除将其当作 Spider/parser bug 的修复项。

- [ ] **Step 4: Run focused regression suite**

Run: `pytest -q tests/unit/test_content_research_source_payloads.py tests/unit/test_content_research_admission_relevance.py`

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_content_research_source_payloads.py docs/bugfix/20260807_f003_presearch_to_spider_closure.md
git commit -m "docs(content-research): clarify author packet contract"
```

## Verification Closure

Run the three task suites together and issue a read-only request to the existing run's Lite endpoint. Do not claim a new real canary: the provider account is abnormal and no second Spider request is permitted.

## Completion Record (2026-08-07)

- Task 1 completed with the `finalizing_report` state boundary. The finalizing Lite API regression is 404; the same report is 200 only after `complete_report_finalization()`.
- Task 2 completed with deterministic `invalid_query_relevance_contract`, `invalid_query_provenance`, `invalid_quote_reference`, `core_entity_not_supported`, and `first_intent_not_supported` codes. The old overloaded product-marketing rejection path was removed.
- Task 3 completed with author projection/availability distinction tests. `author` is not a product-marketing collection requirement.
- Verification: the focused lifecycle, reporting, admission, and source-payload suites pass (`99 passed`). No Spider request was made.
