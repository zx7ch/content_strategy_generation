# Development Guide: phase_1_scraper — scraper.py 核心模块

> Generated: 2026-05-10
> Architect: implementation skill (dev-helper Stage 2)
> Status: Draft → Ready for development
> Source: experiments/xhs_extension_mvp/improvements.md §「2026/05/09 Improvement」 + 「开发前置决策」 + Phase 1

## 1. Task Context

### Scope Boundary

- **Task ID**: phase_1_scraper
- **Task Name**: scraper.py 核心模块（独立可跑）
- **Phase**: 2026/05/09 Improvement Phase 1
- **Dependencies**: 无上游阻塞；前置决策 1-6 已确认
- **Task Goal**: 实现一个纯 Python 模块，给定 keyword 能调起真实 Chrome、人类化滚动 5 次、提取笔记并返回 `list[CaptureItemIn]`。**不接 server，不接 UI**。CLI 入口可单跑验证。

### In Scope

- 新增 `experiments/xhs_extension_mvp/server/scraper.py`（核心采集协程）
- 新增 `experiments/xhs_extension_mvp/server/scraper_runtime.py`（keep-alive 浏览器单例，前置决策 4）
- 新增 `experiments/xhs_extension_mvp/server/scraper_state.py`（全局采集状态注册）
- 新增 `experiments/xhs_extension_mvp/server/scraper_login.py`（`is_logged_in` 检测，前置决策 1+5）
- 新增 `experiments/xhs_extension_mvp/server/scraper_models.py`（`ScrapePhase`、`ScrapeProgress`、`ScrapeState` 数据结构）
- 修改 `experiments/xhs_extension_mvp/extension/src/content.js`（把 `extractSearchResultItems` 等关键函数从 IIFE 暴露到 `globalThis`，前置决策 2）
- 新增 CLI 入口（同文件内 `if __name__ == "__main__"` 或 `python -m ... scraper`）
- 新增依赖：`playwright>=1.40` 写入 `pyproject.toml`
- 修改 `.gitignore`：增加 `data/chrome-profile/` 与 `data/scraper-debug/`
- 单测覆盖（详见 §4）

### Out Of Scope

- ❌ FastAPI endpoint（属于 Phase 2）
- ❌ 工作台 UI 改动（Phase 3）
- ❌ 反爬调参（Phase 4，需要真实环境跑大量数据后再调）
- ❌ 主动驱动登录的代码路径（前置决策 5 明确排除）
- ❌ 多 worker 支持（单 worker 假设，前置决策 6）

### Required Deliverables

**Production**:

| 路径 | 类型 | 用途 |
|---|---|---|
| `experiments/xhs_extension_mvp/server/scraper.py` | NEW | `scrape_search_feed` + `extract_visible_items` + `human_scroll` + CLI |
| `experiments/xhs_extension_mvp/server/scraper_runtime.py` | NEW | `ScraperRuntime` keep-alive 浏览器单例 |
| `experiments/xhs_extension_mvp/server/scraper_state.py` | NEW | `ScrapeStateRegistry` 单例 + asyncio.Lock |
| `experiments/xhs_extension_mvp/server/scraper_login.py` | NEW | `is_logged_in(page)` |
| `experiments/xhs_extension_mvp/server/scraper_models.py` | NEW | `ScrapePhase` enum / `ScrapeProgress` / `ScrapeState` dataclass |
| `experiments/xhs_extension_mvp/extension/src/content.js` | MODIFY | 暴露 `extractSearchResultItems` 等到 `globalThis.__XHS_EXTRACTOR__` |
| `pyproject.toml` | MODIFY | 加 `playwright>=1.40` 到 `[project.optional-dependencies].dev` 或主 deps |
| `.gitignore` | MODIFY | 加 `data/chrome-profile/`、`data/scraper-debug/` |

**Tests**:

