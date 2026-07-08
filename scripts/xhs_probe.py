#!/usr/bin/env python3
"""Test XHS scraping on the server."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import get_settings
from xiaohongshu_parser import XiaohongshuParser


async def main() -> int:
    settings = get_settings()
    print(f"XHS_ENABLED={settings.xhs_enabled}")
    print(f"XHS_HEADLESS={settings.xhs_headless}")
    print(f"XHS_LOW_MEMORY={settings.xhs_low_memory}")
    print(f"XHS_MOBILE_UA={settings.xhs_mobile_ua}")
    print(f"XHS_PLAYWRIGHT={settings.xhs_playwright}")
    print(f"XHS_STORAGE_STATE={settings.xhs_storage_state or '(none)'}")

    found: list[str] = []

    async def capture(post):
        found.append(f"[{post.source.value}] {post.text[:80]}...")

    parser = XiaohongshuParser(on_post=capture)
    await parser.start()
    if not parser.is_active:
        print(f"FAIL — {parser.status_detail}")
        return 1

    print(f"Status: {parser.status_detail}\nPolling once...")
    await parser.poll_recent()
    await parser.stop()

    print(f"\nNotes to pipeline: {len(found)}")
    print(f"Final status: {parser.status_detail}")
    if "логин" in parser.status_detail:
        print("\n→ Need login: run auth_xhs.py on PC, upload xhs_storage.json")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
