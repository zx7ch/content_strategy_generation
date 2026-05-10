"""One-shot helper: open the Playwright browser profile and wait for manual XHS login.

Run from the repository root:
    python experiments/xhs_extension_mvp/scripts/xhs_login.py

The browser window opens at https://www.xiaohongshu.com. Complete the login
there, then press Enter in this terminal to save the session and close the
browser. The session is persisted in data/chrome-profile and will be reused
by the scraper on subsequent runs.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

_DEFAULT_PROFILE_DIR = Path("data/chrome-profile")
_XHS_HOME = "https://www.xiaohongshu.com"


async def _open_and_wait(profile_dir: Path) -> None:
    from experiments.xhs_extension_mvp.server.scraper_runtime import ScraperRuntime

    print(f"[xhs_login] Starting Playwright browser with profile: {profile_dir}")
    rt = ScraperRuntime(profile_dir=profile_dir)
    page = await rt.acquire_page()
    await page.goto(_XHS_HOME)
    print(f"[xhs_login] Browser opened at {_XHS_HOME}")
    print("[xhs_login] 请在弹出的浏览器里完成小红书登录，完成后回到终端按 Enter ...")
    input()
    await rt.shutdown()
    print(f"[xhs_login] Done. Login session saved to: {profile_dir.resolve()}")


def main() -> None:
    asyncio.run(_open_and_wait(_DEFAULT_PROFILE_DIR))


if __name__ == "__main__":
    main()
