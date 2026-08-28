# Task 3.1 impact manifest and test baseline

## Baseline identity

- Git commit: `700fba8`
- Branch: `localwork`
- Recorded at: `2026-08-25T23:36:50+08:00`
- Task status: L2 `NOT READY`; baseline collection only, no schema or production-code changes

> Baseline capture-time status is retained above as history. The updated Contract Pack passed the
> L2 readiness gate on 2026-08-26; implementation may begin at unreachable Checkpoint 3.1-A.

The worktree already contained unrelated or previously approved documentation changes. Task 3.1
must preserve them and compare every later checkpoint against this baseline.

## Lane results

| Lane | Command scope | Result | Classification |
|---|---|---:|---|
| A/B unit contracts | Marketing conclusions, analysis, runtime, migrations, lifecycle, Trace, Scope, admission, RAG | `166 passed` | Green baseline |
| A/B owned integration | Lifecycle, SQLite coordination, report execution/materialization/read, checkpoint recovery, workflow worker | `36 passed, 5 failed` | Stable pre-implementation failures listed below |
| A/B API/E2E | PreResearch, Brief/Scope, Trace, model configuration, runtime connection, XHS login/QR, publication timeline | `35 passed` | Green baseline |
| Creator frontend | Node tests + TypeScript `--noEmit` | `76 passed`; typecheck passed | Green baseline |

## Stable baseline failures

Each failure reproduced when its file or individual test ran alone, so it is not cross-suite order
pollution and was not introduced by Task 3.1 production code.

1. `test_materializes_published_report_as_one_creator_snapshot_and_timeline_result`
   - Materialized artifact exists, but the connected `ThreadStore` reads zero `artifact_result`
     messages.
2. `test_migration_purges_legacy_report_lineage_but_preserves_same_run_non_report_results`
   - The same report-message projection is absent after materialization.
3. `test_checkpoint_recovery_resumes_generation_and_matches_one_shot_result`
4. `test_checkpoint_payload_stays_lightweight`
5. `test_checkpoint_recovery_resumes_after_strategy_interrupt_point`
   - All three fail before exercising recovery because `AsyncSqliteSaver` is a `MagicMock` without
     `from_conn_string` in the current test environment.

These failures are baseline risks, not permission to keep Lane A/B red at Task 3.1 completion. Route
them before the checkpoint that depends on their proof: report-message failures before the first
reachable publication slice; checkpoint-saver failures before analysis checkpoint recovery proof.

Resolution update, 2026-08-26: failures 1–2 were stale tests for the two-phase publication contract.
They now prove zero Timeline messages before Run success and one idempotent `artifact_result` after
`complete_report_finalization` + `publish_timeline_message`; the full materializer file is `10 passed`.
Failures 3–5 came from an obsolete E2E fixture replacing the installed SQLite saver with a
`MagicMock`; the fixture was removed and the real checkpoint recovery suite is `3 passed`.

## Task 3.1 impact surface

- Runtime and dedicated Research embedding adapter registration, health, fingerprint, startup and shutdown; existing RAG/Chroma behavior is regression-only scope.
- Immutable Evidence Snapshot and analysis unit/attempt/checkpoint persistence.
- Worker claim, lease, heartbeat, fencing, retry and recovery.
- Atomic evidence extraction, grouping, three planned-track decisions, sparse visible output and verification.
- Partial/limited publication, report materialization and historical reads.
- Single-transaction Trace snapshot and Creator revision ordering.
- LLM configuration, XHS authentication, Scope/query identity, SQLite coordination and RAG
  compatibility regression lanes.

## Checkpoint 3.1-A entry gate — satisfied

Checkpoint 3.1-A began test-first and kept new runtime/schema behavior unreachable until its migration,
Snapshot, Research embedding adapter, identity and history-preservation acceptance tests passed.

## Checkpoint 3.1-A implementation evidence — 2026-08-26

Status: **COMPLETE; behavior remains unreachable from Content Research commands/workers.**

Delivered:

- dedicated `SentenceTransformerResearchEmbeddingAdapter` with pinned model revision, fixed
  title/body input format, fingerprint, count/dimension/finite-value validation, degraded health and
  shutdown;
- application-lifespan registration behind the existing F003 preview switch, without modifying
  RAG/Chroma behavior;
