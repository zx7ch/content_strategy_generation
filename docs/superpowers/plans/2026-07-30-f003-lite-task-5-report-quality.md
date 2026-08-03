# F003 Lite Task 5 Report Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` task-by-task.

**Goal:** `/lite-report` 交付相关、已准入、可追溯的结构化 finding/observation，提供逐证据来源跳转，并删除不合规历史报告而保留 Gate 2 证据；不交付 prose 分析结论。

**Architecture:** 正式链路仍是唯一生产者：pipeline → admission → governed snapshot → Lite 窄投影。Task 5 只增加 snapshot 合同校验、冻结 anchor 的正式 admission、单笔记 citation 直接跳转和报告级历史清理。

**Tech Stack:** Python、FastAPI、SQLite migration、pytest、Next.js/React、TypeScript、Vitest、Playwright。

## Global Constraints

- 保持 `template_only`，不增加 prose、aggregate、行动假设或 semantic LLM audit。
- `main_findings` 只含 admitted card；finding/observation 均保留并以 `card_kind` 分组；`WeakSignal` 只能在 lead 区。
- 仅 admitted `content_performance` observation 作为 observation；其余目录方向为 finding。
- 清理仅删除报告 publication/draft/audit/materialized message，不删除 workflow/checkpoint/source/citation/trace。
- 先跑定向测试，最后仅跑一条受控浏览器回归。

## Supersession and Execution Order

The original Task 1--5 decomposition is superseded by the inherited-invariant
matrix in `F003_content_research_lite_delivery_plan.md` §3.1.  Earlier local
Task 1 projection work remains uncommitted and is not an acceptance baseline;
earlier Task 2 work remains in repair status.  Execute the following order and
do not start a later unit until its predecessor passes independent review:

1. **Task 5A — Frozen admission foundation:** complete QueryGroup-plan freezing, stable author identity, relevance-qualified/eligible threshold accounting, deterministic policy/fingerprint hashing, replay isolation, and stale-fixture migration.
2. **Task 5B — Report read-model integrity:** consume only current `template_only` governed publications; preserve full card/citation/weak-signal identity, pagination, complete/partial/evidence-only semantics, and shared card-kind mapping.
3. **Task 5C — Single-note citation navigation:** enforce one Xiaohongshu canonical note per Lite citation group and implement direct source navigation plus evidence drawer audit.
4. **Task 5D — Historical report artifact purge:** idempotently remove all pre-cutover report-level artifacts while retaining Gate 2 evidence objects.
5. **Task 5E — Bounded verification record:** execute the inherited-invariant matrix, targeted API/UI checks, and one controlled browser case.
6. **Task 5F — Restore Lite-safe Trace and recovery UI:** restore the former formal Creator Trace Panel and recovery controls, but consume only the current Lite report contract, the read-only safe `/trace` projection, and current QR/resume actions. The former requirement to remove Trace/retry UI is superseded.
7. **Task 5G — Formal Trace parity on Lite-safe data:** restore formal Trace interaction and observability parity without restoring raw provider data or legacy report paths. It starts after the recoverable-failure state-machine repair specified in `2026-08-02-f003-lite-task-5g-trace-parity.md`.

### Task 5A: Frozen admission foundation

**Consumes:** the formal `RunPolicySnapshot`, `DirectionContract`, `QueryGroup`, `SamplePolicy`, `StageCheckpoint`, `ClaimAdmissionDecision`, and direction strategy contracts.

**Produces:** a replay-safe formal admission chain whose frozen policy contains the complete query plan and relevance rules, whose packets preserve stable source/author/query provenance, and whose admission thresholds count only relevant and eligible evidence.

**Must prove before implementation:**

