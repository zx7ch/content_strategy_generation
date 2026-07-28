# F003 Content Research Lite 交付方案

**状态**：Proposed — 待 Gate 0 重新冻结
**日期**：2026-07-22
**范围**：Creator Workbench 内的可交付内容调研闭环
**关联**：[PRD](./F003_content_research_prd.md)、[正式重构计划](./F003_content_research_development_plan.md)、Lite 页面 Mock（待按本版更新为 v5）

**本版变更**：①恢复呈现从"报告状态"改为 workflow 恢复投影（非报告）；②全部持久化词汇并入正式方案共享枚举；③新增 §4.0 共享合同修订（template publication mode）；④status strip 互斥计数口径冻结；⑤新增 §9 子集映射表。旧版中"固定三方向、结构化 unavailable/insufficient、lead 类型化、来源跳转三态、评论字段排除"等已定内容全部保留。

---

## 1. 交付决定

F003 Lite 不新建页面、不重建 UI、不另起一套数据模型。它是正式 F003 在 Creator Workbench 中首批发布的**稳定能力子集**，不是临时 MVP、灰度试验产品或未来需要替换的实现：所有领域对象、API、read model、错误语义与 UI 组件都必须由正式版本继续使用和扩展。发布前可以隔离尚未验收的代码；发布后，Lite 的固定方向不会由运行时开关、canary 结果或某次样本数量动态增减。

**词汇纪律（子集成立的前提）**：Lite 不为任何持久化或跨服务共享的枚举引入 Lite-local 取值。publication 状态、section kind、reason code、来源跳转状态、claim type、workflow 状态一律使用正式方案共享 schema 的既有取值；确需新增的值（见 §4.0、§4.2.2）以共享 schema 变更提交，并在同一个原子变更中交付（§5.0）。

Lite 的唯一用户结果是：

```text
输入调研对象
  -> 确认研究范围
  -> 真实采集有限样本
  -> 输出可追溯的调研结果，或诚实说明证据不足/失败及恢复动作
```

### 1.1 Lite 完成定义

仅当以下条件全部成立，才允许宣布 Lite 已交付：

1. 用户可在 Creator Workbench 从输入到报告完成一次真实调研，无需改 URL、请求体、数据库或本地文件。
2. 三个开放方向都由真实来源链路产出结果，或显式返回 `insufficient_evidence` / `unavailable`；不允许 mock 或静默降级为编造结论。
3. 每一条正式发现均可从报告打开到引用、来源链接（如存在）、原文片段、样本范围和采集时间。
4. Cookie 失效、限流/网络错误、来源为空、字段缺失、样本不足和刷新恢复均有真实 UI 状态与可执行/不可执行的明确动作；可恢复中断呈现为 workflow 恢复卡，不伪装为报告。
5. 同一 run 的刷新、重复点击和恢复不重复外部采集、不写第二份最终报告、不改变既有报告内容。
6. 三个真实案例、一个证据不足案例、一个可恢复失败案例通过浏览器验收并留存产物。

---

## 2. 冻结范围

### 2.1 Lite 开放能力

| 能力 | Lite 合同 | 用户可见结果 |
| --- | --- | --- |
| 输入与 Brief | 品牌、品类或场景词；主体、竞品词与方向确认 | 对话中的 Brief 确认卡 |
| 竞品候选 | 有直接引文、来源与最小独立样本的候选品牌 | "本次样本中出现的竞品候选"，不宣称市场地位 |
| 产品表达 | 基于笔记正文/标题的价值表达与使用场景观察 | 仅描述内容如何表达，不声称真实产品效果 |
| 内容样本 | 可观察的标题、正文、内容形式样本 | 仅描述样本形式，不推导内容效果或转化 |
| 报告与证据 | 正式 report artifact、citation 与共享 evidence drawer/read model | 一条最终报告消息，按需展开证据 |
| 恢复 | 已完成采集不重复；可恢复错误提供继续动作 | workflow 恢复卡："已完成什么 / 未完成什么 / 下一步" |

