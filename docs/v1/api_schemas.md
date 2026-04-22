# Multi-Agent XHS Note Generation System - API Schemas

## 1. Core Data Models

> Model sync rule:
> - Runtime model contracts must be aligned with implementation in `app/models/*.py`.
> - If a richer future schema is needed, mark it as `V2 Planned` with a different class name.

### 1.1 XHSPost (小红书帖子)
```python
class XHSPost(BaseModel):
    # Author info
    author_user_id: str
    author_nick_name: str
    author_avatar: Optional[str] = ""
    author_home_page_url: Optional[str] = ""
    
    # Identity
    note_id: str
    note_url: str
    note_xsec_token: Optional[str] = ""
    note_card_type: Optional[str] = ""
    note_model_type: Optional[str] = ""
    
    # Content
    note_display_title: str
    note_desc: Optional[str] = ""
    note_tags: List[str] = Field(default_factory=list)
    note_image_list: List[str] = Field(default_factory=list)
    note_ip_location: Optional[str] = ""
    
    # Video fields
    video_id: Optional[str] = ""
    video_h264_url: Optional[str] = ""
    note_duration: Optional[str] = ""
    
    # Engagement metrics
    note_liked_count: int = 0
    collected_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    
    # Timestamps
    note_create_time: Optional[str] = ""
    
    # Monetization
    pgy_url: Optional[str] = ""
```

### 1.2 EngagementScore (参与度评分)
```python
class EngagementScore(BaseModel):
    note_id: str
    engagement_rate: float         # 互动率 = lambda_weight * norm_likes + (1 - lambda_weight) * norm_collects (0~1)
    
class ScoredPost(BaseModel):
    post: XHSPost
    score: EngagementScore
```

### 1.3 PlatformPreference (平台偏好分析)
```python
class PlatformPreference(BaseModel):
    avg_title_length: int           # 平均标题长度
    popular_tags: list[str]         # 热门标签
    optimal_posting_times: list[str] # 建议发布时间
    content_patterns: list[str]     # 内容模式（图文/视频/疑问式等）
```

### 1.3.1 SpiderNote (Session内精简笔记)
```python
class SpiderNote(BaseModel):
    note_id: str
    title: str
    content: str
    tags: list[str]
```

### 1.3.1 PlatformPreferenceV2 (Planned, not active)
```python
class ContentPattern(BaseModel):
    avg_title_length: int
    avg_content_length: int
    common_hooks: list[str]
    popular_tags: list[str]
    emoji_usage_rate: float
    image_count_preference: int

class TimingPattern(BaseModel):
    peak_hours: list[int]
    best_weekdays: list[str]

class PlatformPreferenceV2(BaseModel):
    content_pattern: ContentPattern
    timing_pattern: TimingPattern
    trending_topics: list[str]
    audience_insights: dict
```

### 1.4 IntentSnapshot (探索意图快照)
```python
class IntentSnapshot(BaseModel):
    user_query: str
    user_goal: str
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    planning_brief: str
```

### 1.4.1 SearchIntent (探索搜索意图)
```python
class SearchIntent(BaseModel):
    query: str
    platform: str | None = None
    goal: str
    subject_type: Literal["brand", "category", "audience", "topic"]
    seed_entities: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    known_urls: list[str] = Field(default_factory=list)
    session_id: str | None = None
    workflow_stage: str | None = None
    coverage_goal: list[str] = Field(default_factory=list)
    exploration_hypotheses: list[str] = Field(default_factory=list)
    freshness_policy: Literal["prefer_fresh", "balanced", "prefer_stable"] = "balanced"
    diversity_policy: Literal["narrow", "balanced", "broad"] = "balanced"
    risk_policy: Literal["strict", "balanced", "permissive"] = "balanced"
    budget: dict = Field(default_factory=dict)
    constraints: dict = Field(default_factory=dict)
```

**SearchIntent 字段说明**

