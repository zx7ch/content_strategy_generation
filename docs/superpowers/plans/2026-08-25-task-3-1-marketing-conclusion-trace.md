# Task 3.1 营销结论生成与真实 Trace 纵向切片计划

> **Status:** READY。L2 Contract Pack 已覆盖权威身份、事务边界、竞态、未知外部结果、
> 兼容重试、历史读取、publication integrity 和对应 acceptance evidence。

**Goal:** 基于冻结的真实笔记从三个分析视角生成营销结论；证据支持时至少发布一条可核验
结论，只有方向性线索或正常不足时诚实受限；不把三轨当作输出配额；分析失败或 worker
失联时真实恢复；Trace 与报告只展示唯一状态机和 effective analysis attempt 的事实；所有已完成主流程与
基础能力保持正常。

**Spec:**
[Task 3.1 营销结论生成与真实 Trace 设计](../specs/2026-08-25-task-3-1-marketing-conclusion-trace-design.md)

**Research:**
[营销结论质量调研](../../release/2026-08-25-product-marketing-conclusion-quality-research.md)

## Slice Contract

| Field | Contract |
|---|---|
| Outcome | Creator 从真实 query 完成检索后，以独立 analysis attempt 生成并核验稀疏轨道结论；至少一轨合格即可部分发布，失败可恢复且 Trace 真实。 |
| Contract IDs | `STATE-31-*`, `AUTH-31-*`, `INV-31-*`, `FAIL-31-*`, `REG-31-*`, `ACC-31-*` |
| Transition | Coverage 满足/接受受限结果 → 冻结 Evidence Snapshot + analysis queued → worker claim/running → 三个 planned tracks 技术完成 + 稀疏 primary decisions + verification → partial/limited publication → `report_ready`；任一计划轨技术失败 → `recovery_required`；兼容重试复用成功轨 checkpoint，只重跑失败轨。 |
| Authority / transaction | Run state/revision 唯一；Evidence Snapshot 不可变；同一 analysis unit 一个有效 attempt；旧 attempt fenced；Trace 同一只读事务。 |
| Side effect | Retrieval 允许 Spider；analysis 仅调用 LLM/Embedding，分析重试 Spider delta 必须为零。 |
| Read / UI projection | 复用现有 Creator/Trace 结构，显示五个中文阶段、真实计数、安全错误和 allowed actions。 |
| Failure rows | LLM/协议/Embedding/SQLite/worker crash/lease expiry/迟到 worker/Run A/Trace 乱序/历史读取/secret redaction。 |
| Acceptance RED | `test_creator_generates_traceable_marketing_conclusions_without_recollecting`，Browser → Router → SQLite → worker → recording Spider → deterministic LLM/Embedding → report/Trace。 |
| Deployment safety | 先做不可达增量骨架；首次可达切换已包含恢复、Trace、发布门禁、历史兼容和 E2E；旧新 Run 路径不能长期并存。 |

## 影响清单

### 后端生产代码

- Runtime/bootstrap：`app/content_research/bootstrap.py`、应用 lifespan/依赖注册、配置与健康接口。
- Embedding：新增薄边界 `SentenceTransformerResearchEmbeddingAdapter`；固定 Research 输入格式与 fingerprint，禁止零向量/静默换模降级；现有 `app/services/rag_service.py` 和 Chroma 行为保持不变。
- 生命周期/持久化：`app/content_research/lifecycle/*`、`migrations.py`、`persistence_models.py`、store/repository/coordinator。
- Worker/dispatch：`worker.py`、`async_dispatch.py`，复用现有 lease/heartbeat/recovery scan。
- 分析：`marketing_conclusion_analysis.py`、`marketing_conclusions.py`，新增原子证据、观点归并和 verifier 深模块。
- 服务/发布：`service.py`、`reporting/composer.py`、`faithfulness.py`、`publication_materializer.py`、read models。
- Trace/API：`observation/trace_service.py`、`api_schemas.py`、Router/service API。