| 路径 | 类型 | 覆盖 |
|---|---|---|
| `tests/unit/test_scraper_models.py` | NEW | `ScrapePhase`、`ScrapeProgress`、`ScrapeState` 字段默认值 |
| `tests/unit/test_scraper_state.py` | NEW | `ScrapeStateRegistry` 锁 / 状态写入 / 释放 |
| `tests/unit/test_scraper_login.py` | NEW | `is_logged_in` 三种 DOM 场景（含 fake page） |
| `tests/unit/test_scraper_human_scroll.py` | NEW | 滚动距离方差 / 总耗时 / 鼠标分步 |
| `tests/unit/test_scraper_orchestration.py` | NEW | 主流程 mock playwright，测 LOGIN_REQUIRED / 5 次滚动 / 异常 |
| `tests/unit/test_scraper_extract_visible_items.py` | NEW | mock page.evaluate 返回固定数据，验证转换为 `CaptureItemIn` |
| `tests/fixtures/xhs_search_page_sample.html` | NEW | 真实 XHS 搜索页的 DOM 片段（用于校验 selector 假设） |

**Spec/Docs**:
- 修改 `experiments/xhs_extension_mvp/improvements.md` Phase 1「✅ 完成进度」与「⚠️ 遗留问题」（在 Stage 5 由 progress-tracker 完成）

### Acceptance Criteria

| AC ID | 描述 | 验证方式 |
|---|---|---|
| AC1 | `playwright install chromium` 在本机跑通 | 手测；CI/Stage 4 不强制 |
| AC2 | CLI 可独立运行：`python -m experiments.xhs_extension_mvp.server.scraper "敏感肌护肤"` 输出进度 + items 数 | 手测 |
| AC3 | Chrome 窗口启动位置在 `(-2000, -2000)` 屏幕外 | 单测断言 launch args 含 `--window-position=-2000,-2000` |
| AC4 | 触发风控 / 未登录返回 `ScrapePhase.LOGIN_REQUIRED`，不抛异常 | 单测 mock `is_logged_in` 返回 False |
| AC5 | `human_scroll` 10 次调用，滚动距离方差 > 0；单次总耗时落在 [1.8s, 4.5s]（含阅读停顿） | 单测断言 |
| AC6 | `extract_visible_items` 返回 `list[CaptureItemIn]`，字段非空 | 单测 mock page.evaluate |
| AC7 | `ScrapeStateRegistry` 第二次 `try_acquire` 返回 None | 单测断言 |
| AC8 | 浏览器异常时 `ensure_started()` 自动重启 | 单测 mock `_context.is_closed()` 返回 True |
| AC9 | content.js 修改后 extension 原有功能不受影响（`extractSearchResultItems` 同时挂在 IIFE 内部和 globalThis） | 手测 + 既有 MVP 单测仍通过 |
| AC10 | 所有单测全绿 | Stage 4 验证 |

### Residual Obligations

improvements.md 当前 Phase 1 完成进度 / 遗留问题为「尚未开始」占位，无前置 OPEN 残留。

**新发现的不完成项必须回写**到 Phase 1「⚠️ 遗留问题」（由 progress-tracker 处理）。可能产生的残留候选：
- 反爬参数调优（属于 Phase 4，但若 Phase 1 单测中观察到具体阈值不够鲁棒，需写入残留供 Phase 4 参考）
- content.js IIFE 改造可能影响其他扩展模块的正确性（手测过一遍既有 MVP 流程）

### Contract Inventory

- **Upstream contracts**: 无（Phase 1 是独立模块）
- **Downstream contracts**:
  - `scrape_search_feed` 的返回类型必须是 `list[CaptureItemIn]`（[server/models.py:98](experiments/xhs_extension_mvp/server/models.py:98) 已定义），Phase 2 endpoint 直接吃这个类型
  - `ScrapePhase` enum 必须稳定，Phase 2 暴露给 endpoint，Phase 3 给前端
  - `ScrapeProgress` 字段稳定，Phase 2 SSE / Phase 3 状态轮询直接使用
- **Compatibility risks**:
  - `content.js` 是 IIFE 包装的，refactor 时必须保留原有 IIFE 内部逻辑可用（不破坏 Chrome Extension 路径）

### Test Requirements

- **Test layer**: unit only（单测 + mock playwright；不要求集成测试或真实 XHS 测试在 Stage 4 通过）
- **Default markers**: 无；新增 `@pytest.mark.real_xhs` marker（pytest 配置里默认 skip）
- **Fixtures**: `tests/fixtures/xhs_search_page_sample.html` 提供真实 DOM 样本

