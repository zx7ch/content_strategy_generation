# Development Guide: phase_3_workbench_ui — 工作台 UI 改动

> Generated: 2026-05-10
> Architect: implementation skill (dev-helper Stage 2)
> Status: Ready for development
> Source: experiments/xhs_extension_mvp/improvements.md §「Phase 3：工作台 UI 改动」

## 1. Task Context

### Scope Boundary

- **Task ID**: phase_3_workbench_ui
- **Task Name**: 工作台 UI 改动 — 自动采集按钮 + 进度状态行
- **Phase**: 2026/05/09 Improvement Phase 3
- **Dependencies**: Phase 2 ✅ 完成 — 3 个 endpoint 全部可用：`GET /api/scraper/readiness`、`POST /api/tasks/{id}/auto-scrape`、`GET /api/tasks/{id}/scrape-status`
- **Task Goal**: 把 Phase 2 的服务端接口接到现有工作台 UI，让用户能「点按钮 → 看进度 → 候选方向自动刷新」。

### In Scope

- `web/index.html`: 添加 scraper banner（hidden 默认）+ 两条 CSS 规则（`banner--warn`、`status--inline`）
- `web/app.js`: 扩展 state、新增登录态检查、采集触发、状态轮询、渲染逻辑；改造 `renderQueryCard` 添加自动采集按钮 + 状态行；改造 `renderQueryModule` 绑定点击事件

### Out Of Scope

- ❌ 服务端任何改动（Phase 2 已完成）
- ❌ Chrome Extension 路径（保持不变）
- ❌ 新建任何后端 endpoint
- ❌ Phase 4 反爬调试

### Required Deliverables

| 路径 | 类型 | 用途 |
|---|---|---|
| `experiments/xhs_extension_mvp/web/index.html` | MODIFY | banner HTML + CSS |
| `experiments/xhs_extension_mvp/web/app.js` | MODIFY | scraper state + 7 new functions + renderQueryCard/Module 扩展 |

**无新测试文件**（spec 规定只有手测 + JS syntax 检查）

### Acceptance Criteria

| AC | 描述 | 验证 |
|---|---|---|
| AC1 | 未登录态有 banner（指向 README）；已登录 / 未检查时 banner 隐藏 | 手测 |
| AC2 | 状态行实时更新，相位文案与服务端 phase 一一对应 | 手测 |
| AC3 | 采集完成后按钮恢复，候选方向自动刷新（复用 refreshTaskFromCandidateDirections） | 手测 |
| AC4 | 改动总行数 ≤ 80（HTML + CSS + JS，允许合理超出） | `wc -l` diff |
| AC5 | 既有 Chrome Extension 手动采集路径不受影响 | `node -c web/app.js` + 手测 |
| AC6 | 采集中再次点按钮无效（按钮 disabled） | 手测 |

### Phase 2 Carry-Forward Residuals（无需本任务处理）

- #1 runtime 崩溃重启 → Phase 4
- #2 反爬参数 → Phase 4
- #5 pre-existing test failures → 独立 issue
- readiness 登录探针覆盖 → Phase 4

### Contract Inventory

**Upstream (Phase 2 提供)**:
- `GET /api/scraper/readiness` → `{profile_exists, logged_in, last_checked_at, detail}`
- `POST /api/tasks/{id}/auto-scrape` body: `{keyword, scroll_count}` → 202 `{task_id, accepted, started_at}` / 409 busy
- `GET /api/tasks/{id}/scrape-status` → `{task_id, keyword, phase, scroll_index, scroll_total, items_count, error_message, started_at, finished_at}`

**Downstream (Phase 3 向 Phase 4 提供)**:
- `formatScrapePhase` 文案稳定（Phase 4 手测依赖）
- banner 登录引导文案稳定

---

## 2. Architecture Context

### System Position