### 前端生产代码

- `frontend/src/lib/content-research-api.ts`：Trace revision、active attempt、三轨新状态、安全错误。
- `frontend/src/lib/content-research-trace.ts`：唯一中文阶段映射。
- `frontend/src/app/creator/page.tsx`：复用现有 Trace 卡/弹层/报告卡；revision fencing；连续读取失败提示。

### 测试

- Runtime/Embedding unit/integration。
- Analysis extraction/grouping/verifier deterministic unit tests。
- Attempt/lease/fencing/SQLite fault integration。
- Trace API/read snapshot/security tests。
- Creator component、async ordering 和 Browser-to-owned-stack E2E。
- LLM 配置、小红书登录、Scope/Query、Run A/B、历史报告回归套件。

## Test Baseline 与回归 Lane

开发前生成 impact manifest 并把现有测试分为：

| Lane | 定义 | 处理 |
|---|---|---|
| A | 当前主线：PreResearch、Brief、Scope、真实 query、Coverage、Report | 每个检查点必须通过 |
| B | 基础能力：LLM、XHS 登录、Trace 只读、SQLite、Run/history | 每个检查点必须通过 |
| C | Task 3.1 新合同 | 先写 Acceptance RED，再逐步转绿 |
| D | 无关且基线已失败 | 记录 delta；不得新增或恶化 |
| E | 已废弃旧规格 | 删除或按新规格重写；不得 skip/xfail/保留为已知失败 |

基线输出至少记录测试命令、通过/失败数量、失败名称、Git commit 和时间。Task 3.1
完成条件是 Lane A/B/C 全绿、Lane D 无新增失败、Lane E 在影响范围内为零。

## Checkpoint 3.1-A：不可达基础骨架

**Status:** COMPLETE（2026-08-26）。新 analysis command/worker 仍不可达。

**Outcome:** Runtime 在启动时提供可验证的中文 Research Embedding Adapter，数据库和 repository
能够表达 Evidence Snapshot 与 analysis unit/attempt/facts，但当前新 Run 尚不能创建
analysis attempt，现有主线行为零变化。

**Contracts:** `AUTH-31-02`, `AUTH-31-03`, `AUTH-31-06`, `REG-31-04`–`REG-31-10`。

**Reachability:** 新 command/worker registration 保持关闭；schema 为增量且不重写历史。

### Acceptance RED

- `test_runtime_loads_and_warms_research_embedding_adapter`
- `test_research_embedding_unavailable_is_health_failure_not_zero_vector`
- `test_research_embedding_adapter_validates_count_dimension_and_finite_values`
- `test_analysis_schema_migration_preserves_existing_runs_byte_for_byte`
- `test_analysis_attempt_enforces_one_active_successor_and_immutable_snapshot`
- `test_evidence_snapshot_freezes_source_fields_hashes_and_query_provenance`

### Inner TDD Steps

1. 写 Research adapter 的 Runtime 单例、固定输入格式、预热、返回数量/维度/有限数值和 shutdown RED。
2. 写 additive migration/history byte-equivalence RED。
3. 写完整 Snapshot manifest、重复冻结幂等、原始笔记变化后仍复现同一输入的 RED。
4. 写 analysis unit/attempt identity、successor、lease fencing RED。
5. 实现最小 SentenceTransformer Research adapter、fingerprint、schema、models、repository。
6. 增加 RAG/Chroma 回归测试，证明本切片没有修改其加载、fallback、索引或 fingerprint 行为；无内容调研调用入口。
7. 运行 Lane A/B 和 schema/runtime focused suites。

### Proof Layer

- Runtime integration；
- real SQLite migration/repository；
- existing LLM/XHS/Trace foundation smoke。

### Deployment Checkpoint

仅当新行为不可达、历史数据未变、现有主线 E2E 无回归时可以提交。此检查点不能宣称
营销分析已交付。