| 字段 | 作用 | 例子 | 对 SearchWorker 的影响 |
|---|---|---|---|
| `query` | 用户的原始检索表达，保留自然语言意图和关键词锚点 | `“帮我了解高端护肤品牌XXX适合谁”` | 作为初始检索入口和 query rewrite 的基底，不直接决定边界模板 |
| `platform` | 指定或暗示优先搜索的平台范围 | `['xhs', 'weibo']` | 决定 provider 候选集、fallback 顺序和平台配额；为空时由 Orchestrator 按能力和配置选路 |
| `goal` | 本轮探索的总目标，描述“要理解什么” | `understand_brand_positioning` | 决定是偏 direct hits、adjacent leads 还是 contrast signals；影响停止条件和摘要粒度 |
| `subject_type` | 识别搜索对象类型 | `brand` / `category` / `audience` | 决定默认边界模板、桶划分和停止阈值；是 SearchWorker 进行空间建模的第一层输入 |
| `seed_entities` | 核心实体种子，用于锚定搜索空间 | `['XXX', 'XXX 官方', 'XXX 旗舰店']` | 作为高优先级召回锚点，优先进入 core bucket；用于去重、聚类和候选命名 |
| `aliases` | 别名、简称、俗称、竞品名、行业叫法 | `['XXX 小绿瓶', 'XXX serum']` | 扩展召回面，补足漏搜；同时用于 query expansion 和跨平台实体对齐 |
| `known_urls` | 已知的官方页、竞品页、重点内容页 | `['https://.../brand', 'https://.../product']` | 优先触发 fetch / capture 型 provider，补强事实核验和内容抓取，不再把这些页面当作待发现对象 |
| `session_id` | 将搜索行为绑定到当前会话/分支 | `sess_20260421_001` | 决定证据持久化和恢复锚点；同一 session 内复用历史 evidence refs 与 turn snapshot |
| `workflow_stage` | 当前流程阶段 | `exploring` / `strategy` | 决定搜索深度、输出格式和停搜阈值；探索阶段允许更强的扩展和推荐，后续阶段更保守 |
| `coverage_goal` | 声明本轮必须覆盖的信息面 | `['定位', '目标人群', '价格带', '核心诉求']` | 直接参与 coverage_score 计算；决定每个 bucket 的最低覆盖要求和是否需要补搜 |
| `exploration_hypotheses` | 待验证的假设集合 | `['主打通勤场景', '用户更看重成分安全']` | 驱动验证式检索与 contrast 搜索；若假设持续被反证，会促使 SearchWorker 改写边界或降权原路径 |
| `freshness_policy` | 新鲜度偏好和时间窗策略 | `recent_90d` / `balanced` / `stable` | 决定时间窗过滤、旧证据保留比例和趋势桶权重；影响搜索结果的时效性排序 |
| `diversity_policy` | 多样性偏好和去同质化力度 | `narrow` / `balanced` / `broad` | 决定同源/同观点结果的保留上限与 rerank 强度；越宽越鼓励跨平台、跨角度证据 |
| `risk_policy` | 风险控制和可信度约束 | `strict` / `balanced` / `permissive` | 决定低可信、营销化、搬运、疑似重复证据的降权或剔除力度；影响 confidence_score 下限 |
| `budget` | 轮次、请求数、时长、扩展次数等预算 | `{'rounds': 2, 'max_queries': 8, 'max_latency_ms': 15000}` | 决定是否继续扩搜、是否提前收敛，以及 degraded / zero-result 的触发时机 |
| `constraints` | 附加约束，覆盖地域、语言、内容形态等限制 | `{'region': 'CN', 'content_type': ['post', 'video']}` | 约束 provider 选择、查询改写和结果裁剪；也是最后一层防止搜索空间过宽或失焦的过滤器 |


### 1.5 EvidenceSummary (证据摘要)
```python
class EvidenceRef(BaseModel):
    evidence_id: str
    source_platform: str
    query_used: str
    url: str
    freshness_label: str           # "fresh" | "stale" | "unknown"
    confidence_label: str          # "high" | "medium" | "low"

class EvidenceSummary(BaseModel):
    total_evidence_count: int
    cluster_count: int
    coverage_score: float
    diversity_score: float
    summaries: list[str]
    refs: list[EvidenceRef] = Field(default_factory=list)
```

**EvidenceSummary 语义**
- `summaries` 承载压缩后的 cluster summary、adjacent leads、contrast signals 和 gap prompts。
- `refs` 承载可回溯的证据引用，不承载完整原始结果。
- `coverage_score` 和 `diversity_score` 用于判断当前证据是否足以进入收敛。

### 1.6 TopicCandidate (探索候选卡片)
```python
class TopicCandidate(BaseModel):
    candidate_id: str
    title: str
    angle: str
    why_now: str
    fit_score: float
    confidence_score: float
    competition_score: float
    novelty_score: float
    evidence_refs: list[str] = Field(default_factory=list)
    recommended_next_step: str
```

**TopicCandidate 语义**
- `recommended_next_step` 用于承载下一步建议文案。
- `evidence_refs` 用于追溯候选来源。
- `fit_score / confidence_score / competition_score / novelty_score` 只用于候选比较，不用于替代证据质量判断。

### 1.6.1 SearchPlan (探索搜索计划)
```python
class SearchPlan(BaseModel):
    action: Literal["initial", "refine", "refresh"]
    queries: list[str] = Field(default_factory=list)
    intent_delta: str | None = None
    provider_hints: list[str] = Field(default_factory=list)
```

**SearchPlan 语义**
- `initial` 用于首次建立搜索空间。
- `refine` 用于在当前意图上缩小、澄清或补强。
- `refresh` 用于对同一意图重新抓取最新结果。
- `intent_delta` 记录相对上一轮的意图变化。
- `provider_hints` 用于提示推荐 provider，但不暴露具体调度逻辑。

### 1.6.2 SearchStats (搜索统计)
```python
class SearchStats(BaseModel):
    queries_executed: list[str] = Field(default_factory=list)
    providers_used: list[str] = Field(default_factory=list)
    total_hits: int = 0
    latency_ms: int | None = None
```

