# F003 Evidence Admission Design

## Status

Proposed design, agreed during evidence-admission review. This document is the
implementation contract for the evidence-admission stage; it does not claim the
behaviour is implemented yet.

## Problem

The current workflow treats a successful search card as an analysable fact.
Titles, empty descriptions, body text, and engagement counts can all enter the
same fact and claim path. That makes a fact ID citation look like evidence
support even when the source cannot support the claim.

## Decision

F003 will use direction-contract-driven collection rather than storing a full
raw copy of every discovered note.

```text
direction data contract
  -> candidate discovery
  -> required-field projection collection
  -> directional evidence packet
  -> fact / quote extraction
  -> structured analysis
  -> checkpointed recovery
```

### Direction data contract

Each `ResearchDirection` must declare a versioned executable contract:

```text
required_note_fields
optional_note_fields
required_comment_fields
sample_policy
claim_rules
analysis_schema_version
resume_contract_version
```

Missing required fields prevent a formal conclusion for that direction. Missing
optional fields must remain visible as a limitation.

### Directional evidence packet

The persistent unit is a minimal, direction-specific projection, not an
unbounded raw note archive. It contains:

```text
source_ref: platform, note_id, note_url
field_projection: only fields requested by the contract
field_availability: present / missing / not_requested / unavailable
retrieval_context: query, rank, sort, collected_at, contract_version
field_projection_hash
```

At a minimum, all packets retain source identity, the fields used by a claim,
field availability, the retrieval context, and a stable hash. This preserves
auditability and incremental recovery without reprocessing an entire note.

### Note and comment relationship

Notes and comments are separate atomic evidence records. A comment records its
parent note reference (`parent_evidence_id` or equivalent captured-from-note
lineage) so claims can distinguish note text from user comments. The direct
parent reference is for querying; lineage is for auditability.

### Collection policy

All selected candidates first collect the note-detail fields required by their
direction. Comment collection is then determined by the contract:

- `ugc_community` and `comment_insight`: comments are required;
- product marketing, content performance, competitor discovery, brand activity,
  and keyword growth: comments are optional unless the requested claim depends
  on user reaction or objection;
- every comment collection records scope, ordering, cap, completeness, and any
  failure so a partial comment sample cannot be presented as a population view.

### Recovery contract

A direction uses a stage-level recovery state machine, not one opaque task
checkpoint. Each stage persists its input fingerprint, output references,
status, failure reason, retry count, and any budget reservation references:

```text
collect -> packet -> facts -> admission -> reconcile -> aggregate
        -> compose -> faithfulness
```

Recovery reuses all stages whose fingerprint is unchanged and re-enters at the
first failed or invalidated stage. An admission, composition, or faithfulness
failure must never re-fetch an external source. Observation events are telemetry
only; `StageCheckpoint` is the single source of truth for recoverable progress.

## Contract field catalog

Every directional packet retains this universal recovery envelope. It is not a
full raw-note archive.

```text
platform, note_id, note_url, retrieval_query, retrieval_rank,
retrieval_sort, collected_at, source_payload_hash, contract_version
```

Available note fields are grouped as follows:

```text
identity: author_id, author_name, author_profile_url
publication: published_at, ip_location, note_type
content: title, title_is_explicit, body_text, tags, image_urls,
         video_cover_url, video_url
engagement: liked_count, collected_count, comment_count, share_count,
            measured_at
```

Available comment fields are:

```text
comment_id, parent_comment_id, author_id, author_name, body_text,
like_count, published_at, ip_location, image_urls, reply_relation
```

## Direction contracts, v1

Every contract field is a collection requirement: the collector must attempt to
retrieve it. The field class controls failure handling instead:

- **blocking fields**: failure leaves the direction incomplete and prevents a
  formal directional conclusion;
- **warning fields**: failure is persisted as a warning and does not stop the
  current task, but must remain visible in the analysis limitations.

An absent comment contract means comment text is not collected for that
direction's baseline conclusion.

| Direction | Blocking note fields | Warning note fields | Blocking comment fields | Permitted baseline claims |
| --- | --- | --- | --- | --- |
| Product marketing | title, body_text, tags, note_type, engagement snapshot, measured_at | published_at, ip_location, image/cover metadata | — | quoted observations of product-value expression, use context, target-audience framing, and message angle in the sampled notes |
| Content performance | title, body_text, note_type, published_at, engagement snapshot, measured_at, cover/media metadata | tags, ip_location | — | observed high-engagement samples and their visible content format; no causal-performance claim |
| Competitor discovery | title, body_text, author_name, tags, engagement snapshot, measured_at | published_at, ip_location | — | named competitors and their visible content-expression patterns in the sampled notes |
| UGC community | title, body_text, author_name, published_at | engagement snapshot, tags | body_text, published_at, like_count, reply_relation | observed user discussion scenarios, interaction patterns, and language in the sampled thread set |
| Comment insight | parent note title, parent note body_text | parent engagement snapshot | body_text, published_at, like_count, reply_relation | explicit user questions, objections, needs, or repeated language in sampled comments |
| Brand activity | title, body_text, published_at, tags, note_type, engagement snapshot, measured_at | ip_location, media metadata | — | visible campaign, launch, collaboration, and dissemination signals in the sampled period |
| Keyword growth | title, body_text, tags, published_at, engagement snapshot, measured_at | author_name, ip_location | — | keyword and expression patterns in the sampled period; no growth claim without historical comparison data |

### Mandatory boundary rules

- A title can support a title/topic fact, but cannot support a body-expression,
  user-motivation, or campaign-effect claim by itself.
- Engagement values support only a measured engagement observation at the saved
  `measured_at`; they do not prove conversion, preference, or causality.
- A comment claim requires comment text and a parent-note reference; comment
  count alone is not comment evidence.
- `keyword_growth` may report a sampled-period keyword pattern, but needs a
  defined historical baseline and comparison rule before it may use “growth”.
- Missing required fields result in an explicit incomplete/insufficient state,
  not a generic specialist conclusion.

