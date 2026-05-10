# Development Guide: phase_4_scraper_e2e — 反爬调试与稳定性验证

> Generated: 2026-05-10
> Architect: implementation skill (dev-helper Stage 2)
> Status: Ready for development
> Source: experiments/xhs_extension_mvp/improvements.md §「Phase 4：反爬调试与稳定性验证」

## 1. Task Context

### Scope Boundary

- **Task ID**: phase_4_scraper_e2e
- **Task Name**: 反爬调试与稳定性验证 — Step 1 Gate + Step 2 Stress Tooling
- **Phase**: 2026/05/09 Improvement Phase 4
- **Dependencies**: Phase 3 ✅ 完成 — UI 层自动采集按钮、状态轮询、scraper endpoints 全部可用
- **Task Goal**: (1) Step 1 Gate：创建 `scripts/xhs_login.py` 登录助手，使开发者能在 Playwright profile 里完成 XHS 登录；(2) Step 2 Tooling：实现 `scraper_metrics.py`（采集统计数据结构）和 `scripts/stress_scrape.py`（循环压测 CLI），配合手动压测完成反爬参数验证。

### In Scope

- `experiments/xhs_extension_mvp/scripts/__init__.py` — 新建 scripts 包
- `experiments/xhs_extension_mvp/scripts/xhs_login.py` — 打开 Playwright 浏览器等待手动登录的助手脚本
- `experiments/xhs_extension_mvp/server/scraper_metrics.py` — ScrapeMetrics dataclass + record_run / summary
- `experiments/xhs_extension_mvp/scripts/stress_scrape.py` — 循环采集 CLI，接受 keywords + rounds + scroll_count
- `tests/unit/test_scraper_metrics.py` — ScrapeMetrics 单元测试（纯逻辑，无 Playwright）

### Out Of Scope

- ❌ 实际 human_scroll 参数值调整（需要真实浏览器跑完压测后再改，不在本次代码提交范围）
- ❌ 自动化端到端测试（Playwright browser 测试），全部手测
- ❌ `scraper_login.py` selector 修改（Step 1 Gate 若失败才修，先手测验证）
- ❌ 服务端 API 任何改动
- ❌ Phase 3 UI 任何改动

### Required Deliverables

| 路径 | 类型 | 用途 |
|---|---|---|
| `experiments/xhs_extension_mvp/scripts/__init__.py` | NEW | 空包文件 |
| `experiments/xhs_extension_mvp/scripts/xhs_login.py` | NEW | Playwright 登录助手，用于 Step 1 Gate 前置登录 |
| `experiments/xhs_extension_mvp/server/scraper_metrics.py` | NEW | ScrapeMetrics 统计数据结构 |
| `experiments/xhs_extension_mvp/scripts/stress_scrape.py` | NEW | 循环压测 CLI |
| `tests/unit/test_scraper_metrics.py` | NEW | ScrapeMetrics 单元测试 |

**无需修改的文件**（除非 Step 1 手测发现 selector 坏了）：
- `server/scraper_login.py` — 待手测验证
- `server/scraper.py` — human_scroll 参数待压测后再调

### Acceptance Criteria

| AC | 描述 | 验证方式 |
|---|---|---|
| AC1 | `xhs_login.py` 能打开 Playwright 浏览器到 XHS 首页，等待 Enter 后干净关闭 | 手测：python scripts/xhs_login.py |
| AC2 | CLI `python -m ... scraper "keyword" 1` 单次采集不出现 login_required，items > 0 | 手测（Step 1 Gate） |
| AC3 | `ScrapeMetrics.record_run(DONE, items)` 正确累加 success_runs 和 items_per_run | 单元测试 |
| AC4 | `ScrapeMetrics.record_run(LOGIN_REQUIRED, 0)` 正确累加 login_required_runs | 单元测试 |
| AC5 | `ScrapeMetrics.summary()` 返回正确的 success_rate、avg_items_per_run | 单元测试 |
| AC6 | `stress_scrape.py` 接受 `--keywords k1,k2 --rounds N --scroll N` 参数，循环执行并打印 summary | 手测：`python scripts/stress_scrape.py --keywords "敏感肌" --rounds 2 --scroll 1` |
| AC7 | 压测结束打印 `=== Summary ===` 块，字段与 ScrapeMetrics.summary() 一致 | 手测 |

### Carry-Forward Residuals from Phase 3

| # | 项目 | 处理方式 |
|---|---|---|
| #1 | scraper runtime 崩溃重启 | 本次不实现自动重启；stress_scrape.py 捕获 ScraperRuntimeError，打印后继续 |
| #2 | 反爬参数调优 | stress_scrape.py 提供工具；实际参数值在手测后再提 PR |
| #3 | readiness 登录探针覆盖 | xhs_login.py 解决前置登录；readiness endpoint 已有 profile_exists 检查，暂不扩展 |

