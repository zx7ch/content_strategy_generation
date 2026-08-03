# F003 Content Research Lite 交付方案

**状态**：Proposed — 待 Gate 0 重新冻结
**日期**：2026-07-22
**范围**：Creator Workbench 内的可交付内容调研闭环
**关联**：[PRD](./F003_content_research_prd.md)、[正式重构计划](./F003_content_research_development_plan.md)、Lite 页面 Mock（待按本版更新为 v5）

**本版变更**：①恢复呈现从"报告状态"改为 workflow 恢复投影（非报告）；②全部持久化词汇并入正式方案共享枚举；③新增 §4.0 共享合同修订（template publication mode）；④status strip 互斥计数口径冻结；⑤新增 §9 子集映射表；⑥Gate 4 拆分为 4A（最新合同与 UI 收口）/4B（真实能力与发布验收）；⑦三方向改为稳定目录，用户在每次 run 选择非空子集并冻结。旧版中"结构化 unavailable/insufficient、lead 类型化、来源跳转三态、评论字段排除"等已定内容全部保留。

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
2. 每个用户已请求方向都由真实来源链路产出结果，或显式返回 `insufficient_evidence` / `unavailable`；Gate 4B 前，目录中的三个方向均须各自通过真实来源 canary；不允许 mock 或静默降级为编造结论。
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

**正式子集规则**：Lite 的正式、固定方向目录为 `product_marketing`、`competitor_discovery` 与 `content_performance`，始终在 Brief 中可见；用户在每次 run 选择一个或多个方向，`requested_direction_ids` 在开始前冻结。Lite 不是按运行时 canary 动态缩减的 MVP。真实 canary 是每次发布前的验收门槛，不是用户界面的显隐条件。用户未选择的方向为结构化 `not_requested`，不在报告中渲染；用户已选择、但某次运行的来源、认证或字段不可用时，该方向必须以结构化 `unavailable` / `insufficient_evidence` 原因与恢复动作呈现，不能从报告中静默消失。

**方向集合的实现方式**：Lite 的三方向目录是共享 policy 中的发布级配置 `direction_catalog_v1 = [product_marketing, competitor_discovery, content_performance]`，随 `RunPolicySnapshot` 冻结其版本；用户选择的非空 `requested_direction_ids ⊆ direction_catalog_v1` 同时冻结进每个 run。目录不是硬编码、不是运行时开关；正式版以新目录版本扩展可选方向，只影响新 run。

### 2.2 Lite 明确不做

- 评论洞察、UGC 社群、关键词增长、品牌活动等 `direction_catalog_v1` 之外的方向。
- "选择品牌后自动二次深研"和"选择重点内容"的多阶段链路；用户可用该对象重新发起一轮 Lite 调研。
- 跨方向 AggregateClaim 的**渲染**、冲突治理呈现、行动假设、自动业务建议（治理对象在正式链路中既已产生也不受影响，Lite 仅不渲染，见 §9）。
- 语义审计 LLM、报告重写、复杂成本/Token 面板和全量 Trace 日志的**展示**（底层对象与脱敏投影保持正式实现）。
- 新 UI 框架、独立路由、独立 Lite 数据库、旧新双写、兼容 adapter。
- 未有真实后端合同的按钮、空卡片、数字、进度或"即将开放"入口。

不做不表示删除；但 Gate 4A 已明确为 legacy 的 `/report`、`/results`、`/evidence-bundles`、EvidenceBundle 与旧 Creator 呈现例外，必须完整删除。其余上述模块继续留在正式 F003 的隔离路径中；Lite 是正式 F003 的稳定子集，所有 Lite 领域对象、read model、错误语义与 UI 组件必须可被后续正式版本直接复用，而非一次性 MVP 代码。

---

## 3. 可信度与恢复的最小保留集

Lite 简化的是方向数量、报告深度和用户界面，不简化以下不可逆正确性边界。每一行都是正式方案的同一对象，不存在 Lite 副本。

| 必须保留 | Lite 要求 | 正式方案对象 | 不满足时的行为 |
| --- | --- | --- | --- |
| Frozen run scope | `workflow_run_id`、确认后的 Brief、目录版本、用户请求方向、采样上限、policy/capability snapshot 在开始前冻结 | `RunPolicySnapshot` + capability snapshot | 不启动正式采集 |
| 原始来源谱系 | canonical source、来源 URL/稳定 ID、原文片段、采集时间、payload/field hash | `CanonicalSource` / `DirectionSourceProjection` | 不可成为正式发现 |
| 调用检查点 | 搜索/详情调用前记录 operation fingerprint；成功结果先落盘再 completed；in-flight 中断按 outcome_unknown 处理 | `StageCheckpoint`（CL-01） | 中断标为 outcome unknown，禁止自动重复调用 |
| 幂等报告 | 同一 input fingerprint 只 materialize 一份最终 artifact/message | `ReportPublication` materializer（R1） | 重放返回原报告 |
| 准入门槛 | 只由 admitted claim 填充报告；样本不足只能是 lead 或不足状态 | `ClaimAdmissionDecision` / `WeakSignal` | 不生成自由文本结论 |
| 失败语义 | auth、rate limit、temporary error、empty、missing fields、insufficient 分开保存 | 共享 reason code（D3-FDN-1） | UI 显示对应原因和动作 |
| 报告可回溯 | 每项展示冻结 citation group、范围与限制 | `citation_groups`（CL-06/R2） | citation 不完整时降级为 evidence_only_report，不发布完整结果 |

Lite 可舍弃的是：七方向统一覆盖、跨方向治理呈现、复杂版本矩阵、LLM 自由总结/重写、完整审计可视化与扩展性预留。它们不在 Lite 主路径执行，也不阻塞 Lite 发布。

### 3.1 Lite 必须继承的正式不变量（Task 5 前置清单）

本节是 Lite 对正式 F003 的**不可裁剪正确性边界**。Lite 可以减少方向、隐藏审计界面、使用 `template_only`，但不得改变下列对象的身份、冻结、准入、谱系、发布或失败语义。每一项均须由后端合同与定向测试证明；前端不得补算、修正或绕过。