**正式子集规则**：Lite 上线后，`product_marketing`、`competitor_discovery` 与 `content_performance` 是正式、固定的产品方向，始终在 Brief 中可见；Lite 不是按运行时 canary 动态缩减的 MVP。真实 canary 是每次发布前的验收门槛，不是用户界面的显隐条件。某次运行的来源、认证或字段不可用时，该方向必须在同一份 `partial_verified_report` 中以结构化 `unavailable` / `insufficient_evidence` 原因与恢复动作呈现，不能从用户输入或报告中静默消失。

**方向集合的实现方式**：Lite 的三方向集合是共享 policy 中的发布级配置 `direction_set_v1 = [product_marketing, competitor_discovery, content_performance]`，随 `RunPolicySnapshot` 冻结进每个 run；不是硬编码、不是运行时开关。正式版发布时以新配置版本扩展方向集合，只影响新 run。

### 2.2 Lite 明确不做

- 评论洞察、UGC 社群、关键词增长、品牌活动等 `direction_set_v1` 之外的方向。
- "选择品牌后自动二次深研"和"选择重点内容"的多阶段链路；用户可用该对象重新发起一轮 Lite 调研。
- 跨方向 AggregateClaim 的**渲染**、冲突治理呈现、行动假设、自动业务建议（治理对象在正式链路中既已产生也不受影响，Lite 仅不渲染，见 §9）。
- 语义审计 LLM、报告重写、复杂成本/Token 面板和全量 Trace 日志的**展示**（底层对象与脱敏投影保持正式实现）。
- 新 UI 框架、独立路由、独立 Lite 数据库、旧新双写、兼容 adapter。
- 未有真实后端合同的按钮、空卡片、数字、进度或"即将开放"入口。

不做不表示删除。上述既有模块继续留在正式 F003 的隔离路径中；Lite 是正式 F003 的稳定子集，所有 Lite 领域对象、read model、错误语义与 UI 组件必须可被后续正式版本直接复用，而非一次性 MVP 代码。

---

## 3. 可信度与恢复的最小保留集

Lite 简化的是方向数量、报告深度和用户界面，不简化以下不可逆正确性边界。每一行都是正式方案的同一对象，不存在 Lite 副本。

| 必须保留 | Lite 要求 | 正式方案对象 | 不满足时的行为 |
| --- | --- | --- | --- |
| Frozen run scope | `workflow_run_id`、确认后的 Brief、方向、采样上限、policy/capability snapshot 在开始前冻结 | `RunPolicySnapshot` + capability snapshot | 不启动正式采集 |
| 原始来源谱系 | canonical source、来源 URL/稳定 ID、原文片段、采集时间、payload/field hash | `CanonicalSource` / `DirectionSourceProjection` | 不可成为正式发现 |
| 调用检查点 | 搜索/详情调用前记录 operation fingerprint；成功结果先落盘再 completed；in-flight 中断按 outcome_unknown 处理 | `StageCheckpoint`（CL-01） | 中断标为 outcome unknown，禁止自动重复调用 |
| 幂等报告 | 同一 input fingerprint 只 materialize 一份最终 artifact/message | `ReportPublication` materializer（R1） | 重放返回原报告 |
| 准入门槛 | 只由 admitted claim 填充报告；样本不足只能是 lead 或不足状态 | `ClaimAdmissionDecision` / `WeakSignal` | 不生成自由文本结论 |
| 失败语义 | auth、rate limit、temporary error、empty、missing fields、insufficient 分开保存 | 共享 reason code（D3-FDN-1） | UI 显示对应原因和动作 |
| 报告可回溯 | 每项展示冻结 citation group、范围与限制 | `citation_groups`（CL-06/R2） | citation 不完整时降级为 evidence_only_report，不发布完整结果 |

Lite 可舍弃的是：七方向统一覆盖、跨方向治理呈现、复杂版本矩阵、LLM 自由总结/重写、完整审计可视化与扩展性预留。它们不在 Lite 主路径执行，也不阻塞 Lite 发布。

---

## 4. 数据传递与 UI 合同

### 4.0 共享合同修订：template publication mode（随本文档一并冻结）

**问题**：正式发布矩阵要求 `complete_verified_report` 必须通过 `core_conclusions`、`main_findings`、`limitations_scope`，且全部必需章节经 deterministic + semantic 两层审计（R1/R3）。Lite 禁止 LLM 自由总结与重写（§2.2），若不修订，Lite 报告将永远只能以 partial/evidence_only 发布——把语义完整的成果错误地标为降级。

