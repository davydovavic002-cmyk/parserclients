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

from browser_stealth import create_stealth_browser, create_xhs_context, new_stealth_page

OUTPUT = _ROOT / "xhs_storage.json"


async def main() -> int:
    if not sys.stdin.isatty():
        print("ERROR: run in an interactive terminal (not PM2)")
        return 1

    print(f"Output: {OUTPUT}")
    print("Browser will open — log into 小红书, then return here.\n")

    async with async_playwright() as p:
        browser = await create_stealth_browser(p, headless=False)
        context = await create_xhs_context(browser)
        page = await new_stealth_page(context)
        await page.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded")

        input("After login in the browser, press Enter to save session... ")

        await context.storage_state(path=str(OUTPUT))
        await context.close()
        await browser.close()

    print(f"\nOK — saved {OUTPUT}")
    print("Upload to server and set XHS_STORAGE_STATE=xhs_storage.json in .env")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
