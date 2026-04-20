# 数据模型关系图

## 1. 实体关系图 (ER Diagram)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                 Session                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│ PK  session_id: str                                                         │
│     user_id: str ────────────────┐                                          │
│     user_query: str              │                                          │
│     stage: str                   │  init|exploring|candidate_selected|strategy|generation|completed|failed│
│     active_branch_id: str        │  当前 active exploration branch          │
│     current_turn_id: str         │  当前 exploration turn 锚点             │
│     selected_candidate_id: str   │  已确认候选（若存在）                    │
│     lifecycle_state: str         │  alive|frozen|purged                     │
│     pause_requested: bool        │  用户请求暂停但 active job 未清空         │
│     spider_cooldown_until: dt    │  Spider 冷却窗口结束时间                 │
│     reindex_state: str           │  ok|pending|deadletter                   │
│     reindex_attempts: int        │                                          │
│     created_at: datetime         │                                          │
│     updated_at: datetime         │                                          │
│                                  │                                          │
│ FK  content_strategy_id ─────┐   │                                          │
│     spider_results[] ────────┼───┼──────► XHSPost[]                         │
│     platform_pref_id ────────┤   │                                          │
│     proposals[] ─────────────┼───┼──────► Proposal[]                        │
│     generated_notes[] ───────┼───┼──────► GeneratedNote[]                   │
│                              │   │                                          │
└──────────────────────────────┼───┼──────────────────────────────────────────┘
                               │   │
                               │   │
┌──────────────────────────────┼───┴──────────────────────────────────────────┐
│                              ▼                                              │
│  ┌─────────────────┐    ┌─────────────────────┐    ┌───────────────────┐   │
│  │  ContentStrategy│    │ PlatformPreference  │    │    UserProfile    │   │
│  ├─────────────────┤    ├─────────────────────┤    ├───────────────────┤   │
│  │ PK strategy_id  │    │ PK pref_id          │    │ PK user_id        │   │
│  │    positioning  │    │    avg_title_length │    │    account_type   │   │
│  │    target_audience│  │    popular_tags[]   │    │    niche          │   │
│  │    content_pillars│  │    optimal_times[]  │    │    brand_voice    │   │
│  │    key_messaging │   │    content_patterns │    │    target_goals   │   │
│  │    content_types │   │                     │    │                   │   │
│  │    posting_strategy│ │                     │    │                   │   │
│  │    data_quality  │   │                     │    │                   │   │
│  └─────────────────┘    └─────────────────────┘    └───────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────────┐
│                       Exploration Branch / Roll / Candidate                 │
├─────────────────────────────────────────────────────────────────────────────┤
│ Session 1 ─────► N Branches 1 ─────► N Rolls 1 ─────► N Candidates         │
│                                                                             │
│  ┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────────┐ │
│  │ exploration_branches │   │ exploration_rolls    │   │ exploration_     │ │
│  ├──────────────────────┤   ├──────────────────────┤   │ candidates       │ │
│  │ PK branch_id         │   │ PK turn_id           │   ├──────────────────┤ │
│  │ FK session_id        │   │ FK session_id        │   │ PK candidate_id  │ │
│  │ branch_index         │   │ FK branch_id         │   │ FK session_id    │ │
│  │ status               │   │ action               │   │ FK branch_id     │ │
│  │ title_summary        │   │ turn_status          │   │ FK turn_id       │ │
│  │ confirmed_candidate_id│  │ degraded_mode        │   │ title            │ │
│  │ created_at/updated_at│   │ rewrite_suggestions  │   │ rationale        │ │
│  └──────────────────────┘   │ is_stale             │   │ angle            │ │
│                             │ superseded_by_turn_id│   │ why_now          │ │
│                             │ created_at/updated_at│   │ evidence_refs    │ │
│                             └──────────────────────┘   │ is_selected      │ │
│                                                        │ created_at/...   │ │
│                                                        └──────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────────┐
│                                XHSPost                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│ PK  note_id: str                                                            │
│     title: str                                                              │
│     content: str                                                            │
│     author: str                                                             │
│     liked_count: int                                                        │
│     collected_count: int                                                    │
│     comment_count: int                                                      │
│     share_count: int                                                        │
│     note_url: str                                                           │
│     tags: list[str]                                                         │
│     images: list[str]                                                       │
│     raw_data: dict (optional upstream payload)                              │
│                                                                             │
│     ┌───────────────┐                                                       │
│     │ EngagementScore (inline)                                              │
│     ├───────────────┤                                                       │
│     │ total_engagement                                                       │
│     │ engagement_rate                                                        │
│     │ virality_score                                                         │
│     │ weighted_score                                                         │
│     └───────────────┘                                                       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────────┐
│                    RAGDocument (Chroma 存储格式)                              │
├─────────────────────────────────────────────────────────────────────────────┤
│ PK  doc_id: str                          ← 复合 ID: {session_id}:{note_id}   │
│     session_id: str                     ← metadata.session_id                │
│     note_id: str                        ← 原始 XHSPost ID                    │
│     title: str                          ← 帖子标题                          │
│     content: str                        ← 精简正文（通常为首段）            │
│     tags: list[str]                     ← 标签列表                          │
│     embedding_vector: list[float]       ← 文档向量（由 embedding model 生成）│
│     engagement_score: float             ← 检索质量辅助特征                  │
│                                                                             │
│  说明: 由 XHSPost chunk 而来，只保存精简信息用于 RAG 检索                     │
└─────────────────────────────────────────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────────┐
│                        Proposal → GeneratedNote 流程                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌──────────────┐         ┌─────────────────┐                              │
│   │   Proposal   │  ───►   │  GeneratedNote  │                              │
│   ├──────────────┤         ├─────────────────┤                              │
│   │ proposal_id  │         │ note_id         │                              │
│   │ angle        │         │ title           │                              │
│   │ hook         │         │ content         │                              │
│   │ outline      │         │ tags[]          │                              │
│   │ target_emotion│        │ similarity_check│                              │
│   │ content_pillars[]      │ generation_params│                             │
│   │ suggested_tags[]       │ cover_design_prompt│                            │
│   │ score        │         │ suggested_update_time│                          │
│   └──────────────┘         └─────────────────┘                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 2. 状态流转图

