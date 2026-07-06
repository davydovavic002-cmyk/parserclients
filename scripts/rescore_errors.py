#!/usr/bin/env python3
"""Re-score leads rejected due to Gemini API or JSON parse errors."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ai_classifier import qualify_lead
from config import get_settings
from db import LeadDatabase
from models import AIStatus

_PARSE_ERROR = "Некорректный structured output от Gemini"


async def main() -> int:
    db = LeadDatabase(get_settings().db_path)
    await db.connect()
    records = await db.get_gemini_error_leads()
    total = len(records)
    print(f"Found {total} lead(s) to rescore")

    if not total:
        await db.close()
        return 0

    fixed = 0
    newly_qualified = 0
    still_broken = 0

    for index, record in enumerate(records, start=1):
        result = await qualify_lead(record.text)
        if result.why_it_fits == _PARSE_ERROR:
            still_broken += 1
            print(f"[{index}/{total}] still broken — {record.source.value}")
            continue

        status = AIStatus.QUALIFIED if result.is_lead else AIStatus.REJECTED
        await db.update_lead_ai(
            record.external_id,
            record.source,
            status,
            reason=result.reason,
            summary=result.summary,
        )
        fixed += 1
        if result.is_lead:
            newly_qualified += 1
        print(
            f"[{index}/{total}] {status.value} "
            f"score={result.score} — {record.source.value}"
        )
        await asyncio.sleep(0.4)

    await db.close()
    print(
        f"\nDone: rescored={fixed}, newly_qualified={newly_qualified}, "
        f"still_broken={still_broken}"
    )
    return 0 if still_broken == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