**修订（对正式方案 R1/R3 的最小扩集，非 Lite-local 特例）**：

1. run policy 新增冻结项 `report_compose_mode ∈ {prose, template_only}`；正式版默认 `prose`，行为不变。Lite 发布的 run 冻结为 `template_only`。
2. `template_only` 模式下，发布必需章节为 `main_findings + limitations_scope`；`weak_signals` 为有条件章节；不产生 `core_conclusions`、`cross_direction_tensions`、`next_steps`（无自由叙述、不渲染 aggregate）。
3. 该模式下的章节内容仅允许：结构化 card refs、冻结 citation groups、受限模板文本（§4.2.1 状态条）。发布门槛仅为 deterministic audit（对象身份、计数、citation anchor、范围一致性）；semantic audit 定义为仅适用于 prose 模式，在 template_only 下标记 `not_applicable`，不得伪造通过。
4. `compose_mode` 记录在 `ReportPublication` 上。历史 Lite artifact 携带 `template_only`；正式 UI 以共享渲染器原生渲染（卡片、模板块、证据交互均为正式组件），无需兼容层。
5. 修订以共享 schema + Composer + FaithfulnessEvaluator + fixture 的原子变更交付（§5.2）。

### 4.1 单向数据流与呈现路由

```text
Brief confirmation
  -> run（冻结 scope：direction_set_v1、采样上限、policy/capability snapshot）
  -> Source packets + StageCheckpoint（内部持久化）
  -> admitted claims / WeakSignal / insufficient reasons
  -> GovernedResearchSnapshot（唯一事实源）
  -> 呈现路由（二选一，互不冒充）：
     a) workflow terminal success
        -> 发布三档之一（template_only 模式）
        -> report read model -> 一条 artifact_result 消息
     b) workflow 非 terminal 且原因可恢复
        -> workflow 恢复投影（checkpoint_summary）
        -> run 状态卡（publication: none，不产生报告 artifact）
```

前端不得读取或拼接 raw packet、typed record、旧 `EvidenceBundle`、多个方向 API 的结果；也不得计算样本数、可信状态、优先级或恢复位置。

### 4.2 LiteReportReadModel

Lite read model 是 `GovernedResearchSnapshot` / 正式 report read model 的**窄投影**，不创建第二套事实来源。所有持久化枚举取共享 schema 值：

```text
run: run_id, workflow_execution_state, subject, frozen_scope, collected_at
publication: state ∈ {complete_verified_report, partial_verified_report,
                      evidence_only_report} | none,
             artifact_id?, publication_reason, report_version,
             compose_mode = template_only
sections（共享 section_kind）:
  main_findings[]        # 仅卡片；视觉分组由 card_kind 驱动
  weak_signals[]         # “线索/初步信号”在此，仅为展示标签
  limitations_scope[]
finding_card: statement, claim_type, card_kind ∈ {finding, observation},
              direction, sample_summary, scope, citation_group_ids
weak_signal_display: statement, direction, sample_summary,
                     qualification_reason, citation_group_ids
status_strip（受限模板）: completed_direction_count, admitted_finding_count,
                         observation_count, lead_count
citations: display_index（冻结，前端不重编号）, quote,
           field_path ∈ {title, content_text}, source_url?,
           source_collected_at, navigation_state, navigation_reason?
run_direction_states[]: direction,
                        state ∈ {completed, insufficient_evidence, unavailable},
                        reason_code?, recovery_action?
recovery_projection: reason_code, completed_stages[], next_action?, actionability
release: direction_set_version
```

前端只根据 `workflow_execution_state`、`publication.state`、`recovery.reason_code` 和发布级 `direction_set` 决定展示；三个正式方向不得因单次运行失败而消失。`run_direction_states` 仅表达本次运行状态并驱动方向状态与恢复动作；它不改变 Brief 的方向集合。

#### 4.2.1 受限状态条与 lead 的冻结规则

`status_strip` 是合法的受限模板，不是报告结论或自由摘要。它只能直接投影冻结计数：完成方向数、已验证发现数、样本观察数、线索数；不得包含推荐、趋势、优先级、因果、比较或任何由 LLM/前端推导出的词句。