```
                           ┌─────────────┐
                           │   START     │
                           └──────┬──────┘
                                  │
                                  ▼
                    ┌─────────────────────────┐
                    │    Orchestrator.init    │
                    │    Session Bootstrap    │
                    └───────────┬─────────────┘
                                │
            ┌───────────────────┼───────────────────┐
            ▼                   ▼                   ▼
    ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
    │  EXPLORATION  │   │    EDITING    │   │  OPTIMIZATION │
    │  (Planner)    │   │  (Main Flow)  │   │  (Future)     │
    └───────┬───────┘   └───────┬───────┘   └───────────────┘
            │                   │
            ▼                   ▼
    ┌───────────────────────┐   ┌───────────────────────┐
    │ ExplorationPlanner    │   │  StrategyAgent.execute│
    │  ┌─────────────────┐  │   │  ┌─────────────────┐  │
    │  │ SEARCHING       │  │   │  │ INITIALIZED     │  │
    │  └────────┬────────┘  │   │  └────────┬────────┘  │
    │           ▼           │   │           ▼           │
    │  ┌─────────────────┐  │   │  ┌─────────────────┐  │
    │  │ SYNTHESIZING    │  │   │  │ SPIDER_RUNNING  │  │
    │  └────────┬────────┘  │   │  │ (retry loop)    │  │
    │           ▼           │   │  └────────┬────────┘  │
    │  ┌─────────────────┐  │   │           ▼           │
    │  │ AWAITING_USER   │  │   │  ┌─────────────────┐  │
    │  │ _INPUT          │  │   │  │ ANALYZING       │  │
    │  └────────┬────────┘  │   │  │ (engagement)    │  │
    │           ▼           │   │  └────────┬────────┘  │
    │  ┌─────────────────┐  │   │           ▼           │
    │  │ CANDIDATE_      │  │   │  ┌─────────────────┐  │
    │  │ SELECTED        │  │   │  │ INDEXING_RAG    │  │
    │  └─────────────────┘  │   │  │ (quality check) │  │
    └───────────┬───────────┘   │  └────────┬────────┘  │
                │               │           ▼           │
                └──────────────►│  ┌─────────────────┐  │
                                │  │ GENERATING_STRAT│  │
                                │  │ (LLM call)      │  │
                                │  └────────┬────────┘  │
                                │           ▼           │
                                │  ┌─────────────────┐  │
                                │  │ STRATEGY_READY  │  │
                                │  └─────────────────┘  │
                                └───────────────────────┘
                                │
            │                   ▼
            │       ┌───────────────────────┐
            │       │ GenerationAgent.generate
            │       │  ┌─────────────────┐  │
            │       │  │ GENERATING      │  │
            │       │  │ _PROPOSALS      │  │
            │       │  └────────┬────────┘  │
            │       │           ▼           │
            │       │  ┌─────────────────┐  │
            │       │  │ SCORING         │  │
            │       │  └────────┬────────┘  │
            │       │           ▼           │
            │       │  ┌─────────────────┐  │
            │       │  │ GENERATING_NOTES│  │
            │       │  │ (parallel x5)   │  │
            │       │  └────────┬────────┘  │
            │       │           ▼           │
            │       │  ┌─────────────────┐  │
            │       │  │ CHECKING_SIM    │  │
            │       │  │ (similarity)    │  │
            │       │  └────────┬────────┘  │
            │       │           ▼           │
            │       │  ┌─────────────────┐  │
            │       │  │ REWRITING(if    │  │
            │       │  │  needed)        │  │
            │       │  └────────┬────────┘  │
            │       │           ▼           │
            │       │  ┌─────────────────┐  │
            │       │  │ COMPLETED       │  │
            │       │  └─────────────────┘  │
            │       └───────────────────────┘
            │                   │
            └───────────────────┼───────────────────┐
                                ▼                   ▼
                       ┌─────────────────┐   ┌─────────────┐
                       │  Return to User │   │    ERROR    │
                       │  5 Notes        │   │ (with msg)  │
                       └─────────────────┘   └─────────────┘
```

