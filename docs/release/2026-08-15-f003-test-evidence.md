# F003 历史测试错误与回归证据（Release Gate 输入）

日期：2026-08-15
范围：只汇集仓库内真实运行/验收记录、版本提交及当前回归测试；不把设计文档中的假设当作故障事实。

## 证据边界与现状

`docs/release/2026-08-13-content-research-release-audit.md` 是最近一次真实 release 审计：它明确记录了已实际构建 frozen Runtime、以隔离 HOME 操作它、运行目标登录/后端测试，并尝试了 Creator Playwright suite（[audit:5-9](2026-08-13-content-research-release-audit.md#L5-L9)）。

现有门禁仍未成为单一版本化入口：release workflow 只构建、压缩、上传，未执行 F003 测试（[.github/workflows/release.yml:84-104](../../.github/workflows/release.yml#L84-L104)）；CI 只执行 unit suite（[.github/workflows/ci.yml:32-36](../../.github/workflows/ci.yml#L32-L36)）。计划中要求的 `scripts/run_release_gate.sh` 和有总时限的 browser runner 在当前工作树均不存在；因此下列命令是可复现的回归检查，不应被误报为已由 tag-release 自动执行。

## 已发生错误、对应回归测试与复现命令

### 1. Frozen Runtime 遗漏登录的惰性依赖（P0，已修复）

- **真实错误：** release zip 中保存 Cookie 返回 HTTP 500（遗漏 `curl_cffi`）；启动 QR 登录产生 `qr_render_failed` 且无图像（遗漏 `qrcode`）。审计还记录了影响：新用户无法完成登录，内容调研无法进入 source collection（[audit:12-20](2026-08-13-content-research-release-audit.md#L12-L20)）。
- **修复证据：** 提交 `0714b20` 将二者加入 PyInstaller hidden imports；当前 `runtime_main.spec` 的声明在 [runtime_main.spec:77-78](../../runtime_main.spec#L77-L78)。
- **对应测试：** `test_runtime_bundle_declares_lazy_xhs_login_dependencies` 断言这两个 hidden import（[tests/unit/test_runtime_launcher.py:14-18](../../tests/unit/test_runtime_launcher.py#L14-L18)）。它保护声明，不能单独证明 zip 可启动。
- **可复现/回归命令：** `pytest -q tests/unit/test_runtime_launcher.py::test_runtime_bundle_declares_lazy_xhs_login_dependencies`；真实 artifact 行为需重跑审计所述的 build + 隔离 HOME Cookie/QR endpoint 检查。当前可用 artifact 结构检查为 `RELEASE_GATE_REQUIRE_ARTIFACT=1 pytest -q tests/acceptance/test_runtime_release_artifact.py`（[test:14-25](../../tests/acceptance/test_runtime_release_artifact.py#L14-L25)）。

### 2. Presearch 与登录 UI 吞掉真实失败原因（P1，已有回归覆盖）

- **真实错误：** 任意 LLM、Runtime 或 source-service presearch 故障都被 UI 归咎于“小红书登录态”；Cookie/QR 卡也丢弃 API 的安全 error code（[audit:26-44](2026-08-13-content-research-release-audit.md#L26-L44)）。这会把模型 key、代理、API 契约或服务错误误导为登录问题。
- **对应测试：** `released-content feature errors tell the Creator to restart the upgraded Runtime` 与 `unknown API errors preserve a safe actionable server message`，分别覆盖 `F003_LITE_PREVIEW_DISABLED` 和未知服务错误（[frontend/src/lib/content-research-error-feedback.test.ts:7-24](../../frontend/src/lib/content-research-error-feedback.test.ts#L7-L24)）。
- **可复现命令：** `cd frontend && npm test -- src/lib/content-research-error-feedback.test.ts`。

### 3. Login 卡允许重复进行中的请求（P2，已有组件回归）

- **真实错误：** QR 请求 pending 的 45 秒内或 Cookie 保存时可重复点击；没有 busy/disabled UI，用户会发出冗余请求并得到不一致反馈（[audit:46-52](2026-08-13-content-research-release-audit.md#L46-L52)）。
- **对应测试：** `Xiaohongshu login keeps a rejected Cookie editable and blocks a duplicate save`：发起 PUT 后断言按钮 disabled、显示“保存中…”，失败后 Cookie 保持可编辑并显示后端错误（[frontend/src/app/creator/page.test.tsx:64-124](../../frontend/src/app/creator/page.test.tsx#L64-L124)）。
- **可复现命令：** `cd frontend && npm test -- src/app/creator/page.test.tsx`。

### 4. Creator Playwright suite 无全局完成边界（P2，未关闭）

- **真实错误：** 审计实际执行 `.venv/bin/pytest -q tests/e2e/test_content_research_creator_browser.py` 后，suite 超过五分钟、CPU 很低、没有 pass/fail/timeout，因审计继续而人工停止（[audit:54-64](2026-08-13-content-research-release-audit.md#L54-L64)）。
- **对应测试资产：** 该 suite 包含真实浏览器恢复/错误路径，例如 `test_creator_model_failure_edit_save_and_continue_same_presearch` 会注入确定性 503 后检查用户反馈和同一 run 的恢复（[tests/e2e/test_content_research_creator_browser.py:298-397](../../tests/e2e/test_content_research_creator_browser.py#L298-L397)），但这不是 suite 级 timeout。
- **可复现命令：** `.venv/bin/pytest -q tests/e2e/test_content_research_creator_browser.py`。这条命令本身可能无限等待，必须由未来 gate runner 外包一层总时限、超时日志和截图；在此实现前不能作为阻断式 CI 命令直接运行。

### 5. 结构确认与 runtime 并发写入触发 SQLite 锁（P1，已建可重复单测）

- **真实错误：** 真实 canary `run_fabdc32b145c4a6b81dd3a8ec35d947d` 在结构确认写入时触发 `sqlite3.OperationalError: database is locked`；Spider 未启动，因此没有 snapshot、packet、漏斗或报告（[docs/bugfix/20260807_f003_presearch_to_spider_closure.md:31-35](../bugfix/20260807_f003_presearch_to_spider_closure.md#L31-L35)，[64-83](../bugfix/20260807_f003_presearch_to_spider_closure.md#L64-L83)）。
- **对应测试：** `test_subject_confirmation_conflict_rolls_back_brief_checkpoint_and_runtime` 注入同一 `OperationalError`，要求转为 `CONTENT_RESEARCH_SUBJECT_CONFIRMATION_CONFLICT`，且 brief/checkpoint/runtime snapshot 均回滚（[tests/unit/test_content_research_presearch.py:425-483](../../tests/unit/test_content_research_presearch.py#L425-L483)）。
- **可复现命令：** `pytest -q tests/unit/test_content_research_presearch.py::test_subject_confirmation_conflict_rolls_back_brief_checkpoint_and_runtime`。
- **残余：** 文档明确同步/异步 SQLite 写协调仍为 P1，真实 canary 不应以该单测通过替代并发 release 演练（[bugfix:137-143](../bugfix/20260807_f003_presearch_to_spider_closure.md#L137-L143)）。

### 6. 有失败 specialist 时父工作流仍可能完成（历史主链故障，已保护）

- **真实错误：** 失败 specialist 曾被记录为 terminal child outcome，但父 `source_collect_minimal` 仍可完成并把 workflow 标记成功，从而暴露不完整下游状态（[F003 bugfix record:120-130](../features/f003/F003_content_research_bugfix_record.md#L120-L130)）。
- **对应测试：** `test_failed_direction_does_not_run_cross_direction_governance` 构造 failed task，断言不创建 checkpoint/最终 artifact，而是发出 `formal_research_needs_retry`（[tests/unit/test_content_research_governed_completion.py:166-186](../../tests/unit/test_content_research_governed_completion.py#L166-L186)）。
- **可复现命令：** `pytest -q tests/unit/test_content_research_governed_completion.py::test_failed_direction_does_not_run_cross_direction_governance`。

### 7. 有足够来源时报告被错误降级为 evidence-only（Gate 2，已保护）

- **真实错误：** 认证真实 `product_marketing` run 具有四个独立来源、完整 QueryGroup coverage 和八个 citation groups，却 materialise 为 `evidence_only_report`；根因包括 scope fallback 被错误标记 `limitation_reference_unknown`，以及把整篇 note body 作为无界 direct claim（[F003 bugfix record:5-21](../features/f003/F003_content_research_bugfix_record.md#L5-L21)）。
- **对应测试：** `test_audit_accepts_the_composer_scope_card_when_no_limitations_exist` 保护无 limitation 时 composer 的 scope card 可通过（[tests/unit/test_content_research_report_faithfulness.py:344-350](../../tests/unit/test_content_research_report_faithfulness.py#L344-L350)）；`test_execution_exhausts_rewrites_and_publishes_evidence_only` 则保留真正 semantic failure 必须降级的反例（[tests/integration/test_content_research_report_execution.py:152-169](../../tests/integration/test_content_research_report_execution.py#L152-L169)）。
- **可复现命令：** `pytest -q tests/unit/test_content_research_report_faithfulness.py::test_audit_accepts_the_composer_scope_card_when_no_limitations_exist tests/integration/test_content_research_report_execution.py::test_execution_exhausts_rewrites_and_publishes_evidence_only`。

### 8. 产品营销 Q2 丢失首要意图，耗尽搜索预算后零准入（已建立 dispatch guard）

- **真实错误：** 真实 run `run_770af525b4a84dbe87df3128dccc0532` 保存了 30 个真实 note packet、21 名作者，但冻结 Q2 是 `T恤 识别营销话术和内容角度`，没有结构确认的首要意图“凉感”；结果为 30 selected → 0 quote-relevant → 0 eligible → 0 admitted（[docs/bugfix/20260806_f003_product_marketing_query_and_author_metadata.md:7-29](../bugfix/20260806_f003_product_marketing_query_and_author_metadata.md#L7-L29)）。
- **对应测试：** `test_product_marketing_dispatch_guard_rejects_missing_confirmed_fields_before_enqueue` 确认字段不完整时在 enqueue 前返回 422；`test_product_marketing_dispatch_guard_rejects_tampered_q2_without_first_intent_before_enqueue` 将 Q2 篡改为缺少 first intent 的 `T恤 上身感受`，同样必须拒绝且 job 数为零（[tests/e2e/test_content_research_brief_confirm_api.py:241-328](../../tests/e2e/test_content_research_brief_confirm_api.py#L241-L328)）。
- **可复现命令：** `pytest -q tests/e2e/test_content_research_brief_confirm_api.py::test_product_marketing_dispatch_guard_rejects_missing_confirmed_fields_before_enqueue tests/e2e/test_content_research_brief_confirm_api.py::test_product_marketing_dispatch_guard_rejects_tampered_q2_without_first_intent_before_enqueue`。

## Gate 编排所需的最小证据分层

1. **每次 PR、无凭据：** 上述 unit/integration/frontend tests，加 release archive 存在时的 artifact structure test；失败即阻断。
2. **每个候选 release、隔离环境：** 一次 PyInstaller build、zip integrity、frozen Runtime 的 Cookie save 和 QR creation；输出只保留脱敏状态/HTTP 结果。该层是 #1 的必要补足。
3. **浏览器：** 运行 #4 suite，但只能经有总时限和 diagnostics 的 runner；超时是明确失败而非人工停止。
4. **人工/凭据环境：** #5 的真实并发写入与 provider 认证/恢复演练；同一 run 的 retry、唯一报告和刷新恢复是验收证据，密钥、Cookie、QR/raw payload 不入日志。

所有 gate 结果应连同 commit SHA、gate manifest 版本、命令、退出码、耗时和诊断 artifact 保存；这样才能把以上“修复后局部回归测试”升级为版本化、可重复执行的 release evidence。
# 2026-08-15 F003 Release Test Evidence

## Test levels and boundaries

- **Unit / integration:** temporary SQLite and in-process FastAPI routes; they use
  deterministic fixtures and do not call a real LLM or Xiaohongshu.
- **Frontend:** Node TypeScript tests and production build; API responses are
  controlled test responses, not a real browser session.
- **Frozen release gate:** opt-in test `RUN_FROZEN_RUNTIME_RESTART_GATE=1 pytest -q
  tests/acceptance/test_runtime_release_artifact.py`. It uses macOS `unzip` exactly
  as the distributed package does, starts the actual frozen executable twice on an
  isolated port, and verifies the same SQLite run, 28-detail fixture, Trace and
  recoverable report read model after restart. It uses a persisted local fixture,
  not a live provider call.

## 2026-08-23 final vertical-slice evidence

Verified commits: `29b8fae`, `64eda1a`, `e754cfb`, `4da8bd4`, `7065061`.

- Five owned Creator journeys passed in 63.29s: durable Run B precedence beside
  historical Run A, v2 `A / A+B / A+C`, missing B/C replacement, exact edited
  provider query with A-only admission, and real Coverage Expand through the
  Router/SQLite/worker/provider/read model (`5 passed`).
- Version-owned v1/v2 Scope, Limited/Expand/Relax and replay checks passed
  (`18 passed`); frozen draft round-trip and provider-failure recovery checks
  passed separately (`4 passed`).
- Task 5 atomic decision checks passed (`6 passed`): v2 Relax preserves the v2
  schema, duplicate Expand creates one authorization, a predecessor Coverage
  snapshot writes nothing, the lower-level execution-unit entry rejects the same
  stale snapshot, and an Expand query without A writes nothing.
- Foundation checks for Trace, model configuration/LLM and Xiaohongshu QR/Cookie
  state passed (`35 passed`). Frontend tests passed (`82 passed`) and the production
  build completed. The build retained only the previously recorded image and React
  hook lint warnings.
- The broad touched-area run produced `123 passed, 5 failed`. Four failures are
  historical test-fixture assumptions (missing Draft replacement identity or an
  isolated store without the shared workflow tables); one asserts the superseded
  product-marketing rule that season/scenario are required. These are not used to
  change the confirmed v2 contract, where only A is required.
- The repository-wide `pytest -q` was sampled but stopped after the known unbounded
  browser portion again made the command unsuitable as a release gate. It is not
  recorded as a pass. This is the same open gate-runner limitation documented in
  item 4 above; the five owned browser journeys were run explicitly and passed.
- No authenticated canary is claimed. This workspace run did not establish both a
  validated live LLM credential and an authenticated live Xiaohongshu session with
  redacted evidence, so deterministic adapters were used for provider execution.