## Checkpoint 3.1-B：最小完整、真实、可恢复的营销分析闭环

**Status:** COMPLETE（2026-08-26）。独立 analysis worker、120s lease/40s heartbeat/5s reconciler、公开
`retry_analysis`、成功轨 checkpoint 复用、单事务 SQLite Trace 快照与单调
`trace_revision`、Creator 乱序/连续三次读取失败处理、cancel/publication first-commit-wins、
unknown-result 业务幂等、不兼容/legacy retry 拒绝和 publication integrity successor repair
均已接通。Task 3.1 相关 Python lane `595 passed`，Creator frontend `77 passed` + TypeScript，
Browser-to-owned-stack 主线 `1 passed`，触及文件 Ruff 通过。方案 A 稳定化的报告、Trace、
current final artifact publication binding 和 inline evidence Browser 链路已转绿。

稳定化补充采用方案 A：analysis decision/checkpoint 保持不可变；faithfulness 只在当前
publication revision 写入每轨 disposition。被审计撤回的 selected 轨在 Trace 仍显示分析已
选定，在报告显示 `withheld_by_faithfulness`，不得伪装为 `analysis_unavailable`，也不得触发
analysis retry。对应契约为 `AUTH-31-15`、`FAIL-31-17`、`ACC-31-25`。

**Outcome:** 当前已有 bounded marketing candidate/evaluator 在独立 analysis attempt 下
真实运行；三个 planned tracks 都完成技术执行，至少一轨形成可核验结论即可部分发布；
未输出轨道保留真实覆盖原因；失败/重启真实恢复且复用已成功轨 checkpoint；Trace 和
Creator 同时更新；
未完成分析禁止发布报告。

**Contracts:** `STATE-31-*`, `AUTH-31-*`, `INV-31-*`, `FAIL-31-*`, `REG-31-*`,
`ACC-31-01`–`ACC-31-07`, `ACC-31-09`–`ACC-31-17`, `ACC-31-19`–`ACC-31-24`。

### Acceptance RED

`test_creator_generates_traceable_marketing_conclusions_without_recollecting`

真实 owned stack 证明：

```text
Creator → Scope confirm → worker → recording Spider → frozen Evidence Snapshot
→ analysis attempt → existing marketing analyzer/evaluator → publication → Trace
```

并断言 analysis retry 前后 Spider operation ID 集合完全相同。

当前绿色证明：一次 Scope 确认冻结一个 Evidence Snapshot；三轨各自运行并保存 verifier
checkpoint；分析成功后发布 `partial_verified_report`；Trace 引用 Snapshot、effective
attempt、retrieval unit 与 embedding fingerprint/count，不保存向量；一个轨道技术失败时
Run 进入 recovery，successor 只重跑失败轨且 Spider 调用零增量。

补充绿色证明：SQLite successor 写入故障会原子回滚 lifecycle 与 command ledger；过期
analysis lease 创建唯一 successor 并 fence 旧 worker；取消与发布的两种提交顺序均保留
先提交者；flagged publication 默认停止展示，只有仍有效 verified outputs 才能创建唯一、
可重放的 successor，失效 outputs 明确拒绝且不触发外部调用。

### Acceptance / failure proofs（全部绿色）

- `test_creator_exposes_real_analysis_worker_failure_before_report_composition`
- `test_one_verified_track_publishes_partial_report`
- `test_zero_track_insufficient_publishes_limited_report`
- `test_zero_track_failure_blocks_report_ready`
- `test_one_track_failure_blocks_publication_and_retry_reuses_successful_tracks`
- `test_directional_only_publishes_limited_lead_report`
- `test_expired_analysis_lease_recovers_and_fences_late_worker`
- `test_analysis_retry_reuses_snapshot_with_zero_spider_delta`
- `test_changed_or_deleted_source_does_not_change_analysis_snapshot`
- `test_trace_snapshot_uses_current_attempt_in_one_read_transaction`
- `test_creator_discards_lower_trace_revision`
- `test_creator_stops_claiming_running_after_three_trace_read_failures`
- `test_cancel_and_publication_first_commit_wins`
- `test_unknown_provider_result_is_not_committed_and_retry_is_business_idempotent`
- `test_incompatible_analysis_contract_rejects_retry`
- `test_legacy_run_has_no_effective_attempt_or_retry_action`