```
工作台 (web/app.js)
  ├── state.scraper.{readiness, activeQueryId, activeScrapeStatus, pollTimerId}
  ├── initScraperReadiness()    → GET /api/scraper/readiness
  ├── triggerAutoScrape()       → POST /api/tasks/{id}/auto-scrape
  ├── pollScrapeStatus()        → GET /api/tasks/{id}/scrape-status  [1s 轮询]
  ├── renderScraperBanner()     → DOM #scraper-banner
  ├── renderQueryCard()         → [修改] 加 scrape 按钮 + status div
  └── renderQueryModule()       → [修改] 绑 data-auto-scrape 点击事件
```

### Constraints

- 单进程单 registry → 一次只能有一个 activeQueryId
- 状态轮询 1s 间隔；scrape 阶段结束（done/error/login_required）后停止
- Banner 在 readiness 未返回时始终隐藏（不闪烁）
- 不引入任何外部依赖或构建步骤

---

## 3. Technical Design

### 3.1 index.html 变更

**CSS 追加**（`</style>` 前）:
```css
.banner {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  font-size: 13px;
}
.banner--warn { background: #fff8e1; border-bottom: 1px solid #ffe082; color: #5d4037; }
.status--inline { padding: 4px 10px; border-radius: 10px; font-size: 12px; background: var(--accent-soft); color: var(--accent-strong); }
```

**HTML 追加**（`<main>` 开头，`<header>` 之前）:
```html
<div id="scraper-banner" class="banner banner--warn" hidden>
  <span>⚠️ 采集器未登录小红书，请按 README 完成登录后刷新本页。</span>
  <a href="./README.md" target="_blank">查看 README</a>
</div>
```

### 3.2 app.js 变更详解

**state 扩展**（`state` 对象内末尾追加）:
```js
  scraper: {
    readiness: null,
    activeQueryId: null,
    activeScrapeStatus: null,
    pollTimerId: null,
  },
```

**元素引用**（已有元素引用块末尾追加）:
```js
const scraperBanner = document.getElementById("scraper-banner");
```

**启动调用**（`renderHotspotModule()` 后追加）:
```js
void initScraperReadiness();
```

**新增函数**:

```js
async function initScraperReadiness() {
  const resp = await fetch("/api/scraper/readiness").catch(() => null);
  if (!resp?.ok) return;
  state.scraper.readiness = await resp.json();
  renderScraperBanner();
}

function renderScraperBanner() {
  const r = state.scraper.readiness;
  scraperBanner.hidden = !r || r.logged_in;
}

async function triggerAutoScrape(queryId, queryText) {
  if (!state.taskId || state.scraper.activeQueryId) return;
  const resp = await fetch(`/api/tasks/${state.taskId}/auto-scrape`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ keyword: queryText, scroll_count: 5 }),
  }).catch(() => null);
  if (!resp?.ok) { setQueryStatus("自动采集触发失败。"); return; }
  state.scraper.activeQueryId = queryId;
  state.scraper.activeScrapeStatus = null;
  renderQueryModule();
  startScrapeStatusPolling();
}

function startScrapeStatusPolling() {
  stopScrapeStatusPolling();
  state.scraper.pollTimerId = window.setInterval(pollScrapeStatus, 1000);
}

function stopScrapeStatusPolling() {
  if (state.scraper.pollTimerId) {
    window.clearInterval(state.scraper.pollTimerId);
    state.scraper.pollTimerId = null;
  }
}

async function pollScrapeStatus() {
  if (!state.taskId || !state.scraper.activeQueryId) return;
  const resp = await fetch(`/api/tasks/${state.taskId}/scrape-status`).catch(() => null);
  if (!resp?.ok) return;
  const status = await resp.json();
  state.scraper.activeScrapeStatus = status;
  const el = document.querySelector(`[data-scrape-status="${state.scraper.activeQueryId}"]`);
  if (el) { el.hidden = false; el.textContent = formatScrapePhase(status); }
  if (["done", "error", "login_required"].includes(status.phase)) {
    stopScrapeStatusPolling();
    state.scraper.activeQueryId = null;
    renderQueryModule();
    if (status.phase === "done") {
      void refreshTaskFromCandidateDirections();
      setTaskStatus("自动采集完成，候选方向已更新。");
    }
  }
}

function formatScrapePhase(s) {
  if (!s) return "正在启动...";
  if (s.phase === "scrolling") return `滚动 ${s.scroll_index}/${s.scroll_total}，已采集 ${s.items_count} 条`;
  if (s.phase === "done") return `✅ 采集完成，共 ${s.items_count} 条`;
  if (s.phase === "error") return `❌ 失败：${s.error_message}`;
  if (s.phase === "login_required") return "⚠️ 采集器未登录，请完成登录";
  if (s.phase === "navigating") return "正在打开小红书搜索页...";
  return "正在启动采集器...";
}
```

