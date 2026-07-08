#!/usr/bin/env python3
"""
One-time XHS login — saves cookies for the server parser.

Run on your PC (with display), log into xiaohongshu.com in the opened browser,
then press Enter. Upload the generated xhs_storage.json to the server:

  scp xhs_storage.json deploy@parsing-1:~/parserclients/

In .env on server:
  XHS_STORAGE_STATE=xhs_storage.json
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from playwright.async_api import async_playwright

from browser_stealth import STEALTH_LAUNCH_ARGS, create_stealth_context

OUTPUT = _ROOT / "xhs_storage.json"


async def _wait_enter(prompt: str) -> None:
    """Don't block the asyncio loop — Playwright needs it for clicks."""
    await asyncio.to_thread(input, prompt)


async def _launch_auth_browser(playwright):
    """Prefer real Chrome/Edge on Windows — fewer broken UI quirks."""
    for channel in ("chrome", "msedge", None):
        try:
            kwargs = {"headless": False, "args": STEALTH_LAUNCH_ARGS}
            if channel:
                kwargs["channel"] = channel
            return await playwright.chromium.launch(**kwargs)
        except Exception:
            continue
    raise RuntimeError("Could not launch Chromium/Chrome/Edge")


async def main() -> int:
    if not sys.stdin.isatty():
        print("ERROR: run in an interactive terminal (not PM2)")
        return 1

    print(f"Output: {OUTPUT}")
    print()
    print("=== Вход в 小红书 (XHS) ===")
    print("Откроется обычный браузер (десктоп). Залогинься QR-кодом или телефоном.")
    print("Если просит captcha — пройди в окне браузера.")
    print("Когда увидишь ленту / профиль — вернись сюда и нажми Enter.\n")

    async with async_playwright() as p:
        browser = await _launch_auth_browser(p)
        context = await create_stealth_context(
            browser,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = await context.new_page()
        await page.goto(
            "https://www.xiaohongshu.com/explore",
            wait_until="domcontentloaded",
            timeout=90_000,
        )

        await _wait_enter("После входа нажми Enter, чтобы сохранить cookies... ")

        await context.storage_state(path=str(OUTPUT))
        await context.close()
        await browser.close()

    print(f"\nOK — saved {OUTPUT}")
    print("Upload to server and set XHS_STORAGE_STATE=xhs_storage.json in .env")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