**互斥计数口径（随 policy version 冻结）**：`claim_type → card_kind` 映射表冻结在共享 policy 中（`direction_set_v1` 下：`product_marketing`、`competitor_discovery` 的 admitted claim 为 `finding`；`content_performance` 的 observation 类 claim 为 `observation`）。`admitted_finding_count` 仅计 `card_kind=finding` 的 admitted claim；`observation_count` 仅计 `card_kind=observation`；`lead_count` 仅计 weak_signal_display。每条 admitted claim 恰好落入一个桶；计数只能由后端投影产生。`evidence_only_report` 只显示"已保存依据数"和"无法形成正式报告的原因"，不显示发现、观察或线索计数。

`lead` 不新增与 `weak_signals` 平行的容器。它是 weak-signal display 的展示标签，必须包含：直接证据引用与方向归属、样本范围、`qualification_reason`（如 `minimum_independent_sources_not_met`）、明确的"仅供参考，不构成结论"展示状态。lead 不得进入 `main_findings`、`admitted_finding_count`、行动建议或跨方向推导。

#### 4.2.2 引用来源的跳转合同

`source_url` 可选不等于"来源身份可选"。每条 citation 无论是否可跳转，均必须有 quote、field path、source collected at 和安全来源标识。`navigation_state` 固定为：

| 值 | 条件 | 用户文案 |
| --- | --- | --- |
| `available` | 已有可安全打开的来源 URL | `查看原笔记` |
| `missing_source_url` | adapter/来源未提供 URL，但已保存引用材料 | `未保存来源链接；可查看原文片段与采集时间` |
| `navigation_unavailable` | URL 已记录，但因认证、平台限制或安全策略无法在当前环境打开 | `来源链接当前不可打开；可查看原文片段与采集时间` |

`navigation_state` 枚举及其文案提交进**共享 report read model**（`/report` 的消费者同样使用），不是 Lite-local 字段；正式 U1 的 evidence drawer 直接继承。`navigation_unavailable` 绝不能表示"前端尚未接入跳转"。后者是未完成实现，不是可发布的用户状态；Gate 4 的证据交互原子验收不通过时，该方向不得开放。

Lite 的证据字段只允许 `content_text` 与 `title`（投影过滤）。评论保留在原始 provider payload 与正式链路的 comment packet 中，但 Lite report、citation、evidence drawer 和 source detail 不展示评论正文或评论统计，以免暗示评论洞察已开放。

### 4.3 报告三态与 workflow 恢复呈现

| 呈现 | 性质 | 页面内容 | 禁止行为 |
| --- | --- | --- | --- |
| `complete_verified_report`（template_only） | 报告发布 | 已验证发现卡、observation 卡、lead（如有）、范围限制、citation、状态条、三方向运行状态 | 不展示未开放方向、行动建议或自由叙述 |
| `partial_verified_report` | 报告发布 | 已验证卡 + 每个受影响方向的结构化 `insufficient_evidence`/`unavailable` 原因与恢复动作 | 不把缺失方向以空结论补齐，也不移除该方向 |
| `evidence_only_report` | 报告发布 | 已保存依据、无法形成正式报告的原因、方向状态 | 不写叙述性结论，不把证据伪装为发现 |
| workflow 恢复呈现 | **非报告**（run 状态卡，`publication: none`） | 已保存阶段、失败原因（如 `auth_expired`）、唯一恢复动作 | 不 materialize 报告 artifact、不标"研究完成"、不创建第二个 run |

`evidence_only_report` 是正式的安全回退状态：当引用/报告材料无法完整投影时，只显示已保存依据和限制，不写叙述性结论。

---

## 5. 无返工实施规则

### 5.0 共享枚举纪律

1. 任何持久化或跨服务共享的枚举（publication state、section_kind、reason code、navigation_state、claim_type、workflow state、compose_mode）只允许取共享 schema 的既有值。
2. 确需新增的值（如 `template_only`、跳转三态）必须作为共享 schema 变更，与其唯一消费者在同一原子变更中交付（§5.2）；禁止 Lite-local 定义后"以后再对齐"。
3. 展示层文案标签（如"线索""样本观察"分组标题）可以 Lite-local，但必须与共享枚举一一映射，且只存在于 presentation 层。

