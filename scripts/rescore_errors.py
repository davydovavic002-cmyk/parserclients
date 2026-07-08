#!/usr/bin/env python3
"""Re-score Gemini error leads; delete junk that fails pre-filter."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import get_settings
from db import LeadDatabase
from main import LeadPipeline, setup_logging


async def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level)

    db = LeadDatabase(settings.db_path)
    await db.connect()
    records = await db.get_gemini_error_leads()
    total = len(records)
    print(f"Found {total} Gemini error lead(s) to rescore")

    if not total:
        await db.close()
        return 0

    pipeline = LeadPipeline(db)
    deleted = 0
    qualified = 0
    rejected = 0
    still_broken = 0

    for index, record in enumerate(records, start=1):
        print(f"[{index}/{total}] {record.source.value} {record.external_id[:48]}")
        try:
            outcome = await pipeline.rescore_gemini_error(record)
            print(f"  → {outcome}")
            if outcome == "deleted_prefilter":
                deleted += 1
            elif outcome == "qualified":
                qualified += 1
            elif outcome == "still_broken":
                still_broken += 1
            else:
                rejected += 1
        except Exception as exc:
            still_broken += 1
            print(f"  → error: {exc}")
        await asyncio.sleep(0.5)

    remaining = len(await db.get_gemini_error_leads())
    unnotified = await db.count_unnotified_qualified()
    await db.close()

    print(
        f"\nDone: deleted_junk={deleted}, qualified={qualified}, "
        f"rejected={rejected}, still_broken={still_broken}"
    )
    print(f"Gemini errors left in DB: {remaining}")
    if unnotified:
        print(f"Unnotified: {unnotified} — send /push in bot")
    return 0 if still_broken == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
