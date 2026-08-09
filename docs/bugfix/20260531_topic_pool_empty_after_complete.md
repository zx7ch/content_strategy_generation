# Bug Fix: 点击「已完成」后选题库为空

**日期**：2026-05-31
**文件**：`app/services/workflow_artifact_policy.py`
**影响范围**：Creator 工作流完成后，选题库始终显示「还没有已认可的创作结果」

---

## 问题现象

用户在 Creator 页面完成一次完整的内容策略生成工作流（搜索 → 策略 → 生成笔记），点击「已完成」按钮后，跳转到选题库页面显示空白。

数据库里确认有数据：`xhs_agent.db` 的 `workflow_artifacts` 表中存在 `final_result` 和 `generated_note` 类型的 artifact，状态均为 `created`。

---

## 数据流分析

点击「已完成」触发 `POST /threads/{thread_id}/complete`，核心路径如下：

```
complete_thread endpoint
  → _load_workflow_result(thread, publishable_only=True)
      → WorkflowArtifactVersionPolicy.safe_materialize_run_artifacts(run_id)
      → policy.select_publishable_notes(artifacts)          ← bug 在这里
  → 若 notes 非空 → _ensure_publish_candidate_artifacts()  ← 从未被执行
      → 写入 PUBLISH_CANDIDATE artifact 到 xhs_agent.db
  → 若 notes 为空 → count = 0，不写任何数据
```

选题库读取路径：

```
GET /publish-candidates?brand_id=...
  → _list_publish_candidate_artifacts()
      → 读 workflow_artifacts WHERE artifact_type = 'publish_candidate'
```

---

## 根本原因

`_notes_from_final_result` 方法存在两个 bug，导致从 `final_result` artifact 中提取不出任何笔记。

### Bug 1：payload key 名不匹配

`final_result` 的 `payload_json` 实际使用了两种格式，代码只处理了其中一种：

| 实际存储的 key | 代码是否处理 |
|---|---|
| `notes` | ✅ |
| `generated_notes` | ✅ |
| `artifact_refs` | ❌ 缺失 |

`step_executors.py` 在写 `final_result` 时用的是 `artifact_refs`，但 `_notes_from_final_result` 根本找不到这个 key，`candidates` 为空列表，直接返回 `[]`。

### Bug 2：artifact 引用未解析

即使 `generated_notes` 存在，其中的每条 item 也只是一个引用对象：

```json
{"artifact_id": "artifact_xxx", "run_id": "run_xxx"}
```

而非内联的笔记内容。代码把这个引用 dict 直接传给 `note_from_payload`，该函数找不到 `title`/`content` 字段，返回 `None`，所有笔记全部丢失。

---

## 修复方案

**修改文件**：`app/services/workflow_artifact_policy.py`

### 修复 1：新增 `artifact_refs` key 支持

```python
# 修复前
candidates = payload.get("notes") or payload.get("generated_notes") or []

# 修复后
candidates = payload.get("notes") or payload.get("generated_notes") or payload.get("artifact_refs") or []
```

### 修复 2：通过 artifacts_by_id 解析引用

在 `select_publishable_notes` 中构建 id → artifact 的查找表，并传给 `_notes_from_final_result`：

```python
def select_publishable_notes(self, artifacts):
    artifacts_by_id = {a["artifact_id"]: a for a in artifacts}
    # ...
    return self._notes_from_final_result(
        payload,
        proposal_by_id=proposal_by_id,
        artifacts_by_id=artifacts_by_id,   # 新增
    )
```

在 `_notes_from_final_result` 中，若 item 是引用（有 `artifact_id` 但无内联内容），从 `artifacts_by_id` 查出真实 artifact 的 `materialized_payload_json`：

```python
artifact_id = item.get("artifact_id")
if artifact_id and artifacts_by_id and artifact_id in artifacts_by_id:
    resolved = artifacts_by_id[artifact_id]
    nested_payload = resolved.get("materialized_payload_json") or resolved.get("payload_json") or item
elif isinstance(item.get("payload_json"), dict):
    nested_payload = item["payload_json"]
else:
    nested_payload = item
```

---

## 验证结果

| 验证项 | 修复前 | 修复后 |
|---|---|---|
| `select_publishable_notes` 返回笔记数（source DB，15 个 generated_note） | 0 | 16 |
| `select_publishable_notes` 返回笔记数（exe DB，15 个 generated_note） | 0 | 15 |
| `GET /publish-candidates?brand_id=...` 返回条数 | 0 | 15 |

---

## 临时数据修复（已存量数据的补救）

由于 bug 导致历史运行的 `PUBLISH_CANDIDATE` artifact 从未被写入，对已完成的 run 执行了手动回填：

```python
# 直接调用 WorkflowStore.create_artifact 写入 PUBLISH_CANDIDATE
# 针对 exe DB (dist/xhs-runtime/data/xhs_agent.db) 的 run_6740eb...
# 针对 source DB (data/xhs_agent.db) 的 run_1857f2...
```

回填后选题库 API 立即返回正确数据，无需重启 runtime 或重建 exe。