### 5.1 先加后切、验收后露出

1. 不删除现有 UI 或正式 F003 入口来"腾位置"。先新增 Lite read-model projection；如需开关，只能使用发布前隔离开关。
2. 每个 Lite 方向先在发布 flag 关闭状态完成 API、真实 canary、浏览器验收；flag 只用于发布前隔离，不能成为正式产品的长期方向开关。
3. 仅在全部验收通过后，才发布该方向的正式实现；发布后它是 Lite 的固定产品方向，而非临时开关。
4. 任一运行中的方向失败或 capability 不满足时，保留该方向在 Brief 中的正式入口，并在报告中显示结构化 `unavailable` / `insufficient_evidence` 状态与恢复动作；不允许静默移除、空白 section 或指向旧链路。
5. 已发布的 report artifact 永不被新逻辑覆盖。范围/实现升级后，只影响新 run。

### 5.2 禁止中间态切换

以下改动必须在一个独立、可回滚的变更中完成，并同时通过其验证合同；否则不得合入或启用：

| 变更 | 原子边界 |
| --- | --- |
| 共享枚举/schema 新增值 | schema、迁移、全部消费者（含正式 `/report`）、fixture 同时完成 |
| Lite read model | 后端投影、API schema、前端类型、complete/partial/evidence_only 三档 fixture 同时完成 |
| template publication mode | policy 冻结项、Composer、deterministic audit、发布矩阵、publication fixture 同时完成 |
| 方向开放 | adapter capability、admission、真实 canary、Brief 固定方向、报告方向状态同时完成 |
| 恢复动作 | checkpoint reason、恢复投影 API、run 卡按钮、成功/不可恢复错误 UI 同时完成 |
| 证据交互 | citation、共享 drawer/read-model、三种来源跳转状态同时完成（drawer 只走冻结 `citation_groups`/`evidence_refs` 数据通路，与正式 U1 同一组件） |
| 旧组件隐藏 | 新 Lite 对应路径已被浏览器 E2E 覆盖后，才移除旧组件渲染 |

禁止双写、前端 fallback 到旧 bundle、后端"先返回旧 payload 再逐步补字段"、以及"先显示按钮、以后接接口"。

### 5.3 变更准入模板

每项实现任务开始前，必须在 PR/任务说明中填写：

```text
用户可见能力：
唯一事实来源：
发布前隔离开关默认值：off（发布后移除，不得作为方向显隐开关）
可逆性：未发布前不影响现网；发布失败可回滚整个未发布版本，不回滚已发布的产品方向
成功验收：
失败验收：
不做/不显示：
```

缺少这六项的工作不得进入实现。

---

## 6. 顺序与不可跳过 Gate

### Gate 0：冻结 Lite 合同（v2 重新冻结）

- 确认本文件 §2 的开放/隐藏范围，不再新增方向。
- 确认 Lite 不承担二次深研与跨方向治理呈现。
- 确认 §4.0 template publication mode 作为共享合同修订一并冻结。
- 确认 §4.3 呈现路由：报告三档 vs workflow 恢复卡，互不冒充。
- 冻结 §4.2.1 状态条互斥计数口径、`card_kind` 映射表、§4.2.2 跳转枚举归属共享 read model。
- Lite 页面 Mock 按本版更新（v5）：恢复呈现改为 run 卡视觉归属；状态/章节使用正式词汇；以 v5 的四档预览逐项确认 UI、API 和数据归属。

**通过条件**：产品、前端、后端共用同一份 capability 列表、呈现路由、共享枚举映射与 mock v5。

### Gate 1：后端窄投影与稳定恢复

- 实现 LiteReportReadModel（现有 `LiteReportReader` seam 保留），schema 按 §4.2 对齐共享枚举。
- 将三档发布状态、`compose_mode`、固定方向集合、逐方向运行状态、reason code 与唯一恢复动作固化到 API。
- 验证刷新/重放不重复 adapter 调用和最终 artifact；恢复投影不创建报告 artifact。