### 2.1 生命周期状态机（保活策略）

```
alive (active job 或 <=24h)
  │
  ├── 用户请求暂停且仍有 active job ──► pause_requested (内部过渡态)
  │                                         │
  │                                         └── active job 清空 ──► frozen
  │
  ├── 超过24h无活动 ──► frozen (24h~10d)
  │                          │
  │                          ├── POST /sessions/{id}/resume ──► alive
  │                          │
  │                          └── 超过10d ──► purged
  │
  └── 超过10d ──► purged
```

说明：
**判定主线**
1. 规则 1：`active job` 仅包含 `queued/retrying/running`。
   原因：生命周期只看主链路是否仍有未完成工作。
2. 规则 2：只要存在 `active job`，Session 就保持 `alive`。
   原因：系统仍在执行用户任务时，不能视为冻结。
3. 规则 3：`retrying` 即使尚未到 `not_before`，也仍计入 `active job`。
   原因：延迟重试不等于任务完成。
4. 规则 4：用户显式暂停且仍有 `active job` 时，先进入 `pause_requested` 过渡态。
   原因：当前任务可收尾，但后续任务链必须被拦截。
5. 规则 5：`pause_requested` 且无 `active job` 后，Session 转为 `frozen`。
   原因：此时系统才真正停止推进。
6. 规则 6：无 `active job` 时，再看用户活跃度：`<=24h alive`，`(24h,10d] frozen`。
   原因：用户活跃度只在系统空闲时参与判定。
7. 规则 7：无 `active job` 且用户无交互 `>10d` 时，转为 `purged`。
   原因：超出保留窗口后进入不可恢复清理态。

**非判定因素 / 特殊语义**
- `pause_requested`：内部控制位，不是公开 `lifecycle_state`。
- `reindex_state/reindex_attempts`：补偿状态，不计入 `active job`。
- `spider_cooldown_until`：Spider 冷却窗口，不等同于 `frozen`。
- 前端轮询、SSE keep-alive、自动重连不刷新 `last_user_activity_at`。
- `cancel` 不复用 `pause/frozen` 语义，而是直接终止未完成 job。