### Inner TDD Steps

1. 在 Coverage 满足/受限报告授权后原子冻结 Evidence Snapshot 并创建 analysis attempt。
2. 注册 analysis worker path；复用现有 120s lease、40s heartbeat、5s scan 和 fencing。
3. 把当前 marketing analyzer/evaluator 接入 attempt/checkpoint/fact；每轨 verifier 成功后按
   analysis unit + track + stage + input fingerprint 原子提交不可变 checkpoint；每次外部调用
   在事务外。共享 extraction/grouping 单独 checkpoint，三个轨道分别调用 synthesis/verifier，
   禁止把一次多轨响应拆成部分成功 checkpoint。
4. 让已知失败原子关闭 operation/attempt、写安全错误并进入 `recovery_required`。
5. 增加 retry-analysis command：绑定失败 Run、Snapshot、analysis contract、attempt/revision，
   创建 successor；复用身份完全一致的成功轨 checkpoint，仅调度失败轨。不兼容时拒绝
   普通 retry，本期不提供旧 Snapshot 按新契约重分析。
6. 强化 publication gate：三个 planned tracks 都必须技术完成；至少一轨
   `selected/contested` 才称为部分核验；只有 directional/零轨正常不足发布受限报告；任一
   计划轨技术失败禁止发布；持久化完整 track analysis coverage，并把矛盾作为
   `TRACK_COVERAGE_INCONSISTENT` 后端错误处理。
7. 实现单事务 `load_trace_snapshot`、`trace_revision` 和 effective attempt projection；证据
   正文分页读取，embedding Trace 只保存 fingerprint/计数/checksum/结果引用。
8. 前端只渲染服务端中文阶段/allowed actions；乱序响应丢弃；三次失败显示无法确认。
9. 实现 cancel/publication first-commit-wins、未知 provider 结果丢弃、legacy_v1 只读和
   publication integrity flag/repair successor。
10. 删除 Checkpoint 3.1-B 已替代的空分析成功路径和对应旧测试。
11. 运行 Acceptance RED、fault matrix、Lane A/B 和真实 browser E2E。

### Proof Layer

- Browser-to-owned-stack 主线；
- recording Spider + deterministic LLM；
- fake-clock lease/restart；
- fault-injected SQLite；
- frontend async ordering/intercepted UI；
- API secret redaction。

### Deployment Checkpoint

这是首次可达的新路径，必须已经包含 authority、fencing、crash recovery、真实 Trace、
历史兼容、发布门禁和首次 E2E；不得依赖 3.1-C 修复安全性。

## Checkpoint 3.1-C：原子证据、观点归并、反向证据和独立核验

**Status:** COMPLETE（2026-08-26）。固定 100 篇中文场景包的所有发布指标通过；
`test_creator_reports_supported_contested_and_insufficient_tracks_with_exact_quotes` 在 owned
Browser stack 中证明 contested 正反证据、两轨正常不足、精确引用和 Trace 计数一致。

**Outcome:** 在 3.1-B 已安全的闭环上提升结论质量：句子/短句级精确证据、中文观点
归并、qualifiers、支持/反向证据、三轨独立语义、groundedness verifier 和
`contested` 投影全部可追溯。

**Contracts:** `AUTH-31-02`, `AUTH-31-05`, `INV-31-03`–`INV-31-05`,
`FAIL-31-01`, `FAIL-31-03`–`FAIL-31-08`, `REG-31-*`, `ACC-31-01`–`ACC-31-03`,
`ACC-31-07`–`ACC-31-12`。