**通过条件**：API E2E 驱动 complete / partial / evidence_only / 可恢复中断四种路径；前端尚未露出 Lite 入口。

### Gate 2：单方向垂直切片（内部验收，不单独发布）

- 先完成 `product_marketing` 的 Brief → 真实采集 → admission → 报告 → citation → failure/recovery 端到端链路；它只用于验证正式架构，不形成对用户单方向发布的 Lite。
- 使用真实 Cookie 跑一个成功案例和一个 auth failure 案例。

**通过条件**：浏览器从公开入口完成全程；不使用 fixture 预置报告；成功、失败均有截图和 trace/checkpoint 产物。

#### 2026-07-26 报告物化修复记录

- 已修复单方向报告从证据到发布的三处断点：确认时冻结的 policy 现在只包含用户选择的 canonical direction；governed report 继承该 policy 的 `direction_set_version`/`direction_ids`；无限制项时的确定性 scope card 与 faithfulness evaluator 使用同一 ID 规则。
- `product_marketing` 不再将完整笔记正文作为一条 claim，而是保留带准确 offset 的、有长度上限的逐字 observation。语义审计保持严格：真实的不支持实体、聚合措辞或因果语言仍会阻止 prose 发布。
- 已新增 no-fixture 单方向 API E2E，证明 `product_marketing` 可走真实 workflow 产生 cited report 并公开冻结范围。此前 live canary 的 `evidence_only_report` 是修复前不可变产物，必须以新的 authenticated run 重验；不得把旧结果改写为成功。
- 残余：七方向并行回归仍可在同步 SQLite observation-event 写入竞争时出现 `database is locked`。它不阻断本 Gate 的单方向切片，但必须在 Gate 3 的并行方向验收前沿用 async persistence seam 关闭。公开 Creator 成功/失败 browser screenshot、checkpoint、reload/retry 去重证据仍是 Gate 2 的必需项。

#### 2026-07-26 最终真实成功 canary

- 新代码进程、重新认证的真实 run `run_de8687533d3746ad824ae68342e5033d` 已完成 `product_marketing` 的真实 discover/detail → admission → report 链路：4 个 selected/eligible/independent sources、零 QueryGroup coverage gap、8 个稳定 citation group，报告为 `complete_verified_report`。
- report read model 的冻结范围为 `formal_v1 / [product_marketing]`，唯一方向状态是 `formal_directional_result`，faithfulness audit 为 passed 且无 reason code。直接 observation 使用确定性逐字 statement + citation identity 证明；带改写、聚合或推断的 prose 仍需 LLM 审计。
- Trace 安全投影记录 22 次 provider operation（20 completed、2 retryable `detail/transient_error`），而 workflow 仍 succeeded；递归敏感字段检查未发现 cookie/token/raw provider payload。该结果证明局部上游失败可追溯且不会虚构全局失败。
- 后续真实浏览器验收与遗留项见 [Gate 2 最终验收记录](../../bugfix/20260726_f003_gate2_final_acceptance.md)。

#### 2026-07-26 Gate 2 最终验收

- 真实 Creator 路径以受控无效本地凭据触发同一 `workflow_run_id` 的 `auth_required`。失败阶段没有 materialize 报告或 Creator `artifact_result`；用户显式 QR 认证后，该 run 被重新入队并完成真实 discover/detail、admission、报告和 citation 链路。
- 最终 run 只发布一份 `complete_verified_report` 和一条 `artifact_result`，包含 8 个冻结 citation group；失败期的 4 个 operation/checkpoint 已 supersede，恢复后 provider operation 重新落盘为 completed。成功 run 的显式重复重试仍被拒绝，刷新后的已发布报告与 citation 维持同一版本。
- Gate 2 以 `CLEAN` 关闭。timeout/transient 的真实用户恢复演练和失败态刷新恢复 UI 不属于本 Gate 的已关闭必需项，已作为后续稳定性任务登记；不得将其误记为已验证。

### Gate 3：补齐固定方向并一次性发布

- 逐个完成 `competitor_discovery`、`content_performance` 的正式实现与发布验收。
- 三个方向均完成后，`direction_set_v1` 一次性作为正式子集发布；后续 canary 仅决定发布是否通过，不改变产品方向集合。

