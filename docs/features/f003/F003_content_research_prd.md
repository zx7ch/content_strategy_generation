# F003 Content Research PRD(小红书内容调研与证据化洞察)

**文档状态**:设计中  
**版本**:v0.1  
**日期**:2026-06-27  
**负责人**:TBD  
**适用范围**:Content Research — 从品类/品牌/SKU 输入出发的小红书调研、竞品识别、内容筛选与证据化洞察

---

## 1. 需求背景

用户希望从一个品类词、SKU、品牌名或产品方向出发,快速了解小红书上的增长关键词、竞品品牌、品牌活动、产品营销、UGC 社群互动、内容样本和新增品类机会。

现有问题:

- 用户原始输入往往很短,例如“徒步短裤”或“Satisfy Running”,系统如果直接搜索,容易跑偏。
- 不同调研目标需要不同搜索策略,例如竞品分析、产品营销、UGC 社群互动关注的数据不同。
- 如果所有信息都堆在 main agent,会导致上下文臃肿、执行路径不清晰、结果难判断优先级。
- 用户最关心的是可行动洞察,而不是原始搜索结果堆叠。
- 最终结论需要可追溯、有证据边界、有优先级和下一步动作,否则难以建立信任。

因此,F003 需要在正式调研前增加一个轻量预检索和意图确认环节,先帮助用户确认本轮调研主体、关注品牌和调研方向,再由不同方向的 subagent 执行专项调研。

---

## 2. 目标与非目标

### 2.1 产品目标

- 帮助用户从模糊输入快速形成明确的 Research Brief。
- 让用户在正式调研前确认主体、竞品品牌和调研方向。
- 将不同调研方向拆给对应 subagent,避免 main agent 承担所有分析。
- 输出有优先级、可展开证据包、有明确证据边界的调研结果。
- 在两个关键节点引入人工判断:选择进一步查看的品牌、选择重点关注的品牌内容。

### 2.2 用户价值

- 降低用户从“一个词”开始调研的思考成本。
- 提高搜索效率,减少无关结果。
- 帮助用户发现潜在竞品、内容机会和营销表达。
- 让用户能判断每个结论是否可信、证据是否充分。

### 2.3 非目标

- 不在预检索阶段做完整证据链。
- 不要求预检索阶段展示或记录来源。
- 不做全平台通用数据中台,MVP 以小红书为主。
- 不要求完整解析小红书商品详情和店铺详情。
- 不把 F003 的品牌/内容筛选强行接入现有 `app/v2/decision`。

---

## 3. 用户与场景

### 3.1 目标用户

- 品牌运营:关注竞品动作、内容打法、产品表达和用户反馈。
- 内容策略人员:关注选题、标题、卖点、评论需求和 UGC 表达。
- 增长/市场人员:关注新品牌、新产品、新品类和社群互动。

### 3.2 典型输入

```text
品类/SKU: 徒步短裤、露营灯、越野跑背心
品牌: Satisfy Running、Salomon、Arc'teryx
场景/需求: 夏季徒步、防晒通勤、轻量越野跑
```

### 3.3 典型场景

- 用户输入“徒步短裤”,想研究竞品品牌、产品卖点和小红书爆文内容。
- 用户输入“Satisfy Running”,想研究该品牌在小红书上的竞品、品牌活动和 UGC 社群互动。
- 用户输入“越野跑背心”,想发现潜在品牌、新品趋势和用户评论痛点。

---

## 4. 核心流程

### 4.1 总流程

```text
用户输入 Research Seed
  ↓
LLM 轻量预检索和主体识别(<=10 秒)
  ↓
返回 Research Brief Checklist
  ↓
用户确认主体、竞品 tag、调研方向
  ↓
系统生成 Research Plan
  ↓
main agent 按调研方向分发给 subagent
  ↓
subagent 执行专项调研并返回结构化结果
  ↓
系统汇总、生成 Decision Card、生成 Evidence Bundle
  ↓
人工决策点 1:选择进一步查看的品牌
  ↓
系统围绕选中品牌继续调研内容
  ↓
人工决策点 2:选择重点关注的品牌内容
  ↓
输出最终洞察和后续建议
```

### 4.2 预检索阶段

预检索阶段用于明确用户意图,不是正式调研。

约束:

- 等待时间最多 10 秒。
- 不要求展示来源。
- 不要求记录 Evidence Bundle。
- 不输出最终结论。
- 只输出供用户确认的 Research Brief Checklist。

预检索需要返回:

```text
1. 主体识别确认
2. 潜在竞品/相关品牌 tag
3. 用户可手动输入的竞品 tag
4. 建议调研方向
5. 可选的自然语言补充输入
```

