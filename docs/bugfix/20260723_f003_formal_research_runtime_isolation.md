# F003 正式调研运行时隔离修复

**状态**：进行中（Gate 2 阻塞项）
**发现日期**：2026-07-23
**影响范围**：F003 正式调研运行时；Lite Gate 2 真实 Product Marketing 验收。

## 症状

真实小红书采集运行时，Creator 的“开始正式调研”请求长时间未返回；同时 Trace 查询超时，前端显示 `Failed to fetch`，无法提供实时进度或恢复原因。

## 根因

1. `start_formal_research` 在 workflow action 的 HTTP 请求中直接等待整个正式采集完成，未将长任务交给持久化后台 worker。
2. Content Research、workflow 与 usage 的普通连接每次初始化都执行 `PRAGMA journal_mode=WAL`。该 pragma 是数据库级模式切换，不属于只读 Trace 所需操作；它在并发 checkpoint 写入时造成不必要的锁等待。
3. 早期 bundled spider 调用未设超时，扩大了上述同步等待窗口。该项已先行修复为 15 秒上限，但不能替代运行时隔离。

## 修复合同

- 启动接口仅持久化并调度任务，快速返回 `running`。
- 后台 worker 在短事务边界执行采集，写入 checkpoint/terminal state；重启后按已有 checkpoint 恢复，不重复已完成外部调用。
- Trace 仅通过只读连接读取持久化状态；不迁移 schema、不设置 WAL、不写入恢复信息，也不等待 provider。
- timeout/transient 与 auth failure 均以可观察的结构化状态落盘；auth failure 不发布报告。

## 验证要求

- 延迟 provider 运行时，Trace 在短超时内返回。
- 真实 Product Marketing 成功报告有 citation；Cookie 失效显示恢复卡且无报告；reload/retry 不重复完成的 provider 操作。
- 详见 `instructions/F003-G2-RUNTIME-ASYNC_validation_contract.md`。

## 2026-07-23 真实 Provider 验证

- 外网可达 bundled XHS provider；配置文件已被应用读取（Cookie 非空并可解析为 20 个键值对），但当前真实 smoke 明确返回“无登录信息，或登录信息为空”。该 Cookie 缺少登录会话字段（如 `web_session`），因此不是完整的已认证 `xiaohongshu.com` 请求 Cookie；这不是本地 DNS、fixture 或模拟结果。
- 本次验证发现 localized auth 文案此前会消耗 transient retry 预算。`XHSSpiderClient` 现将“登录已过期”等中文登录态错误归类为不可重试的 `auth_required`，让 workflow 直接呈现更新 Cookie 的恢复路径；对应单元测试覆盖一次调用、零次退避。
- 此记录仅构成 Gate 2 的真实 auth-failure 证据的一部分；不替代所需的真实成功报告、citation、浏览器截图或 reload/retry 证据。粘贴完整登录态 Cookie 后必须重新执行完整 Gate 2 canary。

## 2026-07-24 Gate 2 运行记录与新发现

- 已修复两项 Creator 真实运行时问题：空闲 Content Research dispatcher 每 100ms 获取 SQLite 写锁，造成预检索完成后无法及时保存 Brief/返回；以及正式调研异步完成后 Creator 只读取一次 `queued` Trace，报告发布后不会自动回显。前者改为空队列只读探测，后者只轮询同一 workflow 并在 publication 短暂滞后时有限重读，均不重派 provider 操作。
- 当前完整浏览器 run `run_2b25a8aff2ca4ebbb97302220da1b176` 已使用更新后的 Cookie 并完成到 `evidence_only_report`，但 `citation_total=0`。根因位于 **discover/collect page**，不是 admission 或报告：两条 Product Marketing 查询均持久化为 `status=failed`、`actual_count=0`、`completeness=unavailable`；因此 selection 为 `insufficient_evidence`，无详情 packet、无 facts、无 admission decision，最终没有可冻结 citation。
- 当前 safe Trace 能展示“0 引用/0 结论”和各阶段状态，但不能展示上述 provider 失败的具体原因：`collect_page` checkpoint 缺少 `failure_reason`/provider message，operation checkpoint 还残留一个 `running` 标记。因此 Trace **不能真实展现错误根因**，只展现结果；必须补齐 provider failure 的结构化错误码、脱敏原因、checkpoint terminal state 与恢复动作后，才可作为 Gate 2 failure/recovery 证据。

