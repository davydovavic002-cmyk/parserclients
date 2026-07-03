#!/usr/bin/env python3
"""Print lead pipeline funnel stats from leads.db."""
from __future__ import annotations

import asyncio
import json

from config import get_settings
from db import LeadDatabase


async def main() -> None:
    db = LeadDatabase(get_settings().db_path)
    await db.connect()
    stats = await db.get_pipeline_stats()
    await db.close()
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