**执行期约束**
- `frozen` 时：`queued/retrying -> paused`，`running` 仅允许当前步骤收尾。
- `resume` 时：仅 `paused -> queued`，且幂等（重复调用可 `resumed_jobs=0`）。
- `purged` 时：未完成任务统一 `cancelled`，并写 `cancel_reason=session_purged`。
- Worker 抢占任务前二次校验 `lifecycle_state='alive'`。
- Worker 需依赖 `lease_expires_at` 周期性回收超时 `running`。
- 自动派生下一阶段任务前，必须再次校验 `lifecycle_state='alive'` 且 `pause_requested=false`。
- 同一 `session_id` 任意时刻最多一个 `running` 任务。

## 3. 存储架构

### 3.1 双存储架构（aiosqlite + Chroma）

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Storage Architecture                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────────────────────┐    ┌─────────────────────────────────┐   │
│   │      aiosqlite (SQLite)     │    │         Chroma DB               │   │
│   │      ─────────────────      │    │         ───────────             │   │
│   │  Single-file, zero-config   │    │  Single collection + metadata   │   │
│   │  WAL mode for concurrency   │    │  Vector search & retrieval      │   │
│   │  Async wrapper for asyncio  │    │  Filtered by session_id         │   │
│   └─────────────┬───────────────┘    └─────────────────────────────────┘   │
│                 │                                    ▲                      │
│   ┌─────────────┴───────────────┐                    │                      │
│   │        sessions 表           │                    │                      │
│   │  ┌─────────────────────┐    │                    │                      │
│   │  │ session_id (PK)     │    │                    │                      │
│   │  │ user_id, user_query │    │                    │                      │
│   │  │ stage, lifecycle    │    │                    │                      │
│   │  │ active_branch_id,   │    │                    │                      │
│   │  │ current_turn_id,    │    │                    │                      │
│   │  │ selected_candidate_id│   │                    │                      │
│   │  │ spider_cooldown     │    │                    │                      │
│   │  │ reindex_state,      │    │                    │                      │
│   │  │ reindex_attempts    │    │                    │                      │
│   │  │ metadata (JSON)     │────┼────────────────────┘                      │
│   │  │ created_at, etc.    │    │  doc_id = session_id:note_id              │
│   │  └─────────────────────┘    │  metadata.session_id for filter            │
│   │                             │                                           │
│   │  ┌─────────────────────┐    │                                           │
│   │  │ exploration_* 表群   │    │                                           │
│   │  │ - branches          │    │                                           │
│   │  │ - rolls             │    │                                           │
│   │  │ - candidates        │    │                                           │
│   │  └─────────────────────┘    │                                           │
│   │                             │                                           │
│   │  ┌─────────────────────┐    │                                           │
│   │  │ session_data 表      │    │                                           │
│   │  │ - spider_results    │    │  Chroma:                                  │
│   │  │ - content_strategy  │    │  - RAGDocument[] (精简文档)               │
│   │  │ - generated_notes   │    │  - embedding_vector (向量)                │
│   │  │ - similarity_report │    │  - 单 collection + metadata 过滤隔离      │
│   │  └─────────────────────┘    │                                           │
│   └─────────────────────────────┘                                           │
│                                                                             │
│   Storage Design Principles:                                                │
│   • aiosqlite: OLTP workload, frequent updates, small transactions          │
│   • Chroma: Vector search, read-heavy, batch inserts                        │
│   • sessions 只存轻量指针；exploration_* 保存 branch/roll/candidate 历史     │
│   • jobs = task truth，sessions = current-state truth                       │
│   • SessionManager owns both, coordinates lifecycle                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.3 SSE 事件流存储

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            session_events                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│ event_id (PK, autoincrement)                                               │
│ session_id (index with event_id)                                            │
│ job_id                                                                      │
│ event_name                                                                  │
│ stage                                                                       │
│ payload_json  (message/progress/error_code/details)                         │
│ created_at                                                                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

重连补发：
- 客户端通过 `Last-Event-ID` 重连
- 服务端查询 `event_id > Last-Event-ID` 的事件并按序补发（受 `SSE_REPLAY_LIMIT` 限制）