- additive migration `0034` for immutable Evidence Snapshot notes, analysis units, one-active-attempt
  identity and unit/track/stage/input checkpoints;
- real SQLite repository proving Snapshot replay/conflict semantics, explicit successor identity,
  lease fencing and completed-track checkpoint reuse;
- transactional migration rollback after injected failure.

Green proof:

- focused adapter/repository/migration/RAG/runtime/config/import lane: `83 passed`;
- focused Task 3.1-A + migration rollback rerun: all green;
- Ruff on the touched Task 3.1-A implementation/test files: passed;
- Python compileall for the touched runtime/persistence/main modules: passed.

Broader diagnostic, not the frozen baseline command:

- broad Content Research unit glob after fixture/obsolete-contract cleanup: `408 passed`;
- real LangGraph checkpoint recovery: `3 passed`;
- broad Content Research integration glob after schema/workflow-isolation repairs:
  `167 passed`;
- Task 3.1-B Browser-to-owned-stack happy path: `1 passed`;
- failed-track successor recovery proof: `1 passed`.

The three checkpoint-saver failures were caused by an obsolete E2E `conftest` replacing the
installed SQLite saver with `MagicMock`; the mock was removed and the real saver recovery suite is
green. Valid unit fixtures were updated for Scope authority, two obsolete v1/no-live-Run tests were
deleted, and a wall-clock lease test was made deterministic without weakening its fencing assertions.
The 42 old direct-pipeline failures were then inventoried individually: retained product-marketing,
competitor-discovery and content-performance proofs now create a frozen Scope-owned execution context;
six obsolete v1/old-direction cases were deleted only after replacement coverage was present. No
unscoped fallback was added, and the resulting broad integration lane is fully green.

## Checkpoint 3.1-B completion update — 2026-08-26

Checkpoint 3.1-B entered a stabilization pass after real-browser acceptance exposed a projection
contradiction: a selected analysis track withdrawn by faithfulness was rendered as
`analysis_unavailable`. The accepted solution keeps the immutable analysis decision and records a
separate per-publication `withheld_by_faithfulness` disposition; it must pass `ACC-31-25` before B is
closed again.

The reachable path otherwise uses a dedicated durable analysis worker with
lease recovery, a public compatible-only retry command, immutable per-track checkpoints, a consistent
revisioned Trace snapshot, Creator stale-response/failure handling, atomic cancel/publication
arbitration, business-idempotent unknown-outcome recovery, legacy retry fencing, and immutable
publication integrity repair. Evidence: Task 3.1 Python lane `595 passed`; frontend `77 passed` plus
TypeScript; Browser-to-owned-stack happy path `1 passed`; touched-file Ruff clean. The SQLite fault
proof explicitly aborts successor insertion and verifies lifecycle, successor and command ledger all
roll back before a clean retry succeeds.

The broad repository diagnostic still contains 11 unrelated pre-existing failures in legacy
orchestrator injection, runtime-version/config aliases, v2 runtime/schema expectations and the
extension MVP API. They are outside Task 3.1 and none occurs in the Content Research lanes above.

## Checkpoint 3.1-C/D local completion update — 2026-08-26

方案 A 已完整落地：analysis decision/checkpoint 不可变；每个 publication revision 单独记录
`published`、`withheld_by_faithfulness` 或 `omitted_by_publication_policy`。Lite report 和 Trace
同时展示分析状态与当前发布处置，faithfulness 撤回不再伪装成技术失败，也不会触发分析重跑。

3.1-C 新增句子/短句 exact-span 证据、严格三轨输入、qualifier-compatible 中文语义归并、
support/counter lineage、独立 groundedness verifier 和 `contested` 报告/Trace/UI。版本化 100
篇固定中文包结果记录在 `2026-08-26-task-3-1-c-quality-evaluation.{json,md}`；所有指标为
`1.0`，零容忍失败为 `0`。这些数值只代表合成确定性回归包，不包装成线上准确率。

3.1-D 删除了可执行的无 Coverage manifest 新-Run 营销分析分支；缺少 authority 现在直接
后端失败。Lifecycle 从 Run 当前 `artifact_version` 精确绑定的 `final_result` 读取 publication
ID，Trace 不再依赖 latest publication fallback。两条 owned-stack Browser Acceptance 分别证明：