示例:用户输入 `Satisfy Running`

```text
主体识别:
Satisfy Running 可能是一个法国小众户外/跑步品牌,专注于越野跑文化、跑步服饰和社区表达。
请确认这是否是你要研究的主体。

潜在竞品/相关品牌:
[District Vision] [Tracksmith] [Ciele] [Salomon] [On Running] [Nike Trail]

手动补充品牌:
输入框

建议调研方向:
[品牌活动] [产品营销] [UGC 社群互动] [小红书内容表现] [新品/产品线] [品牌视觉与叙事]
```

示例:用户输入 `徒步短裤`

```text
主体识别:
徒步短裤更可能是一个户外服饰品类/SKU,不是品牌。
可能关联场景包括夏季徒步、轻量户外、速干、防晒和通勤户外风。

潜在竞品/相关品牌:
[迪卡侬] [凯乐石] [伯希和] [Patagonia] [Arc'teryx] [Nike ACG]

手动补充品牌:
输入框

建议调研方向:
[高增长关键词] [产品卖点表达] [竞品品牌] [小红书爆文内容] [用户评论痛点] 
```

### 4.3 正式调研阶段

用户确认 Research Brief 后,系统生成 Research Plan,并按调研方向调用对应 subagent。

main agent 职责:

- 整理用户确认后的 Research Brief。
- 生成 Research Plan。
- 调度各方向 subagent。
- 合并结果。
- 处理 priority、evidence boundary 和最终展示。

subagent 职责:

- 只处理一个相对明确的调研方向。
- 输出结构化发现、候选项、证据需求和缺失信息。
- 不直接决定最终展示 priority。

---

## 5. 功能需求

### 5.1 Research Seed 输入

用户输入一个原始调研词。

字段:

```text
seed_text       必填,用户输入
seed_type       系统识别,品类/SKU/品牌/产品/场景/未知
user_note       可选,用户补充目标
```

要求:

- 支持中文、英文和混合输入。
- 支持品牌名、品类词、SKU、场景词。
- 输入不清晰时,预检索阶段必须提示用户确认。

### 5.2 Research Brief Checklist

系统在预检索后返回一个 checklist,供用户确认。

Checklist 包含:

```text
subject_confirmation
subject_structure
competitor_tags
custom_competitor_input
research_directions
custom_research_question
```

`subject_structure` 是 Pre-research LLM 对任意用户主题的通用结构化结果，
不是固定业务词表映射。它至少包含：

```text
canonical_subject
subject_type              品牌/品类/SKU/场景/趋势/未知
core_entities             必须命中的核心对象
research_intents          用户希望研究的动作或问题
context_modifiers         季节、人群、渠道、场景等限定
synonym_groups            核心对象的别称、中英文名称和同义表达
```

Brief 以紧凑行展示结构，例如“核心对象：防晒服饰｜意图：穿搭｜场景：夏季”。
用户必须能够在正式采集前确认或修改结构；修改后重新生成并确认结构化结果。

要求:

- 主体识别必须可确认或修改。
- 主题结构不得依赖预置品类词表；未知输入由 Pre-research LLM 生成受 schema
  约束的结构化结果，并在用户确认后冻结。
- 核心对象、意图和场景修饰必须分开；意图或场景词不得代替核心对象。
- 竞品 tag 可以多选。
- 用户可以自行输入竞品 tag。
- 调研方向可以多选。
- 用户可以补充自然语言调研目标。

### 5.3 调研方向

首批调研方向:

```text
高增长关键词
产品卖点表达
竞品品牌
品牌活动
产品营销
UGC 社群互动
小红书内容表现
用户评论痛点
新品/产品线
品牌视觉与叙事
新品类机会
```

每个方向后续应绑定一个专项 subagent 或 subagent 能力。

示例:

```text
品牌活动        -> BrandActivityResearchAgent
产品营销        -> ProductMarketingResearchAgent
UGC 社群互动    -> UGCCommunityResearchAgent
竞品品牌        -> CompetitorDiscoveryAgent
用户评论痛点    -> CommentInsightAgent
```

### 5.4 Research Plan

用户确认 checklist 后,系统生成 Research Plan。

Research Plan 至少包含:

```text
confirmed_subject
subject_type
selected_competitors
custom_competitors
selected_directions
custom_research_question
subagent_tasks
priority_policy
evidence_boundary_policy
evidence_requirements
```

Research Plan 是正式调研的输入。

### 5.5 第一轮调研结果

系统输出与用户选择方向相关的结果。

结果类型:

```text
keyword_clusters
marketing_phrases
brand_candidates
product_signals
recommended_contents
ugc_insights
campaign_signals
ecommerce_enrichment
```

每个结果项必须包含:

```text
summary
priority_label
evidence_state
evidence_grade
claim_scope
evidence_count
representative_sources
missing_evidence
needs_more_evidence
evidence_bundle_id
```

### 5.6 人工决策点 1:选择品牌

用户从品牌候选中选择需要进一步查看的品牌。

决策值:

```text
selected
rejected
watchlist
```

要求:

- `selected` 默认进入下一阶段完整深入调研。
- `watchlist` 进入观察池,可轻量补证,不默认消耗完整深入调研资源。
- `rejected` 不进入下一阶段,但保留为反馈信号。
- 用户选择前必须能查看该品牌的 priority label、evidence state / grade、claim scope 和证据摘要。

### 5.7 品牌内容深入调研

系统围绕用户选中的品牌继续调研。

可覆盖:

- 品牌账号。
- 品牌近期笔记。
- 高互动内容。
- UGC 内容。
- 评论需求点。
- 产品卖点。
- 活动和联名。
- 商品卡、价格、店铺入口等可选电商线索。

### 5.8 人工决策点 2:选择重点内容

用户从品牌相关内容中选择重点关注的内容。

决策值:

```text
selected
rejected
watchlist
```

内容推荐理由:

```text
high_engagement
strong_product_signal
strong_comment_signal
representative_angle
new_launch_signal
marketing_phrase_source
competitor_positioning
ugc_community_signal
```

### 5.9 最终洞察

最终洞察应围绕用户选择的调研方向组织。

必须包含:

- 核心发现。
- priority 较高的品牌、内容、关键词或产品信号。
- 每个结论的 evidence state / grade 和 claim scope。
- 每个结论的证据包。
- 用户已选择的品牌和重点内容。
- 缺失证据或冲突证据。
- 下一步建议。

---

## 6. Priority / Evidence Boundary / Decision Card

### 6.1 Priority

Priority 用于决定用户先看什么、先验证什么、先投入什么。

Priority 不等于内容质量、爆款概率或转化概率。它只回答:

```text
这个 finding 是否值得在其他合格 finding 之前优先查看或行动?
```

Priority 输出为 label,不输出通用加权分:

```text
high_priority
high_potential_needs_more_evidence
useful_but_lower_priority
evidence_backed_reference
do_not_prioritize
```

### 6.2 Evidence Boundary

Evidence Boundary 用于表达当前证据允许系统把 finding 说到什么程度。

```text
invalid
case_only
signal
partially_supported
verified
```

Evidence Boundary 必须表达“证据允许说什么 / 不允许说什么”,而不是“值不值得看”。判断至少需要考虑:

```text
evidence support       是否有足够证据直接支撑结论
missing evidence       是否缺少阻塞性证据
conflicting evidence   是否存在未解释冲突
citation grounding     证据包是否能支撑具体 claim
source diversity       是否来自多个相对独立来源
```

证据不足的结果可以作为线索展示,但不得被包装成确定结论。最终洞察必须明确展示允许结论、不允许结论、缺失证据和冲突证据。

### 6.3 Decision Card

最终可展示结果必须以 Decision Card 交付:

```text
priority
evidence
claim_scope
next_action
evidence_bundle_id
```

- 高潜力但证据少的结果可以优先展示,但必须标记为 `high_potential_needs_more_evidence`。
- 证据充分但与本轮方向不相关的结果不应成为 `high_priority`。
- 最终展示必须同时展示 priority label、evidence state / grade、claim scope 和 next action。
- 具体 Decision Card payload shape 由
  [F003_content_research_schema_domain_objects.md](./F003_content_research_schema_domain_objects.md)
  定义。

---

## 7. 交互要求

### 7.1 Creator Workbench 入口

F003 不新增独立一级页面。内容调研能力应嵌入 Creator Workbench,作为对话输入区上的一个能力模式。

入口形态:

```text
Creator Workbench
  ↓
对话输入区能力按键
  ↓
选择「内容调研」
  ↓
输入品类/SKU/品牌/场景词
  ↓
进入 Content Research workflow
```

能力按键示例:

```text
普通对话
内容调研
笔记生成
策略复盘
选题库
```

设计原则:

- 内容调研是 Creator Workbench 内的 workflow mode,不是独立工具页。
- 同一个对话线程内可以发起、确认和推进调研任务。
- 用户在对话里输入 seed,系统在对话流里返回 Research Brief Checklist。
- 输入框仍保持可用,用户可以继续补充约束或追问。
- workflow 控制和状态不应全部塞进聊天气泡。