**通过条件**：任一方向在某次运行失败不会破坏其他方向的结果；失败方向仍以结构化状态出现在报告中。三方向的发布前验收均通过后，才允许发布 Lite。

### Gate 4：UI 收口与交付验收

- 按现有 Creator Workbench 结构接入 Lite read model；证据 drawer 使用与正式 U1 相同的组件与数据通路。
- 隐藏 Lite 范围外的 section、旧重试卡、二次深研入口和不可用指标；三个固定方向即使本次不可用，也必须显示其状态而非隐藏。
- 验证宽屏、窄屏、刷新恢复、重复点击、citation drawer、三种来源跳转状态、三档发布呈现与 workflow 恢复卡。

**通过条件**：完成 §1.1 的六项完成定义；再允许发布固定三方向的 Lite 正式子集。

---

## 7. 验收案例

| 案例 | 预期结果 |
| --- | --- |
| 品类词：`夏季通勤短裤` | 三方向 complete 或 partial 结果；每条正式发现均有 citation |
| 品牌词：`Satisfy Running` | Brief 允许确认主体；方向以同一报告合同输出 |
| 无合格样本 | `partial_verified_report` 或 `evidence_only_report`，说明"未找到符合条件样本"，不说"没有竞品/需求" |
| 详情字段缺失 | 受影响方向为 `insufficient_evidence`，其他方向可正常发布 |
| Cookie 失效 | workflow 恢复卡（`publication: none`），显示已保存阶段和"更新 Cookie 后继续"；不产生报告 artifact |
| 中断后恢复 | 不重复已完成 search/detail 调用；同一报告只 materialize 一次 |
| 刷新与重复点击 | 恢复同一 Timeline artifact / run 卡，不产生第二个 run/report/message |

---

## 8. 交付产物

- 固定三方向的发布级配置（`direction_set_v1`）与后端窄 read model。
- §4.0 共享合同修订（template publication mode）的 schema/Composer/audit 变更记录。
- Creator Workbench 正式接入；视觉沿用现有设计，不保留长期 Lite 方向开关。
- 3 个成功/部分成功真实报告样本，1 个可恢复失败样本。
- API E2E、浏览器 E2E、截图、脱敏 checkpoint/trace 与当次 canary 记录。
- 本文档与 Lite 页面 Mock（v5）的状态同步更新。

任何额外方向、报告自由生成、二次深研或打包发布需求，均建立为 Lite 之后的新任务；不得插入上述 Gate 0–4。

---

## 9. 子集映射表（复用证明）

| Lite 呈现/合同 | 正式方案对象 | 复用方式 |
| --- | --- | --- |
| 三方向集合 `direction_set_v1` | `DirectionContract`（N1/N2/N3 admission 已 CLEAN） | 发布级共享配置；正式版以新配置版本扩展 |
| 冻结 Brief 卡 | `RunPolicySnapshot` + capability snapshot | 同一对象 |
| 采集/分页/恢复 | `DirectionalExecutionPipeline`、`StageCheckpoint`（CL-01/02/03） | 同一实现 |
| 已验证发现卡 | admitted `ClaimCandidate`（N1/N3 factory） | 同一对象，直接投影 |
| 样本观察卡 | admitted `ClaimCandidate`（N2 observation 类型） | 同一对象；`card_kind=observation` 仅作分组 |
| 线索/初步信号 | `WeakSignal`（含门槛状态） | 投影；`lead` 仅为展示标签 |
| 状态条 | snapshot 计数的确定性投影 | 后端计算；互斥口径按 policy version 冻结 |
| 正文 [n] / hover / 证据区 | 冻结 `citation_groups`（display index 冻结） | 同一对象；前端不重编号 |
| 来源跳转三态 | citation source_url 安全规则（CL-06/R4） | 类型化进共享 report read model |
| 原笔记 drawer | source detail 投影（`citation_groups`/`evidence_refs`） | 与正式 U1 同一组件与数据通路 |
| 报告三档 | `ReportPublication` 三档发布（R1/R3） | 同一枚举；Lite 为 `template_only` 模式 |
| workflow 恢复卡 | workflow 卡 + `checkpoint_summary` 投影 | 同一呈现路径；非报告、非 Lite 新增产物 |
| 研究限制 | `limitations_scope` section | 同一 section_kind |
| 不渲染：张力/建议/Trace 面板/审计面板 | `cross_direction_records`、`action_hypothesis`、`checkpoint_summary`、faithfulness audit | 正式能力既有；Lite 仅不渲染，对象不受影响 |