### 1.6.3 ExplorationTurn (探索轮快照)
```python
ExplorationTurnStatus = Literal[
    "searching",
    "synthesizing",
    "awaiting_user_input",
    "degraded",
    "failed",
]

ExplorationBranchStatus = Literal["exploring", "selected", "strategy", "failed"]

class EvidenceRef(BaseModel):
    title: str
    url: str
    source: str | None = None
    snippet: str | None = None

class RewriteSuggestion(BaseModel):
    label: str
    input_text: str

class ExplorationTurn(BaseModel):
    turn_id: str
    action: Literal["initial", "refine", "refresh"]
    turn_status: ExplorationTurnStatus
    last_completed_action: Literal["search", "synthesize", "await_user", "handoff"] | None = None
    search_plan: SearchPlan | None = None
    evidence_summary: EvidenceSummary | None = None
    degraded_mode: bool = False
    rewrite_suggestions: list[RewriteSuggestion] = Field(default_factory=list)
    search_stats: SearchStats | None = None
```

**EvidenceSummary / TopicCandidate 语义补充**
- `EvidenceSummary` 只承载压缩后的证据视图，不承载全量原始结果。
- `coverage_score` 表示当前证据是否足以支撑后续合成。
- `diversity_score` 表示当前证据是否覆盖足够多的角度。
- `TopicCandidate` 必须可追溯到具体 `evidence_refs`，否则不能视为可交付候选。
- `fit_score`、`confidence_score`、`competition_score`、`novelty_score` 用于候选比较，不替代证据质量判断。

### 1.6.4 BranchSummary (探索分支摘要)
```python
class BranchSummary(BaseModel):
    branch_id: str
    title: str
    status: ExplorationBranchStatus
    subtitle: str | None = None
    last_updated_at: datetime
```

### 1.7 ExplorationResult (探索结果)
```python
class ExplorationDegradeInfo(BaseModel):
    degraded_mode: bool = False
    reason: str | None = None
    affected_providers: list[str] = Field(default_factory=list)

class DecisionTrace(BaseModel):
    round_index: int
    next_action: Literal["search", "synthesize", "await_user", "handoff"]
    decision_reason: str
    degrade_action: str | None = None

class ExplorationResult(BaseModel):
    active_branch_id: str | None = None
    current_turn: ExplorationTurn | None = None
    previous_turn: ExplorationTurn | None = None
    candidates: list[TopicCandidate] = Field(default_factory=list)
    rewrite_suggestions: list[RewriteSuggestion] = Field(default_factory=list)
    degraded_mode: bool = False
    degrade_info: ExplorationDegradeInfo | None = None
    queued_next_round: bool = False
    queued_action: Literal["initial", "refine", "refresh"] | None = None
    trace_summary: list[DecisionTrace] = Field(default_factory=list)
    retrieved_at: datetime
```

### 1.8 ContentStrategy (内容策略)
```python
class ContentStrategy(BaseModel):
    positioning: str
    target_audience: str
    content_pillars: list[str]
    key_messaging: str
    content_types: list[str]
    posting_strategy: str
    data_source_quality: float      # quality_score (0~1)
```

### 1.9 Proposal (生成提案)
```python
class Proposal(BaseModel):
    proposal_id: str
    angle: str                      # 切入角度
    hook: str                       # 标题/开头钩子
    outline: str                    # 内容大纲（文本）
    target_emotion: str             # 目标情绪
    content_pillars: list[str]
    suggested_tags: list[str]
    score: float = 0.0
    is_used: bool = False
    is_high_risk: bool = False
```

### 1.10 GeneratedNote (生成的笔记)
```python
class SimilarityCheck(BaseModel):
    max_similarity: float           # 最大相似度
    status: str                     # "safe" | "warning" | "rewritten"
    
class GeneratedNote(BaseModel):
    note_id: str
    title: str
    content: str
    tags: list[str]
    cover_design_prompt: str
    suggested_update_time: str
    similarity_check: SimilarityCheck
    generation_params: dict         # {temperature, proposal_id, ...}

class GenerationResult(BaseModel):
    notes: list[GeneratedNote]
    similarity_warnings: list[str]  # 相似度警告信息
    has_rewritten: bool             # 是否有重写过的笔记
```

### 1.11 UserProfile (用户画像)
```python
class UserProfile(BaseModel):
    user_id: str
    account_type: str               # "personal" | "business" | "creator"
    niche: str | None               # 垂直领域
    content_history: list[str]      # 历史内容主题
    brand_voice: str | None         # 品牌调性
    target_goals: list[str]         # 运营目标
```

---

## 2. Component Interfaces

### 2.1 Orchestrator (协调入口)