- selected 分析被审计撤回、其余轨发布、证据内联展开与 Trace disposition 一致；
- value contested（支持 3 篇/3 位作者，反向 2 篇/2 位作者）、need/message 正常不足、正反
  引用均可展开到 exact quote。

最终本地门禁：

- Content Research unit/integration/API/Browser/evaluation：`601 passed, 7 skipped`；
- LLM/XHS Cookie+QR/Scope/SQLite/Trace/RAG 回归：`157 passed`；
- Creator frontend：`77 passed`；TypeScript `--noEmit` 通过；
- Task 3.1 focused quality/report/attempt lane：`169 passed`；
- touched/untracked Python Ruff 通过（保留仓库既有 N818 命名例外）；`git diff --check` 通过。

全仓诊断为 `1297 passed, 48 failed, 14 skipped`。其中 Task 3.1 的两个旧方案-B fixture 已
重写并转绿；主 Browser 和两个 publication timeline 失败均在隔离复跑中通过。其余失败集中
于既有 V2 master-data/settings、legacy orchestrator injection 和 extension MVP，并具有明显的
全局 suite-order 污染；不将它们删除、skip 或误记为 Task 3.1 绿色证据。

## Authenticated release canary — 2026-08-27

经用户授权，使用真实 LLM、已认证 XHS Cookie 与启动时 Research Embedding Adapter 完成
脱敏 canary：`run_e3e9ba05c3686f7083eb121669191155`。Run 在一次 retrieval dispatch 和一次
analysis attempt 内到达 `report_ready`（revision `9`）；冻结的三个搜索词与真实 Spider 调用、
报告范围一致。真实数据覆盖为发现 `54`、去重 `48`、相关 `19`、准入 `19`。

analysis attempt `ana_c764b489563e18e5e4d46ea2` 的 shared embedding 和 need/value/message
verifier checkpoint 全部完成：need 为 selected（4 篇/4 位作者），value/message 为 directional
（2 篇/2 位作者、1 篇/1 位作者），三轨均按方案 A 保留不可变分析事实并进入本次 publication。
浏览器确认报告、Trace 分析阶段、attempt、三轨状态及证据引用一致；证据详情为卡片内联展开，
切换时始终只保留一个详情。搜索词输入框保持焦点时也能直接确认并继续。

此前 canary 的一次 `database is locked` 来自旧后端进程；该 Run 已安全收敛为
`recovery_required`，没有自动重放未知 Spider 调用。清理进程内连接并重启同一代码后，新的
完整 canary 连续提交所有 provider outcome、分析 checkpoint 和 publication，未复现锁。
SQLite dispatch/lifecycle 聚焦回归为 `28 passed`，Content Research 当前完整回归为
`630 passed, 7 skipped`。这是已认证正向 canary 的历史证据；最终发布检查仍需以当前代码和
新构建产物的门禁结果为准。

## Final release check and demo — 2026-08-28

最终 prebuild gate 已把 Task 3.1 的核心契约提升为独立阻断项：证据抽取、分析执行、质量包、
governed completion、dispatch/analysis worker、Lite read model、原子持久化、报告执行与
Snapshot replay 的 11 个核心测试文件必须存在；实际后端阻断 lane 为 `642 passed`。Creator
frontend 为 `78 passed`，TypeScript `--noEmit` 通过。

最初受限环境中的 Browser E2E 因端口不可绑定而被 pytest 标成 skip，release 脚本却仍返回
成功。门禁已增加 `CREATOR_BROWSER_E2E_REQUIRED=1`：发布模式下端口或 Chrome 不可用会
直接失败，日常受限环境仍可按原规则 skip。修正后的完整 release gate 在非沙箱环境真实启动
隔离 backend、frontend 和 Chrome，7 条 Creator Browser E2E 全部执行并通过，不再以 skip
充当绿色证据。Release workflow 使用 Node 22，并在 PyInstaller 之前执行 prebuild gate、
上传之前执行 artifact gate。

真实浏览器复核发现并修复了两项会影响发布的缺口：

- 新的 analysis atom 使用提取期 ID，而报告只能引用 manifest-owned admitted claim ID；现在
  通过 track、note、field 与 exact span 唯一匹配回持久化 claim，无法唯一解析时继续 fail closed。