---

## 10. 执行记录

| Gate | 状态 | 日期 | 当前证据与剩余项 |
| --- | --- | --- | --- |
| Gate 0（v1 合同冻结） | Superseded | 2026-07-22 | 四档状态、受限状态条、lead 合同、标题/正文证据范围、跳转三态已对齐；被本版 v2 替代（词汇与呈现路由变更）。 |
| Gate 0（v2 重新冻结） | CLEAN | 2026-07-22 | `template_only`、`direction_set_v1`、三档报告/非报告恢复呈现和 citation 跳转合同已同步到正式 R1/R3/R4 与 mock v5；产品确认四档预览视觉归属符合预期。Gate 1 必须原子实现共享 policy/Composer/audit/read model；现有 Lite-local seam 不得继续扩展。 |
| Gate 1：共享 template publication runtime | CLEAN | 2026-07-22 | 已完成 F003-LITE-G1-V2：共享 policy 冻结 `direction_set_version`、`direction_ids`、`report_compose_mode`；Composer/faithfulness 保持 `template_only` 的结构化章节与 semantic `not_applicable`；Lite 窄投影改为仅输出共享 publication state、冻结 scope、逐方向状态、三态 citation navigation 与非报告 `recovery_projection`，不再暴露 `complete_lite` 等 Lite-local 状态。三档发布、恢复不产生 artifact、replay 及 `/lite-report` API E2E 回归共 48 项通过。Creator UI 未在 Gate 1 露出，浏览器验收留待 Gate 2/4。 |
| Gate 2：Product Marketing 真实垂直切片 | CLEAN | 2026-07-26 | 已完成真实 Creator Product Marketing 成功与 auth recovery 验收：受控 auth failure 不发布报告或 timeline artifact；显式 QR 认证后，同一 run 恢复完成真实 discover/detail、admission 和 `complete_verified_report`，保留 8 个冻结 citation group，并且只 materialize 一份报告/一条 artifact。失败阶段已 supersede、恢复后的 provider operation 有 completed checkpoint；成功 run 的显式重试不重放，已发布报告刷新后保持同一版本。完整证据与非阻塞遗留项见 [Gate 2 最终验收记录](../../bugfix/20260726_f003_gate2_final_acceptance.md)。 |

### 2026-07-28 Task 2 v2 review repair

- 根因修复：已有 publication 的 lineage/artifact 损坏不再进入 `publication: none` recovery；空 `direction_ids` 不再作为 wildcard；Creator 不再显示历史 Trace 重试文案；workflow restore 的非 404 错误保持可见且不删除 thread-scoped saved run；同一 run 的 recovery card 会被唯一 published report 原位替换。
- 测试层级修复：本地 uvicorn + Next.js + SQLite + Playwright 套件从 `tests/acceptance` 迁至 `tests/e2e`。每个场景使用独立临时 SQLite/Chroma 存储，外部 LLM、source adapter 及 500 restore 分支使用显式 deterministic test fakes；不使用 `page.route`。staging/pre-release acceptance 仍保留给后续 Task 4，本次未启动 Task 3/4。
- RED 证据：新增测试分别复现空 scope 泄露、损坏 publication 被误投影为 recovery、历史 Trace 文案残留、restore 500 被吞并清除 saved run、以及浏览器端损坏 publication 返回 recovery；隔离后的 recovery replacement 回归保持通过。
- GREEN 证据：
  - `pytest -q tests/integration/test_content_research_lite_read_model.py tests/e2e/test_content_research_report_publication_timeline_api.py`：`9 passed`
  - `pytest -q tests/e2e/test_content_research_creator_browser.py`：`8 passed`
  - `npm test`：`27 passed`
  - `npx tsc --noEmit`：通过
  - `npm run lint`：通过，保留 4 条既有 React Hook warning
  - `npm run build`：通过，保留同一组既有 React Hook warning