### Contract Inventory

**Upstream（已有）**:
- `ScraperRuntime(profile_dir)` → `acquire_page()` / `shutdown()`
- `scrape_search_feed(keyword, *, runtime, scroll_count, on_progress)` → `list[CaptureItemIn]`
- `ScrapePhase` enum（scraper_models.py）：DONE / LOGIN_REQUIRED / ERROR 等
- `ScrapeProgress.phase` / `.items_count`

**Downstream（本次提供）**:
- `ScrapeMetrics.record_run(phase, items_count)` — stress_scrape.py 调用
- `ScrapeMetrics.summary()` → dict — stress_scrape.py 打印
- `stress_scrape.py` CLI 接口 — 手测脚本，不对外暴露 HTTP

---

## 2. Architecture Context

### System Position

```
手动压测工具链（只在 dev 环境使用）

scripts/xhs_login.py
  └── ScraperRuntime.acquire_page() → 打开 Playwright 浏览器，等待手动登录

scripts/stress_scrape.py
  ├── scrape_search_feed() [循环 N 轮]
  │     └── on_progress → 打印进度
  └── ScrapeMetrics.record_run() → summary() → 打印统计

server/scraper_metrics.py
  └── ScrapeMetrics（纯数据，无 I/O，可单元测试）
```

### Constraints

- 所有脚本只在仓库根目录下运行（`python experiments/xhs_extension_mvp/scripts/xxx.py` 或作为模块运行）
- 不引入新依赖（Playwright 已有，argparse 是标准库）
- stress_scrape.py 每轮之间默认等待 5s，防止触发短时封禁
- 捕获 scrape_search_feed 抛出的所有异常，不因单次失败中断压测

---

## 3. Technical Design

### 3.1 Module Structure

**Files to Create:**
```
experiments/xhs_extension_mvp/
  scripts/
    __init__.py                 # empty
    xhs_login.py                # login helper
    stress_scrape.py            # stress test CLI
  server/
    scraper_metrics.py          # ScrapeMetrics dataclass
tests/unit/
  test_scraper_metrics.py       # unit tests
```

**Per-file Change Intent**:

| Path | NEW/MODIFY | Required Change | Linked AC |
|------|------------|-----------------|-----------|
| `scripts/__init__.py` | NEW | 空包文件 | — |
| `scripts/xhs_login.py` | NEW | 打开 Playwright，goto XHS，等 Enter，shutdown | AC1 |
| `server/scraper_metrics.py` | NEW | ScrapeMetrics dataclass + record_run + summary | AC3–AC5 |
| `scripts/stress_scrape.py` | NEW | argparse CLI + asyncio main loop + metrics | AC6–AC7 |
| `tests/unit/test_scraper_metrics.py` | NEW | 5 个单元测试覆盖 record_run / summary | AC3–AC5 |

### 3.2 Class & Interface Design

**`server/scraper_metrics.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from experiments.xhs_extension_mvp.server.scraper_models import ScrapePhase


@dataclass
class ScrapeMetrics:
    """Accumulates per-run statistics for stress testing and anti-crawl tuning."""

    total_runs: int = 0
    success_runs: int = 0
    login_required_runs: int = 0
    error_runs: int = 0
    captcha_runs: int = 0       # subset of error_runs, set externally when detected
    items_per_run: list[int] = field(default_factory=list)

    def record_run(self, phase: ScrapePhase, items_count: int = 0) -> None:
        """Record the final phase of one completed scrape attempt."""
        self.total_runs += 1
        if phase == ScrapePhase.DONE:
            self.success_runs += 1
            self.items_per_run.append(items_count)
        elif phase == ScrapePhase.LOGIN_REQUIRED:
            self.login_required_runs += 1
        else:
            self.error_runs += 1

    def record_captcha(self) -> None:
        """Mark the most recent error as captcha-triggered (call after record_run ERROR)."""
        self.captcha_runs += 1

    def summary(self) -> dict:
        """Return a serialisable summary dict."""
        success_rate = self.success_runs / self.total_runs if self.total_runs else 0.0
        avg_items = (
            sum(self.items_per_run) / len(self.items_per_run)
            if self.items_per_run
            else 0.0
        )
        return {
            "total_runs": self.total_runs,
            "success_runs": self.success_runs,
            "success_rate": round(success_rate, 3),
            "login_required_runs": self.login_required_runs,
            "error_runs": self.error_runs,
            "captcha_runs": self.captcha_runs,
            "avg_items_per_run": round(avg_items, 1),
            "items_distribution": list(self.items_per_run),
        }
```

**`scripts/xhs_login.py`**

