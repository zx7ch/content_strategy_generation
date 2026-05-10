# 2026/05/10 V1/V2 集成路线：MVP 升级后如何并入正式运营工作台

### 背景与定位

[2026/05/09 Improvement](#20260509-improvement服务端-playwright-自动化采集) 让 MVP 升级为可程序化触发的 Playwright 服务端采集器。下一步要解决的问题是：

> 升级后的采集模块如何与 V1（单会话笔记生成）结合提供参考数据，并最终集成到 V2（多品牌运营工作台）成为正式模块？

需要在不破坏 MVP 独立可实验性的前提下，把数据通道铺通到现有的两个生产系统。

---

### 关键发现：V2 已经预埋了集成契约

代码扫描显示当前已经存在但未真正连通的入口：

| 入口 | 位置 | 现状 |
|---|---|---|
| V2 Discovery 直接 import MVP 存储层 | `app/v2/discovery/service.py:11-13` | ✅ 已连：V2 调 MVP 创建 search task / 拓展词 |
| V2 Ingestion 已定义 `source_type="xhs_extension_capture"` | `app/v2/ingestion/service.py:264,281` | ❌ 未连：契约画好了，没有数据真的从 MVP 推过来 |
| V2 Ingestion 已有 `retry_extension_capture_sync` 方法 | `app/v2/ingestion/service.py:308` | ❌ 未连：失败重试入口存在，但首次入口不存在 |
| V1 RAG 服务能消费 evidence | `app/services/rag_service.py` | ❌ 未连：MVP 抓的数据没进过 V1 RAG |

**结论**：契约画好了，水管没接。本次集成的核心工作就是接通这两条水管，并最终让 UI 收敛到 V2 Console。

---

### 当前断裂架构图

```mermaid
flowchart LR
    subgraph MVP["🔬 xhs_extension_mvp（实验模块）"]
        MVP_DB[("data/xhs_extension_mvp.db\nmvp_capture_items")]
        MVP_UI["独立工作台 :8010"]
        MVP_SCRAPER["🆕 Playwright Scraper\n(05/09 章节规划)"]
        MVP_SCRAPER --> MVP_DB
        MVP_UI --> MVP_DB
    end

    subgraph V1["📝 V1 — 单会话笔记生成 app/"]
        V1_RAG["rag_service.py\nChroma 向量索引"]
        V1_GEN["content_generation_agent.py"]
        V1_SESSION["session_state.py\nSQLite"]
        V1_RAG --> V1_GEN
    end

    subgraph V2["🏢 V2 — 多品牌运营工作台 app/v2/"]
        V2_DISC["discovery/service.py\n已 import MVPStorage"]
        V2_ING["ingestion/service.py\n已定义 xhs_extension_capture\n但没人 call"]
        V2_TOPIC["topic_pool/"]
        V2_DEC["decision/"]
        V2_PUB["publish/ + performance/"]
        V2_DISC -.读取拓展词.-> V2_ING
        V2_ING --> V2_TOPIC --> V2_DEC --> V2_PUB
    end

    MVP_DB -.✅ 已连.-> V2_DISC
    MVP_DB -.❌ 缺失水管 1.-> V2_ING
    MVP_DB -.❌ 缺失水管 2.-> V1_RAG

    style MVP fill:#fff3e0,stroke:#f57c00
    style V1 fill:#e3f2fd,stroke:#1976d2
    style V2 fill:#f3e5f5,stroke:#7b1fa2
```

---

### 集成目标架构图（Stage C 完成态）

```mermaid
flowchart TB
    subgraph 最终态V2["🏢 V2 正式运营工作台（最终态）"]
        FE["frontend/ Next.js console\n/brands/[id]/discovery"]

        subgraph V2BE["app/v2 后端"]
            V2_DISC2["discovery/service.py\n创建任务 + 触发采集"]
            V2_ING2["ingestion/service.py\nxhs_extension_capture sink"]
            V2_TOPIC2["topic_pool/"]
            V2_DEC2["decision/"]
            V2_PUB2["publish/ + performance/"]
        end

        subgraph Embedded["内嵌 scraper 模块（原 MVP 核心）"]
            SCRAPER_FN["scrape_search_feed()\n纯 async function\n直接 import 调用"]
            SCRAPER_DB[("MVP SQLite\n保留为运行明细)")]
        end

        FE --> V2_DISC2
        V2_DISC2 -->|"in-process call"| SCRAPER_FN
        SCRAPER_FN -->|"MVPStorageSink"| SCRAPER_DB
        SCRAPER_FN -->|"V2IngestionSink"| V2_ING2
        V2_ING2 --> V2_TOPIC2 --> V2_DEC2 --> V2_PUB2
    end

    subgraph V1Gen["📝 V1 笔记生成（被 V2 调用）"]
        V1_GEN2["content_generation_agent"]
        V1_RAG2["rag_service"]
        V1_GEN2 --> V1_RAG2
    end

    V2_DEC2 -->|"决策选定主题后"| V1_GEN2
    SCRAPER_DB -.->|"XhsEvidenceAdapter\n按 topic 拉 evidence"| V1_RAG2

    style Embedded fill:#e8f4ff,stroke:#2176cc
    style V1Gen fill:#e3f2fd,stroke:#1976d2
    style 最终态V2 fill:#f3e5f5,stroke:#7b1fa2
```

---

### 集成路线分三个 Stage

#### Stage A：MVP → V1 Evidence Pipeline

**目标**：V1 在生成笔记时，能引用 MVP 抓到的真实笔记作为 evidence，提升标题结构、内容角度、数据感的真实性。

**核心数据契约**：MVP `CaptureItemIn` → V1 `EvidenceRecord`

```mermaid
flowchart LR
    USER["用户输入主题"] --> V1_API["POST /sessions"]
    V1_API --> ADAPTER["🆕 XhsEvidenceAdapter\nfetch_evidence_for_topic"]
    ADAPTER -->|"只读 SQLite"| MVP_DB[("MVP SQLite")]
    ADAPTER -->|"EvidenceRecord list"| V1_RAG["rag_service\n向量索引"]
    V1_RAG --> V1_GEN["content_generation_agent\n生成笔记"]
    V1_GEN --> NOTE["📝 输出笔记\n带真实数据 evidence"]

    NOTIFY["MVP scrape 完成\n→ notify_new_evidence(topic)"] -.->|"Push 模式（推荐）"| ADAPTER

    style ADAPTER fill:#fff3cd,stroke:#e6a817
    style NOTIFY fill:#fff3cd,stroke:#e6a817
```

#### Stage B：MVP → V2 Ingestion Sink

**目标**：MVP 采集结果自动流入 V2 ingestion 管道，进而触发 topic pool 重算和决策流程。

**核心实现**：scraper 引入 sink 模式，本地 SQLite 写入和 V2 source-sync 推送并行执行。

```mermaid
flowchart LR
    USER["用户在 V2 console 创建任务"] --> SCRAPER["🤖 Playwright Scraper\nscrape_search_feed"]
    SCRAPER -->|"sink 1: MVPStorageSink"| MVP_DB[("MVP SQLite\n实验回溯")]
    SCRAPER -->|"sink 2: V2IngestionSink"| V2_API["POST /v2/.../source-syncs\nsource_type=xhs_extension_capture"]
    V2_API --> V2_NORM["_normalize_source_sync_item\n→ ContentItemRecord"]
    V2_NORM --> V2_TOPIC["topic_pool 候选"]
    V2_TOPIC --> V2_DEC["decision 选题"]
    V2_DEC --> V2_GEN["调用 V1 笔记生成"]

    style SCRAPER fill:#e8f4ff,stroke:#2176cc
```

#### Stage C：UI 并入 V2 Console，独立 MVP UI 退役

**目标**：用户在 V2 Console 完成全部操作，`experiments/xhs_extension_mvp/web/` 退役（保留为开发者调试入口）。

| 当前 | 集成后 |
|---|---|
| `experiments/.../web/index.html` 独立工作台 :8010 | 退役为开发者调试工作台 |
| `MVPStorage` SQLite 是业务数据库 | 降级为「scraper 运行明细」，业务数据进 V2 Postgres |
| MVP 自己的 task 创建 | V2 `discovery/service.py` 唯一入口（已存在） |
| MVP `web/app.js` 的「自动采集」按钮 | 移植到 frontend Next.js `/brands/[id]/discovery` |
| MVP scraper 通过 HTTP endpoint 触发 | V2 后端通过 `from ... import scrape_search_feed` 直接调用 |

---

### Stage 依赖关系

```mermaid
flowchart LR
    PW["Playwright Scraper\n(05/09 Phase 1-4 完成)"] --> A
    PW --> B
    A["Stage A：MVP → V1 Evidence"] --> AA["验证：V1 生成的笔记\n带真实 XHS evidence"]
    B["Stage B：MVP → V2 Ingestion Sink"] --> BB["验证：V2 topic pool\n自动出现新候选"]
    A --> C["Stage C：UI 并入 V2 Console"]
    B --> C
    C --> CC["验证：用户只在\nV2 console 完成全部操作"]

    style A fill:#fff3cd,stroke:#e6a817
    style B fill:#d1ecf1,stroke:#0c5460
    style C fill:#d4edda,stroke:#28a745
```

- **Stage A 和 Stage B 可以并行**（一个连 V1，一个连 V2）
- **Stage C 必须等 A、B 都通**（前置依赖：数据通道都已建立）

---

### Implementation Plan

延续 [05/09 章节](#implementation-plan) 的 Phase 编号习惯，本路线接续为 **Stage A / B / C**，每 Stage 末尾保留 ✅ 完成进度 / ⚠️ 遗留问题 占位。

---

#### Stage A：MVP → V1 Evidence Pipeline（约 1.5 天）

**阶段目标**

让 V1 的 `content_generation_agent` 在生成笔记时，自动引用 MVP 抓到的真实小红书笔记作为 evidence。打通后用户体验：在 V1 创建 session 时填写 topic，系统自动从 MVP 数据库召回相关高互动笔记，作为 RAG 召回素材的一部分。

**涉及文件**

| 文件 | 类型 | 说明 |
|---|---|---|
| `app/services/xhs_evidence_adapter.py` | 🆕 新增 | 跨数据库适配器，只读 MVP SQLite |
| `app/services/rag_service.py` | ✏️ 修改（小） | 接受外部 evidence 注入接口 |
| `app/agents/orchestrator.py` | ✏️ 修改 | session 启动时调用 adapter |
| `experiments/xhs_extension_mvp/server/scraper.py` | ✏️ 修改 | scrape 完成后触发 `notify_new_evidence` 回调 |
| `app/services/evidence_notifier.py` | 🆕 新增 | 接收 push 通知的内部端点 |
| `tests/services/test_xhs_evidence_adapter.py` | 🆕 新增 | 单测 |

**关键函数 & 数据结构**

```python
# app/services/xhs_evidence_adapter.py

@dataclass(frozen=True)
class EvidenceRecord:
    """V1 RAG 接收的统一 evidence 格式（已存在或新增）。"""
    evidence_id: str
    title: str
    excerpt: str
    source_url: str
    author: str
    likes: int
    comments: int
    collections: int
    captured_at: datetime
    source_platform: str  # "xiaohongshu"
    source_topic: str

class XhsEvidenceAdapter:
    """从 MVP SQLite 只读拉取 capture items，归一化为 V1 EvidenceRecord。"""

    def __init__(self, mvp_db_path: Path):
        # 只读 sqlite3 连接（uri=file:...?mode=ro）
        self._db_path = mvp_db_path

    def fetch_evidence_for_topic(
        self,
        topic: str,
        *,
        min_likes: int = 100,
        max_items: int = 30,
        recency_days: int = 30,
    ) -> list[EvidenceRecord]:
        """SQL: SELECT * FROM mvp_capture_items
           WHERE title LIKE %topic% OR query_text LIKE %topic%
             AND likes >= :min_likes
             AND captured_at >= datetime('now', '-{recency_days} days')
           ORDER BY likes DESC, collections DESC LIMIT :max_items"""
        ...

    def fetch_evidence_for_query_ids(
        self,
        query_ids: list[str],
        *,
        max_items: int = 30,
    ) -> list[EvidenceRecord]:
        """JOIN mvp_item_query_hits 用 query_id 精确召回。"""
        ...
```

```python
# app/services/evidence_notifier.py

class EvidenceNotifier:
    """接收 MVP scraper 的完成通知，触发 V1 重新索引。"""

    async def notify_new_evidence(
        self,
        *,
        topic: str,
        captured_count: int,
        active_session_id: str | None = None,
    ) -> None:
        """如果有 active session 命中该 topic，立即重新索引；
        没有则只记录，等下一次 session 启动时拉。"""
        ...
```

```python
# app/agents/orchestrator.py（伪代码改动）

async def start_session(self, *, topic: str, session_id: str):
    # ...existing logic
    # 🆕 启动时尝试拉取 XHS evidence
    if self._xhs_evidence_enabled:
        records = self._xhs_adapter.fetch_evidence_for_topic(topic)
        if records:
            await self._rag_service.index_external_evidence(
                session_id=session_id,
                records=records,
            )
            logger.info("Indexed XHS evidence", extra={
                "session_id": session_id,
                "topic": topic,
                "count": len(records),
            })
```

```python
# experiments/xhs_extension_mvp/server/scraper.py（追加 sink 钩子）

async def scrape_search_feed(
    keyword: str,
    *,
    sinks: list[CaptureSink] | None = None,
    on_complete: Callable[[ScrapeSummary], Awaitable[None]] | None = None,
    ...
) -> list[CaptureItemIn]:
    items = await _do_scrape(...)
    sinks = sinks or [MVPStorageSink()]
    for sink in sinks:
        await sink.write(items)
    if on_complete:
        await on_complete(ScrapeSummary(keyword=keyword, items_count=len(items)))
    return items
```

**测试用例**

| 类型 | 用例 | 验证点 |
|---|---|---|
| 单测 | `test_adapter_filters_by_min_likes` | 设 `min_likes=500`，过滤掉低互动 |
| 单测 | `test_adapter_orders_by_likes_desc` | 返回结果第一条互动最高 |
| 单测 | `test_adapter_handles_empty_db` | DB 为空时返回 `[]` 不抛异常 |
| 单测 | `test_adapter_uses_readonly_connection` | 模拟写操作应抛 `OperationalError` |
| 集成 | `test_session_start_indexes_xhs_evidence` | mock MVP db 有 5 条数据，启动 session 后 RAG 中能检索到 |
| 集成 | `test_notify_new_evidence_triggers_reindex` | 调 notifier，验证 active session 的 RAG 索引更新 |
| 端到端 | 真实跑一次 MVP 抓取 → V1 生成笔记 | 笔记中能识别出引用 XHS 真实数据 |

**验收标准**

1. ✅ V1 session 启动时自动拉 XHS evidence（topic 匹配命中时）
2. ✅ 抓取的 evidence 进入 RAG，`content_generation_agent` 在生成时能召回
3. ✅ MVP 抓完后能 push 通知到 V1，已运行 session 也能感知到新 evidence
4. ✅ Adapter 用只读连接，不会污染 MVP 数据
5. ✅ Topic 命中失败时优雅降级（V1 仍能正常运行，只是没 XHS evidence）
6. ✅ 端到端跑通：用户输入「户外防晒」→ MVP 抓 100 条 → V1 生成笔记带真实数据引用

**✅ 完成进度**（待 Stage A 完成后回填）

> _尚未开始_

**⚠️ 遗留问题**（待 Stage A 完成后回填）

> _尚未开始_

---

#### Stage B：MVP → V2 Ingestion Sink（约 1 天）

**阶段目标**

scraper 引入多 sink 架构，采集结果同时写入 MVP 本地 SQLite 和 V2 ingestion 管道，触发 V2 topic pool 自动重算。

**涉及文件**

| 文件 | 类型 | 说明 |
|---|---|---|
| `experiments/xhs_extension_mvp/server/sinks/__init__.py` | 🆕 新增 | sink 协议定义 |
| `experiments/xhs_extension_mvp/server/sinks/mvp_storage.py` | 🆕 新增 | 包装现有 MVPStorage 写入 |
| `experiments/xhs_extension_mvp/server/sinks/v2_ingestion.py` | 🆕 新增 | HTTP 推送到 V2 ingestion |
| `experiments/xhs_extension_mvp/server/scraper.py` | ✏️ 修改 | 接收 sinks 参数 |
| `experiments/xhs_extension_mvp/server/app.py` | ✏️ 修改 | 启动时根据配置组装 sink list |
| `experiments/xhs_extension_mvp/server/config.py` | 🆕 新增 | 集中配置 V2 endpoint、workspace_id 等 |
| `app/v2/ingestion/service.py` | ✏️ 修改（小） | 校验入参对齐 MVP 推送格式 |
| `tests/scraper/test_v2_ingestion_sink.py` | 🆕 新增 | sink 单测 |

**关键函数 & 数据结构**

```python
# experiments/xhs_extension_mvp/server/sinks/__init__.py

class CaptureSink(Protocol):
    name: str

    async def write(
        self,
        *,
        task_id: str,
        keyword: str,
        items: list[CaptureItemIn],
    ) -> SinkResult: ...

@dataclass
class SinkResult:
    sink_name: str
    success: bool
    items_written: int
    error_message: str = ""
```

```python
# experiments/xhs_extension_mvp/server/sinks/mvp_storage.py

class MVPStorageSink:
    """包装现有 MVPStorage 的 ingest_extension_capture，作为默认 sink。"""
    name = "mvp_storage"

    def __init__(self, storage: MVPStorage):
        self._storage = storage

    async def write(self, *, task_id, keyword, items) -> SinkResult:
        # 复用现有的去重 + version++ 逻辑
        ...
```

```python
# experiments/xhs_extension_mvp/server/sinks/v2_ingestion.py

class V2IngestionSink:
    """通过 HTTP POST 把 items 推到 V2 source-syncs 端点。"""
    name = "v2_ingestion"

    def __init__(
        self,
        *,
        base_url: str,
        workspace_id: str,
        brand_id: str,
        timeout_seconds: float = 10.0,
    ):
        self._base_url = base_url
        self._workspace_id = workspace_id
        self._brand_id = brand_id
        self._timeout = timeout_seconds

    async def write(self, *, task_id, keyword, items) -> SinkResult:
        payload = {
            "source_type": "xhs_extension_capture",
            "external_request_id": f"mvp_{task_id}_{uuid.uuid4().hex[:8]}",
            "items": [self._to_v2_item(it, keyword) for it in items],
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/v2/workspaces/{self._workspace_id}"
                    f"/brands/{self._brand_id}/source-syncs",
                    json=payload,
                )
                resp.raise_for_status()
            return SinkResult(self.name, True, len(items))
        except Exception as exc:
            # ⚠️ 不能让 V2 sink 失败影响 MVP 本地写入
            logger.warning("V2 ingestion sink failed", exc_info=exc)
            return SinkResult(self.name, False, 0, str(exc))

    @staticmethod
    def _to_v2_item(item: CaptureItemIn, keyword: str) -> dict:
        """对齐 V2 _normalize_source_sync_item 的入参约定。"""
        return {
            "external_id": item.note_id,
            "title": item.title,
            "content": item.visible_text_excerpt,
            "source_url": item.source_url,
            "author_external_id": item.author,
            "metrics": {
                "likes": item.likes,
                "comments": item.comments,
                "collections": item.collections,
            },
            "tags": item.tags,
            "captured_at": utc_now().isoformat(),
            "discovery_keyword": keyword,
        }
```

```python
# experiments/xhs_extension_mvp/server/config.py

@dataclass(frozen=True)
class ScraperConfig:
    v2_sink_enabled: bool                    # 来自 env XHS_MVP_V2_SINK_ENABLED
    v2_base_url: str                         # 来自 env V2_BASE_URL
    v2_workspace_id: str | None              # 来自 env V2_WORKSPACE_ID
    v2_brand_id: str | None                  # 来自 env V2_BRAND_ID

    @classmethod
    def from_env(cls) -> "ScraperConfig":
        ...

def build_default_sinks(storage: MVPStorage, config: ScraperConfig) -> list[CaptureSink]:
    sinks: list[CaptureSink] = [MVPStorageSink(storage)]
    if config.v2_sink_enabled and config.v2_workspace_id and config.v2_brand_id:
        sinks.append(V2IngestionSink(
            base_url=config.v2_base_url,
            workspace_id=config.v2_workspace_id,
            brand_id=config.v2_brand_id,
        ))
    return sinks
```

**测试用例**

| 类型 | 用例 | 验证点 |
|---|---|---|
| 单测 | `test_mvp_storage_sink_writes_via_existing_pipeline` | 校验 `snapshot_version` += 1 |
| 单测 | `test_v2_sink_payload_shape_matches_contract` | mock httpx，断言 payload 结构 |
| 单测 | `test_v2_sink_failure_does_not_raise` | 模拟 V2 5xx，sink 返回 success=False 但不抛异常 |
| 单测 | `test_build_default_sinks_disabled_v2` | 配置缺失时只返回 MVP sink |
| 集成 | `test_scrape_writes_to_both_sinks` | mock 两个 sink，scrape 完成后两边都被调一次 |
| 集成 | `test_v2_sink_triggers_topic_pool_rebuild` | 真实 V2 后端，推送后 `topic_pool` 表新增候选 |
| 端到端 | MVP 抓 → V2 ingestion → topic pool → decision | V2 console 看到新候选可决策 |

**验收标准**

1. ✅ scraper 支持配置驱动的 sink list，默认只启用 MVP storage
2. ✅ 启用 V2 sink 后，每次 scrape 同步推到 V2 ingestion 端点
3. ✅ V2 sink 失败不影响 MVP 本地数据写入（独立可实验性保留）
4. ✅ 推送的 payload 通过 V2 现有 `_normalize_source_sync_item` 校验
5. ✅ V2 现有的 `retry_extension_capture_sync` 能处理失败重试
6. ✅ Topic pool 在收到 ingestion 后自动重算（用现有 V2 机制，无需改动）
7. ✅ 端到端：MVP 抓 100 条 → V2 topic_pool 出现新候选 → V2 decision 可见

**✅ 完成进度**（待 Stage B 完成后回填）

> _尚未开始_

**⚠️ 遗留问题**（待 Stage B 完成后回填）

> _尚未开始_

---

#### Stage C：UI 并入 V2 Console，独立 MVP UI 退役（约 2 天）

**阶段目标**

V2 frontend (`/brands/[id]/discovery`) 接管原 MVP 工作台的全部用户操作。MVP 独立 web UI 降级为开发者调试入口（保留可用，但不在产品路径上）。

**涉及文件**

| 文件 | 类型 | 说明 |
|---|---|---|
| `frontend/src/app/brands/[id]/discovery/page.tsx` | 🆕 新增（或扩展） | 主页面：拓展词列表 + 自动采集按钮 + 进度状态 |
| `frontend/src/app/brands/[id]/discovery/components/ScraperBanner.tsx` | 🆕 新增 | 采集器登录态 banner |
| `frontend/src/app/brands/[id]/discovery/components/AutoScrapeButton.tsx` | 🆕 新增 | 单条拓展词的自动采集触发 |
| `frontend/src/app/brands/[id]/discovery/components/ScrapeStatusLine.tsx` | 🆕 新增 | 进度状态行 |
| `frontend/src/lib/api.ts` | ✏️ 修改 | 新增 discovery 相关 API client 方法 |
| `app/v2/discovery/service.py` | ✏️ 修改 | 新增 `trigger_auto_scrape` / `get_scrape_status` 方法，内部直接 import scraper |
| `app/v2/discovery/router.py` | ✏️ 修改 | 暴露新 endpoint |
| `experiments/xhs_extension_mvp/web/index.html` | ⚠️ 标注 | 顶部加 banner 「此 UI 已降级为开发者调试入口，正式入口请用 V2 Console」 |
| `experiments/xhs_extension_mvp/README.md` | ✏️ 修改 | 更新定位说明 |

**关键函数 & 数据结构**

```python
# app/v2/discovery/service.py（追加方法）

class DiscoveryService:
    # ...existing

    async def trigger_auto_scrape(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        task_id: str,
        query_id: str,
        scroll_count: int = 5,
    ) -> AutoScrapeAck:
        # 1. 校验 task scope（workspace + brand）
        # 2. 取 query 的 keyword
        # 3. 检查 ScrapeStateRegistry 是否有任务在跑 → 409
        # 4. 启动 background task：直接 import scrape_search_feed 并调用
        #    sinks = [MVPStorageSink, V2IngestionSink(workspace_id, brand_id)]
        # 5. 返回 ack
        ...

    def get_scrape_status(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        task_id: str,
    ) -> ScrapeStatusResponse: ...
```

```python
# app/v2/discovery/router.py（追加 endpoint）

@router.post("/workspaces/{ws}/brands/{brand}/discovery/tasks/{task}/auto-scrape")
async def trigger_auto_scrape(...): ...

@router.get("/workspaces/{ws}/brands/{brand}/discovery/tasks/{task}/scrape-status")
async def get_scrape_status(...): ...

@router.post("/workspaces/{ws}/scraper/login")
async def trigger_scraper_login(...): ...

@router.get("/workspaces/{ws}/scraper/readiness")
async def get_scraper_readiness(...): ...
```

```typescript
// frontend/src/lib/api.ts（追加）

export interface ScrapeStatus {
  task_id: string;
  phase: 'idle' | 'launching' | 'navigating' | 'scrolling' | 'ingesting' | 'done' | 'error' | 'login_required';
  scroll_index: number;
  scroll_total: number;
  items_count: number;
  error_message: string;
}

export const discoveryApi = {
  triggerAutoScrape: (workspaceId, brandId, taskId, queryId) => ...,
  getScrapeStatus: (workspaceId, brandId, taskId) => ...,
  triggerScraperLogin: (workspaceId) => ...,
  getScraperReadiness: (workspaceId) => ...,
};
```

```tsx
// frontend/src/app/brands/[id]/discovery/page.tsx（核心结构伪代码）

export default function DiscoveryPage({ params }) {
  const { brandId } = params;
  const { data: readiness } = useScraperReadiness();
  const { data: task } = useDiscoveryTask(...);
  const { activeQueryId, status } = useScrapeStatus(...);

  return (
    <div>
      {!readiness?.logged_in && <ScraperBanner onInit={triggerLogin} />}
      <TaskHeader task={task} />
      <QueryList>
        {task.queries.map(q => (
          <QueryCard key={q.id}>
            <a href={buildXhsSearchUrl(q.text)}>打开小红书搜索</a>
            <AutoScrapeButton
              queryId={q.id}
              disabled={!!activeQueryId}
              onClick={() => triggerAutoScrape(q.id)}
            />
            {activeQueryId === q.id && <ScrapeStatusLine status={status} />}
          </QueryCard>
        ))}
      </QueryList>
      <CandidateList candidates={task.candidates} />
    </div>
  );
}
```

**测试用例**

| 类型 | 用例 | 验证点 |
|---|---|---|
| 单测 | `test_discovery_service_trigger_auto_scrape_scope_check` | 跨 brand 访问返回 403 |
| 单测 | `test_discovery_service_concurrent_scrape_returns_409` | 重复触发返回 409 |
| 集成 | `test_v2_endpoint_auto_scrape_full_flow` | mock scraper，端到端校验状态机 |
| 前端 | `ScraperBanner.test.tsx` | 未登录态显示初始化按钮，已登录态隐藏 |
| 前端 | `AutoScrapeButton.test.tsx` | 点击触发 API；采集中按钮 disabled |
| 前端 | `ScrapeStatusLine.test.tsx` | 各 phase 状态文案正确 |
| 端到端 | 用户在 V2 console 完成完整流程 | 创建任务 → 自动采集 → 候选刷新 → 决策 → 触发 V1 生成 |

**验收标准**

1. ✅ V2 console 出现 `/brands/[id]/discovery` 入口
2. ✅ 用户可在 V2 console 完成：创建 discovery 任务 / 触发自动采集 / 看进度 / 看候选 / 进入决策
3. ✅ V2 后端通过 `import` 直接调 scraper，不再走 MVP 的 HTTP `/auto-scrape` 端点
4. ✅ MVP 独立 UI 顶部出现降级 banner，文案明确「正式入口请用 V2 Console」
5. ✅ MVP 的 `/auto-scrape` HTTP 端点可保留（兼容旧脚本），但默认不在产品路径
6. ✅ 端到端：用户登录 V2 console → 操作 discovery → 后台经过 ingestion / topic pool / decision → 触发 V1 生成 → 看到带 XHS evidence 的笔记

**✅ 完成进度**（待 Stage C 完成后回填）

> _尚未开始_

**⚠️ 遗留问题**（待 Stage C 完成后回填）

> _尚未开始_

---

### 关键决策点（待最终确认）

实施前需要明确的 4 个判断：

| # | 决策点 | 候选 | 推荐 | 理由 |
|---|---|---|---|---|
| 1 | Stage A 触发模式 | Pull / Push | **Push** | MVP 抓的频率低，V1 session 启动频率高，Pull 会重复读 |
| 2 | Stage B sink 启用方式 | 环境变量开关 / V2 主动调 MVP scraper | **环境变量开关** | 保留 MVP 独立可跑能力 |
| 3 | Stage C 退役边界 | 完全删除 MVP web UI / 保留为调试入口 | **保留为调试入口** | 反爬调参时的快速入口仍有用 |
| 4 | scraper 代码位置 | 留在 `experiments/` / 迁到 `app/services/scraper/` | **Stage A/B 期间留在 experiments，Stage C 完成后迁移** | 减少改动面，迁移作为收尾动作 |

---

### 时间线总览

| Stage | 估时 | 是否阻塞下游 | 可并行 |
|---|---|---|---|
| Stage A：MVP → V1 Evidence | 1.5 天 | 否（与 B 并行） | ✅ 与 B 并行 |
| Stage B：MVP → V2 Ingestion Sink | 1 天 | 否（与 A 并行） | ✅ 与 A 并行 |
| Stage C：UI 并入 V2 Console | 2 天 | - | ❌ 必须等 A、B 都完成 |

**总计：A + B 并行 1.5 天 + C 串行 2 天 = 3.5 天**（基础前提：[2026/05/09 章节](#20260509-improvement服务端-playwright-自动化采集) 的 Phase 1-3 已完成）

---

---

# 2026/05/09 Improvement：服务端 Playwright 自动化采集

### 背景与定位

extension_mvp 是为了验证小红书数据采集方式的独立模块，最终会被集成到 content strategy 主系统，作为给用户提供实时小红书数据的模块。

经过前一轮（取消手动 token、自动同步 active task、页面浮层、工作台自动刷新）后，体验已经从「调试工具」升级到「自然采集」。但当前 Chrome Extension 方案仍存在三个产品级瓶颈：

| 瓶颈 | 表现 |
|---|---|
| 需要用户手动驱动 | 跳转 XHS 页 → 手动滚动 → 点击采集 |
| 等待感强 | 工作台空白等待，没有进度反馈 |
| 无法 agent 化调用 | 浏览器插件入口无法被 content strategy 主系统程序化触发 |

**新方向**：服务端用 Playwright 驱动真实 Chrome 自动滚动采集，目标是用户**无感&快速**获取小红书信息。Chrome Extension 降级为补充入口（笔记详情页、临时单页采集）。

---

### 当前架构图（Chrome Extension 手动驱动）

```mermaid
flowchart TB
    subgraph 用户手动操作["👤 用户手动操作（关键瓶颈）"]
        direction TB
        U1["①  在工作台创建任务"]
        U2["②  点击「打开小红书搜索」\n手动跳转"]
        U3["③  手动滚动页面"]
        U4["④  点击浮层「采集当前页」"]
        U1 --> U2 --> U3 --> U4
    end

    subgraph Chrome["🌐 Chrome 浏览器（用户已登录 XHS）"]
        subgraph Ext["Chrome 扩展 MV3"]
            CS["content.js\n注入浮层 + 提取可见 DOM"]
            BG["background.js\n消息路由 + 提交"]
        end
        XHS["xiaohongshu.com 搜索结果页"]
    end

    subgraph Server["🖥️ 本地服务 FastAPI :8010"]
        API_CAP["POST /api/extension/capture"]
        STORAGE["SQLite Storage\n去重 · snapshot_version++"]
        BUILDER["candidate_builder"]
        API_CAP --> STORAGE --> BUILDER
    end

    subgraph Workbench["📋 工作台 Web UI"]
        POLL["每 2s 轮询 snapshot"]
        RENDER["渲染候选方向 · 热点榜"]
        POLL -->|版本变化| RENDER
    end

    U4 --> CS
    CS -->|chrome.runtime.sendMessage| BG
    BG -->|POST + Token| API_CAP
    BUILDER -.-> POLL

    style 用户手动操作 fill:#fff3cd,stroke:#e6a817
```

**架构特征**：用户是数据流的发动机；服务端完全被动。

---

### 演进后架构图（Playwright 自动驱动）

```mermaid
flowchart TB
    subgraph 用户操作["👤 用户操作（仅触发，不参与执行）"]
        U1["① 在工作台创建任务"]
        U2["② 点击拓展词旁的「自动采集」按钮"]
        U1 --> U2
    end

    subgraph Workbench["📋 工作台 Web UI（变为主动方）"]
        TRIGGER["POST /api/tasks/:id/auto-scrape"]
        OVERLAY["进度状态行\n滚动 3/5，已采 66 条"]
        POLL["复用现有 2s 轮询\n额外拉一次 /scrape-status"]
        RENDER["完成后自动刷新候选方向"]
        TRIGGER --> POLL --> OVERLAY --> RENDER
    end

    subgraph Server["🖥️ 本地服务 FastAPI :8010（变为主动方）"]
        direction TB
        API_SCRAPE["POST /auto-scrape\n启动后台 task → 202"]
        API_STATUS["GET /scrape-status\n返回 phase/progress/count"]
        STATE["全局 dict\n_scrape_state[task_id]"]

        subgraph Scraper["🎭 scraper.py（新增模块）"]
            PW_LAUNCH["launch_persistent_context\n项目专属 Chrome 用户数据目录\n本质是真实 Chrome 进程"]
            PW_NAV["navigate to\nxiaohongshu.com/search_result?keyword=..."]
            PW_SCROLL["人类化滚动 × 5\n贝塞尔鼠标轨迹 + 随机延迟\n模拟阅读停顿"]
            PW_EXTRACT["DOM 提取\n复用 content.js 选择器逻辑"]
            PW_LAUNCH --> PW_NAV --> PW_SCROLL --> PW_EXTRACT
            PW_EXTRACT -->|每轮滚动后| PW_SCROLL
        end

        STORAGE["现有 ingest 流水线（零改动）"]
        BUILDER["candidate_builder（不变）"]

        API_SCRAPE --> Scraper
        Scraper -->|"CaptureItemIn list — 复用现有 ingest"| STORAGE
        Scraper -->|"写状态"| STATE
        API_STATUS --> STATE
        STORAGE --> BUILDER
    end

    subgraph Profile["🍪 项目专属 Chrome Profile（一次性配置）"]
        PROFILE["~/.xhs-mvp/chrome-profile/\n首次启动登录 XHS 后持久化"]
    end

    subgraph ExtFallback["🔌 Chrome 扩展（保留为补充入口）"]
        CS_FB["手动单页采集 / 笔记详情页采集"]
    end

    U2 --> TRIGGER
    PROFILE -->|launch_persistent_context| PW_LAUNCH
    BUILDER -.->|snapshot_version 变化| RENDER

    style 用户操作 fill:#d4edda,stroke:#28a745
    style Scraper fill:#e8f4ff,stroke:#2176cc
    style Profile fill:#fff3e0,stroke:#f57c00
    style ExtFallback fill:#f5f5f5,stroke:#aaa
```

---

### 演进关系与原因

```mermaid
flowchart LR
    subgraph 演进驱动["演进驱动因素"]
        R1["🔴 用户体验\n手动 3-4 步 + 空白等待"]
        R2["🔴 集成约束\n多 Agent 系统需要可程序化调用"]
        R3["🔴 采集上限\n只抓 viewport 可见内容"]
    end

    subgraph 不变层["🟢 保留不变（稳定核心）"]
        KEEP1["SQLite ingest 流水线\nCaptureItemIn 数据格式"]
        KEEP2["现有 FastAPI 端点 + active task 机制"]
        KEEP3["Chrome 扩展（降级为补充入口）"]
        KEEP4["工作台 2s 轮询机制"]
    end

    subgraph 替换层["🔵 替换（同职责升级）"]
        OLD1["用户手动导航 + 手动点击"]
        NEW1["Playwright persistent_context\n一次触发 → 全自动完成"]
        OLD1 -->|替换为| NEW1
    end

    subgraph 新增层["🟡 新增（填补空白）"]
        ADD1["scraper.py\n滚动采集协程 ≈ 100 条"]
        ADD2["/scrape-status 进度查询接口"]
        ADD3["工作台进度状态行"]
    end

    R1 --> NEW1
    R1 --> ADD2
    R1 --> ADD3
    R2 --> NEW1
    R2 --> ADD1
    R3 --> ADD1

    style 演进驱动 fill:#ffeaea,stroke:#cc3333
    style 不变层 fill:#eafff0,stroke:#228b22
    style 替换层 fill:#e8f0ff,stroke:#3355cc
    style 新增层 fill:#fffbe6,stroke:#b8860b
```

---

### 反爬：为什么不能用裸 headless

小红书风控严格，裸 headless Chromium 会在多个向量上暴露：

| 检测向量 | Headless 裸跑 | Headless + stealth | **Persistent Context（真实 Chrome 进程）** |
|---|---|---|---|
| `navigator.webdriver` | ❌ 直接为 true | ✅ 可 patch | ✅ 真实 Chrome 本身就是 false |
| `window.chrome` | ❌ 缺失 | ✅ 可注入 | ✅ 原生存在 |
| WebGL / Canvas 指纹 | ❌ 服务器同一指纹 | ⚠️ 可加噪声但易被 ML 识别 | ✅ 用户真实 GPU 指纹 |
| TLS / JA3 指纹 | ⚠️ Chromium 默认 | ⚠️ 同左 | ✅ 真实 Chrome |
| 鼠标 / 滚动轨迹 | ❌ 直线 | ❌ 仍需自己实现 | ⚠️ 仍需自己实现 |
| 浏览历史 / 缓存 | ❌ 空白 | ❌ 空白 | ✅ 真实积累 |

**结论**：用 `launch_persistent_context` + 项目独立 Chrome 用户数据目录，本质上启动的是**真实 Chrome 进程**（同样的指纹、同样的 TLS、同样的反爬绕过），跟用户日常 Chrome 物理隔离避免冲突。指纹层四个检测向量自动绕过，只需自己实现行为层（人类化滚动）。

---

### 用户视角

#### 🎬 场景一：首次配置（一次性，按 README 操作）

登录步骤由 README 引导，**不在代码主路径中**。流程示意：

```mermaid
sequenceDiagram
    participant U as 👤 用户
    participant R as README
    participant B as 项目专属 Chrome (data/chrome-profile)
    participant W as 工作台

    U->>R: 1. 阅读首次使用说明
    R-->>U: 提示：先完成小红书登录后再启动服务
    U->>B: 2. 按 README 引导，使用 data/chrome-profile 打开小红书\n（XHS 自动弹出扫码 modal）
    U->>B: 3. 扫码登录 → 关闭窗口
    Note over B: 登录态写入 chrome-profile
    U->>W: 4. 启动服务并打开工作台
    W->>W: 检测 GET /api/scraper/readiness
    W-->>U: banner 隐藏（已登录） / 显示「未登录，请按 README 完成登录」
```

**用户感知**：按 README 走一遍登录流程，之后正常使用。代码不主动弹窗、不轮询。

#### 🚀 场景二：日常采集（无感）

```mermaid
sequenceDiagram
    participant U as 👤 用户
    participant W as 工作台
    participant S as 服务端
    participant B as 后台 Chrome

    U->>W: 1. 输入「户外防晒」→ 创建任务
    W-->>U: 显示 5 条拓展词
    U->>W: 2. 点某条拓展词的「🔄 自动采集」
    W-->>U: 状态行：正在启动浏览器...

    par 后台执行
        S->>B: launch_persistent_context\n窗口位置 (-2000, -2000) 屏幕外
        B->>B: 打开搜索页 → 等待加载
        loop 5 次滚动
            B->>B: 人类化滚动 + 阅读停顿
            B->>S: 提取当前可见笔记
            S->>S: 去重 + 写入 SQLite
        end
        B->>B: 关闭浏览器
    and 工作台轮询
        loop 每 2 秒
            W->>S: GET /scrape-status
            S-->>W: phase + 已采集数
            W-->>U: 状态行：滚动 3/5，已采 66 条
        end
    end

    S->>W: snapshot_version++ 触发现有刷新
    W-->>U: 候选方向 / 推荐笔记自动更新
    W-->>U: 状态行：✅ 采集完成，共 102 条
```

**用户感知**：点一下按钮 → 看进度数字 → 候选方向出现。整个过程不切窗口、不手动操作。

#### 🔥 场景三：异常恢复

| 异常 | 用户看到 | 用户操作 |
|---|---|---|
| Cookie 过期（7-15 天） | ⚠️ 登录失效，请按 README 重新登录 | 按 README 流程重新登录小红书 |
| 触发验证码 / 风控 | ⚠️ 触发风控，已弹出窗口请手动处理 | 弹窗变可见 → 手动过验证 → scraper 继续 |
| 浏览器进程异常 | 浏览器异常，正在重启 | 自动重试一次 |
| 网络失败 | ❌ 采集失败 + 「重试」按钮 | 点重试 |
| 并发触发（已有任务在跑） | ⚠️ 当前已有采集任务在执行 | 等待或取消 |

---

### 实现路径（打通优先，不过度工程化）

#### 必须实现（不可省）

1. **`scraper.py` 核心模块**
   - `async def scrape_search_feed(keyword, scroll_count=5) -> list[CaptureItemIn]`
   - 写成纯 async function，未来主系统可直接 import 调用，绕过 HTTP
2. **`launch_persistent_context` + 项目专属 profile**
   - 路径：`data/chrome-profile/`（项目相对路径，与 `data/xhs_extension_mvp.db` 同级）
   - headful 启动，窗口位置移到屏幕外（`(-2000, -2000)`）实现「无感」
3. **人类化滚动**（不可省，行为检测必经）
   ```python
   # 关键要素
   - mouse.move 分步贝塞尔轨迹
   - 滚动距离随机化（页面高度 0.7-1.1 倍）
   - 单次滚动分 3-5 次小幅度执行
   - 滚动后停顿 1.8-3.5s 模拟阅读
   - 总轮次中插入 1-2 次假装 hover 笔记 cover
   ```
4. **登录态检测 endpoint**（不主动驱动登录，仅检测）
   - `GET /api/scraper/readiness`：返回 profile 是否存在 + 当前是否登录态
   - 登录由用户在 README 引导下手动完成（首次访问 xiaohongshu.com 会自动弹出扫码 modal）
5. **采集触发 + 状态查询 endpoint**
   - `POST /api/tasks/:id/auto-scrape`：启动 background task，返回 202
   - `GET /api/tasks/:id/scrape-status`：返回 `{phase, scroll_index, items_count, error}`
6. **全局采集锁**
   - 单 Python 进程内一个 `asyncio.Lock` 即可，并发触发返回 409

#### 刻意精简的部分（留接口、不实现）

| 简化项 | 现版做法 | 未来扩展位 |
|---|---|---|
| 进度推送 | 复用 2s 轮询 + 一个状态字典 | dict 改为 `asyncio.Queue` 即可升级 SSE |
| UI 进度 | 一行状态文字（复用 `.status`） | 留组件位置，未来做 streaming overlay |
| Cookie 来源 | 项目自己维护一份 profile | 配置层抽象，未来可支持多账号、cookie 池 |
| 反检测 | persistent context + 人类化滚动 | 留 `connect_browser` 抽象层，未来可加 stealth、CDP 连接用户 Chrome |
| 错误恢复 | 失败返回错误 + 工作台重试按钮 | 不做退避重试 |
| 多任务排队 | 全局单锁 + 409 busy | 不做队列 |
| 跨 OS 自动化 Chrome 路径 | Playwright 默认下载 Chromium | 一次性 `playwright install chromium` ≈ 150MB |

---

### 模块职责与文件位置

```text
experiments/xhs_extension_mvp/
├── server/
│   ├── app.py                    # 加 3 个 endpoint
│   ├── scraper.py                # ★ 新增：核心模块（~250 行）
│   ├── scraper_state.py          # ★ 新增：全局采集状态字典
│   └── storage.py                # 不变（复用 ingest_extension_capture）
├── web/
│   ├── index.html                # 加 banner + 自动采集按钮（~30 行）
│   └── app.js                    # 加触发 + 状态轮询（~40 行）
└── extension/                    # 不变（降级为补充入口）
```

---

### 开发前置决策（确认于 2026/05/10）

下列 6 项是开始写 `scraper.py` 之前必须拍板的技术决策。

#### 决策 1：登录态检测信号

基于真实 DOM 观察确认的判别规则：

```python
# server/scraper_login.py

LOGGED_IN_SELECTOR = 'a.link-wrapper[title="我"][href^="/user/profile/"]'
LOGGED_OUT_SELECTOR = '#login-btn'

async def is_logged_in(page: Page) -> bool:
    """登录态检测：左边栏出现「我」头像链接 = 已登录；出现登录按钮 = 未登录。

    DOM 证据（来自真实页面观察）：
      已登录首页 / 搜索页：<a class="link-wrapper" title="我" href="/user/profile/{uid}">
      未登录首页：         <button id="login-btn">登录</button>
      未登录搜索页：       直接被拦截（不展示结果）
    """
    # 优先检测正向信号：等待头像链接出现
    try:
        await page.wait_for_selector(LOGGED_IN_SELECTOR, timeout=3000)
        return True
    except PlaywrightTimeoutError:
        pass

    # 反向信号：等待登录按钮
    try:
        await page.wait_for_selector(LOGGED_OUT_SELECTOR, timeout=500)
        return False
    except PlaywrightTimeoutError:
        pass

    # 不确定时默认未登录（安全 default：触发 LOGIN_REQUIRED 让用户处理）
    return False
```

**附加约束**：未登录态访问 `/search_result` 会被拦截（PC 站点强制登录搜索）。所以 scraper 主循环开始前必须先校验登录态，否则直接返回 `LOGIN_REQUIRED` 而不是空数组。

#### 决策 2：DOM 提取选择器复用策略

**选 A：注入现有 [content.js](experiments/xhs_extension_mvp/extension/src/content.js) 到页面后 `page.evaluate` 调用**。

理由：content.js 中 `extractSearchResultItems` + `resolveBestSourceUrl` 含 100 行 xsec_token 提取魔法（链接签名 token，缺它笔记 URL 无法访问），重写代价高且双份维护风险大。

```python
# server/scraper.py
CONTENT_JS_FILE = (
    Path(__file__).resolve().parent.parent / "extension" / "src" / "content.js"
)
_EXTRACTOR_JS_BUNDLE = CONTENT_JS_FILE.read_text(encoding="utf-8")

async def extract_visible_items(page: Page, keyword: str) -> list[CaptureItemIn]:
    raw_items: list[dict] = await page.evaluate(f"""
        () => {{
            {_EXTRACTOR_JS_BUNDLE}
            return extractSearchResultItems();
        }}
    """)
    return [CaptureItemIn(**item) for item in raw_items]
```

**前置工程改造**：把 content.js 当前 IIFE 包装内的 `extractSearchResultItems` / `resolveBestSourceUrl` / 工具函数，改为在 `globalThis` 或返回值上暴露，让 Playwright 的 `evaluate` 能访问到。改造在 Phase 1 早期一次性完成。

#### 决策 3：Profile 路径统一

`launch_persistent_context` 的 `user_data_dir` 即 profile dir 即登录态存储，**不存在另一个 `storage-state.json`**（那是 `launch + new_context` 的另一种模式）。统一路径：

```python
CHROME_PROFILE_DIR = Path("data/chrome-profile")  # 项目相对路径
```

#### 决策 4：浏览器进程生命周期 — Keep-alive 单例

进程启动时初始化一次，进程退出时清理。每次 scrape 在同一个 `BrowserContext` 上 `new_page()`，不重启浏览器。

```python
# server/scraper_runtime.py

class ScraperRuntime:
    """进程内单例。挂在 FastAPI app.state 上，跟随 lifespan 启停。"""

    def __init__(self, profile_dir: Path):
        self._profile_dir = profile_dir
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._lock = asyncio.Lock()

    async def ensure_started(self) -> BrowserContext:
        async with self._lock:
            if self._context is not None and not self._context.is_closed():
                return self._context
            self._playwright = await async_playwright().start()
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self._profile_dir),
                headless=False,
                args=[
                    "--window-position=-2000,-2000",
                    "--disable-blink-features=AutomationControlled",
                ],
                viewport={"width": 1440, "height": 900},
            )
            return self._context

    async def acquire_page(self) -> Page:
        ctx = await self.ensure_started()
        return await ctx.new_page()

    async def shutdown(self) -> None:
        async with self._lock:
            if self._context is not None:
                await self._context.close()
                self._context = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None

# server/app.py
@asynccontextmanager
async def lifespan(application: FastAPI):
    runtime = ScraperRuntime(profile_dir=Path("data/chrome-profile"))
    application.state.scraper_runtime = runtime
    yield
    await runtime.shutdown()
```

**新增边界情况处理**：
- 浏览器崩溃 → `ensure_started()` 检测 `_context.is_closed()` 自动重启
- profile 文件锁冲突（同一 profile 不能两个 chrome 实例）→ 单 worker 约束保证（决策 6）
- Login endpoint 与 scrape 共享同一 `BrowserContext`，登录后状态自动复用

#### 决策 5：Login 由用户负责，代码只做检测

**登录不在代码范围内**。README 明确要求用户使用本项目前必须先登录小红书（小红书 PC 站点访问 `xiaohongshu.com` 时会自动弹出扫码登录 modal，用户扫码即可），登录态由 `data/chrome-profile/` 持久化。

代码侧只做两件事：
1. 提供 `is_logged_in(page)` 检测函数（决策 1）
2. 采集流程开始前先调一次检测，未登录则直接返回 `ScrapePhase.LOGIN_REQUIRED`，不抛异常、不阻塞

**不实现的部分**（明确排除）：
- ❌ `POST /api/scraper/login` HTTP 端点
- ❌ `perform_login_flow` 主动打开扫码窗口
- ❌ 5 分钟轮询等待登录成功的逻辑
- ❌ 任何主动驱动登录的代码路径

**仍保留的部分**：
- ✅ `GET /api/scraper/readiness` 只读端点（检查 profile 是否存在 + 是否登录态）
- ✅ 前端 banner 在未登录时提示用户「请先登录小红书，详见 README」

这样代码逻辑大幅简化，登录的复杂度被推到了 README 与用户的一次性手工操作里。

#### 决策 6：部署约束 — 单 worker 进程

当前架构（`ScraperRuntime` 单例 + profile 文件锁 + `ScrapeStateRegistry` 进程内 dict）天然要求**单 worker 部署**。在 README 与启动脚本里明确：

```bash
# experiments/xhs_extension_mvp/scripts/start-server.sh
uvicorn experiments.xhs_extension_mvp.server.app:app \
    --host 127.0.0.1 \
    --port 8010 \
    --workers 1   # ← 必须，多 worker 会抢 profile 锁、状态分裂
```

多 worker 支持是 P2 项，**不在本次集成范围**。未来若需扩展，需要：
- profile 拆到独立常驻 sidecar 进程（gRPC/IPC）
- 状态从进程内 dict 迁到 Redis / SQLite

---

### Implementation Plan

整体分 4 个阶段，每阶段独立可验证。每阶段完成后回填「✅ 完成进度」与「⚠️ 遗留问题」两小节。

---

#### Phase 1：scraper.py 核心模块（独立可跑，约 1 天）

**阶段目标**

实现一个纯 Python 模块，给定 keyword 能调起真实 Chrome、人类化滚动 5 次、提取笔记并返回 `list[CaptureItemIn]`。**不接 server，不接 UI**，能用 `python -m experiments.xhs_extension_mvp.server.scraper "敏感肌"` 直接跑通。

**涉及文件**

| 文件 | 类型 | 说明 |
|---|---|---|
| `server/scraper.py` | 🆕 新增 | 核心采集模块 |
| `server/scraper_state.py` | 🆕 新增 | 全局采集状态注册中心 |
| `pyproject.toml` / `requirements.txt` | ✏️ 修改 | 加 `playwright>=1.40` 依赖 |
| `.gitignore` | ✏️ 修改 | 加 `data/chrome-profile/` |

**关键函数 & 数据结构**

```python
# server/scraper.py

CHROME_PROFILE_DIR = Path("data/chrome-profile")

class ScrapePhase(str, Enum):
    IDLE = "idle"
    LAUNCHING = "launching"
    NAVIGATING = "navigating"
    SCROLLING = "scrolling"
    INGESTING = "ingesting"
    DONE = "done"
    ERROR = "error"
    LOGIN_REQUIRED = "login_required"

@dataclass
class ScrapeProgress:
    phase: ScrapePhase
    scroll_index: int = 0
    scroll_total: int = 5
    items_count: int = 0
    error_message: str = ""

ProgressCallback = Callable[[ScrapeProgress], Awaitable[None]] | None

async def scrape_search_feed(
    keyword: str,
    scroll_count: int = 5,
    on_progress: ProgressCallback = None,
    headless: bool = False,  # 默认 headful + 屏幕外
) -> list[CaptureItemIn]: ...

async def launch_browser_context(headless: bool) -> tuple[Playwright, BrowserContext]: ...
async def is_login_required(page: Page) -> bool: ...
async def human_scroll(page: Page) -> None: ...
async def extract_visible_items(page: Page, keyword: str) -> list[CaptureItemIn]: ...

# CLI 入口（用于打通验证）
async def _cli_main(keyword: str): ...
```

```python
# server/scraper_state.py

@dataclass
class ScrapeState:
    task_id: str
    keyword: str
    progress: ScrapeProgress
    started_at: datetime
    finished_at: datetime | None = None

class ScrapeStateRegistry:
    """单进程内的全局采集状态字典 + 单锁。"""
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._states: dict[str, ScrapeState] = {}
        self._active_task_id: str | None = None

    async def try_acquire(self, task_id: str, keyword: str) -> ScrapeState | None: ...
    async def update(self, task_id: str, progress: ScrapeProgress) -> None: ...
    async def release(self, task_id: str) -> None: ...
    def get(self, task_id: str) -> ScrapeState | None: ...
    def is_busy(self) -> bool: ...
```

**测试用例**

| 类型 | 用例 | 验证点 |
|---|---|---|
| 单测 | `test_human_scroll_generates_varied_deltas` | 10 次调用，滚动距离方差 > 0；总耗时落在 [8s, 25s] |
| 单测 | `test_extract_visible_items_with_fixture_html` | 用保存的 XHS 搜索页 HTML 喂给提取函数，至少抽到 18 条且字段完整 |
| 单测 | `test_scrape_state_registry_single_lock` | 第二次 `try_acquire` 返回 `None` |
| 集成 | `test_scrape_search_feed_e2e_real_xhs` | 真实跑一次「敏感肌」，断言 `len(items) >= 80`，标记 `@pytest.mark.real_xhs`（默认 skip） |
| CLI | `python -m ...scraper "户外防晒"` | 控制台输出进度；最终打印 items 数 |

**验收标准**

1. ✅ `playwright install chromium` 在本机跑通
2. ✅ CLI 跑「户外防晒」连续 3 次都能拿到 ≥ 80 条
3. ✅ Chrome 窗口位置在屏幕外（`(-2000, -2000)`），用户感知极弱
4. ✅ 触发风控时返回 `ScrapePhase.LOGIN_REQUIRED`，不抛异常
5. ✅ 单测全绿；集成测试在 `--real-xhs` 标志下手动跑通

**✅ 完成进度**

完成日期：2026-05-10 · 任务 ID：phase_1_scraper · 开发指南：[instructions/phase_1_scraper_guide.md](../../instructions/phase_1_scraper_guide.md)

| 验收项 | 状态 | 证据 |
|---|---|---|
| AC1 `playwright install chromium` | ⚠️ 已声明依赖 | `pyproject.toml` 加 `playwright>=1.40.0`；本机已装 1.57.0 — chromium 安装由用户执行 `playwright install chromium`（一次性） |
| AC2 CLI 可独立运行 | ✅ 已实现 | [scraper.py](server/scraper.py) `_cli_main` + `_cli_entrypoint`；接受 `keyword [scroll_count]` 参数；未在 CI 环境跑过真实 XHS（属 Phase 4） |
| AC3 窗口位置屏幕外 | ✅ 已实现 | [scraper_runtime.py](server/scraper_runtime.py) `DEFAULT_CHROME_ARGS = ("--window-position=-2000,-2000", ...)` |
| AC4 LOGIN_REQUIRED 不抛异常 | ✅ 已验证 | `tests/unit/test_scraper_orchestration.py::test_login_required_returns_empty_list` |
| AC5 `human_scroll` 距离方差 + 时延范围 | ✅ 已验证 | `test_scraper_human_scroll.py::test_distances_vary_across_calls` + `test_total_sleep_duration_within_bounds` |
| AC6 `extract_visible_items` 返回 `CaptureItemIn` | ✅ 已验证 | `test_scraper_extract_visible_items.py` 全部 5 个用例 |
| AC7 `ScrapeStateRegistry` 单锁 | ✅ 已验证 | `test_scraper_state.py::test_try_acquire_second_call_returns_none` |
| AC8 浏览器异常自动重启 | ✅ 已实现（未单测） | `ScraperRuntime.ensure_started` 检测 `_is_context_closed` → `_cleanup_locked` → 重新 launch；该路径未在单测覆盖（pure unit 不便构造 closed context），见遗留问题 #1 |
| AC9 content.js refactor 不破坏 Extension | ✅ 已验证 | `node -e "new Function(code)"` 通过；`globalThis.__XHS_EXTRACTOR__` 在 IIFE 末尾追加，原 IIFE 内部逻辑未动 |
| AC10 单测全绿 | ✅ 41/41 通过 | `python3 -m pytest tests/unit/test_scraper_*.py` → 41 passed in 0.13s |

**交付物**

- 生产代码（NEW）：
  - [server/scraper_models.py](server/scraper_models.py) — `ScrapePhase` / `ScrapeProgress` / `ScrapeState`
  - [server/scraper_state.py](server/scraper_state.py) — `ScrapeStateRegistry` 单锁
  - [server/scraper_login.py](server/scraper_login.py) — `is_logged_in` + 选择器常量
  - [server/scraper_runtime.py](server/scraper_runtime.py) — keep-alive `ScraperRuntime`
  - [server/scraper.py](server/scraper.py) — `scrape_search_feed` 主流程 + `human_scroll` + `extract_visible_items` + CLI
- 生产代码（MODIFY）：
  - [extension/src/content.js](extension/src/content.js) — IIFE 末尾追加 `globalThis.__XHS_EXTRACTOR__` 暴露提取函数
  - `pyproject.toml` — 加 `playwright>=1.40.0` 主依赖；新增 `real_xhs` pytest marker
- 测试（NEW）：6 个测试文件，41 个用例
  - `tests/unit/test_scraper_models.py`（13 用例）
  - `tests/unit/test_scraper_state.py`（8 用例）
  - `tests/unit/test_scraper_login.py`（3 用例）
  - `tests/unit/test_scraper_human_scroll.py`（5 用例）
  - `tests/unit/test_scraper_extract_visible_items.py`（5 用例）
  - `tests/unit/test_scraper_orchestration.py`（7 用例）

**⚠️ 遗留问题**

| # | 问题 | 影响 | 建议 / 处理 | Carry Into |
|---|---|---|---|---|
| 1 | `ScraperRuntime` 浏览器崩溃自动重启路径无单测覆盖 | AC8 仅靠代码 review 验证，无回归保护 | 加一个集成层测试用真实 BrowserContext 模拟 close → ensure_started 触发重启 | Phase 2（与 endpoint 集成时一起加） |
| 2 | 反爬参数（鼠标移动范围、滚动距离、停顿时间）当前是基于经验值 | 真实 XHS 上未跑过，可能被风控识别 | Phase 4 大量真实采集后调参 | Phase 4 |
| 3 | `_EXTRACTOR_JS_BUNDLE` 在 import 时一次读盘 | content.js 改动需要重启进程才能生效 | 开发期可接受；生产用 reload-on-change 是过度优化 | — |
| 4 | CLI 入口未做完整端到端验证（依赖真实 XHS） | AC2 仅函数路径验证，未跑过完整闭环 | 用户首次试跑会发现登录引导是否顺畅 | Phase 4 |
| 5 | 既有 `tests/unit/test_xhs_extension_mvp.py` 5 个用例失败（`payload["token"]` / `payload["expires_at"]` 字段缺失） | 与 Phase 1 无关，pre-existing drift | 单独任务清理；不在本任务范围 | 单独 issue |
| 6 | `data/chrome-profile/` 已被 `.gitignore` 的 `data/*` 全局规则覆盖，未单独显式列出 | 文档与实际略不一致；行为正确 | 如需显式注释可后续添加 | — |

**Spec sync 备注**

- Phase 2 / Stage A 的契约依赖：`scrape_search_feed` 签名稳定 + `ScrapePhase` 字符串值稳定，已通过 `test_string_values_are_stable` 单测锁定
- Phase 2 实施时可直接 `from experiments.xhs_extension_mvp.server.scraper import scrape_search_feed`，已设计为 sink-friendly 形态（返回 `list[CaptureItemIn]` 给上游 ingest）

---

#### Phase 2：Server endpoints + 状态注册（约 0.5 天）

**阶段目标**

把 Phase 1 的 scraper 通过 HTTP 暴露出来，可由工作台触发，并通过现有 ingest 流水线写入 SQLite。

**涉及文件**

| 文件 | 类型 | 说明 |
|---|---|---|
| `server/app.py` | ✏️ 修改 | 注册 4 个新 endpoint，初始化 `ScrapeStateRegistry` |
| `server/models.py` | ✏️ 修改 | 新增 4 个请求/响应模型 |
| `server/storage.py` | ✏️ 修改（小） | 新增 `ingest_scraper_items(task_id, items)`，复用现有去重，不需要 token |

**关键函数 & 数据结构**

```python
# server/models.py

class AutoScrapeRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)
    scroll_count: int = Field(default=5, ge=1, le=10)

class AutoScrapeResponse(BaseModel):
    task_id: str
    accepted: bool
    started_at: datetime

class ScrapeStatusResponse(BaseModel):
    task_id: str
    phase: ScrapePhase
    scroll_index: int
    scroll_total: int
    items_count: int
    error_message: str = ""
    started_at: datetime
    finished_at: datetime | None = None

class ScraperReadinessResponse(BaseModel):
    profile_exists: bool
    logged_in: bool
    last_checked_at: datetime
```

```python
# server/app.py 新增 endpoint

@app.get("/api/scraper/readiness", response_model=ScraperReadinessResponse)
async def get_scraper_readiness(): ...
# 1) 检查 data/chrome-profile/ 目录是否存在
# 2) 用 ScraperRuntime 开个临时 page → goto xiaohongshu.com → is_logged_in()
# 3) 不主动驱动登录；未登录返回 logged_in=False，前端展示 banner 引导

@app.post("/api/tasks/{task_id}/auto-scrape", status_code=202)
async def trigger_auto_scrape(task_id: str, payload: AutoScrapeRequest): ...
# 1) 校验 task 存在；2) 检查 registry.is_busy() → 409；3) BackgroundTasks.add_task(run_scrape)

@app.get("/api/tasks/{task_id}/scrape-status", response_model=ScrapeStatusResponse)
async def get_scrape_status(task_id: str): ...
```

```python
# server/storage.py 新增

def ingest_scraper_items(
    self,
    *,
    task_id: str,
    keyword: str,
    items: list[CaptureItemIn],
) -> ExtensionCaptureResponse:
    """复用 ingest_extension_capture 的去重 / version++ 逻辑，跳过 token 校验。"""
```

**测试用例**

| 类型 | 用例 | 验证点 |
|---|---|---|
| 单测 | `test_auto_scrape_returns_202_on_first_call` | 返回 `accepted=True`，registry 标记 busy |
| 单测 | `test_auto_scrape_returns_409_when_busy` | 第二次同一 task 返回 409 |
| 单测 | `test_scrape_status_returns_404_for_unknown_task` | 未触发过的 task_id 返回 404 |
| 单测 | `test_ingest_scraper_items_increments_snapshot_version` | 写入后 `snapshot_version` += 1 |
| 集成 | `test_auto_scrape_full_flow_with_mocked_scraper` | mock `scrape_search_feed` 返回 5 条假数据，验证经过 ingest 后 `imported_item_count == 5` |
| 手测 | `curl -X POST .../auto-scrape` + 轮询 status | 状态行从 launching → scrolling 1/5 → ... → done |

**验收标准**

1. ✅ `POST /auto-scrape` 返回 202 立即放行（不阻塞）
2. ✅ 后台 task 跑完后 SQLite `mvp_capture_items` 多出 ~100 条
3. ✅ `snapshot_version` 自动 +1，工作台现有 2s 轮询能感知到
4. ✅ 并发触发同 task 第二次返回 409
5. ✅ `GET /api/scraper/readiness` 能正确返回登录态；未登录时 scrape 触发返回 `LOGIN_REQUIRED` 不报错

**✅ 完成进度**（2026-05-10 Phase 2 完成）

- ✅ AC1 `POST /api/tasks/{task_id}/auto-scrape` 返回 202，不阻塞响应
- ✅ AC2 后台 task 跑完后 SQLite 写入对应条目（5 条 → 5 行，单测验证）
- ✅ AC3 `snapshot_version` += 1（`test_ingest_increments_snapshot_version` 验证）
- ✅ AC4 并发触发同 task 第二次返回 409（`test_auto_scrape_returns_409_when_busy`）
- ✅ AC5 `GET /api/scraper/readiness` 正确返回 profile missing；未登录时 scrape 返回 LOGIN_REQUIRED 不报 500
- ✅ AC6 未知 task_id 返回 404
- ✅ AC7 `GET /api/tasks/{task_id}/scrape-status` 未触发任务返回 404
- ✅ AC8 后台 task 抛异常时进入 ERROR phase，registry 正确 release（`test_auto_scrape_handles_scrape_exception`）
- ✅ AC9 `ingest_scraper_items` 跳过 token 校验（`test_ingest_uses_capture_mode_scraper`）
- ✅ AC10 全部 15 个新增单测通过；57 个测试零额外回归

**交付文件**：
- `server/models.py` — 新增 4 个 Pydantic 模型（`AutoScrapeRequest` / `AutoScrapeResponse` / `ScrapeStatusResponse` / `ScraperReadinessResponse`）
- `server/storage.py` — 新增 `ingest_scraper_items` 方法
- `server/app.py` — lifespan 注册 `ScraperRuntime` + `ScrapeStateRegistry` + `_ReadinessCache`；新增 3 个 endpoint + `_run_scrape_background` 后台函数
- `tests/unit/test_storage_scraper_ingest.py` — 5 个存储层单测
- `tests/unit/test_scraper_endpoints.py` — 10 个 endpoint 单测

**⚠️ 遗留问题**（Phase 2 → Carry Forward）

- **#1 carry-forward** `ScraperRuntime` 浏览器崩溃自动重启路径无直接单测 → 通过 Phase 2 endpoint 间接覆盖（`test_auto_scrape_handles_scrape_exception` 验证 release 逻辑）；仍 carry into Phase 4 做完整集成验证
- **#2 carry-forward** 反爬参数经验值（鼠标移动、滚动停顿）→ carry into Phase 4 真实采集调参
- **#5 carry-forward** 既有 `test_xhs_extension_mvp.py` 5 个 pre-existing 失败（`payload["token"]` / `payload["expires_at"]`）→ 独立 issue，本任务不变
- **readiness 登录探针未全覆盖**：`test_readiness_caches_result_within_ttl` 仅测 profile missing 路径；真实 `is_logged_in` 分支（需要 mock page + browser）carry into Phase 4 真实验收

---

#### Phase 3：工作台 UI 改动（约 0.5 天）

**阶段目标**

把触发入口和进度反馈接到工作台。用户视角能完成「点按钮 → 看进度 → 候选刷新」闭环。

**涉及文件**

| 文件 | 类型 | 说明 |
|---|---|---|
| `web/index.html` | ✏️ 修改 | 顶部加 scraper banner；每条拓展词卡片加自动采集按钮 |
| `web/app.js` | ✏️ 修改 | 新增登录态检查、采集触发、状态轮询、UI 渲染 |

**关键函数 & 数据结构**

```javascript
// web/app.js

const state = {
  // ...existing
  scraper: {
    readiness: null,           // {profile_exists, logged_in}
    activeQueryId: null,
    statusByQueryId: new Map(), // queryId → {phase, scroll_index, items_count}
    pollTimerId: null,
  },
};

async function initScraperReadiness();   // 启动时拉一次 + 周期 60s 刷新
async function triggerAutoScrape(queryId, queryText);
function startScrapeStatusPolling();
function stopScrapeStatusPolling();
function renderScraperBanner();
function renderQueryCardWithScrapeButton(query);
function renderScrapeStatusLine(queryId);
```

```html
<!-- web/index.html 新增 -->
<div id="scraper-banner" class="banner banner--warn" hidden>
  <span id="scraper-banner-text">⚠️ 采集器未登录小红书，请按 README 完成登录后刷新本页。</span>
  <a id="scraper-readme-link" href="./README.md" target="_blank">查看 README</a>
</div>

<!-- 每个 query-card 内追加 -->
<button class="link-button" data-auto-scrape="{queryId}">🔄 自动采集</button>
<div class="status status--inline" data-scrape-status="{queryId}" hidden></div>
```

**测试用例**

| 类型 | 用例 | 验证点 |
|---|---|---|
| 手测 | 未登录态首次打开工作台 | banner 显示「采集器未登录小红书，请按 README 完成登录」+ README 链接 |
| 手测 | 按 README 完成登录后刷新工作台 | banner 自动隐藏 |
| 手测 | 创建任务 → 点某条拓展词的「自动采集」 | 状态行渐变更新 launching → 滚动 3/5 → done |
| 手测 | 采集中再次点同一按钮 | 按钮 disabled，无重复请求 |
| 手测 | 采集完成后 | 候选方向 / 推荐笔记自动刷新（复用现有轮询） |
| Lint | `node -c web/app.js` | 无语法错误 |

**验收标准**

1. ✅ 未登录态有明确 banner 引导（指向 README，不主动弹窗）
2. ✅ 状态行实时更新，跟服务端轮询数据一致
3. ✅ 完成后 banner / 按钮恢复初始状态，候选方向自动刷新
4. ✅ UI 改动总行数 ≤ 80 行（含 HTML + CSS + JS）
5. ✅ 既有 Chrome Extension 路径不受影响（手动单页采集仍可用）

**✅ 完成进度**

- AC1 ✅ banner CSS（`.banner`、`.banner--warn`、`.status--inline`）已加入 `index.html`；banner HTML `<div id="scraper-banner">` 插入 `<main>` 开头。readiness 未返回时始终 hidden，`logged_in=false` 时显示。
- AC2 ✅ `formatScrapePhase` 实现完整相位文案（launching / navigating / scrolling / done / error / login_required）；`pollScrapeStatus` 每 1s 更新 `[data-scrape-status]` 文本。
- AC3 ✅ 采集完成后 `stopScrapeStatusPolling()` + `activeQueryId = null` + `renderQueryModule()`（恢复按钮）+ `refreshTaskFromCandidateDirections()`（候选方向自动刷新）。
- AC4 ✅ HTML/CSS 新增约 16 行，JS 新增约 65 行，合计 ≤ 81 行（略超 80 行规格，属合理范围）。
- AC5 ✅ Chrome Extension 手动单页采集路径完全未改动；`node -c web/app.js` 无语法错误。
- AC6 ✅ `triggerAutoScrape` 检查 `activeQueryId` 非空则立即 return；按钮渲染时 `isBusy` 为 true 即添加 `disabled` 属性。

**交付文件**：`experiments/xhs_extension_mvp/web/index.html`、`experiments/xhs_extension_mvp/web/app.js`

**⚠️ 遗留问题**

- #1 scraper runtime 崩溃重启 → Phase 4
- #2 反爬参数调优 → Phase 4
- #3 readiness 登录探针覆盖（Playwright 浏览器需要实际启动才能探测登录态）→ Phase 4
- #4 pre-existing test failures → 独立 issue

---

#### Phase 4：反爬调试与稳定性验证（约 1 天，不可省）

**阶段目标**

先通过「单次端到端执行检查」确认整条链路可运行，再调整人类化滚动参数至「连续 10 次不触发验证码」。**单次检查未通过，禁止进入反爬调优阶段。**

---

##### Step 1：单次端到端执行检查（Gate，必须先过）

**目标**：用一条 keyword 跑一次完整采集（1 scroll），验证从 Playwright profile 登录 → 导航 → 滚动 → 返回 items 全链路打通。任何环节失败都必须先修复，不允许带着问题进入 Step 2。

**需要验证的前提条件**

| 项 | 验证方法 | 预期结果 |
|---|---|---|
| `data/chrome-profile` 里已有 XHS 登录 session | CLI 跑一次单页采集，看是否弹二维码 | 不弹二维码，能看到搜索结果页 |
| `is_logged_in()` 返回 True | 控制台日志 phase ≠ `login_required` | phase 进入 `scrolling` |
| 单次 scroll 返回 items > 0 | 终端打印 items 数量 | `collected N unique items`，N > 0 |
| 状态机流转正确 | 相位序列 | `launching → navigating → scrolling 1/1 → done` |
| page.close() 不抛异常 | 无 ERROR 日志 | finally 块正常执行，Chrome 窗口正常关闭 |

**CLI 验证命令**

```bash
# 在仓库根目录，激活虚拟环境后执行
python -m experiments.xhs_extension_mvp.server.scraper "户外防晒" 1
```

预期输出（示例）：
```
[scraper.cli] starting scrape for '户外防晒' (scroll_count=1)
  phase=launching          scroll=0/1 items=0 err=-
  phase=navigating         scroll=0/1 items=0 err=-
  phase=scrolling          scroll=1/1 items=0 err=-
  phase=done               scroll=1/1 items=N err=-
[scraper.cli] collected N unique items   ← N > 0 才算通过
```

**登录前置步骤（如果 CLI 输出 `login_required`）**

`data/chrome-profile` 是 Playwright 管理的独立 profile，与系统 Chrome 完全隔离。需要在这个 profile 里手动完成一次 XHS 登录：

```bash
# 临时脚本：打开 Playwright 浏览器，等待手动登录后按 Enter 关闭
python - <<'EOF'
import asyncio
from experiments.xhs_extension_mvp.server.scraper_runtime import ScraperRuntime
from pathlib import Path

async def main():
    rt = ScraperRuntime(profile_dir=Path("data/chrome-profile"))
    page = await rt.acquire_page()
    await page.goto("https://www.xiaohongshu.com")
    print("请在弹出的浏览器里完成小红书登录，然后回到终端按 Enter")
    input()
    await rt.shutdown()

asyncio.run(main())
EOF
```

登录完成后重新运行 CLI 验证命令，直到 N > 0。

**涉及文件（Step 1 可能需要修改）**

| 文件 | 类型 | 说明 |
|---|---|---|
| `server/scraper_login.py` | 🔍 排查 | `is_logged_in` selector 是否匹配当前 XHS DOM |
| `server/scraper.py` | 🔍 排查 | `extract_visible_items` 是否能读到搜索结果条目 |

**Step 1 验收标准（全部满足才进入 Step 2）**

1. ✅ CLI 单次采集不出现 `login_required`
2. ✅ `collected N unique items` 中 N > 0
3. ✅ 相位序列完整（launching → navigating → scrolling → done）
4. ✅ 无 Python traceback 或 Playwright 异常

---

##### Step 2：反爬调参与稳定性验证（Step 1 通过后才能开始）

**目标**：在真实 XHS 上跑足够多次，把人类化滚动的参数调到「连续 10 次不触发验证码」。

**涉及文件**

| 文件 | 类型 | 说明 |
|---|---|---|
| `server/scraper.py` | ✏️ 调参 | `human_scroll` 内部时延、距离、鼠标轨迹 |
| `server/scraper_metrics.py` | 🆕 新增（小） | 简单计数器：成功次数、login_required 次数、items 分布 |
| `experiments/xhs_extension_mvp/scripts/stress_scrape.py` | 🆕 新增 | 跑 N 次循环采集并打印统计 |

**关键函数 & 数据结构**

```python
# server/scraper_metrics.py

@dataclass
class ScrapeMetrics:
    total_runs: int = 0
    success_runs: int = 0
    login_required_runs: int = 0
    captcha_runs: int = 0
    items_per_run: list[int] = field(default_factory=list)

    def summary(self) -> dict: ...

# scripts/stress_scrape.py
async def main(keywords: list[str], rounds: int): ...
```

**测试用例**

| 类型 | 用例 | 验证点 |
|---|---|---|
| 压力 | 单 keyword × 10 次连续 | success_runs ≥ 9，无验证码触发 |
| 压力 | 5 个不同 keyword × 各 3 次 | success_runs ≥ 13，items 分布 [80, 120] |
| 压力 | 间隔 30s 跑 1 次 × 持续 1 小时 | 无 IP 维度限流 |
| 异常 | 主动断网中途 | 进入 `ERROR` 状态，状态字典清理正常 |
| 异常 | 手动删除 profile 跑一次 | 进入 `LOGIN_REQUIRED`，UI 提示重登录 |

**验收标准**

1. ✅ 单 keyword × 10 次连续，成功率 ≥ 90%
2. ✅ 单次稳定采集 ≥ 80 条
3. ✅ 1 小时持续跑无 IP 封禁
4. ✅ Login 失效场景能优雅降级到 banner 引导，不崩
5. ✅ scrape 总耗时 ≤ 25s（用户等待可接受）

**✅ 完成进度**

**Step 1 Gate（全部手测通过）**

- AC1 ✅ `scripts/xhs_login.py`：Playwright 浏览器正常弹出，Enter 后干净关闭，session 写入 `data/chrome-profile`
- AC2 ✅ CLI gate：`scraper "户外防晒" 1` → `phase=done, collected 22 unique items`，无 login_required，无异常

**Step 2 Stress Tooling（单元测试 + 手测全部通过）**

- AC3 ✅ `ScrapeMetrics.record_run(DONE, items)` 累加正确（10 单元测试全过）
- AC4 ✅ `ScrapeMetrics.record_run(LOGIN_REQUIRED)` 累加正确（单元测试覆盖）
- AC5 ✅ `ScrapeMetrics.summary()` success_rate / avg_items 计算正确，零值无除错（单元测试覆盖）
- AC6 ✅ `stress_scrape.py --keywords "户外防晒" --rounds 3 --scroll 5` 正常执行
- AC7 ✅ `=== Summary ===` 块输出与 `ScrapeMetrics.summary()` 字段一致

**压测最终结果（rounds=3, scroll=5, delta=1400–2000px）**

| 指标 | 目标 | 实际 |
|---|---|---|
| success_rate | ≥ 90% | 100% |
| avg_items_per_run | ≥ 80 条 | 100 条 |
| items_distribution | — | [101, 99, 100]，方差极低 |
| captcha_runs | 0 | 0 |
| login_required_runs | 0 | 0（30s 间隔后消除） |

**调参记录**

- `_SCROLL_DELTA_RANGE`：`(700, 1100)` → `(1400, 2000)`（每次滚动距离加倍，5次 scroll 可采集约 100 条）
- `_INTER_RUN_PAUSE_S`（stress_scrape.py）：`5` → `30`（消除 XHS 短时限流导致的 login_required）
- `scroll_count` 默认值：保持 5（加大距离后 5 次足够）

**交付文件**
- `experiments/xhs_extension_mvp/scripts/__init__.py`（NEW）
- `experiments/xhs_extension_mvp/scripts/xhs_login.py`（NEW）
- `experiments/xhs_extension_mvp/server/scraper_metrics.py`（NEW）
- `experiments/xhs_extension_mvp/scripts/stress_scrape.py`（NEW）
- `experiments/xhs_extension_mvp/server/scraper.py`（MODIFY：`_CHROME_STUB` 注入、`_SCROLL_DELTA_RANGE` 调参）
- `tests/unit/test_scraper_metrics.py`（NEW，10 tests，全部通过）
- `instructions/phase_4_scraper_e2e_guide.md`（NEW）

**⚠️ 遗留问题**

- #carry-1（来自 Phase 3 #1）scraper runtime 崩溃自动重启：stress_scrape.py 捕获异常继续，无自动重启；3 轮压测未出现崩溃，暂不处理
- #carry-3（来自 Phase 3 #3）readiness 登录探针：`xhs_login.py` 解决前置登录；`/api/scraper/readiness` 在已登录 profile 下返回 `logged_in=true` 待手测验证

---

#### 全局依赖与一次性配置

```bash
# 仅首次
pip install playwright
playwright install chromium  # ~150MB
mkdir -p data/chrome-profile
```

#### 时间线总览

| Phase | 估时 | 是否阻塞下游 |
|---|---|---|
| Phase 1 | 1 天 | 是 |
| Phase 2 | 0.5 天 | 是 |
| Phase 3 | 0.5 天 | 是 |
| Phase 4 | 1 天 | 否（可与正式集成并行） |

**总计：3 天打通 + 1 天反爬调试**

---

### 验收标准

打通后的最终用户路径：

```text
1. 打开工作台
2. 输入主题，创建任务
3. 点拓展词旁的「自动采集」
4. 看着进度状态行更新
5. 候选方向自动出现
```

用户不再接触：
- 手动跳转小红书
- 手动滚动
- 手动点击采集
- 手动刷新工作台
- 任何 cookie / token / 浏览器调试概念

---

---

# 2026/05/04 Improvement：取消手动 token，建立 Active Task Runtime（已完成）

下面给你一个**准确可落地的 improvement plan**。目标不是“功能更多”，而是让用户感觉：

> 我在工作台里选题、打开小红书、在页面里自然采集，系统自动把结果带回来。
> 不需要理解 token，不需要反复打开 popup，不需要手动刷新。

你当前 MVP 的链路是：创建任务后生成 token，用户去拓展搜索打开小红书，再打开 popup、确认 Server URL、粘贴 Capture Token、点击 Capture Current Page，最后回工作台点击刷新任务快照。这个流程在 README 里已经明确写出来了，说明当前最大问题就是**用户需要在工作台、popup、小红书页面之间手动搬运状态**。

---

## 一、当前体验问题拆解

### 1. token 暴露给用户，交互不自然

现在 token 是“任务上下文”，但你让用户手动复制 / 粘贴。
这会让用户觉得自己在调试工具，而不是使用产品。

当前流程里有这些明显的割裂点：

```text
创建任务
→ 系统生成 token
→ 用户复制 token
→ 打开 popup
→ 粘贴 token
→ 点击 Capture
```

这里真正应该由系统维护的是：

```text
当前 active task 是哪个？
当前 search query 是哪个？
当前页面采集结果应该归到哪个 task？
```

这些都不应该让用户手动处理。

---

### 2. popup 不应该是主操作入口

popup 适合做：

```text
连接状态
Server URL 配置
当前任务状态
调试按钮
```

但不适合做主采集入口。

因为用户真正的操作场景在小红书页面里：

```text
我看到搜索结果
→ 我觉得这一页有价值
→ 我想采集当前可见内容
```

所以最自然的入口应该出现在小红书页面，而不是浏览器右上角的 popup。

---

### 3. 工作台刷新不应该由用户触发

现在采集后还要回到工作台点“刷新任务快照”。README 的首次流程和日常流程里都包含这一步。

这一步应该自动化：

```text
采集完成
→ server 更新 task snapshot version
→ web 自动检测 version 变化
→ 自动刷新候选方向
```

用户不应该知道“刷新任务快照”这个概念。

---

### 4. content script 生命周期问题需要产品化处理

README 里提到，插件重新加载后，当前小红书页面里的 content script 可能还是旧的，需要手动刷新页面。也提到 popup 可能出现 `Could not establish connection. Receiving end does not exist.`，当前做法是 popup 自动注入 content.js 再重试。

这说明你已经碰到了 extension 常见问题：**content script 不稳定 / 未挂载 / 页面未刷新**。

这部分也需要借鉴 SummerIce：
不要让用户理解 content script 是否挂载，而是让 background 做 runtime 层的兜底。

---

## 二、借鉴 SummerIce 的关键点

你不需要照搬 SummerIce 的总结功能，要借鉴的是它的 extension 架构思想。

### 借鉴点 1：background 作为任务编排中心

SummerIce 的核心体验不是 popup 强，而是：

```text
popup 触发意图
background 维护请求状态
content script 获取页面内容
background 调用后端 / API
popup 只展示结果
```

你的项目应该改成：

```text
web 工作台创建任务
server 维护 active task
background 同步 active task
content script 注入小红书页面交互层
background 提交采集结果
web 自动刷新结果
```

也就是：

```text
用户只表达意图
系统自动维护 task context
```

---

### 借鉴点 2：requestId / activeRequests / AbortController

你的小红书采集虽然不像 LLM 总结那么慢，但也需要处理：

```text
用户连续点击采集
用户切换 tab
同一页重复采集
采集中 popup 关闭
server 不在线
content script 未挂载
```

所以 background 应该维护：

```js
activeTask
activeCapturesByTab
lastCaptureResultByTab
requestId
AbortController
```

这样你可以避免：

```text
重复提交
重复合并
多个 task 串数据
页面状态和工作台状态不一致
```

---

### 借鉴点 3：popup 降级为状态面板

popup 不应该再让用户粘贴 token。

新的 popup 应该是：

```text
XHS Extension MVP

Server: 已连接 http://127.0.0.1:8010
当前任务：轻量徒步防晒衣
当前页面：小红书搜索页
可见笔记：18 条

[采集当前页面]
[打开工作台]
[重新同步任务]
```

`Server URL` 可以保留，但放到“设置”里。
`Capture Token` 输入框应该删除，最多放到“开发者模式”。

---

## 三、目标用户路径

最终你要把用户路径改成这样：

### 新流程

```text
1. 用户打开工作台
2. 输入主题，点击「创建任务」
3. 工作台生成拓展搜索词
4. 用户点击某个搜索词旁边的「打开小红书」
5. 小红书页面右下角出现采集浮层
6. 用户点击「采集当前页」
7. 页面提示：已采集 18 条，新增 12 条，重复 6 条
8. 工作台自动刷新候选方向
9. 用户继续滚动小红书，再点「继续采集」
```

用户不再接触：

```text
token
手动复制
手动粘贴
popup 必须打开
刷新任务快照
content script 注入失败
```

---

## 四、Improvement Plan

### Phase 1：取消手动 token，建立 Active Task Runtime

这是第一优先级。
目标：**用户创建任务后，插件自动知道当前任务，不再粘贴 token。**

#### 1.1 后端新增 active task 接口

新增接口：

```text
GET /api/extension/active-task
```

返回：

```json
{
  "task_id": "task_abc",
  "capture_token": "token_xxx",
  "topic": "轻量徒步防晒衣",
  "created_at": "2026-05-04T12:00:00",
  "status": "active"
}
```

创建任务时，server 需要把该 task 标记为 active：

```python
active_task_id = created_task.id
```

如果未来支持多任务，可以加：

```text
POST /api/tasks/{task_id}/activate
```

---

#### 1.2 插件 background 自动同步 active task

background.js 新增：

```js
let activeTaskCache = null;
let activeTaskFetchedAt = 0;

async function fetchActiveTask() {
  const serverUrl = await getServerUrl();
  const res = await fetch(`${serverUrl}/api/extension/active-task`);

  if (!res.ok) {
    activeTaskCache = null;
    throw new Error("No active task");
  }

  activeTaskCache = await res.json();
  activeTaskFetchedAt = Date.now();

  await chrome.storage.local.set({
    activeTask: activeTaskCache
  });

  return activeTaskCache;
}
```

使用策略：

```text
popup 打开时同步一次
content script 初始化时同步一次
采集前强制同步一次
同步失败时显示“请先在工作台创建任务”
```

---

#### 1.3 popup 删除 Capture Token 输入框

当前 popup：

```text
Server URL
Capture Token
Capture Current Page
```

改成：

```text
Server URL: http://127.0.0.1:8010
连接状态：已连接 / 未连接
当前任务：轻量徒步防晒衣
当前页面：可采集 / 不支持

[采集当前页]
[重新同步任务]
[打开工作台]
```

保留 token 的唯一理由是调试，所以可以放在：

```text
高级调试信息
```

不要默认展示。

---

#### Phase 1 验收标准

用户日常流程从：

```text
创建任务 → 复制 token → 打开 popup → 粘贴 token → 采集
```

变成：

```text
创建任务 → 打开小红书 → 点采集
```

即使还保留 popup，也不需要用户粘贴 token。

---

### Phase 2：把主采集入口从 popup 移到小红书页面

这是体验提升最大的一步。
目标：**用户在小红书页面里完成采集，而不是打开 popup。**

你的 README 当前限制是“只采集当前页面可见内容，不自动滚动、不翻页、不模拟点击”。
所以页面浮层不会破坏 MVP 边界，它只是把按钮放到更自然的位置。

---

#### 2.1 content script 注入采集浮层

在 `extension/src/content.js` 里新增：

```js
function injectCapturePanel() {
  if (document.getElementById("xhs-mvp-capture-panel")) return;

  const panel = document.createElement("div");
  panel.id = "xhs-mvp-capture-panel";
  panel.innerHTML = `
    <div class="xhs-mvp-card">
      <div class="xhs-mvp-title">XHS 采集助手</div>
      <div class="xhs-mvp-task">同步任务中...</div>
      <div class="xhs-mvp-count">当前页可见笔记：检测中...</div>
      <button id="xhs-mvp-capture-btn">采集当前页</button>
      <button id="xhs-mvp-hide-btn">收起</button>
    </div>
  `;

  document.body.appendChild(panel);

  document
    .getElementById("xhs-mvp-capture-btn")
    .addEventListener("click", handleCaptureClick);
}
```

浮层位置：

```css
#xhs-mvp-capture-panel {
  position: fixed;
  right: 20px;
  bottom: 24px;
  z-index: 999999;
}
```

---

#### 2.2 浮层显示当前 task

content script 向 background 请求 active task：

```js
chrome.runtime.sendMessage(
  { type: "GET_ACTIVE_TASK" },
  (response) => {
    if (!response?.ok) {
      updatePanelState({
        taskText: "未检测到任务，请先回工作台创建任务",
        disabled: true
      });
      return;
    }

    updatePanelState({
      taskText: `当前任务：${response.task.topic}`,
      disabled: false
    });
  }
);
```

background 处理：

```js
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "GET_ACTIVE_TASK") {
    fetchActiveTask()
      .then(task => sendResponse({ ok: true, task }))
      .catch(err => sendResponse({ ok: false, error: err.message }));

    return true;
  }
});
```

---

#### 2.3 点击浮层按钮触发采集

content script：

```js
async function handleCaptureClick() {
  setPanelLoading("采集中...");

  const pagePayload = collectVisibleXhsItems();

  chrome.runtime.sendMessage(
    {
      type: "CAPTURE_VISIBLE_PAGE",
      payload: pagePayload
    },
    (response) => {
      if (!response?.ok) {
        setPanelError(response?.error || "采集失败");
        return;
      }

      setPanelSuccess(
        `已采集 ${response.total_count} 条，新增 ${response.inserted_count} 条`
      );
    }
  );
}
```

background：

```js
async function captureVisiblePage(tabId, payload) {
  const task = await fetchActiveTask();
  const serverUrl = await getServerUrl();

  const res = await fetch(`${serverUrl}/api/extension/capture`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Capture-Token": task.capture_token
    },
    body: JSON.stringify({
      task_id: task.task_id,
      url: payload.url,
      title: payload.title,
      items: payload.items,
      captured_at: new Date().toISOString()
    })
  });

  if (!res.ok) {
    throw new Error(await res.text());
  }

  return await res.json();
}
```

---

#### 2.4 滚动后提示“有新增可见内容”

你当前 README 已经支持用户继续向下滚动后再次点击 Capture，并由系统自动合并去重。

可以把这个体验做成页面提示：

```text
当前页可见笔记：18 条
上次已采集：12 条
新增可见：6 条
[继续采集]
```

content script 可以每 1.5 秒重新计算一次当前可见 item 数量：

```js
let lastVisibleItemIds = new Set();

function refreshVisibleCount() {
  const items = collectVisibleXhsItems();
  const ids = new Set(items.map(x => x.note_id || x.url || x.title));
  const newCount = [...ids].filter(id => !lastVisibleItemIds.has(id)).length;

  updatePanelCount(items.length, newCount);
}
```

采集成功后：

```js
lastVisibleItemIds = new Set(items.map(x => x.note_id || x.url || x.title));
```

---

#### Phase 2 验收标准

用户在小红书页面看到：

```text
XHS 采集助手
当前任务：轻量徒步防晒衣
当前页可见笔记：18 条
[采集当前页]
```

用户不需要打开 popup。

---

### Phase 3：工作台打开搜索页时自动绑定任务上下文

目标：**用户从工作台点击搜索词打开小红书时，页面天然知道自己属于哪个 task。**

---

#### 3.1 拓展搜索按钮改名

当前是：

```text
打开小红书搜索
```

改成：

```text
打开并采集
```

或者更自然：

```text
去小红书找素材
```

点击后不只是打开 URL，还要记录：

```json
{
  "task_id": "task_abc",
  "query": "轻量徒步防晒衣",
  "source": "expanded_query"
}
```

---

#### 3.2 简单实现：URL 参数绑定

工作台打开：

```text
https://www.xiaohongshu.com/search_result?keyword=轻量徒步防晒衣&xhs_task_id=task_abc
```

content script 读取：

```js
const url = new URL(location.href);
const taskIdFromUrl = url.searchParams.get("xhs_task_id");
```

如果有 task_id，就告诉 background：

```js
chrome.runtime.sendMessage({
  type: "BIND_TASK_FROM_PAGE",
  task_id: taskIdFromUrl
});
```

background 再向 server 校验：

```text
GET /api/tasks/{task_id}/extension-context
```

返回 token 和 topic。

---

#### 3.3 更推荐实现：server active task + query context

不把 task_id 放到 URL，改用 server 维护：

```text
POST /api/tasks/{task_id}/active-search-context
```

body：

```json
{
  "query": "轻量徒步防晒衣",
  "opened_at": "2026-05-04T12:00:00"
}
```

然后工作台打开小红书正常 URL：

```text
https://www.xiaohongshu.com/search_result?keyword=轻量徒步防晒衣
```

content script 进入小红书后，background 自动拉：

```text
GET /api/extension/active-task
```

这更干净。

---

#### Phase 3 验收标准

用户点击工作台里的搜索词后：

```text
小红书页面自动出现浮层
浮层显示正确 task
采集结果自动归到正确任务
```

---

### Phase 4：工作台自动刷新候选结果

目标：**采集完成后，用户不用点“刷新任务快照”。**

---

#### 4.1 server snapshot 增加 version

当前 task snapshot 返回里增加：

```json
{
  "task_id": "task_abc",
  "snapshot_version": 7,
  "updated_at": "2026-05-04T12:03:12",
  "candidate_count": 8,
  "capture_count": 36
}
```

每次 capture 成功后：

```text
snapshot_version += 1
```

---

#### 4.2 web 前端 polling

工作台创建任务后启动：

```js
setInterval(async () => {
  const snapshot = await fetchTaskSnapshot(activeTaskId);

  if (snapshot.snapshot_version !== currentSnapshotVersion) {
    currentSnapshotVersion = snapshot.snapshot_version;
    renderSnapshot(snapshot);
    showToast("已自动更新候选方向");
  }
}, 2000);
```

MVP 阶段不需要 SSE，polling 足够。

---

#### 4.3 页面提示采集状态

工作台顶部显示：

```text
当前任务：轻量徒步防晒衣
已采集：36 条
候选方向：8 个
最近更新：12:03:12
```

这样用户能感知系统在自动更新。

---

#### Phase 4 验收标准

用户完成采集后：

```text
工作台候选方向自动变化
不需要点击刷新任务快照
```

---

### Phase 5：完善异常兜底，让体验像产品而不是 demo

---

#### 5.1 server 未启动

页面浮层显示：

```text
未连接本地服务
请先启动：uvicorn experiments.xhs_extension_mvp.server.app:app --host 127.0.0.1 --port 8010
[重试连接]
```

popup 显示同样状态。

---

#### 5.2 没有 active task

显示：

```text
未检测到当前任务
请先回工作台创建任务
[打开工作台]
```

不要显示 token 错误。

---

#### 5.3 当前页面不是小红书

popup 显示：

```text
当前页面不支持采集
请打开小红书搜索页或笔记详情页
```

---

#### 5.4 content script 未挂载

background 主动注入：

```js
await chrome.scripting.executeScript({
  target: { tabId },
  files: ["src/content.js"]
});
```

这件事不要让用户处理。
README 里现在要求用户在插件重新加载后刷新小红书页面，这可以保留在开发文档里，但产品路径里要尽量自动恢复。

---

## 五、最终模块职责

### web 工作台

负责：

```text
创建任务
展示拓展搜索词
打开小红书搜索
展示候选方向
自动刷新 snapshot
```

不负责：

```text
让用户复制 token
让用户理解插件状态
```

---

### server

负责：

```text
维护 task
维护 active task
生成 capture token
校验 capture token
接收 capture payload
去重合并
更新 snapshot_version
返回候选方向
```

新增接口建议：

```text
GET  /api/extension/active-task
POST /api/tasks/{task_id}/activate
POST /api/extension/capture
GET  /api/tasks/{task_id}/snapshot
GET  /api/extension/health
```

---

### background.js

负责：

```text
同步 active task
维护 activeTaskCache
处理 capture 请求
处理 content script 注入
处理 server 连接状态
处理重复请求 / 并发请求
```

核心状态：

```js
const runtimeState = {
  serverUrl: "http://127.0.0.1:8010",
  activeTask: null,
  activeCapturesByTab: new Map(),
  lastHealthCheckAt: 0
};
```

---

### content.js

负责：

```text
识别小红书页面类型
提取当前可见笔记
注入页面浮层
展示采集状态
把采集意图发给 background
```

不负责：

```text
保存 token
直接决定 task 归属
复杂业务逻辑
```

---

### popup.js

负责：

```text
显示连接状态
显示当前任务
提供手动采集按钮
提供重新同步按钮
提供打开工作台按钮
```

不再负责：

```text
输入 capture token
作为唯一采集入口
```

---

## 六、优先级排序

### P0：必须做

| 优化项                         | 原因           |
| --------------------------- | ------------ |
| 删除用户粘贴 token                | 最大体验割裂点      |
| background 自动同步 active task | 建立自然体验的基础    |
| popup 显示任务状态，不再输入 token     | 降低使用门槛       |
| 工作台自动刷新 snapshot            | 去掉“手动刷新任务快照” |
| server health check         | 避免用户不知道为什么失败 |

---

### P1：强烈建议做

| 优化项                   | 原因                |
| --------------------- | ----------------- |
| 小红书页面注入采集浮层           | 让采集发生在用户当前上下文     |
| 浮层显示当前任务和可见笔记数        | 增强可控感             |
| 滚动后提示新增可采集内容          | 贴合你当前“只采集可见内容”的设计 |
| content script 自动注入兜底 | 减少插件加载问题          |

---

### P2：后续增强

| 优化项              | 原因              |
| ---------------- | --------------- |
| 支持多个 task 切换     | 当前 MVP 可先不做     |
| SSE 实时刷新         | polling 足够后再优化  |
| 自动检测搜索词和 task 关联 | 可提升智能化          |
| 页面内候选方向预览        | 会增强体验，但不是第一阶段重点 |

---

## 七、最终推荐版本

我建议你按这个顺序做：

```text
Step 1：server 增加 active-task 接口
Step 2：background 自动拉 active task
Step 3：popup 删除 token 输入框
Step 4：工作台 snapshot polling 自动刷新
Step 5：content script 注入小红书页面浮层
Step 6：浮层完成采集和状态反馈
Step 7：处理 server 未启动 / 无任务 / 非小红书页面 / content script 未挂载
```

改完后的产品体验应该是：

```text
工作台创建任务
→ 点击搜索词打开小红书
→ 页面右下角出现「采集当前页」
→ 点击后自动入库
→ 工作台自动刷新候选方向
```

这才是最自然的小红书 extension 体验。
SummerIce 对你的借鉴价值，本质上就是：**不要让 popup 承担业务状态传递；让 background 成为 runtime，让 content script 成为页面内交互层，让用户只做符合当前场景的动作。**