### 7.2 页面布局

Content Research 在 Creator Workbench 中采用三栏布局:

```text
左侧:会话列表
中间:对话流 + Research Brief Checklist + 调研结果摘要
右侧:Workflow 状态面板
```

中间对话流承载:

- 用户输入。
- LLM 预检索说明。
- Research Brief Checklist。
- 第一轮调研结果摘要。
- 最终洞察摘要。
- 用户继续补充的自然语言约束。

右侧 Workflow 状态面板承载:

- 当前 workflow 阶段。
- 已确认 Research Brief。
- 已创建的 subagent tasks。
- 调研方向和 subagent 映射。
- 结果展示规则,如 priority label、evidence state / grade、claim scope。
- 后续可扩展任务控制,如暂停、继续、取消、补采。

设计原则:

- 对话区负责“沟通和确认”。
- 右侧面板负责“状态和结构化流程”。
- 避免把多阶段 workflow 状态全部放进聊天气泡,导致阅读负担过高。
- 移动端或窄屏可以隐藏右侧面板,将 workflow 状态折叠为顶部状态条。

### 7.3 预检索等待

- 用户提交 seed 后,进入最长 10 秒的预检索等待。
- 超过 10 秒仍未完成时,返回兜底 checklist。
- 兜底 checklist 应允许用户手动确认主体、输入竞品、选择方向。

### 7.4 Checklist 交互

- 主体识别区域支持“确认/修改”。
- 竞品 tag 支持多选。
- 竞品区域必须提供输入框,允许用户自行输入品牌。
- 调研方向支持多选。
- 自定义调研问题为可选输入。

### 7.5 结果展示

列表页展示:

```text
summary
priority_label
evidence_state
evidence_grade
claim_scope
top_sources
missing_evidence_count
action
```

详情页展示:

```text
完整 Evidence Bundle
supporting facts
conflicting facts
missing evidence
lineage
source links
user decisions
```

### 7.6 人工决策交互

两个人工决策点都应在结果列表中完成,而不是跳转到单独决策页面。

品牌筛选:

```text
selected      继续深入调研
watchlist     保留观察
rejected      不进入下一阶段
```

内容筛选:

```text
selected      重点关注
watchlist     保留观察
rejected      不进入最终重点内容
```

每个可决策项都需要展示:

- Priority label。
- Evidence state / grade。
- Claim scope。
- 代表来源数量。
- 缺失证据提示。
- 展开 Evidence Bundle 的入口。

---

## 8. 数据与埋点

### 8.1 关键数据对象

```text
ResearchSeed
ResearchBrief
ResearchPlan
ResearchDirection
SubagentTask
ObservationEvent / Trace
EvidenceRecord
EvidenceLineage
EvidenceBundle
ResearchResultSnapshot
HumanDecision
PriorityPolicy
EvidenceBoundaryPolicy
BenchmarkCase / BenchmarkRun
```

其中 `ObservationEvent / Trace` 记录任务怎么跑,`EvidenceRecord / EvidenceLineage / EvidenceBundle` 记录拿到了什么和如何支撑结论,`ResearchResultSnapshot` 记录一次不可变的结果快照。

### 8.2 核心埋点

```text
research_seed_submitted
presearch_started
presearch_completed
presearch_timeout
brief_confirmed
competitor_tag_selected
competitor_tag_added
research_direction_selected
research_plan_created
subagent_task_started
subagent_task_completed
brand_decision_submitted
content_decision_submitted
evidence_bundle_opened
final_insight_viewed
```

### 8.3 关键指标

- 预检索完成耗时 P95 <= 10 秒。
- 用户确认 Research Brief 的完成率。
- 用户手动添加竞品 tag 的比例。
- 用户选择调研方向的数量分布。
- 第一轮结果中有证据包的比例。
- 用户打开 Evidence Bundle 的比例。
- 用户完成品牌选择的比例。
- 用户完成内容选择的比例。

### 8.4 云端执行数据与静默评测采集

Content Research 需要预留 cloud sync 字段,但 P0 不实际上传云端。Live Evaluation 默认开启,不需要用户逐次同意;后续接入 Cloud Research Runtime 后,运行数据可进入云端执行、评测和质量分析链路。

P0 规则:

```text
保留 cloud-ready 字段
不实际上传 evidence / observation / trace / benchmark 数据
不阻塞本地 workflow 执行
```

后续 Cloud / Live Evaluation 规则:

```text
Live Evaluation 默认开启
不要求用户逐次显式同意
evidence / observation / trace summary / benchmark metrics 均可上传
用户手动补充的竞品、链接、观察说明均可进入 workspace cloud
debug / benchmark mode 可保存更完整 trace
```

