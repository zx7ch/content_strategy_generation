下面给你一个**准确可落地的 improvement plan**。目标不是“功能更多”，而是让用户感觉：

> 我在工作台里选题、打开小红书、在页面里自然采集，系统自动把结果带回来。
> 不需要理解 token，不需要反复打开 popup，不需要手动刷新。

你当前 MVP 的链路是：创建任务后生成 token，用户去拓展搜索打开小红书，再打开 popup、确认 Server URL、粘贴 Capture Token、点击 Capture Current Page，最后回工作台点击刷新任务快照。这个流程在 README 里已经明确写出来了，说明当前最大问题就是**用户需要在工作台、popup、小红书页面之间手动搬运状态**。

---

# 一、当前体验问题拆解

## 1. token 暴露给用户，交互不自然

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

## 2. popup 不应该是主操作入口

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

## 3. 工作台刷新不应该由用户触发

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

## 4. content script 生命周期问题需要产品化处理

README 里提到，插件重新加载后，当前小红书页面里的 content script 可能还是旧的，需要手动刷新页面。也提到 popup 可能出现 `Could not establish connection. Receiving end does not exist.`，当前做法是 popup 自动注入 content.js 再重试。

这说明你已经碰到了 extension 常见问题：**content script 不稳定 / 未挂载 / 页面未刷新**。

这部分也需要借鉴 SummerIce：
不要让用户理解 content script 是否挂载，而是让 background 做 runtime 层的兜底。

---

# 二、借鉴 SummerIce 的关键点

你不需要照搬 SummerIce 的总结功能，要借鉴的是它的 extension 架构思想。

## 借鉴点 1：background 作为任务编排中心

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

## 借鉴点 2：requestId / activeRequests / AbortController

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

## 借鉴点 3：popup 降级为状态面板

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

# 三、目标用户路径

最终你要把用户路径改成这样：

## 新流程

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

# 四、Improvement Plan

## Phase 1：取消手动 token，建立 Active Task Runtime

这是第一优先级。
目标：**用户创建任务后，插件自动知道当前任务，不再粘贴 token。**

### 1.1 后端新增 active task 接口

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

### 1.2 插件 background 自动同步 active task

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

### 1.3 popup 删除 Capture Token 输入框

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

### Phase 1 验收标准

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

## Phase 2：把主采集入口从 popup 移到小红书页面

这是体验提升最大的一步。
目标：**用户在小红书页面里完成采集，而不是打开 popup。**

你的 README 当前限制是“只采集当前页面可见内容，不自动滚动、不翻页、不模拟点击”。
所以页面浮层不会破坏 MVP 边界，它只是把按钮放到更自然的位置。

---

### 2.1 content script 注入采集浮层

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

### 2.2 浮层显示当前 task

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

### 2.3 点击浮层按钮触发采集

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

### 2.4 滚动后提示“有新增可见内容”

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

### Phase 2 验收标准

用户在小红书页面看到：

```text
XHS 采集助手
当前任务：轻量徒步防晒衣
当前页可见笔记：18 条
[采集当前页]
```

用户不需要打开 popup。

---

## Phase 3：工作台打开搜索页时自动绑定任务上下文

目标：**用户从工作台点击搜索词打开小红书时，页面天然知道自己属于哪个 task。**

---

### 3.1 拓展搜索按钮改名

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

### 3.2 简单实现：URL 参数绑定

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

### 3.3 更推荐实现：server active task + query context

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

### Phase 3 验收标准

用户点击工作台里的搜索词后：

```text
小红书页面自动出现浮层
浮层显示正确 task
采集结果自动归到正确任务
```

---

## Phase 4：工作台自动刷新候选结果

目标：**采集完成后，用户不用点“刷新任务快照”。**

---

### 4.1 server snapshot 增加 version

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

### 4.2 web 前端 polling

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

### 4.3 页面提示采集状态

工作台顶部显示：

```text
当前任务：轻量徒步防晒衣
已采集：36 条
候选方向：8 个
最近更新：12:03:12
```

这样用户能感知系统在自动更新。

---

### Phase 4 验收标准

用户完成采集后：

```text
工作台候选方向自动变化
不需要点击刷新任务快照
```

---

## Phase 5：完善异常兜底，让体验像产品而不是 demo

---

### 5.1 server 未启动

页面浮层显示：

```text
未连接本地服务
请先启动：uvicorn experiments.xhs_extension_mvp.server.app:app --host 127.0.0.1 --port 8010
[重试连接]
```

popup 显示同样状态。

---

### 5.2 没有 active task

显示：

```text
未检测到当前任务
请先回工作台创建任务
[打开工作台]
```

不要显示 token 错误。

---

### 5.3 当前页面不是小红书

popup 显示：

```text
当前页面不支持采集
请打开小红书搜索页或笔记详情页
```

---

### 5.4 content script 未挂载

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

# 五、最终模块职责

## web 工作台

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

## server

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

## background.js

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

## content.js

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

## popup.js

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

# 六、优先级排序

## P0：必须做

| 优化项                         | 原因           |
| --------------------------- | ------------ |
| 删除用户粘贴 token                | 最大体验割裂点      |
| background 自动同步 active task | 建立自然体验的基础    |
| popup 显示任务状态，不再输入 token     | 降低使用门槛       |
| 工作台自动刷新 snapshot            | 去掉“手动刷新任务快照” |
| server health check         | 避免用户不知道为什么失败 |

---

## P1：强烈建议做

| 优化项                   | 原因                |
| --------------------- | ----------------- |
| 小红书页面注入采集浮层           | 让采集发生在用户当前上下文     |
| 浮层显示当前任务和可见笔记数        | 增强可控感             |
| 滚动后提示新增可采集内容          | 贴合你当前“只采集可见内容”的设计 |
| content script 自动注入兜底 | 减少插件加载问题          |

---

## P2：后续增强

| 优化项              | 原因              |
| ---------------- | --------------- |
| 支持多个 task 切换     | 当前 MVP 可先不做     |
| SSE 实时刷新         | polling 足够后再优化  |
| 自动检测搜索词和 task 关联 | 可提升智能化          |
| 页面内候选方向预览        | 会增强体验，但不是第一阶段重点 |

---

# 七、最终推荐版本

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