#### `init_session`
```python
class InitSessionRequest(BaseModel):
    user_id: str
    user_query: str
    platform: str = "xiaohongshu"
    mode: Literal["editing", "exploration"] = "editing"
    
class InitSessionResponse(BaseModel):
    session_id: str
    mode: Literal["editing", "exploration"]
    stage: Literal["init"]
    lifecycle_state: Literal["alive"]
    alive_until: datetime
    purge_after: datetime
```

#### `route`
协调入口接口（用于创建会话并分发到后续策略/生成链路）
```python
class RouteRequest(BaseModel):
    user_id: str
    user_query: str
    
class RouteResponse(BaseModel):
    session_id: str
    mode: str
    next_action: Literal["create_session", "enqueue_exploration", "enqueue_strategy", "enqueue_generation", "return_error"]
    result: dict | ErrorResponse
```

#### `execute_exploration`
```python
class ExecuteExplorationRequest(BaseModel):
    action: Literal["initial", "refine", "refresh"]
    input_text: str | None = None

class EnqueueExplorationResponse(BaseModel):
    session_id: str
    stage: Literal["exploring"]
    job_id: str
    job_status: Literal["queued"]
```

#### `confirm_exploration_candidate`
```python
class ConfirmExplorationCandidateRequest(BaseModel):
    candidate_id: str
    constraints: dict | None = None

class ConfirmExplorationCandidateResponse(BaseModel):
    session_id: str
    stage: Literal["candidate_selected"]
    selected_candidate_id: str
    job_id: str
    job_status: Literal["queued"]
```

### 2.2 Session (会话状态)

#### `init`
```python
class SessionInitRequest(BaseModel):
    session_id: str
    user_id: str
    user_query: str
    
class SessionSnapshot(BaseModel):
    session_id: str
    user_id: str
    user_query: str
    stage: Literal["init", "exploring", "candidate_selected", "strategy", "generation", "completed", "failed"]
    lifecycle_state: Literal["alive", "frozen", "purged"]
    active_branch_id: str | None = None
    current_turn_id: str | None = None
    turn_status: ExplorationTurnStatus | None = None
    last_completed_action: Literal["search", "synthesize", "await_user", "handoff"] | None = None
    spider_cooldown_until: datetime | None = None
    reindex_state: Literal["ok", "pending", "deadletter"] = "ok"
    reindex_attempts: int = 0
    
    # 探索阶段数据
    branch_summaries: list[BranchSummary] = Field(default_factory=list)
    exploration_result: ExplorationResult | None
    selected_candidate_id: str | None

    # 策略阶段数据
    spider_results: list[XHSPost] | None
    engagement_scores: list[ScoredPost] | None
    platform_preference: PlatformPreference | None
    content_strategy: ContentStrategy | None
    
    # 生成阶段数据
    proposals: list[Proposal] | None
    generated_notes: list[GeneratedNote] | None
    
    # 元数据
    created_at: datetime
    updated_at: datetime
    error: dict | None

class Session(BaseModel):
    session_id: str
    user_id: str
    user_query: str
    platform: str = "xiaohongshu"
    mode: str = "editing"
    
    # 工作流阶段
    stage: Literal["init", "exploring", "candidate_selected", "strategy", "generation", "completed", "failed"] = "init"

    # 生命周期阶段
    lifecycle_state: Literal[
        "alive",
        "frozen",
        "purged"
    ] = "alive"
    alive_until: datetime | None = None     # 语义固定：last_user_activity_at + 24h
    pause_requested: bool = False           # 内部控制位：用户请求暂停但 active job 未清空
    pause_requested_at: datetime | None = None
    spider_cooldown_until: datetime | None = None
    purge_after: datetime | None = None
    frozen_at: datetime | None = None
    purged_at: datetime | None = None
    active_branch_id: str | None = None
    current_turn_id: str | None = None
    turn_status: ExplorationTurnStatus | None = None
    last_completed_action: Literal["search", "synthesize", "await_user", "handoff"] | None = None
    
    # 探索阶段数据
    branch_summaries: list[BranchSummary] = Field(default_factory=list)
    exploration_result: ExplorationResult | None = None
    selected_candidate_id: str | None = None

    # 策略阶段数据
    spider_notes: Optional[List[SpiderNote]] = None
    platform_preference: Optional[PlatformPreference] = None
    content_strategy: Optional[ContentStrategy] = None
    quality_score: float = 0.0
    expanded_queries: Optional[List[str]] = None
    used_fallback: bool = False

    # RAG 补偿状态
    reindex_state: Literal["ok", "pending", "deadletter"] = "ok"
    reindex_attempts: int = 0
    
    # 生成阶段数据
    proposals: Optional[List[Proposal]] = None
    generated_notes: Optional[List[GeneratedNote]] = None
    similarity_report: Optional[Dict] = None
    
    # 错误信息
    error: Optional[dict] = None
    
    # 时间戳
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime
    last_user_activity_at: datetime
```

#### `get` / `update`
```python
class GetRequest(BaseModel):
    session_id: str
    fields: list[str] | None = None  # 指定获取的字段，None表示全部
    
class UpdateRequest(BaseModel):
    session_id: str
    updates: dict                    # 更新的字段
```