- Spider 返回 `auth_required` 后，小红书登录卡片过去仍显示旧的“已登录”；卡片现在随 Run
  revision/state/reason 重新读取登录状态，并有前端回归覆盖。
- dispatch heartbeat 原先可能在 event loop 中持有 SQLite writer transaction，与同步 workflow
  写入形成自锁并表现为 `database is locked`；lease renewal 现为独立原子同步事务，不再跨
  await 持锁。
- 分析成功后的报告组装失败过去会把已成功 attempt 改写成分析失败；现在分析结果保持不可变，
  Run 进入 report-only recovery。`retry_report` 只重试报告，并复用既有 retrieval、Snapshot、
  embedding 和成功轨道 checkpoint，不重新调用 Spider。
- directional 结论包含反向证据时，Lite report 过去遗漏 counter lineage 并拒绝读取已发布报告；
  当前投影始终保留 counter claim/citation/count，方向性与 contested 语义仍分别呈现。
- Research embedding 失败现在按 attempt 持久化固定白名单错误码、失败计数和耗时，并通过同一
  Trace Snapshot 投放；失败记录不会占用可复用成功 checkpoint 的唯一身份，后续 retry 可保留
  已完成轨道并写入新的成功结果。
- Trace 首次读取、并发读取和异常回滚均只使用 coordinator 持有的一条 query-only SQLite 事务；
  报告 Snapshot 的同步 busy wait 已移出 async event loop，避免竞争写者无法提交而形成 30 秒
  自锁。对应真实 contested Browser 场景从锁超时恢复到 `13.09s`，完整 7 条 Browser gate 连续通过。

使用当前代码重新构建 Apple Silicon `dist/xhs-runtime.zip`（`290,767,224` bytes，SHA-256
`a687e4a4fe4fefb1455d3d7b5c627d6dd39df3e1eba2ee5505d6abe739b28232`）。ZIP 完整性检查通过；
Artifact gate 校验归档结构和无密钥 `config.env`，实际启动冻结 Runtime、读取当前生命周期
authority/Trace/Lite report、停止、再启动并读取同一 SQLite 状态，结果 `2 passed`。

本地验收录屏为 H.264、1726×992、3 分 39 秒，无字幕、无音轨；视频仅保留在验收机器，
不进入 Git 或发布压缩包。整段使用同一真实“夏季凉感T恤”Run
`run_5b48573eca48b36a9c55f7d9c5c07efc`，展示 LLM/Cookie 配置入口、需求与搜索词确认、真实
Spider 检索、运行中多次打开 Trace、报告完成、证据详情内联展开和最终 Trace。该 Run 完成
need/value/message 三轨分析，报告读取成功并包含 `112` 条冻结且可追溯的引用。逐帧中间文件
仅用于本地编码，不进入发布提交。

该真实 canary 的脱敏模型调用证据如下；request ID 为本地不可变 usage request record ID，
session/job ID 均为上述 Run ID，不记录 API Key、Cookie、Prompt 或上游原始响应：

| Provider / model | Request records | Tokens (prompt / completion / total) | 总延迟 | Failure code |
|---|---:|---:|---:|---|
| `openai_compatible` / `gpt-4o-mini` | 6 | 9,240 / 2,305 / 11,545 | 43,909 ms | 无（6 次均 success） |

Request record IDs：`57f68381-a4c6-4f30-bcf1-ed6699f37409`、
`3ac86ccb-9e11-4b11-9885-6cc989210c92`、
`95bf49d2-dbb7-4dbb-987a-6ba8bf6510e4`、
`2c46d77c-9b05-4fa5-a929-b4dd5068f8ed`、
`45ccfee0-3049-4fb1-965c-798d7bdb108b`、
`a21175f2-b604-4957-b111-a215656f4b00`。

技术发布结论：当前代码的 prebuild gate、当前登录态的真实浏览器 canary 和新冻结产物的
artifact gate 均已通过。F003 feature release 分支以通过上述门禁的 commit SHA 为候选点；
正式版本 tag 仅在该功能合入 `master`、从 `master` 再次完成全套门禁后创建，并与
`pyproject.toml` 版本完全一致。当前内部候选二进制为 ad-hoc 签名，面向普通 macOS 用户公开
分发前仍需 Developer ID 签名和 notarization。
