#!/usr/bin/env python3
"""Re-score Gemini error leads and send notifications for newly qualified."""
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
from models import AIStatus


def _is_still_gemini_error(reason: str) -> bool:
    return reason.startswith(
        (
            "Некорректный structured output",
            "Ошибка Gemini API",
            "API-ключ Gemini",
        )
    )


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
    fixed = 0
    still_broken = 0
    newly_qualified = 0

    for index, record in enumerate(records, start=1):
        print(f"[{index}/{total}] {record.source.value} {record.external_id[:48]}")
        try:
            await pipeline.reprocess_lead_record(record)
            updated = (
                await db.get_lead_by_id(record.id)
                if record.id
                else None
            )
            reason = updated.reason or "" if updated else ""
            if _is_still_gemini_error(reason):
                still_broken += 1
                print(f"  → still broken: {reason[:60]}")
            else:
                fixed += 1
                if updated and updated.ai_status == AIStatus.QUALIFIED:
                    newly_qualified += 1
                    print(
                        f"  → qualified score OK, notified={updated.telegram_notified}"
                    )
        except Exception as exc:
            still_broken += 1
            print(f"  → error: {exc}")
        await asyncio.sleep(0.5)

    unnotified = await db.count_unnotified_qualified()
    await db.close()
    print(
        f"\nDone: rescored={fixed}, newly_qualified={newly_qualified}, "
        f"still_broken={still_broken}"
    )
    if unnotified:
        print(f"Unnotified: {unnotified} — send /push in bot")
    return 0 if still_broken == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