- Same anchors/field sets inserted in different orders yield the same effective policy and hash.
- A run policy freezes normalized query, direction, priority, sort, time window, candidate cap, query-plan hash, custom question, anchor/synonym vocabulary, matching mode, and algorithm version before collection.
- `author_id` survives selection, packet, admission, comment lineage, and independent-author accounting; display author is only fallback presentation data.
- Unrelated, field-missing, out-of-window, duplicate-author, and replayed/old-policy packets cannot inflate thresholds.
- Admission replay fingerprints include relevance contract and evaluator algorithm version; old decisions/checkpoints cannot produce new governed cards.
- Every normal shared-admission fixture supplies frozen relevance; one explicit legacy fixture proves missing relevance is fail-closed.

**Scope exclusion:** no Lite read-model/UI behavior, report cleanup, source-navigation UI, cross-direction aggregate, prose, or real browser E2E.

**Independent completion condition:** parameterized contract/unit/pipeline/API tests prove every bullet above; all affected former fail-open fixtures are migrated; `ruff` and `git diff --check` pass; an independent review passes both formal-spec and code-quality axes.

## File Map

- `app/content_research/reporting/lite_read_model.py`：正式卡片校验与窄投影。
- `app/content_research/compression/fact_extractor.py`、`app/content_research/workflow/directional_pipeline.py`：冻结查询相关性。
- `app/content_research/migrations.py`、`app/memory/thread_store.py`、`app/content_research/reporting/publication_materializer.py`：历史报告定向清理。
- `frontend/src/app/creator/page.tsx`：直接 `查看原笔记` 链接。
- 定向测试：`tests/integration/test_content_research_lite_read_model.py`、`tests/unit/test_content_research_product_marketing_admission.py`、`tests/integration/test_content_research_direction_pipeline_store.py`、`tests/integration/test_content_research_report_publication_materializer.py`、`frontend/src/app/creator/page.test.tsx`、`tests/e2e/test_content_research_creator_browser.py`。

### Task 1: Formal-card projection guard

**Files:** `app/content_research/reporting/lite_read_model.py`; `tests/integration/test_content_research_lite_read_model.py`.

**Produces:** `_validated_card(card, citations_by_claim) -> dict | None`；只验证既有 governed-card 身份、scope、direction/claim type 与 citation identity，不重做 admission；有效 finding/observation 都进入 `main_findings`。

- [ ] 写 RED 测试：无 admitted decision/citation identity 的卡被过滤；合法标题型 `message_angle`、合法 `content_performance` observation 均留在 `main_findings`，且后端计数互斥。
- [ ] 运行：`pytest -q tests/integration/test_content_research_lite_read_model.py -k 'title_only or content_performance_observation'`；预期 RED。
- [ ] 实现：仅 `admitted`、具冻结 citation identity、方向／claim type 合法、scope 与正式 snapshot 一致的 card 可投影；不接受 Lite-local `accepted` 状态，不在 read model 推断语义相关性。
- [ ] 运行：`pytest -q tests/integration/test_content_research_lite_read_model.py`；预期 PASS。
- [ ] 提交：`git add app/content_research/reporting/lite_read_model.py tests/integration/test_content_research_lite_read_model.py && git commit -m 'fix: guard Lite findings with formal card contract'`。

### Task 2: Frozen-query relevance admission

**Files:** `app/content_research/compression/fact_extractor.py`、`app/content_research/workflow/directional_pipeline.py`、产品营销 admission/unit/pipeline tests。

**Produces:** 先冻结正式主体相关性规则、输入与 reason code，再使不支持冻结主体／QueryGroup 的候选不能 admitted。