### 2.3 StrategyAgent (策略代理)

#### `execute`
主执行接口
```python
class StrategyExecuteRequest(BaseModel):
    session_id: str
    
class SpiderResult(BaseModel):
    success: bool
    posts: list[XHSPost] | None
    error: str | None
    attempts: int                   # 实际尝试次数
    
class StrategyExecuteResponse(BaseModel):
    session_id: str
    status: Literal["success", "spider_failed", "insufficient_data"]
    
    # 数据收集结果
    spider_result: SpiderResult
    posts_analyzed: int
    
    # 分析结果
    platform_preference: PlatformPreference | None
    content_strategy: ContentStrategy | None
    
    # 质量指标
    data_quality_score: float
    used_fallback: bool             # 是否使用了通用策略
    spider_cooldown_until: datetime | None = None
    
    # 错误信息
    error_message: str | None
```

### 2.4 XHSSpider (小红书爬虫)

#### `search`
```python
class SpiderSearchRequest(BaseModel):
    query: str
    limit: int = 50                 # 最大返回数量
    filters: dict | None = None     # 过滤条件 (时间范围、排序方式等)
    
class SpiderSearchResponse(BaseModel):
    success: bool
    posts: list[XHSPost] | None
    total_found: int | None         # 实际找到的数量
    error_code: str | None          # 错误代码
    error_message: str | None
```

### 2.5 EngagementAnalyzer (参与度分析器)

#### `calc_engagement`
```python
class CalcEngagementRequest(BaseModel):
    posts: list[XHSPost]
    
class CalcEngagementResponse(BaseModel):
    scored_posts: list[ScoredPost]
    summary: dict                   # 统计摘要
```

#### `analyze_preferences`
```python
class AnalyzePreferencesRequest(BaseModel):
    posts: list[XHSPost]
    top_k: int = 20                 # 只分析前K个高质量帖子
    
class AnalyzePreferencesResponse(BaseModel):
    preference: PlatformPreference
    confidence: float               # 分析置信度
    sample_size: int                # 实际分析的样本数
```

#### `score_proposals`
```python
class ScoreProposalsRequest(BaseModel):
    proposals: list[Proposal]
    platform_preference: PlatformPreference
    
class ScoreProposalsResponse(BaseModel):
    scored_proposals: list[Proposal]
```

### 2.6 RAGService (RAG服务)

#### Core Models
```python
class RAGDocument(BaseModel):
    """保存到单 collection Chroma 的精简文档结构"""
    doc_id: str                     # 复合 ID: "{session_id}:{note_id}"
    session_id: str                 # metadata.session_id，供 where 过滤
    note_id: str                    # 原始帖子ID
    title: str                      # 帖子标题
    content: str                    # 精简正文（通常为标题+首段）
    tags: list[str]                 # 标签列表
    embedding_vector: Optional[list[float]] = None
    engagement_score: float = 0.0
```

#### `index_documents`
```python
class IndexDocumentsRequest(BaseModel):
    session_id: str                 # 写入 metadata.session_id（逻辑隔离）
    documents: list[RAGDocument]    # chunked后的精简文档列表
    query: str                      # 原始查询，用于相关性过滤
    min_relevance: float = 0.3      # 最小相关性阈值
    
class IndexDocumentsResponse(BaseModel):
    success: bool
    indexed_count: int              # 成功索引的数量
    filtered_count: int             # 被过滤掉的数量
    quality_score: float            # 索引质量分数 (0-1)
    collection_name: str            # 固定为 "xhs_documents"
```

#### `query`
```python
class RAGQueryRequest(BaseModel):
    collection: str                 # 固定为 "xhs_documents"
    where: dict                     # {"session_id": "..."}
    query: str                      # 查询文本
    top_k: int = 3
    min_similarity: float = 0.0
    
class RAGQueryResult(BaseModel):
    document: RAGDocument           # 精简文档，非完整XHSPost
    similarity: float
    
class RAGQueryResponse(BaseModel):
    results: list[RAGQueryResult]
    query_time_ms: int
```

### 2.7 GenerationAgent (生成代理)

#### `generate`
主生成接口
```python
class GenerationRequest(BaseModel):
    session_id: str
    config: GenerationConfig | None = None
    
class GenerationConfig(BaseModel):
    num_proposals: int = 10         # 生成提案数量
    num_final_notes: int = 5        # 最终返回笔记数量
    embedding_rewrite_threshold: float = 0.6
    embedding_warning_threshold: float = 0.3
    lexical_warning_threshold: float = 0.4
    max_rewrite_attempts: int = 2       # 最大重写次数

class GenerationResponse(BaseModel):
    session_id: str
    status: Literal["success", "partial", "failed"]
    
    # 生成的笔记
    notes: list[GeneratedNote]
    
    # 统计信息
    total_proposals: int
    notes_generated: int
    notes_rewritten: int
    failed_count: int = 0
    similarity_warnings: list[str]
    
    # 处理时间
    generation_time_ms: int
    error_message: str | None = None
```

