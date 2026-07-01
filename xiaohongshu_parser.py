from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from browser_stealth import (
    create_stealth_browser,
    create_stealth_context,
    new_stealth_page,
)
from config import KEYWORDS_XHS, XHS_TRENDING_HASHTAGS, get_settings
from filters import passes_xhs_filter
from models import LeadSource, RawPost

logger = logging.getLogger(__name__)

PostHandler = Callable[[RawPost], Awaitable[None]]

XHS_SEARCH_BASE = "https://www.xiaohongshu.com/search_result"


@dataclass
class XhsNote:
    note_id: str
    text: str
    url: str
    hashtag: str


class XiaohongshuParser:
    """Playwright scraper for Xiaohongshu hashtag / keyword search pages."""

    def __init__(self, on_post: PostHandler) -> None:
        self._settings = get_settings()
        self._on_post = on_post
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._seen_ids: set[str] = set()

    def _hashtag_urls(self) -> list[tuple[str, str]]:
        urls: list[tuple[str, str]] = []
        for tag in XHS_TRENDING_HASHTAGS:
            keyword = tag.lstrip("#")
            urls.append((tag, f"{XHS_SEARCH_BASE}?keyword={keyword}"))
        return urls

    async def _random_delay(self) -> None:
        delay = random.uniform(
            self._settings.boards_delay_min,
            self._settings.boards_delay_max,
        )
        await asyncio.sleep(delay)

    async def _human_scroll(self, page: Page) -> None:
        logger.debug("XHS: scrolling feed")
        for _ in range(random.randint(3, 5)):
            await page.mouse.wheel(0, random.randint(350, 800))
            await asyncio.sleep(random.uniform(0.7, 1.8))

    @staticmethod
    def _note_id_from_href(href: str, fallback: str) -> str:
        match = re.search(r"/explore/([a-f0-9]+)", href)
        if match:
            return match.group(1)
        return hashlib.sha256(fallback.encode()).hexdigest()[:32]

    async def _extract_notes(self, page: Page, hashtag: str) -> list[XhsNote]:
        notes: list[XhsNote] = []

        selectors = [
            "section.note-item",
            "div.feeds-page div.note",
            "a.cover.ld",
            "div.note-item",
            "[class*='note']",
        ]

        for selector in selectors:
            cards = await page.query_selector_all(selector)
            if not cards:
                continue

            logger.info("XHS [%s]: selector '%s' → %d element(s)", hashtag, selector, len(cards))

            for i, card in enumerate(cards[:25]):
                try:
                    text = (await card.inner_text()).strip()
                    href = await card.get_attribute("href") or ""
                    if len(text) < 10:
                        continue

                    note_id = self._note_id_from_href(href, f"{hashtag}-{i}-{text[:40]}")
                    full_url = (
                        f"https://www.xiaohongshu.com{href}"
                        if href.startswith("/")
                        else href or f"{XHS_SEARCH_BASE}?keyword={hashtag}"
                    )

                    notes.append(
                        XhsNote(
                            note_id=note_id,
                            text=text,
                            url=full_url,
                            hashtag=hashtag,
                        )
                    )
                except Exception as exc:
                    logger.debug("XHS card parse error: %s", exc)

            if notes:
                break

        if not notes:
            body = await page.inner_text("body")
            for kw in KEYWORDS_XHS:
                idx = body.find(kw)
                if idx >= 0:
                    snippet = body[max(0, idx - 100) : idx + 150].strip()
                    notes.append(
                        XhsNote(
                            note_id=hashlib.sha256(snippet.encode()).hexdigest()[:32],
                            text=snippet,
                            url=f"{XHS_SEARCH_BASE}?keyword={hashtag.lstrip('#')}",
                            hashtag=hashtag,
                        )
                    )

        return notes

    async def _scrape_hashtag(self, hashtag: str, url: str) -> list[XhsNote]:
        assert self._context is not None
        page = await new_stealth_page(self._context)
        notes: list[XhsNote] = []

        try:
            logger.info("XHS: opening hashtag [%s] %s", hashtag, url)
            response = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)

            if response and response.status == 429:
                logger.error("XHS [%s]: HTTP 429 — skipping", hashtag)
                return []

            body_lower = (await page.inner_text("body")).lower()
            if "captcha" in body_lower[:2500] or "验证" in body_lower[:2500]:
                logger.error("XHS [%s]: CAPTCHA / verification — skipping", hashtag)
                return []

            await asyncio.sleep(self._settings.xhs_page_delay)
            await self._human_scroll(page)
            await self._random_delay()

            notes = await self._extract_notes(page, hashtag)
            logger.info("XHS [%s]: collected %d note(s)", hashtag, len(notes))

        except Exception as exc:
            logger.exception("XHS scrape [%s] failed: %s", hashtag, exc)
        finally:
            await page.close()

        return notes

    async def _process_note(self, note: XhsNote) -> None:
        if note.note_id in self._seen_ids:
            return
        self._seen_ids.add(note.note_id)

        if not passes_xhs_filter(note.text):
            logger.debug("XHS [%s]: keyword filter rejected", note.hashtag)
            return

        logger.info("XHS [%s]: candidate — pipeline (len=%d)", note.hashtag, len(note.text))

        post = RawPost(
            external_id=note.note_id,
            source=LeadSource.XHS,
            text=note.text,
            author=f"xhs_{note.hashtag.lstrip('#')}",
            contact=note.url,
            timestamp=datetime.now(timezone.utc),
        )
        await self._on_post(post)

    async def poll_recent(self) -> None:
        if not self._context:
            return

        logger.info("XHS: polling %d hashtag(s)", len(XHS_TRENDING_HASHTAGS))

        for hashtag, url in self._hashtag_urls():
            try:
                notes = await self._scrape_hashtag(hashtag, url)
                for note in notes:
                    try:
                        await self._process_note(note)
                    except Exception as exc:
                        logger.error("XHS note process error: %s", exc)
            except Exception as exc:
                logger.exception("XHS poll [%s] error: %s", hashtag, exc)

            await asyncio.sleep(self._settings.xhs_poll_delay)

        logger.info("XHS: poll cycle complete")

    async def start(self) -> None:
        if not self._settings.xhs_enabled:
            logger.info("XHS parser disabled (XHS_ENABLED=false)")
            return

        try:
            self._playwright = await async_playwright().start()
            self._browser = await create_stealth_browser(
                self._playwright, headless=True
            )
            self._context = await create_stealth_context(
                self._browser,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            logger.info(
                "XHS parser ready (stealth) — %d hashtags, %d keywords",
                len(XHS_TRENDING_HASHTAGS),
                len(KEYWORDS_XHS),
            )
        except Exception as exc:
            logger.exception("XHS parser init failed: %s", exc)
            await self.stop()

    async def stop(self) -> None:
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    @property
    def is_active(self) -> bool:
        return self._context is not None