- [ ] 写 RED 测试：不相关的职场黑话材料被 rejected；`速干徒步短裤` 命中冻结 category anchor 时仍可进入正式 direction strategy。
- [ ] 运行：`pytest -q tests/unit/test_content_research_product_marketing_admission.py tests/integration/test_content_research_direction_pipeline_store.py -k 'unrelated or query_subject'`；预期 RED。
- [ ] 在正式 `DirectionContract`/direction strategy 冻结 subject/category anchors、同义词、匹配模式和 `query_subject_not_supported`；`query_group_ids` 是必要谱系而非充分条件。候选直接引文必须命中 anchor 且符合 claim-type 字段。
- [ ] 运行：`pytest -q tests/unit/test_content_research_product_marketing_admission.py tests/integration/test_content_research_direction_pipeline_store.py`；预期 PASS。
- [ ] 提交：`git add app/content_research/compression/fact_extractor.py app/content_research/workflow/directional_pipeline.py tests/unit/test_content_research_product_marketing_admission.py tests/integration/test_content_research_direction_pipeline_store.py && git commit -m 'fix: reject unrelated product marketing evidence'`。

### Task 3: Direct source navigation

**Files:** `frontend/src/app/creator/page.tsx`、`frontend/src/app/creator/page.test.tsx`、`tests/e2e/test_content_research_creator_browser.py`。

**Produces:** Lite citation group 必须只含同一篇小红书 canonical note 的 evidence refs；`[n] 查看原笔记` 直接打开该 group 的唯一安全 URL，drawer 继续展示该 note 的冻结证据；其他 navigation state 无外链。

- [ ] 写 RED component tests：available citation 在 drawer 未打开时已有带正确 href 的外链；missing/unavailable 不存在外链。
- [ ] 运行：`cd frontend && npm test -- --run src/app/creator/page.test.tsx`；预期 RED。
- [ ] 验证每个 Lite citation group 的 evidence refs 共享同一 canonical source/URL；可用时将 `[n]` 渲染为带 `target='_blank' rel='noopener noreferrer'` 的 `查看原笔记` 外链，并增加独立“证据详情”按钮打开 drawer。URL/来源不一致时不得形成完整报告，保留现有不可跳转状态文案。
- [ ] 更新受控 browser case，在点击 citation 前验证 link，点击后验证现有 drawer。
- [ ] 运行：`cd frontend && npm test -- --run src/app/creator/page.test.tsx` 与 `pytest -q tests/e2e/test_content_research_creator_browser.py -k 'complete_report_uses_lite'`；预期 PASS。
- [ ] 提交：`git add frontend/src/app/creator/page.tsx frontend/src/app/creator/page.test.tsx tests/e2e/test_content_research_creator_browser.py && git commit -m 'fix: expose direct Lite report source links'`。

### Task 4: Purge nonconforming report artifacts

**Files:** `app/content_research/migrations.py`、`app/memory/thread_store.py`、`app/content_research/reporting/publication_materializer.py`、`app/content_research/reporting/lite_read_model.py` 和两个 integration tests。

**Produces:** 幂等 migration 一次性删除切换前全部 report-level rows 和 `artifact_result` message，保留 Gate 2 evidence。

- [ ] 写 RED migration test：切换前任意 publication/message 均被 purge，checkpoint/source/packet/admission/citation/trace 仍存在；reader/materializer 仅读取新合同 publication。
- [ ] 运行：`pytest -q tests/integration/test_content_research_report_publication_materializer.py tests/integration/test_content_research_lite_read_model.py -k 'purges_legacy or malformed'`；预期 RED。
- [ ] 增加下一个顺序 migration；按 report ID 删除全部既有 publication/draft/faithfulness/materialized link 和对应 `artifact_result`，不触碰 workflow/checkpoint/source/packet/admission/citation/trace。
- [ ] 让 reader/materializer 共享当前合同验证失败语义：not-found，不伪装 recovery，不追加新 message。
- [ ] 连跑两次：`pytest -q tests/integration/test_content_research_report_publication_materializer.py tests/integration/test_content_research_lite_read_model.py`；预期 PASS，第二次证明幂等。
- [ ] 提交：`git add app/content_research/migrations.py app/memory/thread_store.py app/content_research/reporting/publication_materializer.py app/content_research/reporting/lite_read_model.py tests/integration/test_content_research_report_publication_materializer.py tests/integration/test_content_research_lite_read_model.py && git commit -m 'fix: purge nonconforming Lite report artifacts'`。

