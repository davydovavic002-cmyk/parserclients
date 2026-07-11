#!/usr/bin/env python3
"""Where leads get stuck — run on the server inside .venv."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import get_settings
from db import LeadDatabase


async def main() -> int:
    settings = get_settings()
    db = LeadDatabase(settings.db_path)
    await db.connect()

    print("=== Pipeline diagnose ===\n")

    # Config
    print("⚙️  Config:")
    print(f"  GEMINI_API_KEY: {'✅ set' if settings.gemini_api_key.strip() else '❌ EMPTY'}")
    print(f"  NOTIFICATION bot: {'✅' if settings.notification_tg_bot_token and settings.notification_tg_chat_id else '❌'}")
    print(f"  TG API: {'✅' if settings.telegram_api_id and settings.telegram_api_hash else '❌'}")
    print(f"  MIN_LEAD_SCORE={settings.min_lead_score}  MAX_PROPOSALS={settings.max_proposals}")
    if settings.min_lead_score > 50:
        print(f"  ⚠️  MIN_LEAD_SCORE={settings.min_lead_score} in .env — set to 50 for more leads")
    print(f"  MAX_POST_AGE_HOURS={settings.max_post_age_hours}  REJECT_LOW_BUDGET={settings.reject_low_budget}")
    print(f"  Parsers: Google={settings.google_radar_enabled} Maps={settings.maps_enabled} Boards={settings.boards_enabled} XHS={settings.xhs_enabled} Naver={settings.naver_enabled}")
    if settings.maps_enabled and not settings.google_maps_api_key.strip():
        print("  ⚠️  MAPS: set GOOGLE_MAPS_API_KEY for US no-website leads")
    print("  Reddit: ✅ anonymous JSON (OAuth keys optional)")
    print()

    stats = await db.get_pipeline_stats()
    print("📊 Funnel (all time):")
    print(f"  Total rows: {stats['total_rows']}")
    print(f"  Qualified:  {stats['qualified']}  Rejected: {stats['rejected']}  Pending: {stats['pending']}")
    print()

    unnotified = await db.count_unnotified_qualified()
    print(f"📨 Unnotified qualified (need /push): {unnotified}")
    if unnotified:
        print("   → In bot send: /push")
    print()

    assert db._conn is not None
    conn = db._conn

    now = datetime.now(timezone.utc)
    for label, hours in (("24h", 24), ("7d", 24 * 7)):
        cutoff = (now - timedelta(hours=hours)).isoformat()
        cur = await conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM leads WHERE timestamp >= ?
            """,
            (cutoff,),
        )
        total_recent = (await cur.fetchone())["cnt"]
        cur = await conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM leads
            WHERE timestamp >= ? AND ai_status = 'qualified'
            """,
            (cutoff,),
        )
        qual_recent = (await cur.fetchone())["cnt"]
        print(f"🕐 Last {label}: {total_recent} new rows, {qual_recent} qualified")

    cur = await conn.execute(
        """
        SELECT id, source, ai_status, timestamp, telegram_notified
        FROM leads ORDER BY id DESC LIMIT 5
        """
    )
    latest = await cur.fetchall()
    if latest:
        print("\n📋 Latest 5 DB rows:")
        for row in latest:
            notified = "✉" if row["telegram_notified"] else "—"
            print(
                f"  #{row['id']} {row['source']:10} {row['ai_status']:10} "
                f"notified={notified}  {row['timestamp'][:19]}"
            )
    print()

    # Last 50 rows
    cur = await conn.execute(
        """
        SELECT source, ai_status, COUNT(*) AS cnt
        FROM leads
        WHERE id > (SELECT COALESCE(MAX(id), 0) - 50 FROM leads)
        GROUP BY source, ai_status
        ORDER BY source, ai_status
        """
    )
    recent = await cur.fetchall()
    if recent:
        print("🕐 Last ~50 DB rows by source:")
        for row in recent:
            print(f"  {row['source']:10} {row['ai_status']:10} {row['cnt']}")
    else:
        print("🕐 Last ~50 DB rows: (empty — parsers not inserting anything)")
    print()

    # Top rejection reasons
    cur = await conn.execute(
        """
        SELECT reason, COUNT(*) AS cnt
        FROM leads
        WHERE ai_status = 'rejected' AND reason IS NOT NULL
        GROUP BY reason
        ORDER BY cnt DESC
        LIMIT 12
        """
    )
    reasons = await cur.fetchall()
    if reasons:
        print("❌ Top rejection reasons:")
        for row in reasons:
            reason = (row["reason"] or "")[:70]
            print(f"  {row['cnt']:4}x  {reason}")
    print()

    # Gemini API failures still in DB
    cur = await conn.execute(
        """
        SELECT COUNT(*) AS cnt FROM leads
        WHERE ai_status = 'rejected'
          AND (reason LIKE 'API-ключ Gemini%' OR reason LIKE 'Ошибка Gemini%' OR reason LIKE 'Некорректный structured%')
        """
    )
    gemini_fail = (await cur.fetchone())["cnt"]
    if gemini_fail:
        print(f"⚠️  Gemini API/parse failures in DB: {gemini_fail}")
        print("   → Check GEMINI_API_KEY; run: python scripts/rescore_errors.py")
        print()

    cur = await conn.execute("SELECT COUNT(*) AS cnt FROM discovered_chats")
    chats = (await cur.fetchone())["cnt"]
    print(f"📡 TG channels in DB: {chats}")
    print()

    # Interpretation
    print("--- Likely cause ---")
    print("ℹ️  Scout pause is in-memory — check bot /status (⏸ на паузе).")
    if not settings.gemini_api_key.strip():
        print("❌ GEMINI_API_KEY empty — nothing can qualify.")
    elif stats["total_rows"] == 0:
        print("❌ No posts in DB — parsers not running, scout on pause, or all pre-filters block.")
        print("   → pm2 logs parserclients | grep -E 'Poll cycle|Scout paused|parser started'")
    elif stats["qualified"] == 0 and stats["rejected"] > 0:
        top = reasons[0]["reason"] if reasons else ""
        if "Quality gate" in top:
            print("❌ Quality gate too strict — lower MIN_LEAD_SCORE in .env (try 50).")
        elif "CMS-only" in top or "Stale" in top:
            print("❌ Most leads rejected by CMS or age filters.")
        elif "Gemini" in top or "API" in top:
            print("❌ Gemini errors — fix API key or quota.")
        else:
            print("❌ AI rejects everything — check reasons above.")
    elif unnotified > 0:
        print("✅ Leads qualified but not sent — use /push in bot.")
    elif stats["qualified"] > 0:
        print("✅ Qualified leads exist — waiting for NEW posts (dedup skips old).")
    else:
        print("⏳ Waiting for new posts from sources.")

    recent_24h = 0
    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    cur = await conn.execute(
        "SELECT COUNT(*) AS cnt FROM leads WHERE timestamp >= ?",
        (cutoff_24h,),
    )
    recent_24h = (await cur.fetchone())["cnt"]
    if recent_24h == 0 and stats["total_rows"] > 0:
        print(
            "\n⚠️  No new rows in 24h — parsers may be stuck, scout on pause, "
            "or all sources deduped. Check: pm2 logs parserclients | tail -80"
        )

    if settings.xhs_enabled:
        print("\n🇨🇳 XHS (小红书): Playwright often blocked on VPS.")
        print("   China leads also come via Google Radar site:xiaohongshu.com")

    await db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