### 3.4 持久任务队列与可观测存储

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                   jobs                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│ id (PK) | session_id | job_type | status                                   │
│ status: queued/paused/running/retrying/succeeded/failed/cancelled          │
│ attempts | max_attempts | not_before | lease_expires_at                    │
│ idempotency_key (UNIQUE with session_id+job_type)                          │
│ last_error_code | last_error_message | cancel_reason                        │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                                   alerts                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ rule_name + minute_bucket + status(open/resolved/suppressed)               │
│ payload_json: window_start/window_end/current_value/threshold              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 组件依赖图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            System Architecture                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         User Interface                               │   │
│  └────────────────────────────────┬────────────────────────────────────┘   │
│                                   │                                         │
│                                   ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      Orchestrator (入口)                             │   │
│  │  • Session 协调 • 任务分发 • 错误处理                                 │   │
│  └────────────────────┬────────────────────────────────────────────────┘   │
│                       │                                                     │
│       ┌───────────────┼───────────────┐                                    │
│       ▼               ▼               ▼                                    │
│  ┌─────────┐    ┌──────────┐    ┌───────────┐                              │
│  │Session  │    │Strategy  │    │Generation │                              │
│  │Manager  │◄──►│Agent     │    │Agent      │                              │
│  │(aiosqlite)   │(策略生成) │    │(内容生成)  │                              │
│  └────┬────┘    └────┬─────┘    └─────┬─────┘                              │
│       │              │                │                                    │
│       │    ┌─────────┼────────────────┼─────────┐                          │
│       │    │         │                │         │                          │
│       │    ▼         ▼                ▼         ▼                          │
│       │ ┌──────┐ ┌────────┐      ┌────────┐ ┌──────┐                       │
│       │ │Spider│ │Engage  │      │  RAG   │ │ LLM  │                       │
│       │ │      │ │Analyzer│      │Service │ │      │                       │
│       │ └──┬───┘ └───┬────┘      └───┬────┘ └──┬───┘                       │
│       │    │         │               │         │                          │
│       │    ▼         ▼               ▼         ▼                          │
│       │ ┌─────────────────────────────────────────────┐                   │
│       │ │              External Services              │                   │
│       │ │  • XHS Crawler  • Chroma  • LLM API         │                   │
│       │ └─────────────────────────────────────────────┘                   │
│       │                                                                   │
│       └──────────────────────────────────────────────────────────────┐    │
│                                                                      ▼    │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                           Data Storage                               │  │
│  │  • Session State (aiosqlite)  • Vector (Chroma)  • Logs             │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 4. 数据流时序与 Schema 对照

