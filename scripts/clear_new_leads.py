#!/usr/bin/env python3
"""Delete qualified leads in 📬 Новые (not sorted into a folder yet)."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import get_settings
from db import LeadDatabase


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clear uncategorized qualified leads (📬 Новые)."
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Delete without confirmation prompt",
    )
    args = parser.parse_args()

    db = LeadDatabase(get_settings().db_path)
    await db.connect()

    count = await db.count_uncategorized_qualified()
    print(f"Найдено в «Новые» (qualified, без папки): {count}")

    if count == 0:
        await db.close()
        print("Нечего удалять.")
        return 0

    if not args.yes:
        answer = input(f"Удалить все {count} лид(ов)? [y/N] ").strip().lower()
        if answer not in {"y", "yes", "д", "да"}:
            await db.close()
            print("Отменено.")
            return 1

    deleted = await db.delete_uncategorized_qualified()
    await db.close()
    print(f"Удалено: {deleted}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