| 不变量 | 正式方案对象／依据 | Lite 必须继承的行为 | 禁止的 Lite 简化 | 最低验收 |
| --- | --- | --- | --- | --- |
| 运行范围冻结 | `RunPolicySnapshot`；正式计划 §4.4 的 locked query plan | Pre-research LLM 对任意输入生成并由用户确认 `subject_structure`（canonical subject、type、core entities、intents、context modifiers、synonym groups）；在开始采集前连同 Provider/model/prompt/schema/input fingerprint、Brief、requested directions、anchors、方向问题、竞品词、`QueryGroup` 的完整 normalized query、direction、priority、sort、时间窗、candidate cap、query-plan hash 与 policy/algorithm version 一起冻结 | 固定业务品类词表；只冻结 QueryGroup ID；运行时重新解释主题或改写 query；遗漏用户补充问题 | Brief 显示“核心对象｜意图｜场景”紧凑确认；同一 snapshot 重放得到相同结构、query plan/hash；任一冻结字段改变只重算下游 |
| QueryGroup 来源谱系 | `QueryGroup`、candidate manifest、`DirectionSourceProjection` | 每个 packet 保留其全部 frozen query/rank hits；多 query 命中不得缩成一个；comment packet 继承父 note 的完整 hit lineage | 仅保留最后一个 `query_group_id`；以 provider 返回顺序替代冻结 hit | 多 group 命中的 note/comment 可完整回放；伪造或非冻结 group 被拒绝 |
| 相关性双层门槛 | `DirectionContract` + direction strategy + admission | source 必须有 frozen QueryGroup 命中；candidate 的直接允许字段引文必须命中冻结核心对象 anchor 或其同义词；intent/context 不能单独放行；`query_subject_not_supported` 是共享、稳定 reason code | 固定业务词表；完整 query 字面匹配；admission 再调用 LLM；前端／Lite-local 判断；仅凭 provider rank、URL、标题或 metrics 放行 | 不相关高指标、仅 intent/context 材料拒绝；相关标题型 `message_angle` 保留；未知结构 fail-closed 并回到 Brief 确认 |
| 方向字段与 claim 类型 | direction strategy / shared quote-field policy | 允许 claim type、其直接字段与 card kind 由共享 policy/strategy 一处定义，并随 run policy version 冻结；`product_marketing.message_angle` 可由标题或正文支撑，其他产品营销类型遵循正式字段限制 | Lite reader 自建 allow-list；`accepted` 等 Lite-local admission 状态；标题自动升级为 finding | 每个 Lite 方向/claim type 的 allowed/disallowed field 参数化测试；卡片类型跨进程／跨版本稳定 |
| provider-real author identity 与样本计数 | candidate manifest、`SamplePolicy`；正式计划 §4.4 | 优先使用 `id:<author_id>`；Spider 未返回 ID 时使用 `name:<normalized author>` 作为保守 fallback，同名合并且不得写回／伪装成 `author_id`；两者都缺失才 author-ineligible。分别记录 selected、relevance-qualified、eligible、independent-author 计数与 identity kind | 强制要求 Spider 不提供的 `author_id`；把显示名复制到 `author_id`；把同名重复来源计成多人；用不相关或 blocking-ineligible 来源凑样本门槛 | author-id-only、author-name-only、规范化同名折叠、ID 优先于名称、两者均缺失、2 relevant + 1 unrelated、2 eligible + 1 缺字段等对抗组合 |
| eligibility 与 admission 重放 | `ClaimAdmissionDecision`、`StageCheckpoint`、admission fingerprint | 样本门槛只由相关且 field/time/author eligible 的来源计算；admission fingerprint 包含 policy/relevance/algorithm 版本；只消费当前 run + 当前 policy 的 decision | 命中旧 checkpoint 直接跳过新 admission；旧 policy admitted decision 进入新 snapshot | 改 policy/relevance/version 后重新 admission；旧 admitted decision 无法形成新 governed card |
| Fact、claim、citation 身份 | `ClaimCandidate`、`GovernedResearchSnapshot`、`citation_groups`；CL-06 | 每张 Lite finding/observation 必有 current admitted decision、frozen scope、claim type、quote/span/hash、canonical source/安全 URL 与一一匹配的 citation group/admission identity | 仅按 claim ID 关联 citation；缺 span/hash/source identity 时静默展示；前端重编号／重组 | citation collision、foreign ref、混合 URL/source、缺 span/hash 均不发布完整报告；编号和顺序稳定 |
| 单笔记 citation 交互 | shared `navigation_state`；Lite §4.2.2 | 每个 Lite citation group 对应一篇小红书 canonical note；组内 refs 共享 source identity/URL；available 时 `[n] 查看原笔记` 一步直达，drawer 仅审计 quote/范围/时间 | 多笔记任取首个 URL；以 drawer 作为外链中转；用 `navigation_unavailable` 掩盖未实现 | available/missing/unavailable 三态；组内 identity/URL 不一致拒绝完整报告 |
| WeakSignal 与 lead | `WeakSignal`、governed snapshot；CL-06 | downgraded/rejected 材料保留为独立、带 citation、样本范围、qualification reason 的 lead；不进入 finding/observation 或其计数 | 丢弃 weak signal；将其混入发现；无 citation 的空 lead | admitted/weak/rejected 三类互斥；lead 显示“仅供参考，不构成结论”与直接依据 |
| 报告完整性与 publication | `ReportPublication`、deterministic audit；Lite §4.0/§4.3 | `/lite-report` 只读取当前 `template_only` publication；卡片、citation、scope 任一必需 identity 不完整时拒绝完整/部分报告并降为 `evidence_only` 或 not-found；完整 snapshot 不得因分页截断丢卡 | prose/legacy publication fallback；静默过滤坏卡但保留 complete；只读取前 50 个 citation | complete/partial/evidence-only 三态与实际卡片一致；>50 citation 仍完整或有确定性分页合同 |
| 历史报告切换与保留证据 | 正式计划 CL-06；Gate 4A 保留规则 | 切换最新合同前，删除全部历史 report-level publication/draft/faithfulness/materialized message；保留 workflow、checkpoint、source、packet、admission、citation、trace 作为审计证据 | 旧 artifact 兼容投影；为保留报告而删除 Gate 2 证据；新旧双写 | migration 幂等；旧 report 不可被 Creator/Lite reader 读取；Gate 2 证据仍可查询 |
| 确定性与安全投影 | policy hash、safe read model、checkpoint | 所有集合在冻结/哈希前稳定排序；read model 不返回 raw provider payload、prompt、cookie、token；刷新/恢复不重复采集或改写既有 artifact | 无序集合进入 hash；UI 扫 raw packet；以 report 重算恢复位置 | 不同插入顺序／进程得到相同 hash；敏感字段递归检查；replay/refresh 幂等 |