### Acceptance RED

`test_creator_reports_supported_contested_and_insufficient_tracks_with_exact_quotes`

使用中文 human-labeled fixture，证明：

- quote 是原文精确子串；
- 同义证据正确归并，场景/人群不兼容证据不合并；
- 一篇笔记多条证据只计算一篇；同一账号多篇只计算一个账号；
- 支持和反向证据均可打开原笔记；
- 标题表达不能升级为产品功效；
- 三个 planned tracks 都得到明确技术结果；至少一轨可得到 `selected/directional/contested`，
  其他轨道可为 `no_publishable_conclusion`；正常 Run 不出现 `not_evaluated`，任一
  `analysis_failed` 都阻止 publication；
- directional-only 只投影“样本线索”受限报告，不显示“已核验结论”；
- 未达到 contested 门槛的有效反向证据仍在限制区展示；
- verifier 失败不得发布。

### Inner TDD Steps

1. 建立中文 human-labeled evaluation fixture 和 exact-span RED。
2. 实现 strict structured extraction、quote/entity/run/snapshot deterministic validation。
3. 校准并版本化中文 similarity threshold；实现兼容分区 + cosine + 层次归并。
4. 保存 cluster membership、support/counter IDs 和 qualifiers；计数只从关系计算。
5. 重写三轨 bounded synthesis contract；LLM 不提供计数或发布状态。
6. 增加独立 verifier 和 `contested/analysis_failed` durable/read/API/UI union。
7. 更新 report faithfulness、publication、Trace counts 和证据详情。
8. 删除 Checkpoint 3.1-C 已替代的 coarse whole-line evidence、旧 prompt/fixtures。
9. 运行分析 stage metrics、Acceptance RED、Lane A/B 和主线 E2E。

### Evaluation Metrics

首版不建设 metrics 平台。使用 5 类固定中文场景模板、共 100 篇笔记的版本化 fixture，
输出一份 JSON/Markdown 结果。发布门槛为：

| 指标 | MVP 门槛 |
|---|---:|
| 后端接纳的编造 quote、错误 Run/Snapshot/note 引用 | 0 |
| exact-span precision / recall | `>= 95% / >= 75%` |
| 可见结论 track mapping macro-F1 | `>= 80%` |
| cluster pairwise precision / recall | `>= 85% / >= 70%` |
| contradiction precision / recall | `>= 85% / >= 75%` |
| citation correctness / completeness | `100% / 100%` |
| unsupported causality、错误 `report_ready`、retry Spider delta、secret leakage | 0 |
| deterministic decision policy exact match | `100%` |
| gold fixture 存在可发布结论时，至少一轨输出成功率 | `>= 80%` |

未输出某轨不计为失败；把可见结论放错轨仍计入 track mapping。任何零容忍项失败都阻止
3.1-C；一般质量指标未达线时保留安全的 3.1-B，不启用未校准能力。

### Deployment Checkpoint

3.1-C 只增加质量和用户可见的合格结果，不得改变 3.1-B 的 attempt/recovery/Trace
authority。评测阈值未通过时保持 3.1-B，不允许以未校准聚类替换安全闭环。

## Checkpoint 3.1-D：旧规格归零与最终发布门禁

**Status:** COMPLETE（2026-08-27）。旧的无 manifest 新-Run 分析实现已删除；当前 final
artifact 成为 lifecycle/Trace 的精确 publication binding；方案 A 冲突旧 fixture 已按当前合同
重写，没有新增 skip/xfail。authenticated LLM/XHS/Research Embedding canary 已通过。

**Outcome:** 重新运行每个前置检查点自己拥有的证据，删除剩余旧新 Run 行为和测试，
完成 authenticated LLM/XHS canary。此检查点是验证与删除，不得首次修复生命周期。

### 删除清单

