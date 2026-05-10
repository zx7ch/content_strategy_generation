"""Stress-test the XHS scraper over N rounds and print a ScrapeMetrics summary.

Run from the repository root:
    python experiments/xhs_extension_mvp/scripts/stress_scrape.py \\
        --keywords "敏感肌护肤,户外防晒" --rounds 10 --scroll 5

Prerequisites:
  - data/chrome-profile must contain a valid XHS login session.
    Run xhs_login.py first if not done already.
  - Playwright + Chromium must be installed.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from experiments.xhs_extension_mvp.server.scraper import (
    DEFAULT_PROFILE_DIR,
    scrape_search_feed,
)
from experiments.xhs_extension_mvp.server.scraper_metrics import ScrapeMetrics
from experiments.xhs_extension_mvp.server.scraper_models import ScrapePhase, ScrapeProgress
from experiments.xhs_extension_mvp.server.scraper_runtime import ScraperRuntime

_INTER_RUN_PAUSE_S = 30


async def run_stress(keywords: list[str], rounds: int, scroll_count: int) -> ScrapeMetrics:
    runtime = ScraperRuntime(profile_dir=DEFAULT_PROFILE_DIR)
    metrics = ScrapeMetrics()
    total = rounds * len(keywords)
    run_n = 0

    try:
        for round_idx in range(1, rounds + 1):
            for keyword in keywords:
                run_n += 1
                print(f"\n[{run_n}/{total}] round={round_idx} keyword={keyword!r}")

                final_phase: ScrapePhase = ScrapePhase.ERROR
                final_items: int = 0

                async def on_progress(p: ScrapeProgress) -> None:
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
                except Exception as exc:  # noqa: BLE001
                    print(f"  [EXCEPTION] {exc}")
                    # record_run is not called via the normal path; account manually
                    metrics.total_runs += 1
                    metrics.error_runs += 1

                if run_n < total:
                    print(f"  [pause {_INTER_RUN_PAUSE_S}s before next run]")
                    await asyncio.sleep(_INTER_RUN_PAUSE_S)
    finally:
        await runtime.shutdown()

    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="XHS scraper stress test")
    parser.add_argument(
        "--keywords",
        required=True,
        help="Comma-separated list of search keywords",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=10,
        help="Number of rounds to run per keyword (default: 10)",
    )
    parser.add_argument(
        "--scroll",
        type=int,
        default=5,
        help="Number of scroll steps per run (default: 5)",
    )
    args = parser.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    if not keywords:
        parser.error("--keywords must not be empty after stripping whitespace")

    print(f"[stress_scrape] keywords={keywords} rounds={args.rounds} scroll={args.scroll}")
    metrics = asyncio.run(run_stress(keywords, args.rounds, args.scroll))

    print("\n=== Summary ===")
    for key, val in metrics.summary().items():
        print(f"  {key}: {val}")


if __name__ == "__main__":
    main()
