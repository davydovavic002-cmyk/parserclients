#!/usr/bin/env python3
"""Test Google Maps USA no-website scanner."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import MAPS_US_CITIES, get_settings
from google_maps_parser import GoogleMapsParser


async def main() -> int:
    settings = get_settings()
    print(f"MAPS_ENABLED={settings.maps_enabled}")
    print(f"GOOGLE_MAPS_API_KEY={'set' if settings.google_maps_api_key.strip() else 'MISSING'}")
    print(f"MAPS_SEARCHES_PER_POLL={settings.maps_searches_per_poll}")
    print(f"Cities: {len(MAPS_US_CITIES)}")

    if not settings.google_maps_api_key.strip():
        print(
            "\nGet a key: Google Cloud Console → enable Places API (New)"
            "\n→ create API key → GOOGLE_MAPS_API_KEY=... in .env"
        )
        return 1

    found: list[str] = []

    async def capture(post):
        found.append(f"{post.text.splitlines()[1]} — {post.contact}")

    parser = GoogleMapsParser(on_post=capture)
    await parser.start()
    if not parser.is_active:
        print(f"\nFAIL — {parser.status_detail}")
        return 1

    print(f"\nStatus: {parser.status_detail}")
    print("Polling once (3 city/category searches)...\n")
    await parser.poll_recent()
    await parser.stop()

    print(f"\nProspects to pipeline: {len(found)}")
    for line in found[:10]:
        print(f"  • {line}")
    return 0 if found else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