**`renderQueryCard` 修改**（在 button-row 末尾加采集按钮，在 `</article>` 前加 status div）:
```js
// 加在 renderQueryCard 顶部
const isBusy = !!state.scraper.activeQueryId;
const isActive = state.scraper.activeQueryId === query.query_id;
const statusText = isActive ? escapeHtml(formatScrapePhase(state.scraper.activeScrapeStatus)) : "";
// 在 button-row 内加：
`<button class="secondary" type="button" data-auto-scrape="${escapeAttribute(query.query_id)}" data-query-text="${escapeAttribute(query.query_text)}" ${!state.taskId || isBusy ? "disabled" : ""}>🔄 自动采集</button>`
// 在 <strong>query_text</strong> 后加：
`<div class="status--inline" data-scrape-status="${escapeAttribute(query.query_id)}" ${isActive ? "" : "hidden"}>${statusText}</div>`
```

**`renderQueryModule` 修改**（在现有 delete 按钮绑定后追加）:
```js
[queryAutoList, queryCustomList].forEach((list) => {
  list.querySelectorAll("[data-auto-scrape]").forEach((btn) => {
    btn.addEventListener("click", () =>
      void triggerAutoScrape(btn.getAttribute("data-auto-scrape"), btn.getAttribute("data-query-text"))
    );
  });
});
```

### 3.3 Logic Flow

```
页面加载
  → initScraperReadiness() → renderScraperBanner()
       ├─ logged_in=true  → banner hidden
       └─ logged_in=false → banner visible

点击「自动采集」
  → triggerAutoScrape(queryId, queryText)
       ├─ taskId 未设置 / 已有 active → noop
       └─ POST /auto-scrape 202
            → state.scraper.activeQueryId = queryId
            → renderQueryModule()  [按钮全部变 disabled]
            → startScrapeStatusPolling()
                 → pollScrapeStatus() [每 1s]
                      → GET /scrape-status
                      → 更新 [data-scrape-status] 文本
                      → phase ∈ {done, error, login_required}?
                           → stopScrapeStatusPolling()
                           → activeQueryId = null
                           → renderQueryModule()  [恢复按钮]
                           → phase=done: refreshTaskFromCandidateDirections()
```

---

## 4. Testing Strategy

**手测流程**（无自动化测试）:

1. `node -c experiments/xhs_extension_mvp/web/app.js` → 无语法错误
2. 启动服务 `uvicorn ... --reload`
3. 未登录态：刷新工作台 → banner 出现
4. 创建任务 → 拓展词卡片出现「🔄 自动采集」按钮
5. 点击采集 → 状态行渐变更新（launching → scrolling 1/5 → done）
6. 采集中再点 → 无效（disabled）
7. 完成后 → 候选方向刷新，按钮恢复

---

## 5. Implementation Checklist

1. [ ] index.html：添加 CSS（banner, banner--warn, status--inline）+ banner HTML
2. [ ] app.js state：追加 `scraper` 字段
3. [ ] app.js refs：追加 `scraperBanner`
4. [ ] app.js startup：追加 `void initScraperReadiness()`
5. [ ] app.js：实现 7 个新函数
6. [ ] app.js：修改 `renderQueryCard` 加按钮 + status div
7. [ ] app.js：修改 `renderQueryModule` 绑 auto-scrape 点击事件
8. [ ] `node -c web/app.js` 验证无语法错误
9. [ ] 手动在浏览器验证 golden path

## 6. Spec Sync

- Stage 5 由 progress-tracker 在 improvements.md Phase 3「✅ 完成进度」回填
- AC4（≤80 行）若超出记录实际行数并说明
- 任何未关闭项写入「⚠️ 遗留问题」