---

## 2. Architecture Context

### System Position

```
┌─────────────────────────────────────────────────────────────┐
│ FastAPI App (server/app.py)                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Phase 2 endpoints (本任务不涉及)                       │  │
│  │   POST /api/tasks/:id/auto-scrape                    │  │
│  │   GET  /api/tasks/:id/scrape-status                  │  │
│  │   GET  /api/scraper/readiness                        │  │
│  └──────────────────────────────────────────────────────┘  │
│                              │                              │
│                              ▼ (in-process import)          │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ scraper.py (本任务核心)                               │  │
│  │   scrape_search_feed(keyword, scroll_count, ...)     │  │
│  │     └─> ScraperRuntime.acquire_page()                │  │
│  │     └─> is_logged_in(page) → LOGIN_REQUIRED          │  │
│  │     └─> human_scroll × scroll_count                  │  │
│  │     └─> extract_visible_items(page, keyword)         │  │
│  │     └─> ScrapeStateRegistry.update(progress)         │  │
│  └──────────────────────────────────────────────────────┘  │
│                              │                              │
│                              ▼ (lifespan singleton)         │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ ScraperRuntime (keep-alive)                           │  │
│  │   playwright.async_playwright()                       │  │
│  │   chromium.launch_persistent_context(                 │  │
│  │     user_data_dir=data/chrome-profile,                │  │
│  │     headless=False, window-position=-2000,-2000)      │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                               │
                               ▼
                    Real Chrome browser (offscreen)
                               │
                               ▼
                    xiaohongshu.com/search_result
```

### Tech Stack

- Language/runtime: Python 3.10+
- Primary libraries:
  - `playwright>=1.40`（`async_playwright`、`BrowserContext`、`Page`）
  - `pydantic>=2.5`（已有，复用 `CaptureItemIn`）
- Execution pattern: async / await
- Key behavioral constraints:
  - 单 worker 部署（决策 6）
  - keep-alive 浏览器进程（决策 4）
  - 不主动驱动登录（决策 5）

### Constraints

- 必须使用 `launch_persistent_context`，不用 `launch + storage_state`（决策 3）
- 必须复用 content.js 的 `extractSearchResultItems`，不在 Python 重写 xsec_token 提取（决策 2）
- 浏览器窗口位置 `(-2000, -2000)` 实现「无感」
- `data/chrome-profile/` 路径相对项目根

---

## 3. Technical Design

### 3.1 Module Structure

```
experiments/xhs_extension_mvp/
├── server/
│   ├── scraper.py              [NEW]  主流程 + CLI
│   ├── scraper_runtime.py      [NEW]  keep-alive 浏览器单例
│   ├── scraper_state.py        [NEW]  状态注册中心
│   ├── scraper_login.py        [NEW]  is_logged_in 检测
│   └── scraper_models.py       [NEW]  数据结构
└── extension/
    └── src/
        └── content.js          [MODIFY] 暴露提取函数到 globalThis
tests/
├── unit/
│   ├── test_scraper_models.py              [NEW]
│   ├── test_scraper_state.py               [NEW]
│   ├── test_scraper_login.py               [NEW]
│   ├── test_scraper_human_scroll.py        [NEW]
│   ├── test_scraper_orchestration.py       [NEW]
│   └── test_scraper_extract_visible_items.py [NEW]
└── fixtures/
    └── xhs_search_page_sample.html         [NEW]
pyproject.toml                  [MODIFY] 加 playwright
.gitignore                      [MODIFY] 加 chrome-profile
```

### 3.2 Class & Interface Design

**3.2.1 `scraper_models.py`**

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Awaitable, Callable, Optional


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
    phase: ScrapePhase = ScrapePhase.IDLE
    scroll_index: int = 0
    scroll_total: int = 5
    items_count: int = 0
    error_message: str = ""

    def with_phase(self, phase: ScrapePhase, **overrides) -> "ScrapeProgress":
        return ScrapeProgress(
            phase=phase,
            scroll_index=overrides.get("scroll_index", self.scroll_index),
            scroll_total=overrides.get("scroll_total", self.scroll_total),
            items_count=overrides.get("items_count", self.items_count),
            error_message=overrides.get("error_message", self.error_message),
        )