## Gate 2 修复方案：可追溯的 Provider Operation Runtime

**决定**：Trace 可以在读时聚合展示，但绝不能在读时推测运行历史。外部调用的运行事实必须在行为发生时持久化；Trace 仅投影这些事实，永不补写、猜测或改变恢复状态。

- 将 `ProviderOperationOutcome` 作为共享运行时概念，而非 Lite/XHS 特有对象。它可先由增强后的 operation checkpoint 落地，不强制新增物理表；但必须拥有稳定 `operation_fingerprint`、`workflow_run_id`、`subagent_task_id`、provider、operation kind、脱敏请求摘要、开始/结束时间、终态、错误码、脱敏原因、重试信息、可重试性和唯一 recovery action。
- 每次 provider 操作遵循不可跳过的状态机：`planned -> started -> {succeeded|failed|timed_out|auth_required|rate_limited|cancelled|outcome_unknown}`。所有 `started` 操作必须在 `finally` 或恢复器中获得唯一终态；进程中断后超过 lease 的操作转为 `outcome_unknown`，在未确认幂等性前不得自动重放。
- adapter 必须保留 typed failure code 与可安全显示的脱敏原因；directional pipeline 将 outcome ID/终态写入 `collect_page`、detail/comment checkpoint。不得再只写 `failed/unavailable` 而丢弃失败分类。
- Trace 读取 operation outcome、checkpoint、workflow 和 usage 的持久化事实，向 Creator 分别投影用户可理解的原因/恢复动作与受控运维诊断；报告只消费 evidence/admission，不消费内部错误详情。

**Gate 2 验收补充**：针对 timeout、auth、rate-limit、parser failure 和进程中断各证明一个 terminal outcome；浏览器 Trace 显示最后失败操作、已完成阶段和恢复动作；刷新/恢复不重复 `succeeded` 操作，也不盲目重放 `outcome_unknown` 操作。

## 2026-07-25: Provider outcome Trace repair

### Implemented

- `discover`、`detail` 与 `comments` 都在调用 provider 前写入 `running` checkpoint；拿到 typed `SourceOperationResult` 后写入唯一 terminal checkpoint。异常中断则写入 `outcome_unknown`，恢复前必须确认外部调用结果。
- detail router 不再把 provider failure 压缩为 `None`；失败码会穿透到 Directional Pipeline。评论分页同样保存 failure code/retryability，并按 result 关闭 operation。
- XHS adapter 将 timeout 与 `transient_error` 分开：search/detail/comment 均有 15 秒上限；timeout、auth、rate-limit、parser failure 都保留各自的 typed code 与 recovery action。
- `/trace` 只从 operation checkpoint 投影 `operation/status/timing/failure code/retryability/recovery action`，不投影 request、cookie、token 或原始响应。Creator Trace 在存在终态 provider failure 时显示最后失败操作和安全恢复动作。

### Verification

- `42 passed`：XHS adapter、Trace service 与 directional pipeline 定向回归，覆盖 discover auth failure、detail parser failure、comments unavailable、timeout，以及中断后 `outcome_unknown` 不自动重放。
- 前端 API tests `28 passed`，`npm run build` passed；构建仅保留既有 hook dependency warnings。

### Remaining Gate 2 evidence

- Gate 2 仍为 **IP**：还需要在真实 XHS canary 中分别采集成功（含 citation）与 auth/timeout recovery 的 browser screenshot、checkpoint 和 reload/retry 去重证据。当前实现已能记录并投影这些事实，但不能把已有 0-citation 外部运行追溯成新的成功样本。
- 2026-07-25 的真实 smoke（受控外网）返回 `Auth error: 登录已过期`，且在首次尝试停止，没有进入 transient retry。故当前 `.env` 中的 Cookie 对 XHS 已无有效登录态；这是外部凭据状态，不是 selection/admission rule、Trace projection 或本地网络问题。新的有效 Cookie 是继续真实成功 canary 的必要前置条件。

