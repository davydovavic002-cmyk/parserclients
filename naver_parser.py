from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional
from urllib.parse import quote_plus

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from browser_stealth import (
    create_stealth_browser,
    create_stealth_context,
    new_stealth_page,
)
from config import KEYWORDS_KR, get_settings
from filters import passes_naver_filter
from models import LeadSource, RawPost

logger = logging.getLogger(__name__)

PostHandler = Callable[[RawPost], Awaitable[None]]

NAVER_SEARCH_BASE = "https://search.naver.com/search.naver"

# Naver «last 24 hours» filter parameter
NAVER_RECENCY_PARAM = "nso=so:dd,p:1d"


@dataclass
class NaverSnippet:
    keyword: str
    section: str
    title: str
    snippet: str
    url: str
    date_hint: str


class NaverParser:
    """
    Playwright scraper for Naver Blog + Cafe search results (KR keywords, 24h).
    """

    def __init__(self, on_post: PostHandler) -> None:
        self._settings = get_settings()
        self._on_post = on_post
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._seen_ids: set[str] = set()

    def _build_search_urls(self, keyword: str) -> list[tuple[str, str]]:
        encoded = quote_plus(keyword)
        recency = NAVER_RECENCY_PARAM if self._settings.naver_recency_hours <= 24 else ""
        suffix = f"&{recency}" if recency else ""

        return [
            (
                "blog",
                f"{NAVER_SEARCH_BASE}?where=blog&query={encoded}&sort=date{suffix}",
            ),
            (
                "cafe",
                f"{NAVER_SEARCH_BASE}?where=article&query={encoded}&sort=date{suffix}",
            ),
        ]

    async def _random_delay(self) -> None:
        delay = random.uniform(
            self._settings.naver_delay_min,
            self._settings.naver_delay_max,
        )
        logger.debug("Naver: delay %.1f s", delay)
        await asyncio.sleep(delay)

    async def _human_scroll(self, page: Page) -> None:
        for _ in range(random.randint(2, 4)):
            await page.mouse.wheel(0, random.randint(300, 700))
            await asyncio.sleep(random.uniform(0.6, 1.5))

    @staticmethod
    def _snippet_id(keyword: str, section: str, url: str) -> str:
        raw = f"{keyword}:{section}:{url}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    async def _extract_blog_snippets(
        self, page: Page, keyword: str
    ) -> list[NaverSnippet]:
        snippets: list[NaverSnippet] = []
        items = await page.query_selector_all(
            "li.bx, div.total_wrap, div.api_subject_bx"
        )

        for item in items[:20]:
            try:
                title_el = await item.query_selector(
                    "a.api_txt_lines.total_tit, a.title_link, a.link_tit"
                )
                desc_el = await item.query_selector(
                    "div.dsc_wrap, div.api_txt_lines.dsc, div.total_dsc"
                )
                date_el = await item.query_selector(
                    "span.sub_time, span.sub_txt.sub_time, span.date"
                )

                if not title_el:
                    continue

                title = (await title_el.inner_text()).strip()
                href = await title_el.get_attribute("href") or ""
                snippet_text = (
                    (await desc_el.inner_text()).strip() if desc_el else ""
                )
                date_hint = (
                    (await date_el.inner_text()).strip() if date_el else ""
                )

                if len(title) < 5:
                    continue

                snippets.append(
                    NaverSnippet(
                        keyword=keyword,
                        section="blog",
                        title=title,
                        snippet=snippet_text,
                        url=href,
                        date_hint=date_hint,
                    )
                )
            except Exception:
                continue

        return snippets

    async def _extract_cafe_snippets(
        self, page: Page, keyword: str
    ) -> list[NaverSnippet]:
        snippets: list[NaverSnippet] = []
        items = await page.query_selector_all(
            "li.bx, div.total_wrap, ul.lst_total li"
        )

        for item in items[:20]:
            try:
                title_el = await item.query_selector(
                    "a.api_txt_lines.total_tit, a.title_link, a.link_tit"
                )
                desc_el = await item.query_selector(
                    "div.dsc_wrap, div.api_txt_lines.dsc"
                )
                date_el = await item.query_selector("span.sub_time, span.date")

                if not title_el:
                    continue

                title = (await title_el.inner_text()).strip()
                href = await title_el.get_attribute("href") or ""
                snippet_text = (
                    (await desc_el.inner_text()).strip() if desc_el else ""
                )
                date_hint = (
                    (await date_el.inner_text()).strip() if date_el else ""
                )

                snippets.append(
                    NaverSnippet(
                        keyword=keyword,
                        section="cafe",
                        title=title,
                        snippet=snippet_text,
                        url=href,
                        date_hint=date_hint,
                    )
                )
            except Exception:
                continue

        return snippets

    async def _scrape_section(
        self, keyword: str, section: str, url: str
    ) -> list[NaverSnippet]:
        assert self._context is not None
        page = await new_stealth_page(self._context)
        results: list[NaverSnippet] = []

        try:
            logger.info("Naver: searching [%s/%s] %s", section, keyword, url[:80])
            response = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)

            if response and response.status == 429:
                logger.error("Naver: HTTP 429 — skipping")
                return []

            body = (await page.inner_text("body")).lower()
            if "captcha" in body[:2000]:
                logger.error("Naver: CAPTCHA — skipping")
                return []

            await self._random_delay()
            await self._human_scroll(page)

            if section == "blog":
                results = await self._extract_blog_snippets(page, keyword)
            else:
                results = await self._extract_cafe_snippets(page, keyword)

            logger.info(
                "Naver [%s/%s]: found %d snippet(s)",
                section,
                keyword,
                len(results),
            )

        except Exception as exc:
            logger.exception("Naver scrape failed [%s/%s]: %s", section, keyword, exc)
        finally:
            await page.close()

        return results

    async def _process_snippet(self, item: NaverSnippet) -> None:
        full_text = (
            f"Title: {item.title}\n"
            f"Section: {item.section}\n"
            f"Date: {item.date_hint}\n"
            f"Snippet: {item.snippet}"
        )

        ext_id = self._snippet_id(item.keyword, item.section, item.url)
        if ext_id in self._seen_ids:
            return
        self._seen_ids.add(ext_id)

        if not passes_naver_filter(full_text):
            logger.debug("Naver: pre-filter rejected '%s'", item.title[:60])
            return

        logger.info("Naver: candidate '%s' — pipeline", item.title[:80])

        post = RawPost(
            external_id=ext_id,
            source=LeadSource.NAVER,
            text=full_text,
            author=f"naver_{item.section}",
            contact=item.url or None,
            timestamp=datetime.now(timezone.utc),
        )
        await self._on_post(post)

    async def poll_recent(self) -> None:
        if not self._context:
            return

        logger.info("Naver: polling %d keyword(s)", len(KEYWORDS_KR))

        for keyword in KEYWORDS_KR:
            for section, url in self._build_search_urls(keyword):
                try:
                    snippets = await self._scrape_section(keyword, section, url)
                    for snippet in snippets:
                        try:
                            await self._process_snippet(snippet)
                        except Exception as exc:
                            logger.error("Naver snippet error: %s", exc)
                except Exception as exc:
                    logger.exception(
                        "Naver poll error [%s/%s]: %s", section, keyword, exc
                    )
                await self._random_delay()

        logger.info("Naver: poll cycle complete")

    async def start(self) -> None:
        if not self._settings.naver_enabled:
            logger.info("Naver parser disabled (NAVER_ENABLED=false)")
            return

        try:
            self._playwright = await async_playwright().start()
            self._browser = await create_stealth_browser(
                self._playwright, headless=True
            )
            self._context = await create_stealth_context(
                self._browser,
                locale="ko-KR",
                timezone_id="Asia/Seoul",
            )
            logger.info(
                "Naver parser ready (stealth) — %d KR keywords, recency=%dh",
                len(KEYWORDS_KR),
                self._settings.naver_recency_hours,
            )
        except Exception as exc:
            logger.exception("Naver parser init failed: %s", exc)
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
