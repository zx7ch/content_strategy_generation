# Schema 设计分析与决策

## 1. 设计原则

### 1.1 状态管理策略

**决策：使用 SessionState 作为中央状态存储**

```
┌─────────────────────────────────────────────────────────────┐
│                      SessionState                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ Original     │  │ Strategy     │  │ Generation       │   │
│  │ Query        │  │ Phase Data   │  │ Phase Data       │   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
└─────────────────────────────────────────────────────────────┘
         ▲                 ▲                 ▲
         │                 │                 │
    IntentRouter    StrategyAgent    GenerationAgent
```

**原因**：
- 多 Agent 协作需要共享上下文
- 支持断点续传和错误恢复
- 便于实现流式进度反馈

### 1.2 存储架构决策

**决策：aiosqlite (SQLite) + Chroma 双存储架构**

```
┌─────────────────────────────────────────────────────────────┐
│                      Storage Layer                           │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────────────┐  ┌──────────────────────────────┐ │
│  │ aiosqlite (SQLite)   │  │ Chroma (Vector DB)           │ │
│  │ ─────────────────    │  │ ─────────────────────────    │ │
│  │ SessionState 表       │  │ Per-session collections      │ │
│  │ - metadata            │  │ - Post embeddings            │ │
│  │ - spider_results      │  │ - Similarity search          │ │
│  │ - content_strategy    │  │ - Isolated by session_id     │ │
│  │ - generated_notes     │  │                              │ │
│  └──────────────────────┘  └──────────────────────────────┘ │
│           ▲                           ▲                      │
│           │                           │                      │
│     SessionManager              RAGService                  │
└─────────────────────────────────────────────────────────────┘
```

**原因**：
- **aiosqlite**: 单文件、零配置、支持 WAL 并发，适合会话状态频繁更新
- **Chroma**: 专职向量检索，按 session 隔离 collection，便于清理
- **轻量部署**: 无需 Redis/PostgreSQL 等额外服务

### 1.3 RAG Collection 隔离策略

**决策：每个 Session 创建独立的 Chroma Collection**

```python
collection_name = f"xhs_session_{session_id}"
```

**原因**：
- 用户 A 的搜索数据不应影响用户 B
- 便于后续清理（直接删除整个 collection）
- 支持并发处理多个用户请求
- SQLite 记录 collection_name，用于故障时清理

---

## 2. 关键数据流

### 2.1 Strategy Phase 数据流

```
User Query
    ↓
[XHSSpider] ──retry 5 times──→ list[XHSPost]
                                    ↓
[EngagementAnalyzer] ──────────→ list[ScoredPost]
                                    ↓
                        ┌───────────────────────────┐
                        ↓                           ↓
              sort & take top 20         [PlatformPreference]
                        ↓                           ↓
              [RAGService.index_documents]           │
                        ↓                            │
              quality_score, filtered_count          │
                        ↓                            │
              ┌──────────────────────────────────────┘
              ↓ (if quality < 0.35)
        expand_query → retry spider
              ↓
        [LLM.generate_strategy]
              ↓
        ContentStrategy
```

### 2.2 Generation Phase 数据流

```
ContentStrategy + UserProfile
              ↓
    [LLM.generate_proposals] n=10
              ↓
    list[Proposal]
              ↓
    [EngagementAnalyzer.score_proposals]
              ↓
    list[ScoredProposal] → sort → take top 5
              ↓
    ┌─────────┼─────────┬─────────┬─────────┐
    ↓         ↓         ↓         ↓         ↓
 Proposal 1 Proposal 2 Proposal 3 Proposal 4 Proposal 5
    │         │         │         │         │
    └─────────┴────┬────┴─────────┴─────────┘
                   ↓ Parallel Execution
         ┌─────────────────────────────────┐
         ↓                                 ↓
    [LLM.generate_note]             [RAGService.query]
         │                                 │
         │                            similar_posts
         ↓                                 ↓
    ┌──────────────────────────────────────────────┐
    ↓                                              │
  calc_similarity(note, similar_posts)             │
         │                                         │
         ├─> 0.6 → [rewrite_note] ─────────────────┘
         │              ↓
         │         mark_rewritten
         │
         ├─> 0.3-0.6 → mark_warning
         │
         └─> <0.3 → mark_safe
                   ↓
         collect_5_notes
                   ↓
         GenerationResponse
```

---

## 3. 关键设计决策

### 3.1 为什么 Engagement Score 需要加权计算？

```python
# 简单的计数 vs 加权计算

# ❌ 简单计数
total = likes + collects + comments + shares

# ✅ 加权计算 (反映真实价值)
total_engagement = likes + collects*2 + comments*3 + shares*4
engagement_rate = total_engagement / estimated_exposure
```

**原因**：
- 收藏代表价值认可，比点赞更有价值
- 评论和分享代表深度参与，权重最高
- engagement_rate 消除粉丝基数影响，公平比较

### 3.2 为什么 RAG 质量阈值设为 0.35？

```python
if quality_score < 0.35:
    expand_query_and_retry()
```

**考量因素**：
- 过低（<0.2）：搜索相关性太差，无法生成有效策略
- 过高（>0.5）：可能导致过度重试，增加延迟和成本
- 0.35 是平衡点：允许一定噪声但保证基本相关性

### 3.3 为什么相似度阈值设为 0.6/0.3？