## 2026-07-25: Lite adopts the formal async runtime contract

**Decision**: Lite does not use `to_thread` as a compatibility path. The same formal async runtime contract is adopted now: `aiosqlite` UoW/repository owns dispatch/lease writes; confirmation commits a complete executable run plus durable outbox before signalling the worker; the worker waits for an in-process event and uses low-frequency persisted-job recovery scanning after missed events/restarts.

- The confirmation commit establishes only the initial durable truth (`brief_confirm`, `plan_build`, queued `formal_research`, child tasks, queued job). Each actual provider/stage behavior later writes its own short committed checkpoint/outcome; Trace reads both layers and never infers history.
- Claim/renew/complete/fail use `lease_owner` + `lease_token`; a stale worker cannot terminalize a replacement lease. Provider calls remain outside transactions. Recovery-critical `started` and terminal operation facts are never time-batched.
- Completed outbox archival/TTL is deferred to formal release work, but terminal jobs remain retained for Gate 2 audit. Postgres is an implementation replacement behind this runtime contract, not a Lite-specific redesign.
- Implementation status (2026-07-25): confirmation now commits workflow transitions/child tasks together with the Brief, Plan, Policy Snapshot, Directions, Content Research tasks, and queued outbox job on one `aiosqlite` transaction; event notification occurs only after that commit. Async queue/lease fencing, owner heartbeat, and event wake-up are in place. Directional execution now uses an async persistence session: it loads and flushes via `aiosqlite`, and flushes provider-operation `started`/terminal facts at the call boundary. The legacy synchronous store is retained only for non-worker read models and direct legacy test fixtures, not the async worker/provider path.
- Old-runtime removal: the synchronous store dispatch APIs (`enqueue/lease/complete_formal_research_dispatch`), the high-frequency poll compatibility parameter, confirmation fallback, and their obsolete fake-runtime interfaces have been removed. There is now no supported synchronous queue/lease path to re-enable accidentally.
- 2026-07-25 real-provider evidence: after the Cookie refresh, the controlled `test_real_spider_smoke.py` passed. This proves the current Cookie can make an authenticated read-only provider request; it is not yet the required citation-bearing Creator canary.
- 2026-07-25 detail canary root cause: three independent real detail probes (two distinct search queries) returned provider code `-1` while discovery succeeded. A local URL parser bug that truncated Base64-padded `xsec_token` values was fixed, but the provider still rejects the complete token. HAR comparison then fixed the root-cause boundary: the browser completed `POST /api/sns/web/v1/feed` with the same JSON body for which the bundled spider receives HTTP 406 / provider code `-1`. The divergence is the request-security envelope, not Cookie validity, note ID, `xsec_token`, or request body: the static spider signer and fixed browser fingerprint do not reproduce the browser's current `x-s-common` and contextual headers. The provider does not return an internal subcode, so the exact rejected field is unproven. The adapter emits `provider_access_rejected` as non-retryable and Trace directs recovery to a compatible browser-session detail provider rather than repeatedly replacing a working Cookie. The full Creator canary therefore correctly publishes an `evidence_only_report` with zero citations; Gate 2 success remains blocked on that provider integration and a fresh citation-bearing canary.

## 2026-07-25: Browser-session detail bridge

- An opt-in local `XHS_BROWSER_CDP_URL` detail transport is now available. It creates a disposable Chrome target, enables network observation before navigation, and reads the browser page's own successful `/api/sns/web/v1/feed` response. It never reconstructs `x-s`/`x-s-common`, reads Cookie storage, persists HAR data, or exposes raw provider response data to Trace/checkpoints.
- The bridge is intentionally local-only. Chrome's current security policy does not accept traditional remote-debugging switches for its default personal data directory; the user must explicitly authorize/expose a local browser debugging session before setting the endpoint. This is a security boundary, not a missing Cookie or an application fallback.
- The bridge has deterministic fake-CDP coverage only at this point. Gate 2 remains **IP** until an explicitly authorized browser session produces a real detail success, citation-bearing Creator run, and reload/retry audit evidence.