#### `generate_proposals` (内部)
```python
class GenerateProposalsRequest(BaseModel):
    content_strategy: ContentStrategy
    user_profile: UserProfile
    n: int = 10
    
class GenerateProposalsResponse(BaseModel):
    proposals: list[Proposal]
```

#### `generate_note` (内部，并行调用)
```python
class GenerateNoteRequest(BaseModel):
    proposal: Proposal
    content_strategy: ContentStrategy
    user_profile: UserProfile
    
class GenerateNoteResponse(BaseModel):
    note: GeneratedNote
```

#### `rewrite_note` (内部，去重时调用)
```python
class RewriteNoteRequest(BaseModel):
    original_note: GeneratedNote
    similar_posts: list[RAGQueryResult]
    diversify_instructions: str
    
class RewriteNoteResponse(BaseModel):
    rewritten_note: GeneratedNote
    changes_made: list[str]         # 修改说明
```

---

## 3. LLM Prompt Interfaces

### 3.1 Strategy Generation
```python
class StrategyPromptInput(BaseModel):
    mode: str                       # "data_driven" | "generic"
    user_query: str
    user_profile: UserProfile
    filtered_posts: list[XHSPost] | None
    platform_preference: PlatformPreference | None
    
class StrategyPromptOutput(BaseModel):
    content_strategy: ContentStrategy
```

### 3.2 Proposal Generation
```python
class ProposalPromptInput(BaseModel):
    content_strategy: ContentStrategy
    n: int
    
class ProposalPromptOutput(BaseModel):
    proposals: list[Proposal]
```

### 3.3 Note Generation
```python
class NotePromptInput(BaseModel):
    proposal: Proposal
    content_strategy: ContentStrategy
    user_profile: UserProfile
    
class NotePromptOutput(BaseModel):
    title: str
    content: str
    tags: list[str]
```

### 3.4 Rewrite Prompt
```python
class RewritePromptInput(BaseModel):
    original_note: GeneratedNote
    similar_posts: list[XHSPost]
    diversification_hints: list[str]
    
class RewritePromptOutput(BaseModel):
    title: str
    content: str
    tags: list[str]
    changes_description: str
```

---

## 4. Error Schemas

```python
class ErrorResponse(BaseModel):
    error_code: str
    error_message: str
    error_details: dict | None
    retryable: bool
    suggested_action: str | None

# 常见错误码
ERROR_CODES = {
    "SPIDER_SERVICE_UNAVAILABLE": "爬虫服务失败",
    "SPIDER_RATE_LIMITED": "爬虫请求频率限制",
    "LLM_TIMEOUT": "LLM 服务超时",
    "LLM_RATE_LIMITED": "LLM 速率限制",
    "INSUFFICIENT_DATA": "数据不足以生成策略",
    "SESSION_FROZEN": "会话已冻结，请先恢复会话",
    "SESSION_PURGED": "会话已被清理，无法恢复",
    "SESSION_NOT_FOUND": "会话不存在",
    "JOB_NOT_FOUND": "任务不存在",
    "BUDGET_EXCEEDED": "生成预算已用尽，请缩小范围或重试",
    "REINDEX_DEADLETTER": "索引重建失败，请稍后重试",
    "INVALID_QUERY": "无效的查询"
}
```

---

## 5. SSE Event Schemas (流式更新)

```python
class SessionEventPayload(BaseModel):
    message: str
    progress: float | None = None       # 0-100, 无进度则 None
    error_code: str | None = None
    details: dict = Field(default_factory=dict)

class SessionEvent(BaseModel):
    event_id: int
    event_name: Literal[
        "stage_changed",
        "task_progress",
        "task_failed",
        "task_completed",
        "session_frozen",
        "session_resumed",
        "session_purged",
        "heartbeat",
    ]
    session_id: str
    job_id: str | None = None
    stage: str | None = None
    timestamp: datetime
    payload: SessionEventPayload
```

### 5.1 SSE Endpoint Contract

```http
GET /sessions/{session_id}/events
Accept: text/event-stream
Last-Event-ID: 12345
```

- 仅支持标准重连游标 `Last-Event-ID`
- 服务端补发 `event_id > Last-Event-ID` 的历史事件（受 `SSE_REPLAY_LIMIT` 限制）
- 事件推送语义：`at-least-once`，客户端需按 `event_id` 去重

### 5.2 Resume Endpoint Contract

```http
POST /sessions/{session_id}/resume
```

```python
class ResumeSessionResponse(BaseModel):
    session_id: str
    lifecycle_state: Literal["alive"]
    resumed_jobs: int                 # 幂等：重复调用可返回 0
    alive_until: datetime
    purge_after: datetime
```

### 5.3 Job Status Endpoint Contract

```http
GET /jobs/{job_id}
```

