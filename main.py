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
from filters import is_cms_only_scope, passes_prefilter
from google_radar_parser import GoogleRadarParser
from models import AIStatus, LeadRecord, LeadSource, RawPost
from naver_parser import NaverParser
from parser_status import set_parser_status
from quality import (
    approve_unknown_budget_if_eligible,
    passes_ai_quality_gate,
    should_skip_by_age,
)
from reddit_parser import RedditParser
from telegram_bot import (
    NotificationBot,
    is_scout_paused,
    send_lead_notification,
    start_notification_bot,
)
from tg_links import resolve_tg_lead_urls
from tg_parser import TelegramParser
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
                "Pipeline: new post [%s] %s — AI check",
                post.source.value,
                post.external_id,
            )

            age_reason = should_skip_by_age(
                post, self._settings.max_post_age_hours
            )
            if age_reason:
                await self._db.update_lead_ai(
                    post.external_id,
                    post.source,
                    AIStatus.REJECTED,
                    reason=f"Stale: {age_reason}",
                )
                logger.info(
                    "Rejected (stale): %s — %s", post.external_id, age_reason
                )
                return

            if is_cms_only_scope(post.text):
                await self._db.update_lead_ai(
                    post.external_id,
                    post.source,
                    AIStatus.REJECTED,
                    reason="CMS-only (WordPress/Tilda/Webflow и др.)",
                )
                logger.info(
                    "Rejected (CMS-only): %s", post.external_id
                )
                return

            if not self._settings.enable_ai_classifier:
                await self._db.update_lead_ai(
                    post.external_id,
                    post.source,
                    AIStatus.QUALIFIED,
                    reason="AI disabled",
                    summary=post.text[:200],
                )
                from ai_classifier import AIQualificationResult
                from models import EstimatedBudget, LeadApprovalStatus

                mock = AIQualificationResult(
                    status=LeadApprovalStatus.APPROVED,
                    score=70,
                    estimated_budget=EstimatedBudget.UNKNOWN,
                    summary=post.text[:200],
                    why_it_fits="AI disabled",
                )
                await self._notify_qualified(post, mock)
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

            result = approve_unknown_budget_if_eligible(
                result, min_score=self._settings.min_lead_score
            )

            status = AIStatus.QUALIFIED if result.is_lead else AIStatus.REJECTED
            reason = result.reason

            if result.is_lead:
                ok, gate_reason = passes_ai_quality_gate(
                    result,
                    min_score=self._settings.min_lead_score,
                    reject_low_budget=self._settings.reject_low_budget,
                )
                if not ok:
                    status = AIStatus.REJECTED
                    reason = f"Quality gate: {gate_reason}"

            await self._db.update_lead_ai(
                post.external_id,
                post.source,
                status,
                reason=reason,
                summary=result.summary,
            )

            if status == AIStatus.QUALIFIED:
                await self._notify_qualified(post, result)
            else:
                logger.info(
                    "Rejected: %s — score=%d | %s",
                    post.external_id,
                    result.score,
                    reason,
                )

    async def _notify_qualified(
        self, post: RawPost, result, *, lead_id: Optional[int] = None
    ) -> bool:
        summary = result.summary or result.reason
        self._print_lead(post, summary, result=result)
        if lead_id is None:
            lead_id = await self._db.get_lead_id(post.external_id, post.source)

        contact = post.contact or post.author or "—"
        link = post.contact or "—"
        if post.source == LeadSource.TELEGRAM:
            contact, link = resolve_tg_lead_urls(
                post.external_id,
                post.text,
                author=post.author,
                stored_contact=post.contact,
            )
        elif post.contact and str(post.contact).startswith(("http://", "https://")):
            link = post.contact

        sent = await send_lead_notification(
            {
                "lead_id": lead_id,
                "source": post.source.value,
                "text": post.text,
                "contact": contact,
                "summary": summary,
                "link": link,
                "reason": result.reason,
                "score": result.score,
                "estimated_budget": result.estimated_budget.value,
            }
        )
        if sent:
            await self._db.mark_lead_notified(post.external_id, post.source)
        else:
            logger.warning(
                "Lead qualified but Telegram notification failed [%s] %s",
                post.source.value,
                post.external_id,
            )
        return sent

    async def notify_qualified_record(self, record: LeadRecord) -> bool:
        """Send Telegram alert for an already-qualified DB row."""
        from ai_classifier import AIQualificationResult
        from models import EstimatedBudget, LeadApprovalStatus

        post = RawPost(
            external_id=record.external_id,
            source=record.source,
            text=record.text,
            author=record.author,
            contact=record.contact,
            timestamp=record.timestamp,
        )
        result = AIQualificationResult(
            status=LeadApprovalStatus.APPROVED,
            score=0,
            estimated_budget=EstimatedBudget.UNKNOWN,
            summary=record.summary,
            why_it_fits=record.reason or "Квалифицирован",
        )
        return await self._notify_qualified(
            post, result, lead_id=record.id
        )

    async def flush_unnotified_qualified(self) -> int:
        records = await self._db.get_unnotified_qualified_leads()
        sent = 0
        for record in records:
            try:
                if await self.notify_qualified_record(record):
                    sent += 1
                    await asyncio.sleep(0.4)
            except Exception as exc:
                logger.error("Notify qualified %s failed: %s", record.external_id, exc)
        if sent:
            logger.info("Telegram: pushed %d unnotified qualified lead(s)", sent)
        return sent

    async def reprocess_lead_record(self, record: LeadRecord) -> None:
        """Re-run Gemini for rows rejected due to API/model errors."""
        await self.rescore_gemini_error(record)

    async def rescore_gemini_error(self, record: LeadRecord) -> str:
        """
        Re-score a Gemini-failure row. Returns outcome label for logging.
        Junk google crawl rows are deleted when pre-filter rejects them.
        """
        post = RawPost(
            external_id=record.external_id,
            source=record.source,
            text=record.text,
            author=record.author,
            contact=record.contact,
            timestamp=record.timestamp,
        )

        async with self._lock:
            logger.info(
                "Pipeline: rescore [%s] %s",
                post.source.value,
                post.external_id,
            )

            if not passes_prefilter(post.text, post.source):
                deleted = await self._db.delete_lead(
                    post.external_id, post.source
                )
                logger.info(
                    "Rescore deleted (pre-filter junk) [%s] %s rows=%d",
                    post.source.value,
                    post.external_id,
                    deleted,
                )
                return "deleted_prefilter"

            if is_cms_only_scope(post.text):
                await self._db.update_lead_ai(
                    post.external_id,
                    post.source,
                    AIStatus.REJECTED,
                    reason="CMS-only (WordPress/Tilda/Webflow и др.)",
                )
                return "rejected_cms"

            age_reason = should_skip_by_age(
                post, self._settings.max_post_age_hours
            )
            if age_reason:
                await self._db.update_lead_ai(
                    post.external_id,
                    post.source,
                    AIStatus.REJECTED,
                    reason=f"Stale: {age_reason}",
                )
                return "rejected_stale"

            result = await qualify_lead(post.text)
            from ai_classifier import is_gemini_failure

            if is_gemini_failure(result):
                await self._db.update_lead_ai(
                    post.external_id,
                    post.source,
                    AIStatus.REJECTED,
                    reason=result.reason,
                )
                return "still_broken"

            result = approve_unknown_budget_if_eligible(
                result, min_score=self._settings.min_lead_score
            )

            status = AIStatus.QUALIFIED if result.is_lead else AIStatus.REJECTED
            reason = result.reason

            if result.is_lead:
                ok, gate_reason = passes_ai_quality_gate(
                    result,
                    min_score=self._settings.min_lead_score,
                    reject_low_budget=self._settings.reject_low_budget,
                )
                if not ok:
                    status = AIStatus.REJECTED
                    reason = f"Quality gate: {gate_reason}"

            await self._db.update_lead_ai(
                post.external_id,
                post.source,
                status,
                reason=reason,
                summary=result.summary,
            )

            if status == AIStatus.QUALIFIED:
                await self._notify_qualified(post, result)
                return "qualified"

            logger.info(
                "Rescore rejected: %s — score=%d | %s",
                post.external_id,
                result.score,
                reason,
            )
            return "rejected"

    @staticmethod
    def _print_lead(
        post: RawPost, summary: str, *, result: Optional[object] = None
    ) -> None:
        bar = "=" * 60
        print(f"\n{bar}\n✅ QUALIFIED LEAD\n{bar}")
        print(f"Source:  {post.source.value}")
        if result is not None and hasattr(result, "score"):
            print(f"Score:   {result.score}/100")
            if hasattr(result, "estimated_budget"):
                print(f"Budget:  {result.estimated_budget.value}")
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

    parsers: list[tuple[str, LeadSourceParser]] = []
    bg_tasks: list[asyncio.Task] = []

    notify_bot = await start_notification_bot(db)
    if notify_bot:
        bg_tasks.append(asyncio.create_task(notify_bot.run_polling()))

    pipeline = LeadPipeline(db)

    if notify_bot:
        pushed = await pipeline.flush_unnotified_qualified()
        if pushed:
            logger.info("Recovered %d lead notification(s) missed earlier", pushed)

    gemini_retries = await db.get_gemini_error_leads()
    if gemini_retries:
        logger.info(
            "Retrying %d lead(s) previously rejected by Gemini API errors",
            len(gemini_retries),
        )
        for record in gemini_retries:
            try:
                await pipeline.reprocess_lead_record(record)
            except Exception as exc:
                logger.exception("Retry failed for %s: %s", record.external_id, exc)

    if notify_bot:
        await notify_bot.send_startup_ping(["загрузка…"])

    if settings.telegram_api_id and settings.telegram_api_hash:
        tg = TelegramParser(db=db, on_post=pipeline.process_post)
        await tg.start()
        if tg.is_active:
            parsers.append(("Telegram", tg))
            bg_tasks.append(asyncio.create_task(tg_discovery_loop(tg)))
            set_parser_status("Telegram", True, tg.status_detail)
        else:
            set_parser_status("Telegram", False, tg.status_detail)
    else:
        set_parser_status(
            "Telegram",
            False,
            "нет TG_API_ID / TG_API_HASH в .env",
        )

    reddit = RedditParser(on_post=pipeline.process_post)
    await reddit.start()
    if reddit.is_active:
        parsers.append(("Reddit", reddit))
        set_parser_status("Reddit", True, "сабреддиты forhire / startups")
    else:
        set_parser_status("Reddit", False, "нет REDDIT_CLIENT_ID/SECRET в .env")

    xhs = XiaohongshuParser(on_post=pipeline.process_post)
    await xhs.start()
    if xhs.is_active:
        parsers.append(("XHS", xhs))
        set_parser_status("XHS", True, xhs.status_detail)
    else:
        reason = (
            "XHS_ENABLED=false"
            if not settings.xhs_enabled
            else "ошибка Playwright — см. pm2 logs"
        )
        set_parser_status("XHS", False, reason)

    boards = BoardsParser(on_post=pipeline.process_post)
    await boards.start()
    if boards.is_active:
        parsers.append(("Boards", boards))
        set_parser_status("Boards", True, "Upwork/Freelancer и др.")
    else:
        reason = (
            "BOARDS_ENABLED=false"
            if not settings.boards_enabled
            else "ошибка Playwright — см. pm2 logs"
        )
        set_parser_status("Boards", False, reason)

    naver = NaverParser(on_post=pipeline.process_post)
    await naver.start()
    if naver.is_active:
        parsers.append(("Naver", naver))
        set_parser_status("Naver", True, "Playwright, Naver blog/cafe KR")
    else:
        reason = (
            "NAVER_ENABLED=false"
            if not settings.naver_enabled
            else "ошибка Playwright — см. pm2 logs"
        )
        set_parser_status("Naver", False, reason)

    behance = BehanceParser(on_post=pipeline.process_post)
    await behance.start()
    if behance.is_active:
        parsers.append(("Behance", behance))
        set_parser_status("Behance", True, "Behance Joblist")
    else:
        reason = (
            "BEHANCE_ENABLED=false"
            if not settings.behance_enabled
            else "ошибка Playwright — см. pm2 logs"
        )
        set_parser_status("Behance", False, reason)

    radar = GoogleRadarParser(on_post=pipeline.process_post)
    await radar.start()
    if radar.is_active:
        parsers.append(("GoogleRadar", radar))
        set_parser_status("GoogleRadar", True, "Google/DDG site: поиск")
    else:
        set_parser_status("GoogleRadar", False, "GOOGLE_RADAR_ENABLED=false")

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