```
相似度 > 0.6 → 重写 (疑似抄袭)
相似度 0.3-0.6 → 警告 (参考过度)
相似度 < 0.3 → 安全 (原创性良好)
```

**阈值选择**：
- 0.6：两段内容相似度超过60%即需要重写，避免抄袭嫌疑
- 0.3：30%相似度可接受，毕竟同主题内容难免有相似表达

### 3.4 为什么 Proposal 阶段生成10个选5个？

```
生成 10 个提案 → 评分排序 → 选择 top 5 → 并行生成

不是直接生成 5 个？
- LLM 一次生成多个质量不稳定
- 筛选机制确保最终输出质量
- 10个的池子提供足够的选择空间
```

---

## 4. 错误处理策略

### 4.1 爬虫重试策略（指数退避）

```python
for attempt in range(1, 6):
    result = spider.search(query)
    if result.success:
        break
    if attempt < 5:
        sleep(2 ** attempt)  # 2, 4, 8, 16, 32 seconds
    else:
        return spider_failed
```

### 4.2 降级策略

```
数据收集失败
      ↓
┌────────────────────────────────────┐
↓                                    ↓
RAG质量<0.35                      爬虫完全失败
(扩展查询重试)                    (使用通用策略)
      ↓                                    ↓
┌────────────────────────────────────┐
↓                                    ↓
扩展后质量>=0.35                 扩展后仍<0.35
(用数据驱动策略)                (用通用策略)
      ↓                                    ↓
┌────────────────────────────────────┘
↓
[LLM.generate_strategy]
```

### 4.3 用户反馈

```python
# 不同失败情况的用户反馈
ERROR_RESPONSES = {
    "spider_failed": "搜索服务繁忙，请换关键词或稍后重试",
    "insufficient_data": "该话题数据较少，建议换个角度或扩大范围",
    "llm_unavailable": "生成服务暂时不可用，请稍后重试",
    "timeout": "处理超时，建议简化查询或稍后重试",
}
```

---

## 5. 扩展性考虑

### 5.1 未来可能增加的模式

当前只有 EDITING MODE，未来可能增加：

```python
class Mode(Enum):
    EXPLORATION = "exploration"     # 探索热门话题
    EDITING = "editing"             # 生成笔记 (当前实现)
    OPTIMIZATION = "optimization"   # 优化现有笔记
    ANALYSIS = "analysis"           # 分析竞争对手
    SCHEDULING = "scheduling"       # 排期建议
```

### 5.2 平台扩展

当前只支持小红书，未来可扩展：

```python
class Platform(Enum):
    XIAOHONGSHU = "xhs"
    DOUYIN = "douyin"
    WEIBO = "weibo"
    BILIBILI = "bilibili"

# 每个平台有自己的 Spider 和 Preference Analyzer
```

### 5.3 多语言支持

```python
class Language(Enum):
    ZH_CN = "zh-CN"
    ZH_TW = "zh-TW"
    EN = "en"
    
# ContentStrategy 需要支持多语言模板
```

---

## 6. Schema 演进计划

### Phase 1: MVP
- 基础数据模型（XHSPost, ContentStrategy, GeneratedNote）
- 核心接口（Spider, RAG, LLM）
- 基础错误处理

### Phase 2: 优化
- 增加 Engagement Score 细化
- 增加 PlatformPreference 深度分析
- 完善错误码体系

### Phase 3: 扩展
- 增加用户反馈接口
- 增加 A/B 测试支持
- 增加历史数据学习

---

## 7. 待讨论问题

1. **相似度计算方式**：使用什么 Embedding 模型？（BERT、OpenAI、本地模型？）
2. **LLM 选型**：GPT-4、Claude、还是本地模型？
3. **数据保留策略**：Session 数据保留多久？
4. **并发限制**：单个用户可以同时有几个活跃 Session？
5. **成本优化**：是否需要缓存热门查询的结果？


---

## 8. 存储方案对比与决策

### 8.1 候选方案对比

| 方案 | 优点 | 缺点 | 适用场景 |
|------|------|------|----------|
| **纯内存** | 最快，零 IO | 数据丢失 | 开发测试 |
| **JSON 文件** | 可读，简单 | 无并发控制 | 原型验证 |
| **SQLite** | 单文件，ACID，零配置 | 高并发写需 WAL | ✅ 本项目首选 |
| **PostgreSQL** | 功能全，并发强 | 需安装配置 | 多用户生产 |
| **Redis** | 快，支持分布式 | 额外依赖，需运维 | 高并发缓存 |
| **Chroma** | 专职向量 | 不存结构化数据 | ✅ 向量专用 |

### 8.2 最终决策

```python
# SessionState 存储
Backend: aiosqlite (SQLite 异步封装)
Config: 
  - journal_mode = "WAL"  # 读写并发
  - synchronous = "NORMAL"
  - db_path = "./data/xhs_agent.db"

# Vector 存储  
Backend: Chroma
Config:
  - persist_directory = "./data/chroma"
  - per_session_collection = True
```

### 8.3 为什么不选 Redis？

**用户要求**: 轻量、易部署、本地友好

| Redis | aiosqlite + Chroma |
|-------|-------------------|
| 需单独安装服务 | `pip install` 即可 |
| 本地开发多一个进程 | 单 Python 进程 |
| 部署需配置内存/持久化 | 文件自动保存 |
| 适合高并发缓存 | 本项目 OLTP + 向量检索够用 |

**结论**: 当前阶段引入 Redis 是过早优化，增加不必要的复杂度。