### Task 5: Bounded verification record

- [ ] 运行：`pytest -q tests/unit/test_content_research_product_marketing_admission.py tests/integration/test_content_research_lite_read_model.py tests/integration/test_content_research_direction_pipeline_store.py tests/integration/test_content_research_report_publication_materializer.py`。
- [ ] 运行：`cd frontend && npm test -- --run src/app/creator/page.test.tsx && npx tsc --noEmit`。
- [ ] 仅运行：`pytest -q tests/e2e/test_content_research_creator_browser.py -k 'complete_report_uses_lite'`。
- [ ] 将精确结果写入 Lite 文档 Task 5 记录；不声明 Gate 3、Gate 4B 或三方向 real canary 完成。
- [ ] 提交：`git add docs/features/f003/F003_content_research_lite_delivery_plan.md && git commit -m 'docs: record Lite report quality verification'`。

### Task 5F: Restore the formal Trace Panel on Lite-safe data

**Files:** `frontend/src/app/creator/page.tsx`, `frontend/src/lib/content-research-api.ts`, `tests/e2e/test_content_research_creator_browser.py`, `tests/e2e/test_content_research_trace_api.py`, `docs/features/f003/F003_content_research_lite_delivery_plan.md`.

**Produces:** The former Creator Trace Inspector, full Trace button, refresh control, timeline, child-task statuses, provider-operation statuses, QR authentication recovery, and retry control return to the Lite Creator surface. The panel reads only the current `/trace` safe projection and `/lite-report`; it does not restore `/report`, `/results`, EvidenceBundle, ungoverned evidence, raw provider payloads, or prose/aggregate recommendations.

- [ ] Write RED browser/API tests for a run whose parent status is `running` while a child task is `auth_required`: the Creator sidebar must show an authentication-recovery state rather than "专家调研进行中"; the full Trace control must show the child task and provider failure; QR login and resume controls must be available.
- [ ] Run: `pytest -q tests/e2e/test_content_research_creator_browser.py -k 'trace or auth_required' tests/e2e/test_content_research_trace_api.py`; expect RED.
- [ ] Restore the pre-Lite Trace Inspector and its sidebar affordances in `page.tsx`, adapting its types and reads to `ContentResearchTraceResponse`, `ContentResearchLiteReportResponse`, `getContentResearchTrace`, `startXHSQRLogin`, `getCurrentXHSQRLogin`, and `resumeContentResearchFormalResearch` only.
- [ ] Treat a failed/recoverable child task or failed provider operation as higher priority than a non-terminal parent `run_status`; it must produce explicit recoverable failure copy, never a misleading running state. Report-unavailable UI may show safe execution state only, never ungoverned source, claim, or metric content.
- [ ] The refresh control is a read-only trace refresh. The retry control invokes exactly one existing resume action after authentication; terminal completed reports must not gain a retry path.
- [ ] Run focused browser/API tests and `cd frontend && npm test && npx tsc --noEmit`; expect PASS.
- [ ] Update the Lite delivery plan to state that safe trace/recovery UI is part of the formal Lite subset, while legacy report and EvidenceBundle consumers remain removed.
- [ ] Commit: `git add frontend/src/app/creator/page.tsx frontend/src/lib/content-research-api.ts tests/e2e/test_content_research_creator_browser.py tests/e2e/test_content_research_trace_api.py docs/features/f003/F003_content_research_lite_delivery_plan.md && git commit -m 'fix(content-research): restore Lite-safe trace recovery UI'`.

## Plan Self-Review

Task 1–2 覆盖共同准入与相关性，Task 3 只改证据交互，Task 4 完成授权的报告级清理且保存 Gate 2 底座，Task 5 保持验证边界，Task 5F 恢复正式 Trace UI 的 Lite-safe 子集。计划没有旧报告兼容路径、自由总结或全量真实 E2E。