@dataclass
class ScrapeState:
    task_id: str
    keyword: str
    progress: ScrapeProgress
    started_at: datetime
    finished_at: Optional[datetime] = None


ProgressCallback = Optional[Callable[[ScrapeProgress], Awaitable[None]]]
```

**3.2.2 `scraper_state.py`**

```python
class ScrapeStateRegistry:
    """单进程内的全局采集状态字典 + 单锁。

    单 worker 假设（决策 6）：状态不持久化，进程重启后丢失。
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._states: dict[str, ScrapeState] = {}
        self._active_task_id: str | None = None

    async def try_acquire(self, *, task_id: str, keyword: str) -> ScrapeState | None:
        """成功获取锁则创建初始 state；已有任务在跑则返回 None。"""

    async def update(self, task_id: str, progress: ScrapeProgress) -> None: ...

    async def release(self, task_id: str, *, finished_at: datetime | None = None) -> None: ...

    def get(self, task_id: str) -> ScrapeState | None: ...

    def is_busy(self) -> bool: ...
```

**3.2.3 `scraper_runtime.py`**

```python
class ScraperRuntime:
    """keep-alive 浏览器单例。挂在 FastAPI app.state，跟随 lifespan 启停。"""

    def __init__(self, profile_dir: Path):
        self._profile_dir = profile_dir
        self._playwright = None
        self._context = None
        self._lock = asyncio.Lock()

    async def ensure_started(self) -> "BrowserContext":
        """幂等：已启动且未关闭则直接返回；否则启动 playwright + persistent context。"""

    async def acquire_page(self) -> "Page":
        """每次 scrape 在同一 context 上 new_page；不重启浏览器。"""

    async def shutdown(self) -> None: ...

    @property
    def is_started(self) -> bool: ...
```

启动 args（必须包含）：
```python
args=[
    "--window-position=-2000,-2000",
    "--disable-blink-features=AutomationControlled",
]
viewport={"width": 1440, "height": 900}
headless=False
```

**3.2.4 `scraper_login.py`**

```python
LOGGED_IN_SELECTOR = 'a.link-wrapper[title="我"][href^="/user/profile/"]'
LOGGED_OUT_SELECTOR = '#login-btn'

async def is_logged_in(page) -> bool:
    """登录态检测：先正向（头像链接），再反向（登录按钮）；不确定时返回 False。"""
```

**3.2.5 `scraper.py`** — 主流程

```python
SEARCH_URL_TEMPLATE = "https://www.xiaohongshu.com/search_result?keyword={kw}"
CONTENT_JS_FILE = (
    Path(__file__).resolve().parent.parent / "extension" / "src" / "content.js"
)
_EXTRACTOR_JS_BUNDLE = CONTENT_JS_FILE.read_text(encoding="utf-8")


async def scrape_search_feed(
    keyword: str,
    *,
    runtime: ScraperRuntime,
    scroll_count: int = 5,
    on_progress: ProgressCallback = None,
) -> list[CaptureItemIn]:
    """主流程：launch → navigate → login_check → scroll loop → extract → return.

    Returns:
        采集到的 CaptureItemIn 列表（去重交给上游 ingest）

    LOGIN_REQUIRED 时返回 []，并通过 on_progress 通知 phase=LOGIN_REQUIRED。
    """


async def human_scroll(page) -> None:
    """人类化滚动一轮（impl 见 §3.3）。"""


async def extract_visible_items(page, keyword: str) -> list[CaptureItemIn]:
    """注入 content.js 后调 extractSearchResultItems，转换为 CaptureItemIn。"""


# CLI 入口
async def _cli_main(keyword: str, scroll_count: int = 5) -> None:
    """开发期 smoke test：直接跑一轮，打印进度和 items 数。"""

if __name__ == "__main__":
    # python -m experiments.xhs_extension_mvp.server.scraper "敏感肌护肤"
    import sys
    keyword = sys.argv[1] if len(sys.argv) > 1 else "敏感肌护肤"
    asyncio.run(_cli_main(keyword))
```

### 3.3 Algorithm & Logic Flow

**主流程 `scrape_search_feed`**：

```
1. progress = ScrapeProgress(phase=LAUNCHING)
   await on_progress(progress)
2. context = await runtime.ensure_started()
3. page = await runtime.acquire_page()
4. progress = progress.with_phase(NAVIGATING)
   await on_progress(progress)
   await page.goto(SEARCH_URL_TEMPLATE.format(kw=quote(keyword)),
                   wait_until="domcontentloaded", timeout=20000)
5. # 登录检测
   if not await is_logged_in(page):
     progress = progress.with_phase(LOGIN_REQUIRED,
                                    error_message="未登录小红书，请按 README 完成登录")
     await on_progress(progress)
     await page.close()
     return []
6. # 滚动 + 提取
   collected: dict[str, CaptureItemIn] = {}  # by note_id 去重
   for i in range(1, scroll_count + 1):
     progress = progress.with_phase(SCROLLING, scroll_index=i)
     await on_progress(progress)
     await human_scroll(page)
     items = await extract_visible_items(page, keyword)
     for item in items:
       key = item.note_id or item.source_url or item.title
       collected[key] = item
     progress = progress.with_phase(SCROLLING, scroll_index=i,
                                     items_count=len(collected))
     await on_progress(progress)
7. progress = progress.with_phase(DONE, scroll_index=scroll_count,
                                   items_count=len(collected))
   await on_progress(progress)
   await page.close()
   return list(collected.values())

异常处理（任意步骤）：
   progress = progress.with_phase(ERROR, error_message=str(exc))
   await on_progress(progress)
   await page.close()
   raise / return []
```

**`human_scroll`** — 决策 4 + 反爬要求：

```
1. 滚动前随机鼠标移动一下
   x = random.randint(200, 800)
   y = random.randint(200, 600)
   await page.mouse.move(x, y, steps=random.randint(10, 25))

2. 滚动距离随机化（页面高度的 0.7-1.1 倍 → 700-1100）
   delta_total = random.randint(700, 1100)

3. 分 3-5 次小幅度滚动
   sub_count = random.randint(3, 5)
   sub_delta = delta_total // sub_count
   for _ in range(sub_count):
     await page.mouse.wheel(0, sub_delta)
     await asyncio.sleep(random.uniform(0.15, 0.4))

4. 滚动后阅读停顿
   await asyncio.sleep(random.uniform(1.8, 3.5))

总耗时约：sub_count * (0.15-0.4) + 1.8-3.5 ≈ [2.25, 5.5] 秒
```

**`extract_visible_items`**：

```python
async def extract_visible_items(page, keyword: str) -> list[CaptureItemIn]:
    raw_items: list[dict] = await page.evaluate(f"""
        () => {{
            {_EXTRACTOR_JS_BUNDLE}
            // content.js 是 IIFE，需要把 extractSearchResultItems 暴露到 globalThis
            // 决策 2：refactor content.js 让 globalThis.__XHS_EXTRACTOR__.extractSearchResultItems 可调
            return globalThis.__XHS_EXTRACTOR__.extractSearchResultItems();
        }}
    """)
    items: list[CaptureItemIn] = []
    for raw in raw_items:
        # filter掉 search_page_context 这种伪条目
        if raw.get("debug_url_source") == "search_page_context":
            continue
        try:
            items.append(CaptureItemIn(**raw))
        except Exception as exc:
            # 单条解析失败不阻塞整轮
            logger.warning("Failed to parse capture item", extra={
                "raw_keys": list(raw.keys()),
                "error": str(exc),
            })
    return items
```

**`content.js` refactor**（决策 2）：

最小改动 — 在 IIFE 内部 return 之前，把关键函数挂到 `globalThis.__XHS_EXTRACTOR__`：

```javascript
// extension/src/content.js  在 IIFE 末尾、return 之前追加
globalThis.__XHS_EXTRACTOR__ = {
  extractSearchResultItems,
  extractNoteDetailItem,
  extractItemFromRoot,
  detectPageType,
  resolveBestSourceUrl,
  normalizeXhsUrl,
};
```

这样 IIFE 原有逻辑不动（Extension 路径不受影响），Playwright 注入相同代码后能调用。

### 3.4 Implementation Checklist

- [ ] 实现 `scraper_models.py`
- [ ] 实现 `scraper_state.py` + 单测
- [ ] 实现 `scraper_login.py` + 单测
- [ ] 实现 `scraper_runtime.py`（注意 lifecycle 重入安全）
- [ ] Refactor `content.js` 暴露 `globalThis.__XHS_EXTRACTOR__`，验证既有 MVP 仍能采集
- [ ] 实现 `scraper.py` 主流程 + CLI
- [ ] 写单测（每个模块至少 1 个 happy path + 1 个 error path）
- [ ] 更新 `pyproject.toml` 与 `.gitignore`
- [ ] 手测 CLI 跑一次（需要先 `playwright install chromium`）

### 3.5 Error Handling Strategy

```
ScrapeError (基类)
├── ScraperRuntimeError   # 浏览器启动 / 重启失败
├── PageNavigationError   # page.goto 失败
└── (LOGIN_REQUIRED 不抛异常，通过 progress callback 通知)
```

**State / Persistence Notes**:
- `ScrapeStateRegistry` 是进程内 dict，进程重启清空 — 这是单 worker 设计的内在约束，不修复
- `ScraperRuntime._context.is_closed()` 检测崩溃后自动重启
- `data/chrome-profile/` 由 playwright 自管，本任务不删不清理

---

## 4. Testing Strategy

### 4.1 Test Pyramid Mapping

| Level | File | Count | Focus | Mock Strategy |
|---|---|---|---|---|
| Unit | `tests/unit/test_scraper_models.py` | 3-4 | 数据结构默认值 / `with_phase` | 无 mock |
| Unit | `tests/unit/test_scraper_state.py` | 4-5 | 锁 / 状态写入 / 释放 | 无 mock |
| Unit | `tests/unit/test_scraper_login.py` | 3 | 登录态三种 DOM | mock `page` 的 `wait_for_selector` |
| Unit | `tests/unit/test_scraper_human_scroll.py` | 3 | 距离方差 / 总耗时 / 调用次数 | mock `page.mouse`、`asyncio.sleep` |
| Unit | `tests/unit/test_scraper_orchestration.py` | 5-6 | 主流程：LOGIN_REQUIRED / 5 次滚动 / 异常 / progress 触发 | mock `ScraperRuntime`、`is_logged_in`、`extract_visible_items` |
| Unit | `tests/unit/test_scraper_extract_visible_items.py` | 3 | mock `page.evaluate` 返回固定 dict 列表 | mock `page` |

### 4.2 Critical Test Scenarios

**`test_scraper_state.py`**:
1. `test_try_acquire_first_call_returns_state`
2. `test_try_acquire_second_call_returns_none`
3. `test_release_clears_active_task`
4. `test_update_writes_progress`
5. `test_get_returns_none_for_unknown`

**`test_scraper_login.py`**:
1. `test_returns_true_when_avatar_link_present` — fake page 的 `wait_for_selector` 第一次成功
2. `test_returns_false_when_login_button_present` — 第一次 timeout，第二次成功
3. `test_returns_false_when_neither_signal_present` — 两次 timeout

**`test_scraper_human_scroll.py`**:
1. `test_scroll_distances_vary_across_calls` — 10 次调用，记录每次总 delta，断言 `len(set(deltas)) > 1`
2. `test_scroll_calls_mouse_move_first` — 顺序断言
3. `test_scroll_total_duration_within_bounds` — mock sleep 求和验证 [1.8, 4.5]

**`test_scraper_orchestration.py`**:
1. `test_login_required_returns_empty_list` — `is_logged_in` mock 返回 False，断言返回 `[]` + progress phase=LOGIN_REQUIRED
2. `test_full_scroll_loop_collects_items` — 5 次滚动，每次 mock 返回 5 条不重复 items，断言总 items=25
3. `test_dedupe_by_note_id` — mock 返回有重复 note_id，断言去重后数量
4. `test_progress_callback_called_with_phases` — 断言 LAUNCHING/NAVIGATING/SCROLLING/DONE 都触发过
5. `test_navigation_failure_propagates_error_phase` — mock `page.goto` 抛异常，断言 progress phase=ERROR

**`test_scraper_extract_visible_items.py`**:
1. `test_returns_capture_item_in_list`
2. `test_filters_out_search_page_context`
3. `test_skips_invalid_item_without_raising`

### 4.3 Test Data Fixtures

```python
# tests/unit/conftest.py 或 test 文件内 fixture
@pytest.fixture
def fake_page_logged_in():
    """模拟 playwright Page，wait_for_selector 对 LOGGED_IN_SELECTOR 立即返回。"""
    ...

@pytest.fixture
def fake_page_logged_out():
    ...

@pytest.fixture
def fake_runtime(fake_page_logged_in):
    """模拟 ScraperRuntime.acquire_page() 返回 fake page。"""
    ...
```

### 4.4 Real-XHS Marker（不在 Stage 4 强制）

```python
# pyproject.toml 或 pytest.ini 可选追加：
# markers = ["real_xhs: marks tests requiring real Xiaohongshu access"]

@pytest.mark.real_xhs
@pytest.mark.skip(reason="Requires real XHS access; run manually")
async def test_scrape_real_keyword():
    ...
```

Stage 4 默认跳过 `@real_xhs`；只在手动 `pytest -m real_xhs` 时跑。

---

## 5. Implementation Checklist

### Coding Sequence (Order Matters)

1. [ ] `scraper_models.py` — 数据结构（基础）
2. [ ] `scraper_state.py` + test — 不依赖 playwright，先打通
3. [ ] `scraper_login.py` + test — 依赖 page interface（用 fake mock）
4. [ ] `scraper_runtime.py` — 依赖 playwright，先写代码不跑测
5. [ ] `content.js` refactor — 暴露 globalThis 提取函数
6. [ ] `scraper.py` 主流程 + `extract_visible_items` + `human_scroll` + CLI
7. [ ] 写主流程 / extract / human_scroll 单测
8. [ ] `pyproject.toml` 加依赖；`.gitignore` 加路径

### Dependencies to Install/Verify

```bash
pip install "playwright>=1.40"
playwright install chromium      # 一次性，~150MB
```

### Configuration Required

无（路径常量在代码里硬编码 `data/chrome-profile`，由调用方注入 `ScraperRuntime(profile_dir=...)` 实现注入）。

---

## 6. Risk & Notes

**Technical Debt Warning**:
- content.js 同时被 Extension 和 Playwright scraper 引用，未来改动需要在两边都验证
- `_EXTRACTOR_JS_BUNDLE` 在 import 时一次读盘 — 进程重启才能感知 content.js 改动（开发期可接受）

**Architecture Decision**:
- 决策 4 选 keep-alive，意味着 `ScraperRuntime` 必须 lifespan 管理，不能任意 drop
- 决策 5 选「不主动登录」，前端 banner 只能引导，不能弹窗

**Spec Alignment**:
- `CaptureItemIn` 字段约束：缺失 `title` 时 `extractItemFromRoot` 返回 None，已被 `extract_visible_items` 过滤
- `ScrapePhase` 枚举值后续 Phase 2 的 Pydantic 模型直接复用，不要改名

**Cross-task Dependencies**:
- Phase 2 依赖：`scrape_search_feed` 签名稳定 + `ScrapePhase` 字符串值稳定
- Stage A (V1 集成) 依赖：scraper 写 SQLite 的逻辑（本任务暂不实现，Phase 2 通过 sink 引入）

## 7. Spec Sync Expectations

- Stage 5 由 progress-tracker 在 improvements.md Phase 1「✅ 完成进度」回填实际交付
- 任何未在本任务关闭的承诺（如 content.js IIFE 改造副作用、反爬阈值经验值）写入 Phase 1「⚠️ 遗留问题」
- 若发现需要影响 Phase 2 / Phase 3 的契约变化，回写到对应 Phase 的「⚠️ 遗留问题」并标注 `Carry Into: Phase X`