- `template_only`/缺失 policy 令新 Run 静默跳过营销分析的路径；
- 空 marketing conclusions 仍发布 verified complete 的实现/fixture；
- Trace 从 Brief/current step/旧或 latest checkpoint fallback 的当前状态推断；
- 浏览器内部阶段名和客户端自造 recovery；
- 与独立 analysis attempt、`contested`、`analysis_failed` 冲突的旧测试。

历史 decoder/read-only fixtures 保留并证明零 mutation。

### Verification

1. 重跑 3.1-B、3.1-C Browser Acceptance；失败返回所属 checkpoint 修复。
2. 重跑 worker/lease/SQLite/Trace/security fault suites。
3. 重跑 Lane A/B/C；Lane D 比较 baseline delta；扫描 Lane E 为零。
4. 运行前端完整 test/build 和后端完整 pytest，当前有效范围无新增失败。
5. 使用真实 LLM、已认证 XHS、启动时 Research Embedding Adapter 运行一条脱敏 canary：

```text
frozen query == Spider query == Trace query
retrieval attempt != analysis attempt
analysis retry Spider delta == 0
at least one published track has a governed verified conclusion, or zero-track insufficiency is explicit
every planned track has one truthful execution/decision/publication-role coverage record
all visible conclusions resolve to exact quotes
Trace run/attempt/revision matches authoritative state
LLM/XHS/Research Embedding status is healthy and secrets are redacted
historical Run remains readable and unchanged
```

### Authenticated canary evidence（2026-08-27）

- Run：`run_e3e9ba05c3686f7083eb121669191155`，最终状态 `report_ready`，revision `9`；
- 冻结词、Spider 实际调用和报告范围一致：`T恤`、`T恤 凉感`、`T恤 夏季`；
- 真实 XHS：发现 `54`、去重 `48`、相关 `19`、准入 `19`，dispatch attempt `1` 成功；
- analysis attempt `ana_c764b489563e18e5e4d46ea2` 首次成功；Research Embedding 与
  need/value/message 三轨 verifier 均形成不可变 completed checkpoint；
- Trace 显示 need `selected`（4 篇/4 位作者）、value/message `directional`
  （2 篇/2 位作者、1 篇/1 位作者），三轨 publication role 均真实可见；
- 报告证据详情在当前卡片内展开，切换引用后 DOM 中始终只有一个 expanded button 和一个
  detail region；光标留在搜索词输入框时确认动作仍可提交；
- 后端生命周期、报告页面和 Trace Snapshot 一致，无 lifecycle error、无 successor、无重试
  Spider、无凭据或模型密钥暴露。

## 回归门禁

每个 checkpoint 必须重新证明：

- PreResearch、Brief、Scope 和确认搜索词交互不变；
- A-only 硬性准入、B/C 可选和后端 query compiler 不变；
- 用户预览 query 与实际 Spider query 一致；
- duplicate confirm 不重复 dispatch；
- Run A/Run B、刷新/重启和历史读取不回归；
- LLM 配置/连接和 XHS Cookie/QR 登录正常且脱敏；
- retrieval lease/heartbeat 和 SQLite 协调无新增竞争；
- Trace 读取无写入且文件句柄有界；
- 当前契约测试无新增失败，旧规格测试不留作已知失败。

## 估算

| 范围 | 预计改动 |
|---|---:|
| 生产代码 | 1,800–2,800 行 |
| 测试与 fixtures | 1,500–2,400 行 |
| 规格、迁移与证据 | 300–600 行 |
| 总计 | 3,600–5,800 行 |
| 工期 | 9–14 个有效工程日 |

## Plan Readiness

**READY。** 3.1-A 只建立不可达增量骨架；3.1-B 是第一个完整可达且安全的纵向闭环，
并在首次可达时同时交付 fencing、兼容恢复、成功轨复用、真实 Trace、发布门禁、历史兼容
和 publication integrity；3.1-C 只增加分析质量；3.1-D 只删除旧规格和复核证据。
