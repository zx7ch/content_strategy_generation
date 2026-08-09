# Bug Fix: exe 启动报 sqlite3.OperationalError: unable to open database file

**日期**：2026-05-31
**文件**：`runtime_main.py`（修改）、`app/memory/thread_store.py`（修改）
**影响范围**：将数据目录迁移到 Application Support 后，exe 启动即崩溃

---

## 问题现象

重新打包后启动 exe，立即报错退出：

```
ERROR: sqlite3.OperationalError: unable to open database file
  File "app/memory/thread_store.py", line 42, in connect
      self._conn = await aiosqlite.connect(self.db_path)
```

---

## 根本原因

将数据目录从 `dist/xhs-runtime/data/` 迁移到 `~/Library/Application Support/xhs-growth-agent/` 时，只重定向了部分数据库路径，遗漏了 `ThreadStore` 的 `creator_threads.db`。

| 数据库 | 迁移后路径 | 是否重定向 |
|---|---|---|
| `xhs_agent.db` | Application Support | ✅ 已设置 `SQLITE_DB_PATH` |
| `chroma/` | Application Support | ✅ 已设置 `CHROMA_PERSIST_DIR` |
| `creator_threads.db` | **仍为 `./data/creator_threads.db`（相对路径）** | ❌ 遗漏 |

`ThreadStore.__init__` 的默认路径是硬编码的 `./data/creator_threads.db`，相对于 exe 所在目录。PyInstaller 每次重建时会清空整个 `dist/xhs-runtime/` 目录（包括 `data/`），导致该父目录不存在，aiosqlite 尝试打开时失败。

---

## 修复方案

**`app/memory/thread_store.py`** — 让默认路径读取环境变量：

```python
# 修复前
def __init__(self, db_path: str = "./data/creator_threads.db"):

# 修复后
import os
def __init__(self, db_path: str = os.environ.get("CREATOR_THREADS_DB_PATH", "./data/creator_threads.db")):
```

**`runtime_main.py`** — 补上 `CREATOR_THREADS_DB_PATH` 的重定向：

```python
os.environ.setdefault("CREATOR_THREADS_DB_PATH", os.path.join(_data_home, "creator_threads.db"))
```

---

## 背景：为什么要迁移数据目录

PyInstaller 打包时会先完整删除 `dist/xhs-runtime/` 目录，导致之前存放在 `dist/xhs-runtime/data/` 中的用户数据（对话记录、选题库）每次升级都被清空。

迁移到 `~/Library/Application Support/xhs-growth-agent/` 后，数据独立于 exe 包，升级不影响历史数据。