This contract is a baseline, not a claim that every field is already obtainable
through the active adapter. Adapter capability comparison follows after the
contract has been reviewed and accepted.

## Historical baseline for keyword growth

A historical baseline is a comparable earlier observation window, not a vague
claim that a keyword appears frequently today. A valid comparison records:

```text
same platform and direction contract
same or explicitly versioned query expansion and sample policy
current period [T1_start, T1_end]
baseline period [T0_start, T0_end]
eligible-note count and keyword-bearing-note count in each period
collection timestamps and any missing-field warnings
```

The basic observable is keyword share among eligible notes in each window:

```text
keyword_share = notes containing the keyword / eligible notes in the window
```

Only after comparing the current and baseline shares may the report call a
keyword a growth signal. Ranking by likes alone is not a valid population growth
measure; it can at most describe a high-engagement sample. Engagement changes
also require an age-normalized comparison because older notes have had more time
to accumulate interactions.

### On-demand recent-versus-six-month policy

F003 does not require a continuously running background service. Every user
initiated keyword research run obtains its comparison windows on demand. The
contract uses a recent window and a six-month reference window:

```text
1. Lock the query set, synonym list, source, field contract, and sample policy.
2. Discover candidates with the platform's recent-week and recent-six-month
   filters, using non-popularity discovery order.
3. Retrieve note details and split eligible notes by published_at:
   recent = [today - 7d, today)
   reference = [today - 6mo, today - 7d)
4. Apply the same eligibility and per-author cap to both windows. Sampling caps
   are recorded separately because the windows have different durations.
5. Persist an on-demand WindowSnapshot with query-plan hash, collection time, eligible
   note IDs, field warnings, and keyword counts.
6. Compare recent keyword share to the six-month reference keyword share and
   report the result as a recent-versus-reference signal.
```

The reference window excludes the most recent week, so the two samples do not
overlap. Its purpose is not to estimate a platform-wide growth rate; it is an
on-demand indication of whether a keyword is disproportionately visible in the
recent week versus the wider recent history. The report must label it as
`近一周相对近半年信号`, state both eligible sample counts, and disclose any
collection bias.

The active adapter exposes only coarse relative time filtering. It must collect
note details and use `published_at` to exclude overlap. If the source does not
return enough older eligible notes under the six-month query, the system reports
`reference_window_insufficient` rather than a growth signal.

## Sample policy contract

Each direction contract also includes a versioned `SamplePolicy`:

```text
query_set and query_plan_hash
candidate_limit_per_query
candidate_sort and time_window
detail_fetch_target / detail_fetch_cap
minimum_eligible_note_count
max_notes_per_author
query_coverage_rule
comment_note_target / comment_note_cap
comments_per_note_target / comments_per_note_cap
comment_order and reply-depth policy
stop_condition
```

### Common rules

- Candidate discovery is a search-stage operation; a candidate is not evidence
  until the direction's detail fields have been collected.
- Detail selection is deterministic and persisted: relevance to the locked
  query, field availability, query coverage, and author diversity are recorded
  as selection reasons.
- Popularity sort is allowed for the content-performance direction but not as
  the sole discovery sort for trend, keyword-growth, or user-need claims.
- `max_notes_per_author` prevents a prolific creator from being mistaken for a
  cross-user pattern.
- A partially collected comment set records its cap, ordering, reply depth, and
  completeness; it is never presented as all comments.
- Every result states its sample scope: queries, time window, eligible note
  count, author count, detail and comment completeness, and selection policy.

### Initial operational defaults

These are safety caps rather than evidence thresholds and can be changed by a
versioned policy:

| Policy field | Initial default |
| --- | --- |
| Candidate limit | 20 per locked query |
| Detail fetch cap | 30 eligible notes per direction |
| Per-author cap | 2 notes per author per direction |
| Detail minimum for note-only conclusion | 12 eligible notes, unless the claim is explicitly case-level |
| Comment-note cap | 10 eligible notes for comment-required directions |
| Comment cap | 50 top-level comments per selected note, plus replies under the recorded depth policy |
| Comment minimum for comment conclusion | 30 eligible comments from at least 5 distinct authors |

The values are not claims of statistical representativeness. They are bounded
collection and disclosure rules for a first evidence-safe implementation.

## 2026-07-14 agreed operating decisions

The following decisions clarify how the v1 contracts operate. They are agreed
product and implementation constraints, not claims that the current runtime
already implements them.