```
时序图中的步骤                    对应的 Schema/Interface
─────────────────────────────────────────────────────────────────────────────

1. U -> API: user_query + user_id
   Schema: RouteRequest {user_id, user_query}

2. API -> SessionManager: init(session_id, user_id, user_query)
   Schema: SessionInitRequest
   Result: SessionSnapshot (stage: "init")

3. Orchestrator -> EP: execute(session_id) [mode=exploration]
   Schema: EnqueueExplorationResponse {stage: "exploring", job_status: "queued"}

4. EP -> SearchWorker: SearchPlan(initial/refine/refresh)
   Schema: SearchPlan {action, queries}
   Result: EvidenceSummary + SearchStats

5. SearchWorker -> ExplorationStateStore: persist(branch pointer, current turn, refs, search_stats)
   Schema: ExplorationTurn {action, turn_status: "searching" -> "synthesizing"}

6. EP -> SynthesisWorker: synthesize(EvidenceSummary)
   Schema: ExplorationResult {current_turn, previous_turn, candidates, rewrite_suggestions}

7. SynthesisWorker -> ExplorationStateStore: persist(roll, candidates, stale previous roll)
   Result: SessionSnapshot (stage: "exploring", turn_status: "awaiting_user_input")

8. API -->> U: candidate cards or zero-result rewrite suggestions

9. Orchestrator -> SA: execute(session_id) [after candidate_selected]
   Schema: StrategyExecuteRequest
   
10. SA -> SP: search(query) [loop 1-5]
   Schema: SpiderSearchRequest {query, limit}
   Result: SpiderSearchResponse {success, posts[], error}
   
11. SA -> EA: calc_engagement(list[XHSPost])
   Schema: CalcEngagementRequest {posts}
   Result: CalcEngagementResponse {scored_posts}
   
12. SA -> RAG: index_documents(session_id, documents, query)
   Note: XHSPost → chunk → RAGDocument (title+1st_para+tags+embedding)
   Schema: IndexDocumentsRequest {session_id, documents, query}
   Result: IndexDocumentsResponse {indexed_count, quality_score}
   
13. SA -> EA: analyze_preferences(filtered_posts)
   Schema: AnalyzePreferencesRequest {posts, top_k}
   Result: AnalyzePreferencesResponse {preference, confidence}
   
14. SA -> LLM: generate_strategy(...)
   Schema: StrategyPromptInput {mode, query, profile, posts, preference}
   Result: ContentStrategy
   
15. SA -> SS: update(ContentStrategy, PlatformPreference)
   Schema: UpdateRequest {updates: {content_strategy, platform_preference}}

16. Orchestrator -> GA: generate(session_id)
    Schema: GenerationRequest {session_id, config}
    
17. GA -> LLM: generate_proposals(..., n=10)
    Schema: ProposalPromptInput {content_strategy, n}
    Result: list[Proposal]
    
18. GA -> EA: score_proposals(list[Proposal], PlatformPreference)
    Schema: ScoreProposalsRequest {proposals, platform_preference}
    Result: ScoreProposalsResponse {scored_proposals}
    
19. GA -> LLM: generate_note(Proposal, ContentStrategy) [par x5]
    Schema: NotePromptInput {proposal, content_strategy, user_profile}
    Result: NotePromptOutput {title, content, tags}
    
20. GA -> RAG: query(collection=xhs_documents, where={session_id}, query=note.content, top_k=3)
    Schema: RAGQueryRequest {collection, where, query, top_k}
    Result: RAGQueryResponse {results: [{document: RAGDocument, similarity}]}
    
21. GA -> LLM: rewrite_note(note, diversify=True) [if similarity>0.6]
    Schema: RewritePromptInput {original_note, similar_posts}
    Result: RewritePromptOutput {title, content, tags}
    
22. GA -> SS: update(list[GeneratedNote], similarity_report)
    Schema: UpdateRequest {updates: {generated_notes, similarity_report}}
    
23. API -->> U: 5 notes + similarity_warning
   Schema: GenerationResult {notes[], similarity_warnings[]}
```

## 5. 关键字段说明

### Session.stage 生命周期

| 状态值 | 说明 | 进入条件 |
|--------|------|----------|
| init | 会话已初始化 | `POST /sessions` 完成 |
| exploring | exploration 会话正在搜索、收敛或等待用户 refine/refresh/confirm | `POST /sessions/{id}/explore` |
| candidate_selected | 用户已确认候选，等待进入 strategy | `POST /sessions/{id}/exploration/confirm` |
| strategy | 策略阶段（入队或执行中） | `POST /sessions/{id}/strategy` 或 worker 执行 |
| generation | 生成阶段（入队或执行中） | `POST /sessions/{id}/generate` 或 worker 执行 |
| completed | 全部完成 | 笔记生成完成 |
| failed | 任务失败 | 达到最大重试或永久错误 |

### Session / Job / Reindex 状态职责

| 字段 | 作用 | 说明 |
|------|------|------|
| `Session.stage` | 表达会话主链路推进位置 | `init -> exploring -> candidate_selected -> strategy -> generation -> completed/failed` |
| `Session.active_branch_id` | 表达当前工作台正在查看的 exploration branch | 新 branch 创建或用户切换 branch 时更新 |
| `Session.turn_status` | 表达 exploration 当前轮状态 | `searching/synthesizing/awaiting_user_input/degraded/failed` |
| `Session.lifecycle_state` | 表达会话保活/恢复状态 | `alive/frozen/purged` |
| `pause_requested` | 表达“用户已请求暂停，但当前仍有 active job” | 内部控制位，不对外暴露为生命周期枚举 |
| `spider_cooldown_until` | 表达 Spider 临时冷却窗口 | 存在时拒绝新的 Spider 相关入队，但不改变 `lifecycle_state` |
| `Job.job_type` | 表达后台任务类型 | `explore` / `strategy` / `generate` |
| `Job.status` | 表达后台任务执行状态 | `queued/running/retrying/succeeded/failed/...` |
| `reindex_state/reindex_attempts` | 表达 RAG 补偿重建状态 | `ok/pending/deadletter` + 当前尝试次数 |

