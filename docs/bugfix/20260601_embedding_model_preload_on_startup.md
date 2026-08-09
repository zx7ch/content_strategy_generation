# Embedding 模型改为 Runtime 启动时预加载

**日期**：2026-06-01
**影响范围**：模型加载时机、用户首次使用体验
**涉及文件**：`app/api/routes/router.py`（修改）、`app/main.py`（修改）

---

## 背景

### 模型不在 exe bundle 里

排查后确认：`BAAI/bge-base-zh-v1.5`（~780 MB）**不在**打包产物中。bundle 的 722MB 由 torch（289MB）、scipy（60MB）、chromadb_rust（49MB）等库文件构成，无模型权重文件（`.safetensors`）。

`scripts/build_runtime.sh` 里的"预下载模型"步骤只是在开发者机器上缓存，供本地测试用，并不影响分发给用户的 zip 内容。文档中"模型已打进 exe"的描述有误。

### 原有加载时机

```
用户启动 Runtime → 前端打开 → 前端调用 POST /runtime/prewarm → 开始下载/加载模型
```

用户打开前端前，模型完全未被触碰。首次任务执行时若模型还未加载完毕（下载中），任务会等待，体验差。

---

## 问题

模型下载/加载是高延迟操作（首次下载 ~780MB，加载到内存也需数秒），应在服务启动时就并行开始，而不是等用户操作触发。

---

## 修复

### `app/api/routes/router.py` — 提取可复用函数

将原 `prewarm_runtime` 端点内的任务创建逻辑提取为独立函数，供 lifespan 和 HTTP 端点共用：

```python
def schedule_embedding_prewarm() -> None:
    """Start embedding model preload as a background task.

    Safe to call multiple times — no-ops if already running or ready.
    Must be called from inside a running event loop (e.g. app lifespan).
    """
    global _embedding_prewarm_task
    if _embedding_prewarm_task is None or _embedding_prewarm_task.done():
        if _embedding_prewarm_status.get("status") != "ready":
            _embedding_prewarm_task = asyncio.create_task(_run_embedding_prewarm())
```

`POST /runtime/prewarm` 端点改为调用此函数，消除重复逻辑。

### `app/main.py` — lifespan 启动时立即触发

```python
from app.api.routes.router import app, schedule_embedding_prewarm

@asynccontextmanager
async def _worker_lifespan(application):
    # ... 其他服务初始化 ...

    # Start embedding model preload immediately in background.
    # Model downloads (~780 MB) or loads from cache without blocking startup.
    schedule_embedding_prewarm()

    yield
    # ... 清理 ...
```

---

## 新的启动时序

```
用户启动 Runtime
    ↓
uvicorn 启动，_worker_lifespan 执行
    ├── DB 连接、job worker、v2 服务初始化（几百毫秒）
    ├── schedule_embedding_prewarm()  ← 后台任务，不阻塞
    └── yield → 服务就绪

        ↓（并行进行）
    后台下载/加载模型
      ├── 有缓存 → 数秒内 status: ready
      └── 无缓存 → 下载 ~780MB，几分钟后 ready

用户打开前端时，模型已在加载中（而不是还没开始）
```

---

## 幂等保证

`schedule_embedding_prewarm()` 内有双重检查：
1. task 为 None 或已完成才新建
2. status 已为 `ready` 则跳过

因此前端仍可调用 `POST /runtime/prewarm`，不会触发重复下载。

---

## 后续修复（2026-06-01 同日完成）

### 1. 模型缓存迁移到数据目录

**`runtime_main.py`** 新增一行，在所有 HuggingFace 下载发生前设置根目录：

```python
os.environ.setdefault("HF_HOME", os.path.join(_data_home, "hf_cache"))
```

效果：
- 模型存入 `~/Library/Application Support/xhs-growth-agent/hf_cache/`（macOS）或 `%APPDATA%\xhs-growth-agent\hf_cache\`（Windows）
- 与数据库在同一数据目录，升级 exe 不影响已下载的模型
- 后续新增任意 HuggingFace 模型（reranker、本地 LLM 等）自动存入同一位置，无需额外配置

### 2. ChromaDB 模型指纹

**`app/services/rag_service.py`** 改造 `_get_collection()` 方法：

- **创建时**：把 `embedding_model` 写入 collection metadata
  ```python
  metadata={"hnsw:space": "cosine", "embedding_model": self.embedding_model}
  ```
- **已有 collection**：检查 metadata 里记录的模型和当前配置是否一致
  - 不一致 → `logger.warning`，明确提示需要重建索引，不静默返回错误结果
  - 无指纹（旧 collection）→ 自动 backfill，`collection.modify()` 补写当前模型名
- 对存量数据无破坏：只读取和写入 metadata，不影响已有向量

换模型时用户看到的日志：
```
WARNING: Embedding model mismatch: collection 'xhs_documents' was built with
'BAAI/bge-base-zh-v1.5' but current config is 'BAAI/bge-large-zh-v1.5'.
Query results will be incorrect until you re-index.
To re-index: delete <CHROMA_PERSIST_DIR> and restart the runtime.
```