```python
"""One-shot helper: open Playwright browser in data/chrome-profile and wait for manual XHS login."""

import asyncio
from pathlib import Path

DEFAULT_PROFILE_DIR = Path("data/chrome-profile")
XHS_HOME = "https://www.xiaohongshu.com"

async def main(profile_dir: Path = DEFAULT_PROFILE_DIR) -> None:
    from experiments.xhs_extension_mvp.server.scraper_runtime import ScraperRuntime
    rt = ScraperRuntime(profile_dir=profile_dir)
    page = await rt.acquire_page()
    await page.goto(XHS_HOME)
    print(f"[xhs_login] Browser opened at {XHS_HOME}")
    print("[xhs_login] 请在弹出的浏览器里完成小红书登录，然后回到终端按 Enter ...")
    input()
    await rt.shutdown()
    print("[xhs_login] Browser closed. Login session saved to", profile_dir)

if __name__ == "__main__":
    asyncio.run(main())
```

**`scripts/stress_scrape.py`**

```python
"""Stress-test the scraper over N rounds and print a ScrapeMetrics summary.

Usage:
    python experiments/xhs_extension_mvp/scripts/stress_scrape.py \
        --keywords "敏感肌护肤,户外防晒" --rounds 10 --scroll 5
"""

import argparse
import asyncio
from pathlib import Path

from experiments.xhs_extension_mvp.server.scraper import scrape_search_feed, DEFAULT_PROFILE_DIR
from experiments.xhs_extension_mvp.server.scraper_metrics import ScrapeMetrics
from experiments.xhs_extension_mvp.server.scraper_models import ScrapePhase
from experiments.xhs_extension_mvp.server.scraper_runtime import ScraperRuntime

_INTER_RUN_PAUSE_SECONDS = 5


async def run_stress(keywords: list[str], rounds: int, scroll_count: int) -> None:
    runtime = ScraperRuntime(profile_dir=DEFAULT_PROFILE_DIR)
    metrics = ScrapeMetrics()
    total = rounds * len(keywords)
    run_n = 0

    try:
        for round_idx in range(1, rounds + 1):
            for keyword in keywords:
                run_n += 1
                print(f"\n[{run_n}/{total}] round={round_idx} keyword={keyword!r}")
                final_phase = ScrapePhase.ERROR
                final_items = 0

                async def on_progress(p):
                    nonlocal final_phase, final_items
                    final_phase = p.phase
                    final_items = p.items_count
                    print(
                        f"  phase={p.phase.value:<16} "
                        f"scroll={p.scroll_index}/{p.scroll_total} "
                        f"items={p.items_count}"
                    )

                try:
                    items = await scrape_search_feed(
                        keyword,
                        runtime=runtime,
                        scroll_count=scroll_count,
                        on_progress=on_progress,
                    )
                    metrics.record_run(final_phase, len(items))
                except Exception as exc:
                    print(f"  [EXCEPTION] {exc}")
                    metrics.total_runs += 1
                    metrics.error_runs += 1

                if run_n < total:
                    print(f"  [pause {_INTER_RUN_PAUSE_SECONDS}s]")
                    await asyncio.sleep(_INTER_RUN_PAUSE_SECONDS)
    finally:
        await runtime.shutdown()

    print("\n=== Summary ===")
    for key, val in metrics.summary().items():
        print(f"  {key}: {val}")


def main() -> None:
    parser = argparse.ArgumentParser(description="XHS scraper stress test")
    parser.add_argument("--keywords", required=True, help="Comma-separated keywords")
    parser.add_argument("--rounds", type=int, default=10, help="Number of rounds per keyword")
    parser.add_argument("--scroll", type=int, default=5, help="Scroll count per run")
    args = parser.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    if not keywords:
        parser.error("--keywords must not be empty")

    asyncio.run(run_stress(keywords, args.rounds, args.scroll))


if __name__ == "__main__":
    main()
```

### 3.3 Logic Flow

```
stress_scrape.py main()
  → argparse: keywords / rounds / scroll
  → ScraperRuntime(data/chrome-profile)
  → ScrapeMetrics()
  → for round in rounds:
      for keyword in keywords:
          scrape_search_feed(keyword, runtime, scroll_count, on_progress)
            → on_progress: capture final_phase + final_items, print live
          metrics.record_run(final_phase, items_count)
          asyncio.sleep(5)  ← inter-run cooldown
  → runtime.shutdown()
  → metrics.summary() → print

xhs_login.py main()
  → ScraperRuntime.acquire_page()
  → page.goto(xhs_home)
  → input() — block waiting for Enter
  → runtime.shutdown()
```

### 3.4 Implementation Checklist