### Exploration Truth Sources & Recovery

| 层级 | 作用 | 恢复优先级 |
|------|------|-----------|
| `jobs` | 任务运行、待执行、重试、lease 恢复 | 1 |
| `sessions` | 当前 stage、active branch/current turn/selected candidate 指针 | 2 |
| `exploration_branches / exploration_rolls / exploration_candidates` | branch/roll/candidate 内容、展示与历史 | 3 |

强约束：
- 恢复时必须先恢复 `jobs`，再读取 `sessions`，最后按 `active_branch_id/current_turn_id` 读取 exploration 三表
- 不允许扫描 exploration 三表猜当前 branch、turn 或 selected candidate
- orphan roll / candidate 不展示、不允许 confirm、不参与恢复
- 任一关键指针失效时直接 fail-safe：`EXPLORATION_STATE_INCONSISTENT`

### Exploration Transaction Boundaries

| 事务 | 固定写入顺序 |
|------|-------------|
| 新 branch + initial | insert `exploration_branches` -> insert 首个 `exploration_rolls` -> update `sessions.active_branch_id/current_turn_id/stage/turn_status` |
| 某轮结果完成 | insert/update 当前 `exploration_rolls` -> insert 当前轮 `exploration_candidates` -> stale 上一轮 -> update `sessions.current_turn_id/turn_status/last_completed_action` -> 执行 latest-two retention |
| confirm candidate | 校验 active + non-stale -> update `exploration_candidates.is_selected` -> update `exploration_branches.confirmed_candidate_id/status` -> update `sessions.selected_candidate_id/stage` |
| confirm 后 enqueue strategy | 在同一事务内 insert `strategy` job -> update `exploration_branches.status=strategy` -> 保证不存在 confirm 成功但 strategy job 缺失的半状态 |

### Lifecycle / Replay Corner Cases

| 场景 | 状态变化 | 说明 |
|------|----------|------|
| Spider 达到失败上限 | `lifecycle_state` 保持 `alive`，仅设置 `spider_cooldown_until` | 冷却不等同于冻结 |
| 用户显式暂停但仍有 `running` job | `pause_requested=true`，等待 active job 收尾后再转 `frozen` | 区分“请求暂停”与“立即冻结” |
| 会话进入 `frozen` 时已有 `running` job | `running` 允许收尾，`queued/retrying -> paused` | 防止冻结瞬间的派生任务 |
| 上一阶段完成后准备自动派生下一阶段 | 需再次校验 `lifecycle_state='alive'` 且 `pause_requested=false` | 防止暂停/冻结瞬间继续扩散任务链 |
| 调用 `resume` 但无 `paused` 任务 | `resumed_jobs=0` | 接口幂等 |
| 会话进入 `purged` 时有未完成 job | `queued/paused/retrying/running -> cancelled` | 并写 `cancel_reason=session_purged` |
| lease 过期 | `running -> retrying/failed` | 支持 worker 崩溃后的恢复重放，避免僵尸任务长期占用 `alive` |
| `retrying` 但 `not_before` 尚未到 | 生命周期仍视为有 active job | 延迟重试不等于无任务 |
| RAG 补偿失败未超限 | `ok -> pending` | 主流程可继续，状态需可观测，且不计入 active job |
| RAG 补偿超限 | `pending -> deadletter` | 检索能力降级，等待人工处理 |
| 用户显式取消当前任务 | `queued/paused/retrying/running -> cancelled` | 区分“暂停后可恢复”与“终止当前执行” |
| SSE 断线重连 | 补发 `event_id > Last-Event-ID` 后继续实时流 | 客户端按 `event_id` 去重 |