```python
class JobStatusResponse(BaseModel):
    job_id: str
    session_id: str
    job_type: Literal["explore", "strategy", "generate"]
    status: Literal["queued", "paused", "running", "retrying", "succeeded", "failed", "cancelled"]
    attempts: int
    max_attempts: int
    last_error_code: str | None = None
    last_error_message: str | None = None
    cancel_reason: str | None = None
```

### 5.4 Session Status Endpoint Contract

```http
GET /sessions/{session_id}
```

```python
class SessionStatusResponse(BaseModel):
    session_id: str
    user_id: str
    stage: Literal["init", "exploring", "candidate_selected", "strategy", "generation", "completed", "failed"]
    lifecycle_state: Literal["alive", "frozen", "purged"]
    active_branch_id: str | None = None
    current_turn_id: str | None = None
    turn_status: ExplorationTurnStatus | None = None
    last_completed_action: Literal["search", "synthesize", "await_user", "handoff"] | None = None
    alive_until: datetime | None = None
    spider_cooldown_until: datetime | None = None
    purge_after: datetime | None = None
    job_status: Literal["queued", "paused", "running", "retrying", "succeeded", "failed", "cancelled"] | None = None
    current_job_id: str | None = None
    token_used: int = 0
    token_budget: int = 0
    budget_remaining: int = 0
    budget_degraded: bool = False
    reindex_state: Literal["ok", "pending", "deadletter"] = "ok"
    reindex_attempts: int = 0
    branch_summaries: list[BranchSummary] = Field(default_factory=list)
    exploration_result: ExplorationResult | None = None
    selected_candidate_id: str | None = None
    created_at: datetime
    updated_at: datetime
    error: str | None = None
```

字段职责说明：
- `stage`：会话主链路阶段，仅用于表达当前业务推进位置
- `job_status`：当前活跃或最近相关后台任务的执行状态
- `spider_cooldown_until`：Spider 临时冷却窗口结束时间，不等同于 `frozen`
- `reindex_state/reindex_attempts`：RAG 补偿重建的可观测状态

### 5.5 任务与会话生命周期对齐（按判定顺序）

**判定主线**
1. 规则 1：`active job` 只包含 `queued/retrying/running`。
   原因：生命周期只看主链路是否仍有未完成工作。
2. 规则 2：只要存在 `active job`，Session 就保持 `alive`。
   原因：系统仍在处理用户主任务时，不能判定为冻结。
3. 规则 3：`retrying` 即使 `not_before` 未到，也仍是 `active job`。
   原因：等待重试不等于任务结束。
4. 规则 4：用户显式暂停且仍有 `active job` 时，仅记录 `pause_requested`，不立刻进入 `frozen`。
   原因：当前任务可收尾，但不能继续派生后续任务。
5. 规则 5：`pause_requested` 且无 `active job` 后，Session 转为 `frozen`。
   原因：这时系统才真正进入静止态。
6. 规则 6：无 `active job` 时，再按 `last_user_activity_at` 判定：`<=24h` 保持 `alive`，`(24h,10d]` 为 `frozen`。
   原因：只有没有未完成工作时，用户活跃度才是主判据。
7. 规则 7：无 `active job` 且用户无交互 `>10d` 时，Session 转为 `purged`。
   原因：超出恢复窗口后应进入不可恢复清理态。

**非判定因素 / 特殊语义**
- `reindex_state/reindex_attempts` 仅表示系统补偿状态，不计入 `active job`。
- `spider_cooldown_until` 不等同于 `frozen`，只是 Spider 临时冷却窗口。
- 前端轮询、SSE keep-alive、自动重连不刷新 `last_user_activity_at`。
- `pause_requested` 是内部过渡控制位，不属于公开 `lifecycle_state` 枚举。
- `cancel` 不复用 `pause/frozen` 语义，而是直接终止未完成 job。

**执行期约束**
- `frozen` 时：`queued/retrying -> paused`，`running` 仅允许当前步骤收尾。
- `purged` 时：未完成任务统一 `cancelled`，`cancel_reason=session_purged`。
- `resume` 必须幂等：重复调用仅返回 `resumed_jobs=0`，不重复激活任务。
- worker 抢占任务时必须校验 `lifecycle_state='alive'`。
- worker 必须依赖 `lease_expires_at` 回收僵尸 `running`，将其转为 `retrying` 或 `failed`。
- 若 `pause_requested=true` 或会话已不满足 `alive`，则禁止自动派生下一阶段 job。
- 同一 `session_id` 同时只允许一个 `running` job。

### 5.5.1 API Corner Cases