1. [ ] 创建 `scripts/__init__.py`（空文件）
2. [ ] 实现 `scripts/xhs_login.py`（ScraperRuntime + page.goto + input + shutdown）
3. [ ] 实现 `server/scraper_metrics.py`（ScrapeMetrics dataclass + record_run + record_captcha + summary）
4. [ ] 实现 `scripts/stress_scrape.py`（argparse + run_stress + on_progress + metrics）
5. [ ] 写单元测试 `tests/unit/test_scraper_metrics.py`（5 个场景，覆盖 AC3–AC5）
6. [ ] 运行单元测试，确认全部通过
7. [ ] 手测 `xhs_login.py`（AC1）
8. [ ] 手测 CLI gate（AC2）
9. [ ] 手测 `stress_scrape.py --rounds 2 --scroll 1`（AC6–AC7）

### 3.5 Error Handling Strategy

```
stress_scrape.py
├── scrape_search_feed → LOGIN_REQUIRED  → record_run(LOGIN_REQUIRED) → continue
├── scrape_search_feed → ERROR phase    → record_run(ERROR) → continue
├── scrape_search_feed → raises exc     → total_runs++, error_runs++ → continue
└── runtime.shutdown()  在 finally 块，确保 Chrome 关闭

xhs_login.py
└── 不捕获异常（如果 Playwright 启动失败，让错误直接打印）
```

---

## 4. Testing Strategy

### 4.1 Test Pyramid Mapping

| Level | File | Count | Focus | Mock Strategy |
|-------|------|-------|-------|---------------|
| Unit | `tests/unit/test_scraper_metrics.py` | 5 | ScrapeMetrics 纯逻辑 | 无 mock（纯 dataclass） |
| Manual E2E | — | Step 1 + Step 2 手测 | 实际 Playwright + XHS | 无 mock |

### 4.2 Critical Test Scenarios

**Unit（必须全过）**:

1. `test_record_done_increments_success_and_items` — DONE phase → success_runs +1, items_per_run 追加
2. `test_record_login_required_increments_counter` — LOGIN_REQUIRED → login_required_runs +1
3. `test_record_error_increments_error_runs` — ERROR → error_runs +1
4. `test_summary_success_rate_calculation` — 2 DONE / 3 total → success_rate=0.667
5. `test_summary_empty_metrics` — 零次运行 → all zeros, no division error

**手测（Step 1 Gate）**:
```bash
python experiments/xhs_extension_mvp/scripts/xhs_login.py
# → Chrome 弹出，登录后 Enter
python -m experiments.xhs_extension_mvp.server.scraper "户外防晒" 1
# → phase=done, items > 0
```

**手测（Step 2 Stress）**:
```bash
python experiments/xhs_extension_mvp/scripts/stress_scrape.py \
    --keywords "户外防晒" --rounds 2 --scroll 1
# → 输出 2 轮进度 + === Summary === 块，success_rate > 0
```

### 4.3 Test Data Fixtures

```python
# test_scraper_metrics.py — 不需要 fixtures，直接实例化 ScrapeMetrics()
```

---

## 5. Implementation Checklist

### Coding Sequence

1. [ ] `scripts/__init__.py` — touch
2. [ ] `server/scraper_metrics.py` — dataclass 实现
3. [ ] `tests/unit/test_scraper_metrics.py` — 5 个单元测试
4. [ ] `scripts/xhs_login.py` — login helper
5. [ ] `scripts/stress_scrape.py` — 依赖 scraper_metrics，最后实现

### Dependencies to Verify

```bash
# Playwright 已安装
python -c "import playwright; print('ok')"
# Chromium 已下载
playwright install chromium --dry-run 2>&1 | head -3
```

---

## 6. Risk & Notes

**Architecture Decision**:
- `stress_scrape.py` 不引入新 HTTP 层，直接调 `scrape_search_feed`；保持与 HTTP endpoint 同一代码路径
- `ScrapeMetrics` 有意设计为纯 dataclass（无 I/O），方便单元测试和未来持久化

**Technical Debt**:
- `captcha_runs` 目前需要调用方在检测到验证码特征后手动调 `record_captcha()`；自动检测 captcha DOM selector 留给 Phase 4 手测后决定

**Spec Alignment**:
- `human_scroll` 参数本次不改；参数值需要用 stress_scrape.py 跑出基线数据后再调整，调整结果提单独 commit

---

## 7. Spec Sync Expectations

- 完成后在 improvements.md Phase 4「✅ 完成进度」回填 AC1–AC7 的验证结果
- 遗留问题（human_scroll 参数调整结果）写入「⚠️ 遗留问题」
- Phase 3 carry-forward #1/#2/#3 在本次部分关闭（#3 关闭，#1/#2 标记为「工具已就绪，待手测后调参」）
