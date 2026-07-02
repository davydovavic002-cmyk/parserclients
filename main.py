from __future__ import annotations

import asyncio
import logging
import sys
from typing import Optional, Protocol, runtime_checkable

from ai_classifier import qualify_lead
from behance_parser import BehanceParser
from boards_parser import BoardsParser
from config import get_settings
from db import LeadDatabase
from google_radar_parser import GoogleRadarParser
from habr_parser import HabrParser
from models import AIStatus, RawPost
from naver_parser import NaverParser
from reddit_parser import RedditParser
from telegram_bot import (
    NotificationBot,
    is_scout_paused,
    send_lead_notification,
    start_notification_bot,
)
from tg_parser import TelegramParser
from vk_parser import VKParser
from xiaohongshu_parser import XiaohongshuParser

logger = logging.getLogger(__name__)


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


@runtime_checkable
class LeadSourceParser(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def poll_recent(self) -> None: ...
    @property
    def is_active(self) -> bool: ...


class LeadPipeline:
    """AI classification + DB persistence + real-time console output."""

    def __init__(self, db: LeadDatabase) -> None:
        self._db = db
        self._settings = get_settings()
        self._lock = asyncio.Lock()

    async def process_post(self, post: RawPost) -> None:
        """
        Pipeline order (cost-safe):
          1. SQLite dedup by external_id — skip BEFORE OpenAI
          2. insert_lead
          3. qualify_lead (GPT) only for new rows
        """
        async with self._lock:
            if await self._db.lead_exists(post.external_id, post.source):
                logger.debug(
                    "Duplicate [%s] %s — skipped before AI",
                    post.source.value,
                    post.external_id,
                )
                return

            inserted = await self._db.insert_lead(post)
            if not inserted:
                logger.debug(
                    "Duplicate [%s] %s — race skip before AI",
                    post.source.value,
                    post.external_id,
                )
                return

            logger.info(
                "New lead [%s] %s — AI check",
                post.source.value,
                post.external_id,
            )

            if not self._settings.enable_ai_classifier:
                await self._db.update_lead_ai(
                    post.external_id,
                    post.source,
                    AIStatus.QUALIFIED,
                    reason="AI disabled",
                    summary=post.text[:200],
                )
                self._print_lead(post, post.text[:200])
                return

            try:
                result = await qualify_lead(post.text)
            except Exception as exc:
                logger.exception("AI classify error: %s", exc)
                await self._db.update_lead_ai(
                    post.external_id,
                    post.source,
                    AIStatus.REJECTED,
                    reason=f"AI error: {exc}",
                )
                return

            status = AIStatus.QUALIFIED if result.is_lead else AIStatus.REJECTED
            await self._db.update_lead_ai(
                post.external_id,
                post.source,
                status,
                reason=result.reason,
                summary=result.summary,
            )

            if result.is_lead:
                summary = result.summary or result.reason
                self._print_lead(post, summary)
                lead_id = await self._db.get_lead_id(post.external_id, post.source)
                link = post.contact or "—"
                await send_lead_notification(
                    {
                        "lead_id": lead_id,
                        "source": post.source.value,
                        "text": post.text,
                        "contact": post.contact or post.author or "—",
                        "summary": summary,
                        "link": link,
                        "reason": result.reason,
                    }
                )
            else:
                logger.info("Rejected: %s — %s", post.external_id, result.reason)

    @staticmethod
    def _print_lead(post: RawPost, summary: str) -> None:
        bar = "=" * 60
        print(f"\n{bar}\n✅ QUALIFIED LEAD\n{bar}")
        print(f"Source:  {post.source.value}")
        print(f"Author:  {post.author}")
        print(f"Contact: {post.contact or 'N/A'}")
        print(f"Time:    {post.timestamp.isoformat()}")
        print(f"Summary: {summary}")
        preview = post.text[:500] + ("..." if len(post.text) > 500 else "")
        print(f"Text:\n{preview}\n{bar}\n")


async def tg_discovery_loop(tg: TelegramParser) -> None:
    interval = get_settings().tg_discovery_interval_seconds
    while True:
        try:
            await tg.run_discovery_cycle()
        except Exception as exc:
            logger.exception("TG discovery error: %s", exc)
        await asyncio.sleep(interval)


async def _safe_poll(name: str, parser: LeadSourceParser) -> None:
    """Wrap poll_recent so one parser failure never crashes the cycle."""
    try:
        await parser.poll_recent()
    except Exception as exc:
        logger.exception("%s poll failed (isolated, continuing): %s", name, exc)


async def run_forever(
    parsers: list[tuple[str, LeadSourceParser]],
    notify_bot: Optional[NotificationBot] = None,
) -> None:
    interval = get_settings().poll_interval_seconds

    while True:
        if is_scout_paused():
            if notify_bot:
                notify_bot.set_active_parsers([n for n, _ in parsers])
            logger.info("Scout paused via bot — sleep 30 s")
            await asyncio.sleep(30)
            continue

        names = [n for n, _ in parsers]
        if notify_bot:
            notify_bot.set_active_parsers(names)
        logger.info("── Poll cycle: %s ──", ", ".join(names))

        results = await asyncio.gather(
            *[_safe_poll(name, parser) for name, parser in parsers],
            return_exceptions=True,
        )
        for (name, _), result in zip(parsers, results):
            if isinstance(result, Exception):
                logger.error("%s poll raised: %s", name, result)

        logger.info("Cycle done — sleep %d s", interval)
        await asyncio.sleep(interval)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    db = LeadDatabase(settings.db_path)
    await db.connect()

    pipeline = LeadPipeline(db)
    parsers: list[tuple[str, LeadSourceParser]] = []
    bg_tasks: list[asyncio.Task] = []

    notify_bot = await start_notification_bot(db)
    if notify_bot:
        bg_tasks.append(asyncio.create_task(notify_bot.run_polling()))

    if settings.telegram_api_id and settings.telegram_api_hash:
        tg = TelegramParser(db=db, on_post=pipeline.process_post)
        await tg.start()
        if tg.is_active:
            parsers.append(("Telegram", tg))
            bg_tasks.append(asyncio.create_task(tg_discovery_loop(tg)))

    reddit = RedditParser(on_post=pipeline.process_post)
    await reddit.start()
    if reddit.is_active:
        parsers.append(("Reddit", reddit))

    vk = VKParser(on_post=pipeline.process_post)
    await vk.start()
    if vk.is_active:
        parsers.append(("VK", vk))

    xhs = XiaohongshuParser(on_post=pipeline.process_post)
    await xhs.start()
    if xhs.is_active:
        parsers.append(("XHS", xhs))

    boards = BoardsParser(on_post=pipeline.process_post)
    await boards.start()
    if boards.is_active:
        parsers.append(("Boards", boards))

    naver = NaverParser(on_post=pipeline.process_post)
    await naver.start()
    if naver.is_active:
        parsers.append(("Naver", naver))

    habr = HabrParser(on_post=pipeline.process_post)
    await habr.start()
    if habr.is_active:
        parsers.append(("Habr", habr))

    behance = BehanceParser(on_post=pipeline.process_post)
    await behance.start()
    if behance.is_active:
        parsers.append(("Behance", behance))

    radar = GoogleRadarParser(on_post=pipeline.process_post)
    await radar.start()
    if radar.is_active:
        parsers.append(("GoogleRadar", radar))

    if not parsers:
        if notify_bot:
            logger.warning(
                "No source parsers active — running notification bot only "
                "(fill .env or authorize Telegram session)"
            )
        else:
            logger.error(
                "No parsers active — fill .env credentials or enable Playwright parsers"
            )
            await db.close()
            return

    logger.info("Active parsers: %s", [name for name, _ in parsers] or ["(none)"])
    if notify_bot:
        notify_bot.set_active_parsers([name for name, _ in parsers])

    try:
        await run_forever(parsers, notify_bot=notify_bot)
    except asyncio.CancelledError:
        logger.info("Shutdown requested")
    finally:
        for t in bg_tasks:
            t.cancel()
        if bg_tasks:
            await asyncio.gather(*bg_tasks, return_exceptions=True)
        if notify_bot:
            await notify_bot.stop()
        for _, p in parsers:
            await p.stop()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted")