---

## 9. 异常与边界

### 9.1 预检索失败

当预检索失败或超时时:

- 返回通用 checklist。
- 不阻塞用户手动输入竞品和选择方向。
- 标记 `presearch_timeout` 或 `presearch_failed`。

### 9.2 主体识别不确定

当系统无法判断输入类型时:

- 展示多个可能主体。
- 要求用户选择或手动修改。
- 不直接进入正式调研。

### 9.3 竞品 tag 不准确

竞品 tag 只是预检索建议,不作为最终结论。

- 用户可以不选择任何推荐 tag。
- 用户可以手动输入竞品。
- 正式调研阶段仍可发现新的品牌候选。

### 9.4 数据采集不完整

正式调研阶段遇到采集失败、限流、登录态或解析失败时:

- 失败也需要进入证据记录。
- 结果中应展示缺失证据。
- 不得把证据不足的结果包装成确定结论。
- 系统必须区分“没有发现”和“登录态失效、限流、权限不足或解析失败导致没有拿到”。
- 当小红书登录态失效或即将失效时,用户应看到明确提示,并知道哪些结果受影响。
- 小红书登录态复用现有机制;P0 不做自动续期。
- 前端不提供独立登录态管理入口。用户第一次使用时,页面顶部居中弹出提示,引导用户先登录小红书网页端。

### 9.5 证据不足与冲突证据

当证据不足、证据冲突或关键来源缺失时:

- 结果可以作为线索保留。
- evidence state / grade 必须降级。
- 结果中必须展示 missing evidence 或 conflicting evidence。
- 最终洞察不得把证据不足线索写成确定判断。

---

## 10. 验收标准

### 10.1 P0 验收

- 用户输入品类/SKU/品牌后,系统能在 10 秒内返回 Research Brief Checklist 或兜底 checklist。
- Checklist 包含主体识别、潜在竞品 tag、手动竞品输入框和调研方向。
- 用户可以确认或修改主体。
- 用户可以选择推荐竞品 tag,也可以自行输入竞品。
- 用户可以选择一个或多个调研方向。
- 用户确认后,系统能生成 Research Plan。
- Research Plan 能按方向拆分 subagent tasks。
- Presearch 必须进入 Observation Layer,用于恢复状态、查看耗时和记录超时。
- Workflow 状态面板或 Trace 视图能展示当前阶段、已创建任务和异常状态。
- 用户提交 seed 起必须创建 workflow run,presearch 是第一个 workflow stage。
- P0 必须支持真实小红书采集,但不要求完成全部正式调研 source kind。

### 10.2 P1 验收

- subagent 能按方向返回结构化调研结果。
- 第一轮结果包含 priority label、evidence state / grade、claim scope、next action 和 evidence bundle id。
- 正式调研结果必须写入 EvidenceRecord / EvidenceBundle,并能从结果项展开查看证据包。
- P1 Source Adapter 至少支持 `search_result`、`note_detail`、`comment`、`topic_or_keyword_page`。
- 失败、限流、登录态失效或解析失败必须以 missing evidence 或 failure evidence 表达。
- 用户可以选择进一步查看的品牌。
- 系统只对 `selected` 品牌默认继续完整深入调研;`watchlist` 品牌进入观察池或轻量补证。
- 用户可以选择重点关注的品牌内容。
- 最终洞察能围绕用户选择的调研方向展示。
- 最终洞察中的每个核心结论都必须能追溯到 evidence bundle,或明确标记为 signal / case_only / 缺失证据。

### 10.3 体验验收

用户应能完成以下路径:

```text
输入“徒步短裤”
  ↓
10 秒内看到主体识别、潜在竞品和调研方向
  ↓
选择竞品品牌和“产品卖点表达 / 竞品品牌 / 用户评论痛点”
  ↓
系统生成专项调研任务
  ↓
看到带 priority、evidence boundary 和证据包的第一轮结果
  ↓
选择 2-3 个品牌继续查看
  ↓
看到品牌内容候选
  ↓
选择重点关注的内容
  ↓
得到最终洞察
```

---

## 11. 后续规划

- 将调研方向和 subagent 能力配置化。
- 根据用户选择行为优化预检索推荐。
- 支持更多平台来源,如 Instagram、淘宝。
- 引入更细的行业词库和品牌别名识别。
- 支持团队协作和历史 Research Brief 复用。
- 建立 ContentResearchBench,评估 brief 质量、检索覆盖、证据支撑、priority / evidence-boundary 校准和用户决策帮助度。