The seven-direction blocking-field, warning-field, and comment-field table in
[Direction contracts, v1](#direction-contracts-v1) remains the single
canonical field contract. The operating rules below apply that table at runtime
and must not duplicate or silently change it.

### User intent and internal research intents

Presearch remains the only user confirmation step for research scope: the user
confirms what to research and which directions to run. A directional specialist
may then create multiple internal `ResearchIntent` records to make execution
testable, but these are not a second user-facing direction-selection step.

Each internal intent declares its question, allowed claim types, field
requirements, time window, and coverage condition. A `QueryGroup` contains one
or more locked queries. An LLM may propose queries, but policy validates
deduplication, platform suitability, sorting, and time window before the query
set is locked. Every query retrieves at most 20 candidate cards. The persisted
query plan and its hash must identify every later candidate and evidence
packet.

Candidates from all query groups for a direction are deduplicated into one
shared pool by canonical note identity. Each candidate retains all matching
intent and query identifiers; one selected note is fetched once and may support
multiple intents. A later gap-fill query is a new recorded query group rather
than an untracked change to the original sample definition.

### Specialist research-contract template

Every specialist is specified before adapter or storage implementation with the
following contract. The template separates the expert's business behaviour from
the per-run execution snapshot.

```text
A. Specialist identity: decision the user can make
B. Scope: research object and explicit exclusions
C. Internal intents: question, allowed claim, prohibited claim
D. Evidence contract: fields and source fields that can support each claim
E. Sampling plan: query construction, selection, and collection limits
F. Analysis and quality gates: facts, citations, case/repeated/provisional states
G. Output contract: conclusion, evidence, scope, limitations, and next action
```

Global invariants apply to every specialist: a candidate is not formal evidence;
every claim exposes a quote, evidence id, field path, and source link; missing
data is disclosed rather than filled by an LLM; one sample is a case-level
observation; and an interrupted run reuses its existing data unless the user
changes research intent, direction, or enters a new deep-research run.

### Claim admission and report evaluation contract

The final report is not the primary object of evidence evaluation. A specialist
first produces structured claim candidates; a deterministic evaluator decides
which claims may be presented and at what state; an LLM or renderer then writes
user-facing text from those admitted claims. This prevents a fluent summary from
silently becoming stronger than its evidence.

```text
evidence packets + facts
  -> ClaimCandidate[]
  -> ClaimAdmissionEvaluator
  -> AdmittedClaim[] + ClaimAdmissionDecision[] + DirectionResultDecision
  -> report writer / renderer
  -> ReportFaithfulnessEvaluator
  -> final report
```

Each `ClaimCandidate` must carry the following minimum data before it can be
evaluated:

```text
claim_id, direction_id, intent_id, claim_type, statement, scope,
requested_state, evidence_refs[], quote_refs[] (evidence_id, field_path,
quote, text_start, text_end, source_text_hash, source_url), proposed_metrics,
limitation_refs[]
```

`ClaimAdmissionEvaluator` is deterministic and policy-driven. It must:

1. allow only the direction and intent's declared claim types, and reject
   prohibited claim types;
2. validate every evidence reference, source URL, field path, quote span, and
   required parent-note lineage for a comment claim;
3. recompute eligibility, unique evidence/note/author counts, sample scope,
   and any window or distribution metric from persisted packets rather than
   trusting LLM-proposed numbers;
4. apply blocking/warning field policy, sample caps, research-depth limits,
   claim thresholds, and keyword-window comparability rules;
5. assign exactly one claim evidence state (`case_level`,
   `repeated_observation`, `provisional`, or `insufficient_evidence`) and
   downgrade rather than promote when a gate is not met; then independently
   compute whether the direction is a `formal_directional_result` from its
   eligible-sample and all-claim gates;
6. emit structured reason codes, required disclosures, and a recovery action
   for every rejection, downgrade, or incomplete state; and
7. persist the policy snapshot/hash and computed metrics so the decision can be
   reproduced after a retry or policy change.

The evaluator does **not** decide whether a business recommendation is wise,
infer missing evidence, or use a persuasive summary as evidence.

Its output is an auditable `ClaimAdmissionDecision`:

```text
claim_id, decision (admitted / downgraded / rejected), claim_evidence_state,
satisfied_rule_ids, violated_rule_ids, computed_metrics, evidence_refs,
required_disclosures, recovery_action, policy_snapshot_hash
```

`ReportFaithfulnessEvaluator` evaluates the report text only after claim
admission. It must verify that every material conclusion maps to an admitted
`claim_id`; that quoted text and numeric values match the cited evidence and
computed metrics; that the report does not widen sample scope, introduce new
entities/comparisons/causality, or upgrade a state; and that all required
limitations are displayed. A semantic LLM audit may flag possible paraphrase or
scope violations, but it cannot admit a claim or override deterministic rules.

### Shared user-facing report contract

All directional outputs use the same layered report shape. The default view is
a concise decision-oriented report; evidence is expandable rather than hidden
in prose.

```text
[Direction] | [direction result state] | [research depth]
Scope: locked queries; time window; sort/selection policy; eligible notes;
authors; comment completeness where applicable; collection time.

Admitted observations
  Claim card: statement limited to its sample scope
  State + evidence counts + eligible sample counts
  Expand: direct quotes, evidence IDs, field paths, source links, and for
          comments their parent-note context and reply relation
  Suggested next action (never an asserted outcome)

Leads and incomplete items
  provisional / case-level material, explicit gap and recovery action

Limitations
  required field warnings, sampling/selection bias, and unsupported questions
```

Every claim card must visibly show `claim_evidence_state`, `claim_id`, evidence
count, distinct-author count when relevant, and the exact scope label. A card
may only use the language permitted by its claim state: a `case_level` card says
“个案观察”; a `repeated_observation` card says “本样本中重复出现”; and a
formal result remains limited to the recorded sample. `provisional` and
`insufficient_evidence` cards must never be visually or linguistically styled
as conclusions.

### Product-marketing specialist contract, v1

#### A. 专家身份

- **方向名称：**产品营销研究（`ProductMarketingResearchAgent`）
- **帮助用户做什么决策：**决定哪些产品表达、受众框架和使用场景值得在小红书内容中测试。

#### B. 研究对象与范围

| 研究什么 | 明确不研究什么 |
| --- | --- |
| 样本笔记中的产品价值表达、使用场景、目标受众框架与叙事角度 | 真实产品功效、实际购买者、转化、保证的营销效果；评论痛点（归评论洞察专家） |

#### C. 内部 intent

| Intent | Question | Allowed observation | Prohibited claim |
| --- | --- | --- | --- |
| `value_proposition` | How do sampled notes express product value, differentiation, or benefit? | Quoted value-expression patterns | The benefit is true or proven effective |
| `usage_context` | In which explicit scenes or problems is the product mentioned? | Quoted use contexts and problems | The scene is a dominant user need |
| `target_audience` | Which identities or life stages do notes explicitly address? | Content's target-audience framing | Actual buyer or platform-user profile |
| `message_angle` | Which narrative angle structures the expression? | Experience, comparison, ingredient, gifting, or expert-endorsement framing | The angle necessarily performs better |

#### D. 证据合同

| 证据项 | Blocking | Warning | 可支撑的 claim / 边界 |
| --- | --- | --- | --- |
| 笔记字段 | `title`、`body_text`、`tags`、`note_type`、互动快照、`measured_at` | `published_at`、`ip_location`、图片/封面元数据 | `body_text` 支撑价值与场景；`title` 只支撑标题框架，不能证明真实受众；标签、类型、互动和采集时间仅描述样本与选择上下文 |
| 评论字段 | — | — | 基线不采集评论，不能据此输出评论痛点 |
| 来源与 claim | 笔记标题、正文、标签及记录的元数据 | — | 只能输出样本中被引用的产品价值、使用语境、受众框架和表达角度；互动不证明偏好、转化或表达效果；缺失 warning 必须列入限制 |

#### E. 采样计划

Every query returns at most 20 candidates. Query candidates are generated from
the confirmed subject, aliases, category/product terms, user question, and the
relevant intent, then validated and locked in the query plan.

| Research depth | Query plan | Candidate ceiling | Detail ceiling |
| --- | --- | ---: | ---: |
| Quick exploration | One base query for each of the four intents | 80 | 8 |
| Standard research | Four base queries plus two locked disambiguation or coverage queries | 120 | 18 |
| Deep research | Four base queries; after the first coverage check, up to two recorded gap-fill queries | 120 | 30 |

Base query shapes are: product/category plus a benefit or problem
(`value_proposition`); plus a scene/problem (`usage_context`); plus an explicit
identity/life-stage term (`target_audience`); and plus a known expression entry
such as comparison, experience, ingredient, or gifting (`message_angle`).
Candidate selection prioritizes intent coverage, relevance to the confirmed
question, per-author cap, sort/time-window policy, and detail-field
availability. Comment collection is not part of the baseline plan.

#### F. 分析与质量 gate

The fact extractor may emit only `value_statement`, `context_statement`,
`audience_framing`, or `message_angle` facts, each with intent id, evidence id,
field path, quote, source URL, author, and collection time. An LLM may cluster
quoted facts and write Chinese summaries, but cannot add an unsupported value,
scene, audience, or angle.

| Result state | Minimum condition | Permitted presentation |
| --- | --- | --- |
| `case_level` | One direct quote | Clearly labelled case observation |
| `repeated_observation` | Three direct quotes from at least two authors | “Repeated in this sample” |
| `formal_directional_result` | At least 12 eligible detail records; every formal finding meets `repeated_observation` | Formal directional observation |
| `provisional` | Useful lead or partial evidence, but a field or sample gate is unmet | Stage finding, missing fields/reasons, and recovery action |
| `insufficient_evidence` | No citable observation | Completed collection, failure/absence reasons, and next step only |

A blocking-field failure excludes that record from the formal eligible sample,
but never discards the already-collected material. `provisional` output must
distinguish title-level leads from body-supported observations, state the exact
missing fields and failure reasons, and never upgrade itself into a formal
claim.

#### G. 输出合同

用户看到的结果按以下模板呈现：

```text
产品营销研究｜[formal_directional_result / provisional / insufficient_evidence]
样本范围：查询 […]；时间窗 […]；合格笔记 […] 篇；作者 […] 位；选择规则 […]。

已准入观察
1. 值得测试的表达 Claim card
   - 结论限定为“本样本中的观察”；显示 state、claim id、引文数 / 作者数 / 合格样本数
   - 展开：3–5 条原文引述、evidence id、字段路径和来源链接
2. 使用场景 / 受众框架 / 叙事角度 Claim card
   - 同级 state、范围、证据与建议动作

线索与缺口：case/provisional 材料、缺失字段与补采动作。
限制：缺失字段、样本覆盖不足及其影响。
下一步：待验证的内容测试或补采动作（不是效果承诺）。
```

`provisional` 必须先展示阶段状态、可用线索、证据缺口和具体重试/补采动作。

### Content-performance specialist contract, v1

#### A. 专家身份

- **方向名称：**内容表现研究（`ContentPerformanceResearchAgent`）
- **帮助用户做什么决策：**决定哪些内容样本值得人工拆解或进入测试。

#### B. 研究对象与范围

| 研究什么 | 明确不研究什么 |
| --- | --- |
| 指定时间窗和排序下，样本可见的内容结构、主题、标题/开头框架与媒体元数据 | 表现原因、点击或转化、因果效果；构图、美学质量、产品是否视觉居中的图像语义判断（v1 外） |

#### C. 内部 intent

| Intent | 要回答的问题 | 可输出什么 | 禁止输出什么 |
| --- | --- | --- | --- |
| `engagement_cohort` | 哪些笔记进入指定互动排序下的样本？ | 样本选择条件和记录的互动快照 | 将其当作分析结论或偏好证明 |
| `content_pattern` | 样本使用了何种内容类型、结构、主题和场景？ | 被引述支撑的可见内容模式与分布 | 该模式导致高表现 |
| `framing_and_packaging` | 标题、开头和可用媒体元数据呈现什么框架？ | 标题/开头框架、记录的媒体属性 | 未采集到的图像语义或包装效果 |

#### D. 证据合同

| 证据项 | Blocking | Warning | 可支撑的 claim / 边界 |
| --- | --- | --- | --- |
| 笔记字段 | `title`、`body_text`、`note_type`、`published_at`、互动快照、`measured_at`、封面/媒体元数据 | `tags`、`ip_location` | 正文支撑结构、主题和开头；类型支撑媒体类型；标题支撑标题框架；媒体元数据仅支撑其记录属性；互动和时间只定义样本上下文 |
| 评论字段 | — | — | 基线不采集评论，不输出评论结论 |
| 来源与 claim | 笔记正文、标题、类型、媒体元数据、互动快照 | — | 允许“按某互动指标排序靠前的样本”及可见模式；禁止因果表现、点击和转化主张。缺媒体字段时，包装观察不能正式成立，但可保留有缺口说明的内容模式线索 |

#### E. 采样计划

Every query returns at most 20 candidates. Quick exploration uses two queries
(confirmed subject plus one scene/theme variation); Standard uses four
(subject, main scene, confirmed problem/audience, and adjacent expression);
Deep starts from those four and may append two recorded gap-fill queries after
coverage inspection. The user's sort preference is applied consistently. Likes,
comments, or collection sort permits the phrase “samples ranked near the top by
that interaction metric”; general and latest sort only permits “content
samples”.

#### F. 分析与质量 gate

Allowed facts are `content_type`, `content_structure`, `topic_context`,
`title_framing`, `opening_framing`, `media_metadata`, and
`engagement_snapshot`. Structure may have multiple labels from a bounded set:
problem-solution, use experience, comparison-selection, tutorial/checklist,
knowledge explanation, scene narrative, or campaign explanation. Every label
retains its field quote. Results report distributions such as `7 / 12 eligible
notes, 5 authors`; they may display raw interaction ranges with publication
dates, but not infer that one label caused greater interaction. Case,
repeated-observation, formal, provisional, and insufficient states follow the
global product-marketing thresholds.

#### G. 输出合同

```text
内容表现研究｜[状态]
样本范围：查询 […]；时间窗 […]；排序 […]；合格笔记 […] 篇；作者 […] 位。

已准入观察
1. 内容模式 Claim card：结构 / 主题 / 场景的样本分布（如 7/12 笔记、5 位作者）
   - 展开：原文、evidence id、字段路径、来源及原始互动快照
2. 框架与包装 Claim card：标题、开头和已采集媒体属性
   - 展开同级证据和 state

线索与缺口：未达到媒体字段或样本门槛的材料。
限制：字段缺失、排序和样本选择偏差。
下一步：人工拆解或实验建议；不宣称其会提升表现。
```

### Competitor-discovery specialist contract, v1

#### A. 专家身份

- **方向名称：**竞品发现（`CompetitorDiscoveryAgent`）
- **帮助用户做什么决策：**识别样本类目中可见的竞品品牌，并比较其内容表达。

#### B. 研究对象与范围

| 研究什么 | 明确不研究什么 |
| --- | --- |
| 被样本明确提及的竞品品牌，以及其价值、场景和叙事表达 | 市占率、官方账号身份、商业关系、竞争表现或市场领导地位 |

#### C. 内部 intent

| Intent | 要回答的问题 | 可输出什么 | 禁止输出什么 |
| --- | --- | --- | --- |
| `competitor_identification` | 哪些品牌被样本明确点名？ | 有标题/正文/标签引文的品牌候选 | 从作者身份推断品牌或官方账号 |
| `brand_expression` | 各品牌如何表达价值、场景和叙事？ | 样本中的表达模式 | 品牌真实定位或效果判断 |
| `engagement_signal` | 样本记录了哪些互动上下文？ | 原始互动快照 | 竞争表现或受欢迎程度结论 |

#### D. 证据合同

| 证据项 | Blocking | Warning | 可支撑的 claim / 边界 |
| --- | --- | --- | --- |
| 笔记字段 | `title`、`body_text`、`author_name`、`tags`、互动快照、`measured_at` | `published_at`、`ip_location` | 品牌名必须由标题、正文或标签直接引述；正文/标签支撑表达；互动仅为样本上下文 |
| 评论字段 | — | — | 基线不采集评论 |
| 来源与 claim | 笔记标题、正文、标签及互动快照 | — | 同一池保留官方和普通创作者材料；允许“样本中的候选/表达”，禁止市场领导者、官方身份或表现推断 |

#### E. 采样计划

查询由已确认的主题/类目、比较词和已发现的品牌别名生成。Quick/Standard/Deep 分别锁定 2/4/4（外加至多 2 条记录在案的 gap-fill）条查询，每条最多 20 个候选；按相关性、intent 覆盖、作者上限和字段可用性选择详情。基线不采集评论。

#### F. 分析与质量 gate

品牌识别和表达事实都必须保留引文、evidence id、字段路径和来源。正式方向结果至少有 12 篇合格详情；每个重复表达须有至少 2 位作者的 3 条直接引文。未达到门槛时仅作 case/provisional，并说明缺口。

#### G. 输出合同

```text
竞品发现｜[状态]
样本范围：查询 […]；时间窗 […]；合格笔记 […] 篇；作者 […] 位。

已准入观察
1. 可见竞品候选 Claim card：品牌名、被点名的原文、字段路径与来源
2. 各候选的样本表达 Claim card：价值 / 场景 / 叙事，以及原始互动上下文

线索与缺口：未达到重复或字段门槛的候选。
限制：搜索样本不代表市场格局；官方身份未核验。
下一步：品牌身份或表达的验证动作。
```

### UGC-community specialist contract, v1

#### A. 专家身份

- **方向名称：**UGC 社区研究（`UGCCommunityResearchAgent`）
- **帮助用户做什么决策：**理解样本社区的自述成员、可见连接机制及直接表达的参与动机/收益。

#### B. 研究对象与范围

| 研究什么 | 明确不研究什么 |
| --- | --- |
| 时间限定样本中的成员自述、连接机制、参与动机和收益 | 社区形成或增长的历史/因果解释；未表达的内在动机；“真假 UGC”的断言 |

#### C. 内部 intent

| Intent | 要回答的问题 | 可输出什么 | 禁止输出什么 |
| --- | --- | --- | --- |
| `member_profile` | 成员如何自述身份、阶段、场景、兴趣或归属？ | 直接自述的成员画像 | 从参与或互动推断成员身份 |
| `connection_mechanism` | 可见的连接如何发生？ | 线下活动、线上互动、传播、共创、激励、仪式等可见机制 | 社区形成的因果机制 |
| `participation_motivation_and_value` | 用户直接说出了哪些参与动机或收获？ | 第一人称直接引文的动机/收益 | 非第一人称或行为推测的心理状态 |

#### D. 证据合同

| 证据项 | Blocking | Warning | 可支撑的 claim / 边界 |
| --- | --- | --- | --- |
| 笔记字段 | `title`、`body_text`、`author_name`、`published_at` | 互动快照、`tags` | 核心 UGC 正文须有实质体验、参与、观点或互动；仅标签、空白、复制/模板内容仅是线索 |
| 评论字段 | `body_text`、`published_at`、`like_count`、`reply_relation` | — | 评论可支撑采样线程中的讨论、互动语言；需保留父笔记引用 |
| 来源与 claim | 笔记正文和有父级上下文的评论 | — | 动机/收益只接受第一人称引文；有机/推广信号只供用户复核，不能判定 true/false UGC |

#### E. 采样计划

锁定 3 条 latest-sort 查询：品牌；品牌 + 确认产品/场景；品牌 + 社区/活动线索，每条至多 20 个候选。仅发现明确标签线索后才追加 1 条标签扩展查询。选择合格笔记后按合同采集评论，并记录范围、排序、cap、回复深度和完整性。

#### F. 分析与质量 gate

事实按成员自述、连接机制、动机/收益分类，并保留引文、证据和父级链路。单一引文是 case；重复观察须至少 2 位作者的 3 条引文；评论结论另须至少 30 条合格评论、5 位作者。未满足时标记 provisional/insufficient，而非补全推断。

#### G. 输出合同

```text
UGC 社区研究｜[状态]
样本范围：查询 […]；时间窗 […]；合格笔记 […] 篇 / 评论 […] 条；作者 […] 位；评论完整性 […]。

已准入观察
1. 成员自述与参与场景 Claim card
2. 可见连接机制 Claim card
3. 直接表达的动机 / 收益 Claim card（第一人称引文和父笔记上下文）

每项展开：原文、evidence id、字段路径、来源链接与父级链路（如适用）。
线索与缺口：未达到评论或作者门槛的材料。
限制：样本为时间切片；有机/推广仅为待复核信号。
下一步：账户确认、补采或用户验证动作。
```

### Comment-insight specialist contract, v1

#### A. 专家身份

- **方向名称：**评论洞察（`CommentInsightAgent`）
- **帮助用户做什么决策：**识别评论中直接表达的用户问题、异议、需求和重复措辞。

#### B. 研究对象与范围

| 研究什么 | 明确不研究什么 |
| --- | --- |
| 带父笔记上下文的评论文本 | 沉默读者、平台级情绪、未写出的动机；以评论数代替用户证据 |

#### C. 内部 intent

| Intent | 要回答的问题 | 可输出什么 | 禁止输出什么 |
| --- | --- | --- | --- |
| `explicit_question` | 用户直接问了什么？ | 原文明确问题 | 推断的潜在问题 |
| `objection_or_failure` | 用户直接提出哪些异议、失败或障碍？ | 有引文的异议/失败描述 | 总体满意度或因果归因 |
| `repeated_need_language` | 哪些需求措辞在样本中重复出现？ | 受样本门槛约束的重复语言 | 平台级需求比例或未表达需求 |

#### D. 证据合同

| 证据项 | Blocking | Warning | 可支撑的 claim / 边界 |
| --- | --- | --- | --- |
| 笔记字段 | 父笔记 `title`、`body_text` | 父笔记互动快照 | 父笔记只提供评论语境，不能代替评论证据 |
| 评论字段 | `body_text`、`published_at`、`like_count`、`reply_relation` | — | 评论文本与父笔记引用共同支撑问题、异议、需求和措辞；评论数本身不构成证据 |
| 来源与 claim | 合格评论原文、父笔记上下文及回复关系 | — | 每个事实保留 `parent_evidence_id`、回复关系、引文和来源；没有父级链路不能成为正式评论 claim |

#### E. 采样计划

查询先发现相关父笔记；Quick/Standard/Deep 分别使用 2/4/4（外加至多 2 条记录在案的 gap-fill）条查询，每条最多 20 个候选。仅对选中的合格父笔记采集评论，并记录评论排序、cap、回复深度和完整性。

#### F. 分析与质量 gate

评论事实按直接问题、异议/失败、重复需求语言分类。单条评论是 case；重复需求/语言须至少 2 位作者的 3 条评论；正式方向结果还须达到至少 30 条合格评论、5 位作者。缺失字段或样本不足时输出 provisional/insufficient。

#### G. 输出合同

```text
评论洞察｜[状态]
样本范围：父笔记查询 […]；时间窗 […]；合格父笔记 […] 篇；评论 […] 条；作者 […] 位；评论完整性 […]。

已准入观察
1. 直接问题 Claim card
2. 异议 / 失败描述 Claim card
3. 重复需求语言 Claim card

每项展开：限定结论、评论原文、父笔记上下文、回复关系、evidence id、字段路径和来源链接。
线索与缺口：不足 30 条合格评论或 5 位作者时仅显示 comment leads。
限制：仅代表已采集的评论线程，不是平台总体情绪。
下一步：补采评论或验证问题优先级。
```

### Brand-activity specialist contract, v1

#### A. 专家身份

- **方向名称：**品牌活动研究（`BrandActivityResearchAgent`）
- **帮助用户做什么决策：**识别样本时间段内可见的活动、上新、合作和传播表达。

#### B. 研究对象与范围

| 研究什么 | 明确不研究什么 |
| --- | --- |
| 样本期内可见的活动信号、参与机制和传播表达 | 活动触达、销售提升或因果成功 |

#### C. 内部 intent

| Intent | 要回答的问题 | 可输出什么 | 禁止输出什么 |
| --- | --- | --- | --- |
| `activity_identification` | 可见的活动、上新或合作是什么？ | 有日期/引文的活动信号 | 未被样本证实的正式活动事实 |
| `participation_mechanism` | 样本显示了哪些参与方式？ | 可见参与机制 | 参与规模或实际效果 |
| `dissemination_expression` | 内容如何表达活动传播？ | 传播文案/内容表达 | “传播成功”或因果结果 |

#### D. 证据合同

| 证据项 | Blocking | Warning | 可支撑的 claim / 边界 |
| --- | --- | --- | --- |
| 笔记字段 | `title`、`body_text`、`published_at`、`tags`、`note_type`、互动快照、`measured_at` | `ip_location`、媒体元数据 | 标题/正文/标签和日期支撑可见活动及表达；互动只描述样本上下文 |
| 评论字段 | — | — | 基线不采集评论 |
| 来源与 claim | 有字段路径的笔记文本、日期和元数据 | — | 允许“样本中的活动信号”；禁止活动成效、触达和销量主张 |

#### E. 采样计划

查询由确认品牌/类目与活动、上新、合作、事件、campaign 等词组合。Quick/Standard/Deep 为 2/4/4（外加至多 2 条记录在案 gap-fill）条查询，每条至多 20 个候选；依查询覆盖、日期、相关性、作者上限和字段可用性选择详情。基线不采集评论。

#### F. 分析与质量 gate

将事实分类为活动识别、参与机制和传播表达，保留引文、日期、evidence id、字段路径和来源。正式重复观察须至少 12 篇合格详情，且每项至少 2 位作者的 3 条引文；否则只按 case/provisional 展示。

#### G. 输出合同

```text
品牌活动研究｜[状态]
样本范围：查询 […]；活动观察时间 […]；合格笔记 […] 篇；作者 […] 位。

已准入观察
1. 可见活动信号 Claim card（日期、字段路径和来源）
2. 参与机制 Claim card
3. 传播表达 Claim card 及原始互动上下文

线索与缺口：日期、正文或样本门槛未满足的材料。
限制：样本可见性和字段缺失；不衡量活动成功。
下一步：活动事实、账户或效果的后续验证动作。
```

### Keyword-growth specialist contract, v1

#### A. 专家身份

- **方向名称：**关键词增长研究（`KeywordGrowthResearchAgent`）
- **帮助用户做什么决策：**识别关键词和表达模式；仅在存在可比基线时，判断近一周相对近半年的信号。

#### B. 研究对象与范围

| 研究什么 | 明确不研究什么 |
| --- | --- |
| 样本期关键词/表达模式，以及近期窗口相对参考窗口的可比信号 | 平台总体增长率；仅凭高互动排序得出的增长；未做年龄归一化的互动变化 |

#### C. 内部 intent

| Intent | 要回答的问题 | 可输出什么 | 禁止输出什么 |
| --- | --- | --- | --- |
| `keyword_discovery` | 样本中出现哪些关键词和表达？ | 有标题/正文/标签引文的关键词模式 | 未被文本支持的关键词 |
| `usage_context` | 关键词在哪些场景或表述中出现？ | 带引文的使用语境 | 关键词背后的未表达动机 |
| `relative_window_comparison` | 近期关键词占比是否高于可比参考窗口？ | `近一周相对近半年信号` 或 `reference_window_insufficient` | 平台级增长率或绝对趋势 |

#### D. 证据合同

| 证据项 | Blocking | Warning | 可支撑的 claim / 边界 |
| --- | --- | --- | --- |
| 笔记字段 | `title`、`body_text`、`tags`、`published_at`、互动快照、`measured_at` | `author_name`、`ip_location` | 标题/正文/标签支撑关键词与语境；`published_at` 用于窗口归属；互动不支撑增长 |
| 评论字段 | — | — | 基线不采集评论 |
| 来源与 claim | 两窗口中合格笔记的文本引文及可审计计数 | — | 比较 claim 只能使用两窗口的合格笔记数和含关键词笔记数；缺少足够参考样本必须输出不足状态 |

#### E. 采样计划

从 Brief、同义词和 intent 生成并锁定查询集；两窗口必须使用相同来源、字段合同、每作者上限和每条查询 20 候选 cap。近期窗口为 `[today - 7d, today)`，参考窗口为 `[today - 6mo, today - 7d)`；以非 popularity 的发现顺序收集详情，再按 `published_at` 去重、分窗、应用同一合格规则。基线不采集评论。

#### F. 分析与质量 gate

提取带引文的关键词和使用语境事实，并按窗口统计 `keyword_share = keyword-bearing eligible notes / eligible notes`。只有查询计划、政策和字段合同可比且参考窗口详情充足，才可比较占比；否则为 `reference_window_insufficient`。输出必须披露两窗口样本量、采集偏差和 warning，不能提升为平台总体增长。

#### G. 输出合同

```text
关键词增长研究｜[近一周相对近半年信号 / reference_window_insufficient]
样本范围：锁定查询 […]；相同字段合同/作者上限 […]；采集时间 […]。
窗口：近期 […]（合格笔记 N1，含词笔记 K1，占比 K1/N1）；
      参考 […]（合格笔记 N0，含词笔记 K0，占比 K0/N0）。

已准入观察
1. 关键词与表达模式 Claim card：原文引述、evidence id、字段路径、来源链接
2. 使用语境 Claim card
3. 相对窗口信号 Claim card：展示重算的 K1/N1、K0/N0 与可比占比差异；不称平台增长率

线索与缺口：`reference_window_insufficient` 时展示缺少的样本/字段及补采动作，不展示增长结论。
限制：窗口样本、检索偏差、缺失字段和参考样本充足性。
下一步：补足参考窗口、锁定新一轮比较或人工验证。
```

### Versioned policy and user controls

The system combines a versioned platform policy with user preferences and
freezes the resulting effective policy on the research run:

```text
PolicyDefinition + permitted user preferences
  -> validated effective policy
  -> immutable RunPolicySnapshot
```

The snapshot records the base policy id/version, requested overrides,
validation result, effective values, contract versions, and an effective policy
hash. Existing runs must continue to use their snapshot after defaults change.

The first user-facing controls are limited to:

- research depth;
- time range;
- discovery sort preference.

Sample budget is represented by the selected research-depth preset rather than
as a separate initial UI control. Users may change collection cost and scope,
but cannot weaken evidence-admission rules, blocking fields, mandatory
disclosures, or claim boundaries. A reduced budget is valid; it lowers the
permitted output level instead of causing the system to assert stronger claims.

### Research-depth presets

All limits below apply per research direction, not to an entire workflow.
Candidate cards from all internal intents share the directional candidate pool.

| Preset | Query allocation | Candidates per query | Candidate pool | Detail cap | Author cap | Comment collection | Permitted output |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Quick exploration | Direction-specific, low-coverage plan | 20 | 20 × locked-query count | 8 | 1 | Disabled by default; comment directions may inspect up to 3 notes × 10 top-level comments | Candidate leads, case observations, and validation hypotheses only |
| Standard research | Direction-specific baseline plan | 20 | 20 × locked-query count | 18 | 2 | Up to 6 notes × 30 top-level comments | Directional observations only after all field, citation, and sample gates pass |
| Deep research | Baseline plan plus recorded gap-fill queries | 20 | 20 × locked-query count | 30 | 2 | Up to 10 notes × 50 top-level comments | Same claim boundaries as Standard, with greater coverage and gap-fill budget |

Quick comment results are always labelled `comment_leads_only`; they cannot be
reported as user-pain-point conclusions. A comment conclusion still requires at
least 30 eligible comments from at least five distinct authors. A note-only
directional conclusion still requires at least 12 eligible detail records,
unless the claim is explicitly labelled case-level. Deep research is not a
stronger truth category: all blocking fields, intent-coverage conditions,
citations, and limitations still apply.

### Field availability and state propagation

Every field in the agreed directional field-contract table is an acquisition
attempt, not a strict input-schema requirement. The packet records one of:

```text
present | missing | not_requested | unavailable | not_applicable
```

- `present`: the requested value was collected and normalized.
- `missing`: collection completed but the source did not provide a value.
- `not_requested`: the field is outside this direction's contract.
- `unavailable`: the source/request could not provide it; record the structured
  failure reason and retryability.
- `not_applicable`: the field does not apply to this source item (for example,
  a video-only field on a text-only note).

Missing or unavailable warning fields preserve a warning and an analysis/report
limitation. Missing or unavailable blocking fields disqualify that note or
comment from the applicable formal conclusion and trigger selection of another
candidate while budget remains. If the eligible minimum cannot be reached, the
intent/direction ends as `insufficient_evidence` or `incomplete`, with a
structured recovery action; it is not a runtime crash. A source request failure
is therefore distinct from a failed workflow and from a claim that lacks enough
evidence.

## Cross-direction governance, aggregation, and safe reporting

### Canonical source identity without cross-direction re-deduplication

`SourceRegistry` resolves one platform item to a stable `canonical_source_id`.
It is a global identity service, not a global sample or evidence counter. Each
direction preserves its own projection, selection reason, eligibility state,
and claim context for the same canonical source. A source used in two directions
therefore remains one source identity but may support two different directional
claims; its appearance must not be counted twice as independent corroboration.

Any reported source count must state its denominator and scope, including the
run ID, locked query plan, `run_as_of_at`, direction where applicable, and
eligibility rule. A phrase such as “87 篇笔记提及” is invalid without that
scope metadata.

### Budget control and gap-fill loop

External collection is the only non-replayable stage. `BudgetGuard` must be
called immediately before every adapter request and atomically reserve the
expected cost using an idempotency key. `BudgetLedger` commits or releases the
reservation when the request resolves. Recovery inherits prior reservations and
consumption; concurrent directions cannot independently observe and spend the
same remaining budget.

`DirectionResultDecision` may request a recorded gap-fill action. The action
returns to `BudgetGuard`, is bounded by the locked policy's attempt cap, and
creates a new query group or collection attempt; it never mutates the original
sample definition invisibly. If budget is exhausted, the result is downgraded
with its existing admitted claims, weak signals, limitations, and a structured
recovery action.

### Cross-direction reconciliation

`CrossDirectionReconciler` runs only on admitted directional claims. It is
read-only and may create append-only `OverlapRecord` and `ContradictionRecord`
objects with the relevant claim IDs, canonical source IDs, classification,
reason, and resolution state. It must not:

- alter a direction's admission decision or sample counts;
- treat repeated use of one source across directions as independent evidence;
- synthesize a new business conclusion; or
- hide a directional limitation.

The report composer may display these records but cannot reinterpret them.

### Aggregate claims

Simple presentation of direction cards does not create a new claim. A statement
that connects multiple directions, however, is an `AggregateClaim` and must
record its derivation before it can appear as a report conclusion or action
hypothesis.

```text
aggregate_claim_id
aggregate_type: cross_direction_corroboration | cross_direction_tension |
                action_hypothesis
statement
source_claim_ids
derivation_method
scope_intersection
inherited_limitations
policy_snapshot_hash
```

`AggregateClaimEvaluator` verifies that every input is admitted, source claims
have compatible scope, the derivation method is permitted by policy, and no
single canonical source is represented as independent corroboration. It may not
upgrade co-occurrence to causality or increase a source count by summing claims.

### Gray-area decisions and weak signals

Deterministic admission rules are authoritative. A policy may allow an LLM only
for explicitly named gray-area classifications; the decision records model and
prompt version, input evidence IDs, output, rationale, confidence, and policy
version in the Evidence Layer. Such fallback cannot bypass blocking fields or
produce a formal directional result by itself.

Useful but downgraded material enters `WeakSignalPool` with its evidence,
missing gate, limitation, and recovery action. The final report may show it in
“证据不足但值得注意”, never as a formal conclusion.

### Report completion and faithfulness failure

The composer may use only admitted directional claims, admitted aggregate
claims, reconciliation records, weak signals, and required disclosures.
`ReportFaithfulnessEvaluator` first deterministically checks claim IDs,
citation/quote resolution, numeric values, state labels, and scope metadata;
an LLM then audits wording, scope expansion, causal language, and aggregate
derivation wording.

An audit failure retries composition at most the policy-defined `N` times. If
retries are exhausted, the system must preserve all completed work by publishing
one of the following rather than an empty result or an unfaithful narrative:

```text
complete_verified_report
  All required report sections passed faithfulness evaluation.

partial_verified_report
  Shows every verified direction and aggregate claim; omits only failed free-text
  sections and names their audit/recovery status.

evidence_only_report
  Renders admitted claim cards, evidence, weak signals, limitations, and
  recovery actions directly from structured records when narrative composition
  is unavailable.
```

An unfaithful draft may remain as an internal audit artifact, but it must not be
presented as a final conclusion.

### Time semantics

`RunPolicySnapshot` fixes `run_as_of_at` once per run. The adapter's note-info
publication field is normalized as `source_published_at`; the system records
`source_collected_at` when it retrieves the item. These fields are not duplicate
source collection: they distinguish publication time, collection time, and the
run's immutable definition of “now”. All report time language, window
eligibility, historical comparison, and engagement interpretation must name the
applicable field.
