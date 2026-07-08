#!/usr/bin/env python3
"""Test Naver KR search on the server."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import KEYWORDS_KR, get_settings
from naver_parser import NaverParser


async def main() -> int:
    settings = get_settings()
    print(f"NAVER_ENABLED={settings.naver_enabled}")
    print(f"NAVER_PLAYWRIGHT={settings.naver_playwright}")
    print(f"NAVER_RECENCY_HOURS={settings.naver_recency_hours}")

    found: list[str] = []

    async def capture(post):
        found.append(f"[{post.source.value}] {post.text[:100]}...")

    parser = NaverParser(on_post=capture)
    await parser.start()
    if not parser.is_active:
        print(f"FAIL — {parser.status_detail}")
        return 1

    print(f"Status: {parser.status_detail}")
    print(f"Keywords: {len(KEYWORDS_KR)} — polling once...\n")
    await parser.poll_recent()
    await parser.stop()

    print(f"\nNotes to pipeline: {len(found)}")
    print(f"Final: {parser.status_detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