| 场景 | 推荐返回 / 表现 | 说明 |
|------|----------------|------|
| `session_id` 不存在 | `404` + `SESSION_NOT_FOUND` | 适用于 `/sessions/*` 相关端点 |
| `job_id` 不存在 | `404` + `JOB_NOT_FOUND` | 适用于 `/jobs/{job_id}` |
| `explore/strategy/generate` 在错误阶段调用 | `409` + `INVALID_STAGE` | 如 `init` 时直接 `generate` |
| `exploration/confirm` 不在 `exploring + turn_status=awaiting_user_input|degraded` 调用 | `409` + `EXPLORATION_CONFIRM_INVALID_STAGE` | 需先完成当前轮探索 |
| `exploration/confirm` 的 `candidate_id` 不存在 | `404` + `EXPLORATION_CANDIDATE_NOT_FOUND` | 提示重新选择候选 |
| `exploration/confirm` 的 `candidate_id` 属于 stale turn | `409` + `EXPLORATION_CANDIDATE_STALE` | 提示刷新当前分支结果后重选 |
| `explore/strategy/generate` 在 `frozen` 会话调用 | `423` + `SESSION_FROZEN` | 需先 `resume` |
| `explore/strategy/generate` 在 `purged` 会话调用 | `410` + `SESSION_PURGED` | 不可恢复 |
| Spider 冷却窗口内再次调用 | `429`，并返回 `spider_cooldown_until` | 表明是临时冷却，不是冻结 |
| 重复 `Idempotency-Key` | `202`，返回已有 `job_id` | 不重复创建新 job |
| `resume` 时没有 `paused` 任务 | `200` + `resumed_jobs=0` | 必须幂等 |
| 用户显式暂停时仍有 `running` job | 先记录 `pause_requested=true`（或等价内部语义），不立刻返回 `frozen` | 当前 job 可收尾，但不得继续派生 |
| 前端轮询 / SSE keep-alive 持续存在 | 不应改变 `alive_until` / `last_user_activity_at` | 不可把连接保活当成用户交互 |
| 预算超限 | `BUDGET_EXCEEDED`，允许返回 partial result | 不应继续后续生成 |
| 生成阶段部分 slot 失败 | `status="partial"` 或等价错误码 | 主链路允许部分成功 |
| 当前关键指针失效（active_branch/current_turn/selected_candidate） | `409` + `EXPLORATION_STATE_INCONSISTENT` 或 fail-safe 快照 | 禁止自动猜测修复 branch/turn/candidate |

### 5.5.2 Recovery & Replay Corner Cases

| 场景 | 约定 | 目的 |
|------|------|------|
| lease 过期回收 | `running -> retrying/failed`，允许再次执行或终止 | 支持单机进程异常恢复 |
| `running` job 长时间不回收 | 必须通过周期性 lease sweep 转为 `retrying/failed` | 避免 Session 被僵尸任务永久保持 `alive` |
| exploration 恢复 | 先恢复 `jobs`，再读取 `sessions` 指针，再按 `active_branch_id/current_turn_id` 装配 exploration 数据 | 明确 `jobs > sessions > exploration_*` 的恢复优先级 |
| orphan roll / candidate | 不展示、不允许 confirm、不参与恢复，可延后清理 | 避免脏数据反向影响当前业务状态 |
| 业务写入重复执行 | 必须依赖 UPSERT / 幂等写入 | 适配 `at-least-once` 语义 |
| `retrying` 但 `not_before` 未来才到 | 仍视为 active job，不可冻结会话 | 避免把延迟重试误判为“无任务” |
| `frozen` 时已有 `running` 任务 | 允许当前步骤收尾，不得派生下一阶段任务 | 降低冻结瞬间的竞态风险 |
| 上一阶段完成后准备自动派生下一阶段 | 若 `pause_requested=true` 或会话已满足冻结条件，则不得自动 enqueue | 保证暂停/冻结语义优先于任务链派生 |
| `purged` 时仍有未完成任务 | 统一 `cancelled`，并写 `cancel_reason=session_purged` | 保证清理语义可追踪 |
| `reindex_state=pending` | 主流程可继续，但状态必须可观测且不计入 active job | 补偿失败不阻塞主流程，也不应阻止冻结 |
| `reindex_state=deadletter` | 检索能力可能降级，需提示人工处理 | 明确补偿已超限 |
| 用户显式取消当前任务 | `queued/paused/retrying/running -> cancelled` | 区分“暂停后可恢复”与“终止当前执行” |
| SSE 断线重连 | 基于 `Last-Event-ID` 补发，客户端按 `event_id` 去重 | 保证事件流恢复 |

### 5.6 SessionEventPayload 最小 JSON Schema（必须）

```json
{
  "type": "object",
  "required": ["message", "progress", "error_code", "details"],
  "properties": {
    "message": { "type": "string", "minLength": 1 },
    "progress": { "type": ["number", "null"], "minimum": 0, "maximum": 100 },
    "error_code": { "type": ["string", "null"] },
    "details": { "type": "object" }
  },
  "additionalProperties": true
}
```

### 5.7 Observability 告警契约（内部）

```python
class AlertRecord(BaseModel):
    id: int
    rule_name: str
    severity: Literal["info", "warning", "critical"]
    status: Literal["open", "resolved", "suppressed"]
    minute_bucket: datetime
    fired_at: datetime
    resolved_at: datetime | None = None
    payload_json: dict
```