**Task 5 执行规则**：任何实现任务开始前，必须从上表逐项声明“本任务触及哪些不变量、哪些不变量不受影响”，并为触及项列出 RED 用例。发现一个正式不变量尚未在 Lite 映射或测试中出现时，先补本节和实施计划；不得以局部页面修复继续开发。

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
Brief confirmation（展示 direction_catalog_v1，选择非空 requested_direction_ids）
  -> run（冻结 scope：catalog version、requested_direction_ids、采样上限、policy/capability snapshot）
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
run: run_id, workflow_execution_state, subject, frozen_scope,
     direction_catalog_version, requested_direction_ids, collected_at
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
                        state ∈ {completed, insufficient_evidence, unavailable, not_requested},
                        reason_code?, recovery_action?
recovery_projection: reason_code, completed_stages[], next_action?, actionability
release: direction_catalog_version
```

前端只根据 `workflow_execution_state`、`publication.state`、`recovery.reason_code`、发布级方向目录与冻结 `requested_direction_ids` 决定展示；Brief 的三个正式方向不得消失。报告只渲染已请求方向；其中任一运行失败仍以结构化状态与恢复动作出现。`not_requested` 只保留在 read model/审计投影，不渲染成失败或空卡；`run_direction_states` 不改变 Brief 的方向目录。

#### 4.2.1 受限状态条与 lead 的冻结规则

`status_strip` 是合法的受限模板，不是报告结论或自由摘要。它只能直接投影冻结计数：完成方向数、已验证发现数、样本观察数、线索数；不得包含推荐、趋势、优先级、因果、比较或任何由 LLM/前端推导出的词句。

**互斥计数口径（随 policy version 冻结）**：`claim_type → card_kind` 映射表冻结在共享 policy 中（`direction_catalog_v1` 下：`product_marketing`、`competitor_discovery` 的 admitted claim 为 `finding`；`content_performance` 的 observation 类 claim 为 `observation`）。`admitted_finding_count` 仅计 `card_kind=finding` 的 admitted claim；`observation_count` 仅计 `card_kind=observation`；`lead_count` 仅计 weak_signal_display。每条 admitted claim 恰好落入一个桶；计数只能由后端投影产生。`evidence_only_report` 只显示"已保存依据数"和"无法形成正式报告的原因"，不显示发现、观察或线索计数。

`lead` 不新增与 `weak_signals` 平行的容器。它是 weak-signal display 的展示标签，必须包含：直接证据引用与方向归属、样本范围、`qualification_reason`（如 `minimum_independent_sources_not_met`）、明确的"仅供参考，不构成结论"展示状态。lead 不得进入 `main_findings`、`admitted_finding_count`、行动建议或跨方向推导。

#### 4.2.2 引用来源的跳转合同

`source_url` 可选不等于"来源身份可选"。每条 citation 无论是否可跳转，均必须有 quote、field path、source collected at 和安全来源标识。`navigation_state` 固定为：

| 值 | 条件 | 用户文案 |
| --- | --- | --- |
| `available` | 已有可安全打开的来源 URL | `查看原笔记` |
| `missing_source_url` | adapter/来源未提供 URL，但已保存引用材料 | `未保存来源链接；可查看原文片段与采集时间` |
| `navigation_unavailable` | URL 已记录，但因认证、平台限制或安全策略无法在当前环境打开 | `来源链接当前不可打开；可查看原文片段与采集时间` |

`navigation_state` 枚举及其文案提交进**共享 Lite report read model**，不是 Lite-local 字段；正式 U1 的 evidence drawer 直接继承。`navigation_unavailable` 绝不能表示"前端尚未接入跳转"。后者是未完成实现，不是可发布的用户状态；Gate 4A 的证据交互原子验收不通过时，该方向不得开放。

Lite 的证据字段只允许 `content_text` 与 `title`（投影过滤）。评论保留在原始 provider payload 与正式链路的 comment packet 中，但 Lite report、citation、evidence drawer 和 source detail 不展示评论正文或评论统计，以免暗示评论洞察已开放。

### 4.3 报告三态与 workflow 恢复呈现

| 呈现 | 性质 | 页面内容 | 禁止行为 |
| --- | --- | --- | --- |
| `complete_verified_report`（template_only） | 报告发布 | 已验证发现卡、observation 卡、lead（如有）、范围限制、citation、状态条、已请求方向的运行状态 | 不展示未请求方向、行动建议或自由叙述 |
| `partial_verified_report` | 报告发布 | 已验证卡 + 每个受影响已请求方向的结构化 `insufficient_evidence`/`unavailable` 原因与恢复动作 | 不把缺失方向以空结论补齐，也不移除已请求方向 |
| `evidence_only_report` | 报告发布 | 已保存依据、无法形成正式报告的原因、方向状态 | 不写叙述性结论，不把证据伪装为发现 |
| workflow 恢复呈现 | **非报告**（run 状态卡，`publication: none`） | 已保存阶段、失败原因（如 `auth_expired`）、唯一恢复动作 | 不 materialize 报告 artifact、不标"研究完成"、不创建第二个 run |

`evidence_only_report` 是正式的安全回退状态：当引用/报告材料无法完整投影时，只显示已保存依据和限制，不写叙述性结论。

---

## 5. 无返工实施规则

### 5.0 共享枚举纪律

1. 任何持久化或跨服务共享的枚举（publication state、section_kind、reason code、navigation_state、claim_type、workflow state、compose_mode、direction state）只允许取共享 schema 的既有值。
2. 确需新增的值（如 `template_only`、跳转三态、`not_requested`）必须作为共享 schema 变更，与其唯一消费者在同一原子变更中交付（§5.2）；禁止 Lite-local 定义后"以后再对齐"。
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
| 共享枚举/schema 新增值 | schema、迁移、全部 `/lite-report` 消费者、fixture 同时完成 |
| Lite read model | 后端投影、API schema、前端类型、complete/partial/evidence_only 三档 fixture 同时完成 |
| template publication mode | policy 冻结项、Composer、deterministic audit、发布矩阵、publication fixture 同时完成 |
| 方向开放 | adapter capability、admission、真实 canary、Brief 目录/用户选择、报告方向状态同时完成 |
| 恢复动作 | checkpoint reason、恢复投影 API、run 卡按钮、成功/不可恢复错误 UI 同时完成 |
| 证据交互 | `/lite-report` 的 citation 摘要 + 同路径按需 evidence-detail 投影、共享 drawer/read-model、三种来源跳转状态同时完成（drawer 只走冻结 `citation_groups`/`evidence_refs` 数据通路） |
| 旧合同删除 | 新 Lite 对应路径已被浏览器 E2E 覆盖后，删除旧组件、endpoint、EvidenceBundle 模型与测试；禁止隐藏或 fallback |

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

### Gate 0：冻结 Lite 合同（v3 重新冻结）

- 确认本文件 §2 的开放/隐藏范围，不再新增方向。
- 确认 Lite 不承担二次深研与跨方向治理呈现。
- 确认 §4.0 template publication mode 作为共享合同修订一并冻结。
- 确认 §4.3 呈现路由：报告三档 vs workflow 恢复卡，互不冒充。
- 冻结 §4.2.1 状态条互斥计数口径、`card_kind` 映射表、§4.2.2 跳转枚举归属共享 read model。
- 冻结 v3 合同修订：`direction_catalog_v1` 与冻结的非空 `requested_direction_ids` 分离；`not_requested` 仅为 read-model/审计状态，报告只渲染已请求方向。
- Lite 页面 Mock 按本版更新（v5）：恢复呈现改为 run 卡视觉归属；状态/章节使用正式词汇；以 v5 的四档预览逐项确认 UI、API 和数据归属。

**通过条件**：产品、前端、后端共用同一份 capability 列表、呈现路由、共享枚举映射与 mock v5。

### Gate 1：后端窄投影与稳定恢复

- 实现 LiteReportReadModel（现有 `LiteReportReader` seam 保留），schema 按 §4.2 对齐共享枚举。
- 将三档发布状态、`compose_mode`、方向目录、冻结请求方向、逐方向运行状态、reason code 与唯一恢复动作固化到 API。
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

### Gate 3：补齐方向目录的真实能力

- 逐个完成 `competitor_discovery`、`content_performance` 的正式实现与发布验收。
- 三个目录方向均完成后，`direction_catalog_v1` 一次性作为正式子集发布；后续 canary 仅决定发布是否通过，不改变产品方向目录或用户的选择权。

**通过条件**：任一已请求方向在某次运行失败不会破坏其他已请求方向的结果；失败方向仍以结构化状态出现在报告中。三方向的发布前验收均通过后，才允许发布 Lite。

### Gate 4A：最新合同与 UI 收口（内部预发布）

- 原子引入 `direction_catalog_v1 + requested_direction_ids`、`not_requested` 共享状态、用户非空多选 Brief、冻结 scope/read model/API schema 与后端 capability 投影；空选择被拒绝。
- Creator 接入 `/lite-report`，并恢复只读安全 `/trace` 的正式 Trace Panel、认证恢复与同 run resume UI；citation drawer 通过 `/lite-report` 按需读取冻结 citation detail。继续删除 `/report`、`/results`、`/evidence-bundles` 与其所有消费者、旧 Creator report/二次深研/聚合建议 UI、EvidenceBundle service/store/schema/table/测试。Trace/retry UI 不再删除，但不得读取旧报告、EvidenceBundle、未冻结来源或 raw provider payload。

#### Task 5G — Lite-safe formal Trace parity

恢复正式 Trace Panel 的完整交互与可观察性：自动刷新、实时耗时、明确的 `running` / `waiting_retry` / `failed` / `completed` 状态、真实 retry/requeue、事件时间线、checkpoint 摘要、Provider 诊断和报告关联。Lite 仍只消费脱敏安全投影；不得返回原始采集内容、Cookie、Token、provider 原始请求/响应，也不得恢复 legacy `/report`、`/results` 或 EvidenceBundle 路径。后端的 durable state 为 `waiting_user`，前端将其渲染为 `waiting_retry`（“等待恢复”）；可恢复 specialist 失败必须先由父状态机收敛到该状态，不能继续伪装为 `running`。

##### Task 5G-1 — Fresh state and basic elapsed-time compatibility（已完成）

- 已完成 Trace 打开即刷新、非终态每三秒轮询、无时区 SQLite 时间按 UTC 解析，以及进入 `waiting_user` 后停止继续累加等待时间。
- 当前耗时仍是基于旧事件边界的兼容估算，不代表真实 queue / active execution / backoff / waiting 分段；真实时间语义由 Task 5G-2B 接替，不能将 5G-1 描述为完整耗时修复。

##### Task 5G-2A — Shared collection correctness（P0，实现与真实验收完成）

- Lite 继续使用共享正式采集内核；不得在 Creator 增加 Lite-only 采集特判。详情调用前必须过滤非法 ID、URL fragment 和非笔记搜索卡；`invalid_candidate` / `note_unavailable` 是候选级结果，在冻结 `detail_fetch_cap=30` 内补位，只有最终证据门槛无法满足时才决定 direction 的 partial/unavailable/recovery 状态。
- Provider 错误必须稳定分类；`笔记不存在` 映射为不可重试的 `note_unavailable`，`transient_error` 只表示真实临时传输故障。登录恢复只由 `auth_required` / `auth_expired` 触发，本 Task 不增加独立登录状态入口。
- 当前冻结采样值保持不变：每 QueryGroup `candidate_cap=20`、`minimum_samples=3`、`minimum_independent_authors=2`、`author_cap=3`、`detail_fetch_cap=30`、`comment_limit=30`。预校验过滤不消耗额度；候选一旦被某方向纳入详情评估便消耗该方向一个 `detail_fetch_cap` slot，即使底层详情来自同 run 复用。物理 Provider 调用另行去重计数，补位不得突破方向冻结上限。
- 重试语义必须分层并被实际计数：provider operation 最多 3 次自动重试，specialist 最多 2 次用户恢复，workflow child 最多 3 次执行（首次 + 2 次恢复）；非法候选、笔记不存在、parser/permanent failure 不重试，认证成功后才允许 auth failure 消耗一次用户恢复。
- 本阶段只修改共享正式采集内核和安全错误投影，不增加 Lite-only 采集分支，也不改变 Trace 当前倒序展示。
- 交付状态：
  - `5G-2A.1` 候选预校验与稳定错误分类已完成：非法 ID、URL/ID 不一致、fragment、缺少详情安全参数和非 note 卡片在共享 Xiaohongshu source boundary 被拒绝；`笔记不存在` 为不可重试的 `note_unavailable`，未知永久错误不再降级成 `transient_error`。
  - `5G-2A.2` 候选失败隔离与冻结预算内补位已完成并由 Pipeline 集成测试覆盖；候选级失败不产生 run-level 登录/网络恢复动作。
  - `5G-2A.3` 分层重试计数已完成：单次 provider operation 为首次调用加最多 3 次自动重试；同一 run 的 specialist 最多 2 次用户恢复，workflow child 最多执行 3 次。三类计数均由真实执行边界消费并通过安全 Trace 分别投影；第三次用户恢复会在 provider 调用前拒绝。
  - 恢复重放的稳定 checkpoint ID 现可从 `superseded` 原位更新为新执行事实，避免新一轮 Provider 失败被静默丢弃后误判成功。空队列 dispatcher 轮询不再获取 SQLite 写锁；E2E 使用与生产一致的提交后唤醒边界。
  - Provider 级认证、访问拒绝、解析和永久错误会立即停止该方向后续外部调用，并按真实 blocking failure 收敛 direction/run；候选级失败仍隔离补位，已被足量证据补偿的临时失败不会误生成 run-level 恢复卡。
  - 恢复不再只重启 workflow 元数据：discover/detail/comment 会按失败阶段真实重放，并保留已成功的 sibling evidence；认证恢复必须先通过服务端认证就绪门禁。workflow step 重启与 child attempt 增量在同一 manager 事务内完成，并发的同 run 恢复请求由互斥锁串行化。
  - Trace 的 provider operation 以 `specialist + fingerprint` 区分并输出脱敏 operation ID，避免不同专家的相同调用互相覆盖；永久失败不会伪装成 partial/success 或可恢复中断，已发布/成功 run 的重试仍在 provider 调用前拒绝。
  - 验收范围仍是共享正式采集内核下的 Lite 三方向目录；没有新增 Lite-only 数据源分支，也没有重新放开旧七方向合同。Task 5G-2B 的真实 queue / active / backoff / waiting 时间分段仍独立待做。
- 2026-08-03 真实验收记录：
  - 新 run `run_04a898dc71634c3fa7f49ddff3bc6a65` 经 Creator 正式路径发起 `product_marketing`，小红书安全 Trace 记录 2 次 discovery + 30 次 detail，共 32/32 completed，认证错误、自动重试和用户恢复均为 0；当前 Cookie 有效且 Spider 接口有返回。
  - 初次 `evidence_only_report` 的根因是准入合同与 Spider 真实字段不一致：Spider 稳定返回作者名称，pipeline 却额外硬要求 `author_id`，使 15 个相关来源全部 author-ineligible。合同现改为 `id:<author_id>` 优先，否则使用 `name:<normalized author>` 保守计数；同名折叠、不伪造 `author_id`，两者均缺失才不可准入。
  - 已对同一 run 执行 packet-only downstream replay：复用原 30 个 packet，重放 admission → direction result → governance → snapshot → audit → publication；重放前后 64 个 Provider operation checkpoint ID 差异为 0、packet ID 差异为 0，确认未再次调用 Spider。最新 admission checkpoint 为 15 relevant / 15 eligible / 15 independent authors，产生 24 admitted claim；报告 `rpp_43e3b0ad60ae91d91f759dbd` 为 `complete_verified_report`、24 个 citation，方向为 `formal_directional_result`，Creator 仍只有 1 条该 run 的 `artifact_result`。
  - `published report artifact is missing` 现按 publication-pending 处理：Content Research API error 保留 HTTP status，404／artifact-missing 不再终止轮询或写入永久对话错误；报告可见后同一 run 原位显示最新 publication。
  - 该 run 暴露了旧 `cheap_fast` 预检索路由的 Kimi Coding 会员权益 402。预检索现已切换到既有 `balanced` OpenAI 路由；最小真实调用确认 provider=`openai`、model=`gpt-4o-mini` 且返回非空。当前 persisted packets 已完成 admitted-evidence 验收，无需再次采集。

##### Task 5G-2B — Recorded Trace timing semantics（P0，已完成，2026-08-03）

- Trace 保持当前倒序时间线：最近/当前阶段在最上面，最早阶段在最下面，阶段编号仍表示原始 workflow 顺序。
- 在共享 workflow runtime 记录真实 queue、active execution、retry/backoff 和 waiting 边界；Lite `/trace` 只做安全投影。等待时间不得继续累加为执行耗时，历史秒级记录必须标记为 `estimated`。
- 完整设计与验收矩阵见 `docs/superpowers/specs/2026-08-02-f003-lite-trace-collection-correctness-design.md`。
- `F003_LITE_PREVIEW_ENABLED` 是唯一的整体验收隔离开关：默认 off；off 时 Creator 不显示入口且后端拒绝创建/确认新 Lite run；它不得控制单一方向。Gate 4B 发布时移除。
- Gate 2 的已验证 workflow、run、checkpoint、canonical source、冻结 citation group、trace 和验收产物保留。删除 EvidenceBundle 表前，必须证明这些留存证据不依赖旧模型，并用迁移前后回归保护。
- API/schema 覆盖全部 7 种非空选择组合和空选择拒绝；浏览器覆盖单选、双选、全选。真实采集复用 `product_marketing`；`partial`、`evidence_only`、恢复卡与 citation 跳转三态使用后端受控真实状态，浏览器不得 mock API payload。
- 验证宽屏、窄屏、刷新恢复、重复点击、citation drawer、三种来源跳转状态、三档发布呈现与 workflow 恢复卡。
- 完成记录：workflow runtime 已耐久记录 queue、active、retry/backoff、waiting/pause 边界；暂停、取消、失败和完成会以同一 UTC 边界闭合父子 span，等待时间不计 active。历史缺少完整 timing 的记录继续标记 `estimated`。
- Creator 保持最近事件在最上、原始阶段编号不变。组合验收通过 150 项后端/API、53 项前端、production build，以及 API-backed 浏览器的 newest-first、阶段编号和 recorded timing 文案回归。

#### Task 5H — Lite runtime model configuration and pre-research recovery（P0，已完成，2026-08-03）

为避免 `.env` 中固定 LLM Provider、模型或 API Key 不可用时阻断 Lite 发布，Creator 在右侧栏“本次研究摘要”下方增加紧凑“模型服务”卡片。用户只配置 `base_url`、`model`、`api_key`；Lite 限定 OpenAI-compatible Chat Completions 协议，但不维护具体模型 ID 白名单。`temperature`、输出长度、结构化输出提示、超时和自动重试仍由系统按任务管理，不开放给用户。

- API Key 可明文保存在本地 SQLite，但查询接口只返回是否已配置和末四位；日志、Trace、usage event、异常及 Creator 消息不得包含完整 Key、Authorization header 或原始上游响应。
- 运行时优先使用当前 Workspace/用户的已验证配置，未配置时使用 `.env` 系统默认；用户配置调用失败后不得静默切换 Provider、模型或 `.env`。
- 保存配置前必须通过真实最小 Chat Completions 验证，不依赖 `/models`；验证覆盖连接、鉴权、模型可用、非空文本和可解析结构化文本。失败候选不得覆盖当前有效配置，保存后无需重启服务即可生效。
- LLM 失败稳定分类为鉴权、账户/权益、模型不存在、限流、服务不可用、协议不兼容和结构化输出无效。可恢复失败使 workflow 收敛到 `waiting_user`，右侧卡片提供“配置模型”和验证成功后的“继续调研”。
- 恢复沿用同一 run 和现有用户恢复预算，从最早未完成 LLM checkpoint 继续；已经完成的 Spider packet/checkpoint 必须复用且 Provider operation 数不得增加。若 pre-research 在首次采集前失败，则只重试 pre-research，随后进入首次 Spider 采集。
- 本 Task 不建设加密托管、Workspace 管理 UI、独立设置中心、Anthropic 原生协议、用户自定义推理参数、模型目录同步或自动多模型 fallback。
- 该最小闭环与 5G-2B 的真实时间分段解耦；模型不可用会直接阻断真实 Lite run，因此 Task 5H 优先实施，5G-2B 不是其前置条件。
- 完整设计与验收矩阵见 `docs/superpowers/specs/2026-08-03-f003-lite-model-configuration-design.md`。
- 完成记录：本地 SQLite 按 Workspace/用户保存已验证的 OpenAI-compatible `base_url`、`model`、`api_key`；API/Trace/日志只投影安全摘要和 Key 末四位。配置即时贯穿 pre-search、analysis 与 faithfulness，不会在用户配置失败后静默回退 `.env`。
- Creator 空闲时即在“本次研究摘要”下显示模型卡。LLM 故障进入同一 run 的 `waiting_user`；刷新后从 durable `llm_recovery` 恢复，只有故障后重新验证保存的配置才能继续，且复用原 workflow/attempt/brief，不重复已完成采集。
- 组合验收通过 150 项后端/API、53 项前端、production build，以及 Key 遮罩、reload 同 run 恢复和 Trace timing 三项真实浏览器回归。

#### Task 5I — Lite structured subject and deterministic query plan（P0，已完成，2026-08-04）

Lite 只交付结构化主题到可信采集的核心闭环，不扩展为通用语义规划平台。完整设计见 `docs/superpowers/specs/2026-08-03-f003-lite-structured-relevance-replay-design.md`。

- Pre-research LLM 输出受 schema 约束的 `subject_structure`；代码而非 LLM 自报置信分数决定能否开始采集。核心对象、grounded raw mention、结构一致性和歧义状态不满足时回到 Brief/模型恢复边界，不调用 Spider。Lite 只自动执行单一明确核心对象，多对象要求用户确认主对象。
- Brief 显示“核心对象｜意图｜场景”。确认后由版本化编译器冻结最多两个主 QueryGroup：Q1 为核心对象 + 主研究意图，Q2 为核心对象 + 用户明确重点／尚未覆盖场景；规范化后相同则合并，不为凑数执行重复搜索。
- 同义词不作为默认独立 QueryGroup，仅作为确定性准入等价词和最多一个预编译补位 Q3。Q3 只有在相关 eligible 样本、独立作者、核心对象、用户明确重点或详情失败补位仍有缺口时激活；刷新、恢复和重试不得生成不同 Q3。
- 冻结 `primary_query_group_cap=2`、`coverage_fallback_query_group_cap=1`、每 QueryGroup `candidate_cap=20`；正常搜索候选上限为每方向 40，触发补位后最多 60。既有 `detail_fetch_cap=30`、样本、作者和重试预算继续由共享 `RunPolicySnapshot` 唯一定义，重试不能重置或放大预算。
- Task 5I 只做单方向核心：等价 normalized Q1/Q2 合并，同一方向内 canonical note 去重，并保留每个 QueryGroup/rank hit 的完整 lineage。跨方向原子 single-flight、共享 collection artifact/binding 和共享失败传播延期到 Gate 4B，在多个方向开放真实并发采集前完成。
- 既有 `detail_fetch_cap=30` 明确为每方向“详情样本评估上限”；Task 5I 沿用现有单方向 operation 计数，不新增物理/逻辑双账本。Gate 4B 引入跨方向复用时再分别投影 physical call 与方向 evaluated count。
- 新增安全逻辑 checkpoint：`subject_structure`、`query_plan`、`coverage_decision`、`fallback_decision`、`relevance_revision`。它们在现有倒序 Trace 内作为专家/预检索细节展示，不增加伪 workflow 阶段；Q3 不计为错误/重试。
- `needs_confirmation` 是独立产品状态而非模型失败。Creator 复用底部正常对话输入框，将消息以同 run `clarify_subject` action 提交；Pre-research 卡片只显示澄清问题、结构摘要与确认状态，不增加输入框。澄清不消耗模型故障恢复预算、不调用 Spider；采集开始后修改主题必须新建 run。
- Task 5I 沿用现有单方向 auth/outcome-unknown/自动重试/note-unavailable 语义。公开 Trace 不返回完整 query、原始主题、note ID、Prompt、凭据、请求头或 provider 原始 payload。
- Spider 返回数量不等于可用证据，必须分别记录 discovered、deduplicated、relevant、detail-eligible、admitted。现有候选和 packet 必须先耗尽/重放，再允许 Q3；任何下游修复不得为了重做 admission/report 重跑 Spider。
- 最小验收覆盖：可信主题、对话澄清歧义/空主体、Q1/Q2 去重、同方向 note 去重、Q3 确定性激活、Trace 脱敏、显式重点未覆盖的 partial 语义，以及刷新/恢复不增加既有 provider operation。
- 明确延期：多核心对象自动拆分、可视化主题编辑器、embedding 相关性、动态全局预算、多语言 query expansion、复杂否定规划和多轮 LLM query 改写。

- 完成记录：新 run 已冻结通用结构化主题、`query_relevance_v2` 与确定性 `2 + 1` 计划；Q3 仅在持久化主组覆盖不足时激活，恢复不会重复主组 operation。历史任务通过 append-only `relevance_revision` 从既有 packets 重放准入到发布，并校验 provider operation 与 packet ID 集合不变。安全 Trace 保持最近记录在上，仅投影短哈希、组数、阶段计数与稳定原因码。聚焦后端/API/Trace/前端验收 112 项、内容调研单元测试 270 项、前端 54 项及 production build 通过；浏览器自动化依赖缺失时不计为通过证据。

**通过条件**：预发布环境中新合同为唯一 Creator/report 合同；选择、scope、报告三态、恢复卡、citation drawer、模型配置与模型故障后的同 run 恢复均通过上述 API/浏览器验收。Gate 4A 不对正式用户发布，也不代表所有目录方向的采集能力已完成。

### Task 5：Lite 报告质量合同收口

**目标**：将 Lite 报告收敛为“可信的结构化研究发现”，修复 `/lite-report` 将原始样本、弱信号或不相关来源误投影为"核心发现"的风险。Lite 严格复用正式方案的 `admitted ClaimCandidate -> GovernedResearchSnapshot -> LiteReportReadModel` 合同；它交付相关、可追溯、有范围限制的 finding/observation，不承诺自由 prose、跨方向综合或营销策略结论。

**任务边界**：

1. **共同推导链必须执行**：冻结 Brief/scope、`QueryGroup`、确定性 related-selection、canonical source 与 packet 谱系、Fact、方向专用 `ClaimCandidate`、admission、`DirectionResultDecision`、`WeakSignal`、`GovernedResearchSnapshot`、冻结 citation group、deterministic audit 和幂等 report materialization。它们是正式方案同一对象，不得以 Lite-local fallback 或前端推断替代。
2. **实现单元 A：read-model 合同一致性**：Lite 只验证并投影既有 `GovernedResearchSnapshot` 合同，不在 read model 重造 admission。`main_findings` 中的每张卡必须有 admitted decision、合法 direction/claim type、冻结 scope 和一一对应的 citation identity；finding 与 observation 均保留在该 section，由 `card_kind` 分组并由后端互斥计数。`product_marketing.message_angle` 可由相关标题或正文支撑，但标题、搜索命中、原始 packet 和普通 citation 均不得自动成为 finding。
3. **实现单元 B：正式主体相关性合同**：Pre-research LLM 先把任意用户输入生成受 schema 约束的 `subject_structure`，Brief 以“核心对象｜意图｜场景”供用户确认；确认后将结构、模型/Prompt/Schema 身份和由其确定性编译的核心对象 anchors/同义词冻结到正式 `RunPolicySnapshot` 与 `DirectionContract`。双层门槛要求 source 保存冻结 `QueryGroup` 命中谱系，candidate 的直接允许字段引文命中冻结核心对象或其同义词；intent/context 不得单独放行。不得使用固定业务词表、Lite-local/前端判断、完整 query 字面匹配或 admission-time LLM；不相关材料必须 rejected/downgraded，不能进入 finding。
4. **实现单元 C：Lite citation 直接跳转**：Lite 的每个 frozen citation group 固定对应一篇小红书 canonical note；组内可含标题／正文等多个 evidence ref，但必须共享同一 canonical source/安全 `source_url`。当 `navigation_state=available` 时，`[n] 查看原笔记` 直接打开该小红书笔记；evidence drawer 仅用于按需查看 quote、字段、范围和采集时间，不作为外链中转。`missing_source_url` 与 `navigation_unavailable` 使用共享合同文案。
5. **实现单元 D：历史 artifact 清理**：切换最新 Lite report 合同前，以 idempotent migration 一次性删除所有既有 report-level `ReportPublication`、`ReportDraft`、`ReportFaithfulnessDecision`、materialized report link 与关联 Creator `artifact_result` message；不得再由 `/lite-report` 投影。保留 Gate 2 已验证的 workflow、checkpoint、canonical source、packet、admission decision、冻结 citation group 和 trace，避免为清理报告而重复真实采集。

**明确不做**：

- 不实现 `competitor_discovery`、`content_performance` 的真实采集能力或三方向真实 canary（Gate 3 / Gate 4B）。
- 不生成或渲染 `AggregateClaim`、跨方向张力、行动假设、核心 prose 结论、推荐或下一步业务建议。
- 不运行 semantic LLM audit 或报告重写；`template_only` 下只运行对象身份、计数、citation anchor 与 scope 一致性的 deterministic audit，semantic audit 为 `not_applicable`。本任务不承诺“分析师式”综合洞察；该能力另行以正式 prose/aggregate 任务交付。
- 不把本任务变成全量真实 E2E 重跑；以 admission/read-model/API/UI 定向测试为主，最后只增加一条受控浏览器回归。

**独立验收**：

1. 不相关笔记标题、搜索命中、`title + metric` 和未准入样本均不能出现在 `main_findings`。
2. 每一张 finding/observation 卡均可回链到 admitted decision、冻结 direction/scope、quote/span/hash 与稳定 citation group；finding/observation 均可渲染且计数互斥，缺任一项即不发布完整报告。
3. `WeakSignal` 与 finding/observation 在 section、计数、视觉标签和语义上互斥；前者明确为"仅供参考，不构成结论"。
4. 每个具备安全 `source_url` 的 Lite citation group 可一步打开其唯一的小红书原笔记，同时仍可按需展开 evidence drawer；组内 evidence ref 的 source identity/URL 不一致时不得发布完整报告；三种 navigation state 均由后端 read model 控制。
5. 清理后，legacy report artifact 不能通过 Creator、`/lite-report` 或任何兼容 fallback 显示；保留的 Gate 2 证据仍可用于新合同 read model 的定向回归。
6. 定向后端与前端测试覆盖：相关 admitted finding、不相关材料的正式 admission 拒绝、标题支撑的 admitted `message_angle`、admitted observation、WeakSignal 分流、单笔记 citation 的三种来源跳转状态、legacy artifact 清理；受控浏览器回归验证报告页面不再出现标题集合型"核心发现"。

### Gate 4B：真实能力与正式交付验收

- 在多个方向开放真实并发采集前，实现 run 级原子 single-flight：共享物理 operation、collection artifact/binding、失败传播和 physical/logical 双计数；同 query/note 的跨方向调用必须由真实并发 E2E 证明只执行一次。
- 完成 Gate 3 后，三个方向各自通过一次真实成功 canary，并完成一次三方向全选的真实 run，验证并行、汇总计数、幂等报告物化和 citation。
- 完成一次已请求方向的证据不足 run 与一次可恢复失败后继续的 run；在移除 preview flag 的候选发布环境重跑 Gate 4A 的浏览器主路径。
- Task 5 的报告质量合同与 legacy report artifact 清理必须通过，才可将三方向真实 canary 的输出视为正式 Lite 报告。

**通过条件**：满足 §1.1 的六项完成定义、Gate 3 的真实方向验收与本 Gate 的发布组合；移除 `F003_LITE_PREVIEW_ENABLED` 后，正式发布 Lite。

---

## 7. 验收案例

| 案例 | 预期结果 |
| --- | --- |
| 品类词：`夏季通勤短裤` | 用户选择的方向 complete 或 partial 结果；每条正式发现均有 citation |
| 品牌词：`Satisfy Running` | Brief 允许确认主体；方向以同一报告合同输出 |
| 无合格样本 | `partial_verified_report` 或 `evidence_only_report`，说明"未找到符合条件样本"，不说"没有竞品/需求" |
| 详情字段缺失 | 受影响已请求方向为 `insufficient_evidence`，其他已请求方向可正常发布 |
| Cookie 失效 | workflow 恢复卡（`publication: none`），显示已保存阶段和"更新 Cookie 后继续"；不产生报告 artifact |
| 中断后恢复 | 不重复已完成 search/detail 调用；同一报告只 materialize 一次 |
| 刷新与重复点击 | 恢复同一 Timeline artifact / run 卡，不产生第二个 run/report/message |

---

## 8. 交付产物

- 三方向发布级目录（`direction_catalog_v1`）、冻结 `requested_direction_ids` 合同与后端窄 read model。
- §4.0 共享合同修订（template publication mode）的 schema/Composer/audit 变更记录。
- Creator Workbench 正式接入；视觉沿用现有设计，不保留长期 Lite 方向开关、旧报告 endpoint 或 EvidenceBundle 实现。
- 3 个成功/部分成功真实报告样本，1 个可恢复失败样本。
- API E2E、浏览器 E2E、截图、脱敏 checkpoint/trace 与当次 canary 记录。
- 本文档与 Lite 页面 Mock（v5）的状态同步更新。

任何额外方向、报告自由生成、二次深研或打包发布需求，均建立为 Lite 之后的新任务；不得插入上述 Gate 0–4。

---

## 9. 子集映射表（复用证明）

| Lite 呈现/合同 | 正式方案对象 | 复用方式 |
| --- | --- | --- |
| 三方向目录 `direction_catalog_v1` + `requested_direction_ids` | `DirectionContract`（N1/N2/N3 admission 已 CLEAN） | 发布级共享目录 + run 级用户冻结子集；正式版以新目录版本扩展 |
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
| Gate 0（v2 重新冻结） | Superseded | 2026-07-22 | `template_only`、三档报告/非报告恢复呈现和 citation 跳转合同已冻结；其“每个 run 固定三方向”解释由 v3 的目录/用户选择合同替代。 |
| Gate 0（v3 目录与 Gate 4 拆分） | CLEAN | 2026-07-26 | 已冻结 `direction_catalog_v1 + requested_direction_ids`、`not_requested`、Gate 4A/4B、单一 `/lite-report` 合同、EvidenceBundle 删除边界与 preview 隔离。 |
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

### 2026-08-01 Task 5E 有界验证记录

- `pytest -q tests/unit/test_content_research_product_marketing_admission.py tests/integration/test_content_research_lite_read_model.py tests/integration/test_content_research_direction_pipeline_store.py tests/integration/test_content_research_report_publication_materializer.py`：退出码 `0`；`87 passed in 8.80s`。
- `cd frontend && npm test -- --run src/app/creator/page.test.tsx && npx tsc --noEmit`：退出码 `0`；Node test runner 为 `tests 37`、`pass 37`、`fail 0`、`cancelled 0`、`skipped 0`、`todo 0`；`npx tsc --noEmit` 无输出并成功退出。运行中出现 4 次既有 `[MODULE_TYPELESS_PACKAGE_JSON]` 性能 warning，均不影响退出状态。
- `pytest -q tests/e2e/test_content_research_creator_browser.py -k 'complete_report_uses_lite'`：退出码 `0`；`3 passed, 16 deselected in 24.96s`。本次未发生 browser socket sandbox 阻塞，故无 skip/block 记录。
- 有界结论：上述结果仅证明 Task 5 指定 admission/read-model/pipeline/materializer、Creator 定向测试/类型检查，以及受控 `complete_report_uses_lite` 浏览器回归的当前状态。**不据此声明 Gate 3 完成、Gate 4B 完成，或三个方向真实来源 canary 完成。**