### ContentStrategy.data_source_quality

| 条件 | 含义 | 处理方式 |
|------|------|----------|
| `doc_count >= 10` | 数据量充足 | 不做 expansion，直接数据驱动 |
| `doc_count < 10 且 quality_score < 0.35` | 数据不足且质量低 | 触发 expansion |
| expansion 后 `quality_score >= 0.35` | 达标 | 使用数据驱动策略 |
| expansion 后 `quality_score < 0.35` | 未达标 | 降级 generic |

Expansion 停止条件：
- 新增 unique 文档数 `< 3`：停止继续 expansion
- `quality_score` 提升 `< 0.05`：停止继续 expansion

### SimilarityCheck.status

| 状态 | 条件 | 用户可见 |
|------|------|----------|
| safe | similarity < 0.3 | 无警告 |
| warning | 0.3 <= similarity < 0.6 | 黄色警告："该笔记与热门内容有相似之处" |
| rewritten | similarity >= 0.6 | 已自动重写，可能提示："已优化原创性" |


## 6. 存储生命周期管理

### 6.1 Session 创建 → 清理 完整流程

```
用户请求创建 Session
         │
         ▼
┌─────────────────────┐
│ 1. SQLite: 插入记录  │  aiosqlite.execute()
│    stage=init        │  WAL mode, 立即持久化
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 2. Exploration Phase│
│    - SQLite: 写 sessions 轻量指针
│    - SQLite: 写 exploration_branches/rolls/candidates
│    - 恢复以 jobs + sessions 为准
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 3. Strategy Phase   │
│    - Spider 获取数据 │
│    - SQLite: 存储 posts
│    - Chroma: 初始化 xhs_documents（若不存在）
│    - Chroma: 索引 posts 向量
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 4. Generation Phase │
│    - SQLite: 存储策略
│    - Chroma: 相似度查询
│    - SQLite: 存储最终笔记
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 5. Session 生命周期  │
│    - active job 存在: alive
│    - pause_requested: 收尾后 frozen
│    - 24h~10d 且无 active: frozen
│    - >10d: purged，不可恢复
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 5. 自动清理与取消     │
│    - SQLite: 未完成 job -> cancelled
│    - Chroma: 删除该 session 文档
│    - Session: lifecycle_state=purged
└─────────────────────┘
```

### 6.2 Chroma 文档 ID 规则（单 Collection）

```python
# 单 collection
collection_name = "xhs_documents"

# 复合 ID：按 session 逻辑隔离
doc_id = f"{session_id}:{note_id}"
# metadata = {"session_id": session_id, ...}

# 清理时
chroma_client.delete(where={"session_id": session_id})
```

### 6.3 故障恢复

| 故障场景 | SQLite (aiosqlite) | Chroma |
|---------|-------------------|--------|
| 进程崩溃 | WAL 文件自动恢复 | 持久化到磁盘，重启重建索引 |
| Session 中断 | 先恢复 `jobs`，再按 `sessions.active_branch_id/current_turn_id` 装配 exploration 快照 | 保留文档，可复用 |
| 手动清理 | DELETE FROM sessions | client.delete(where={"session_id": ...}) |

补充约束：
- `sessions` 只保留 exploration 轻量指针，不保存完整 branch/roll/candidate 历史
- old branch 长期保留；每个 branch 只裁剪更老 roll，不裁剪 branch 自身
- orphan roll / candidate 可延后清理，但恢复流程必须忽略
- 指针不一致时禁止猜测修复，直接进入 `EXPLORATION_STATE_INCONSISTENT`

### 6.4 部署配置

```python
# aiosqlite 配置
SQLITE_CONFIG = {
    "db_path": "./data/xhs_agent.db",
    "journal_mode": "WAL",        # 启用 WAL 模式支持并发
    "synchronous": "NORMAL",      # 平衡性能与安全
    "busy_timeout": 5000,         # 锁等待 5 秒
}

# Chroma 配置
CHROMA_CONFIG = {
    "host": "localhost",
    "port": 8000,
    "persist_directory": "./data/chroma",  # 持久化目录
    "anonymized_telemetry": False,
}
```
