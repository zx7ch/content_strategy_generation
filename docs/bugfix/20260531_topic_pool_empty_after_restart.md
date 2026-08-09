# Bug Fix: 重启 exe 后选题库为空

**日期**：2026-05-31
**文件**：`app/v2/foundation/sqlite_store.py`（新增）、`app/v2/foundation/bootstrap.py`（修改）
**影响范围**：用户关闭 exe 再重新打开后，选题库显示「还没有已认可的创作结果」

---

## 问题现象

用户完成一次创作工作流，选题库显示正常。关闭 exe 后再次打开，选题库变为空白，之前的数据全部消失。

---

## 根本原因

本地 exe 使用 `InMemoryMasterDataStore` 存储品牌数据，**每次重启都重新创建，所有数据丢失**。

重启后的连锁反应：

```
exe 重启
  → InMemoryMasterDataStore 重新初始化（空）
  → _ensure_default_demo_data 重新 seed "轻量户外" demo brand
      → service.create_brand() 生成全新随机 UUID（每次不同）
  → 前端 BrandProvider 从 localStorage 读到旧 brand_id
      → 旧 brand_id 不在新 brands 列表 → fallback 到新 demo brand UUID
  → 选题库页面用新 UUID 请求 /publish-candidates?brand_id=<new_uuid>
  → PUBLISH_CANDIDATE artifacts 里存的是旧 UUID → 0 条匹配 → 空
```

---

## 修复方案

### 1. 新增 `SQLiteMasterDataStore`

新文件 `app/v2/foundation/sqlite_store.py`：将 workspaces / brands / channels / policies / state_snapshots 五类数据持久化到 `xhs_agent.db`，实现与 `InMemoryMasterDataStore` 相同的 `MasterDataStore` 协议。

- 同步 SQLite（非 aiosqlite），与现有 protocol 接口匹配
- 使用 `ON CONFLICT(id) DO UPDATE` 实现幂等写入
- JSON 序列化存储 dict 类型字段

### 2. 修改 `bootstrap.py`：切换 local 路径的 store

```python
# 修改前
store = InMemoryMasterDataStore()

# 修改后
if config.SQLITE_DB_PATH.strip() and config.SQLITE_DB_PATH.strip() != ":memory:":
    store = SQLiteMasterDataStore(config.SQLITE_DB_PATH)
```

### 3. 新增启动时孤儿 brand 协调

历史数据（切换前在 InMemoryMasterDataStore 里创建的品牌）的 `brand_id` 从未写入 SQLite，切换后变为孤儿引用。

`_reconcile_orphaned_brands`：启动时扫描 `workflow_artifacts` 表中所有 `publish_candidate` 记录引用的 `brand_id`，对 store 里不存在的 brand_id 自动补建最小品牌记录，使历史数据继续可查询。

```python
def _reconcile_orphaned_brands(service, db_path):
    # 扫描 publish_candidate 里所有 brand_id
    # 对 store 里不存在的：save_brand(BrandRecord(id=brand_id, name="已完成创作品牌", ...))
```

---

## 验证结果

| 验证项 | 结果 |
|---|---|
| 同一 DB 两次初始化，demo brand UUID 一致 | ✅ |
| 孤儿 brand_id 启动后自动补建 | ✅ |
| `publish_candidate` artifacts 全部匹配到 brand | ✅ |
| `GET /publish-candidates?brand_id=...` 返回 15 条 | ✅ |
| exe DB 同样处理后数据一致 | ✅ |
