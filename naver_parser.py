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
    safe_close_playwright,
)
from config import KEYWORDS_KR, get_settings
from filters import passes_naver_filter
from models import LeadSource, RawPost
from naver_http import NaverHttpClient, NaverHttpSnippet

logger = logging.getLogger(__name__)

PostHandler = Callable[[RawPost], Awaitable[None]]

NAVER_SEARCH_BASE = "https://search.naver.com/search.naver"
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
    """Naver blog/cafe search — HTTP default, optional Playwright."""

    def __init__(self, on_post: PostHandler) -> None:
        self._settings = get_settings()
        self._on_post = on_post
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._http: Optional[NaverHttpClient] = None
        self._engine: str = "off"
        self._seen_ids: set[str] = set()
        self._status_detail: str = "не запущен"
        self._keyword_offset: int = 0

    @property
    def status_detail(self) -> str:
        return self._status_detail

    def _poll_keywords(self) -> list[str]:
        batch = max(1, self._settings.naver_keywords_per_poll)
        kws = list(KEYWORDS_KR)
        start = self._keyword_offset % max(len(kws), 1)
        chunk = kws[start : start + batch]
        if len(chunk) < batch:
            chunk = chunk + kws[: batch - len(chunk)]
        self._keyword_offset = (start + batch) % max(len(kws), 1)
        return chunk

    def _build_search_urls(self, keyword: str) -> list[tuple[str, str]]:
        encoded = quote_plus(keyword)
        recency = (
            NAVER_RECENCY_PARAM if self._settings.naver_recency_hours <= 24 else ""
        )
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
        await asyncio.sleep(delay)

    async def _human_scroll(self, page: Page) -> None:
        for _ in range(random.randint(2, 4)):
            await page.mouse.wheel(0, random.randint(300, 700))
            await asyncio.sleep(random.uniform(0.6, 1.5))

    @staticmethod
    def _snippet_id(keyword: str, section: str, url: str) -> str:
        raw = f"{keyword}:{section}:{url}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    @staticmethod
    def _from_http(item: NaverHttpSnippet) -> NaverSnippet:
        return NaverSnippet(
            keyword=item.keyword,
            section=item.section,
            title=item.title,
            snippet=item.snippet,
            url=item.url,
            date_hint=item.date_hint,
        )

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

    async def _scrape_section_pw(
        self, keyword: str, section: str, url: str
    ) -> list[NaverSnippet]:
        assert self._context is not None
        page = await new_stealth_page(self._context)
        results: list[NaverSnippet] = []

        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            if response and response.status == 429:
                return []
            await self._random_delay()
            await self._human_scroll(page)
            if section == "blog":
                results = await self._extract_blog_snippets(page, keyword)
            else:
                results = await self._extract_cafe_snippets(page, keyword)
        except Exception as exc:
            logger.exception("Naver PW [%s/%s]: %s", section, keyword, exc)
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
        if self._engine == "off":
            return

        keywords = self._poll_keywords()
        logger.info("Naver (%s): polling %d keyword(s)", self._engine, len(keywords))

        for keyword in keywords:
            try:
                if self._engine == "http":
                    assert self._http is not None
                    items, status = await self._http.search_keyword(keyword)
                    self._status_detail = f"HTTP, {status}"
                    for http_item in items:
                        await self._process_snippet(self._from_http(http_item))
                else:
                    for section, url in self._build_search_urls(keyword):
                        snippets = await self._scrape_section_pw(keyword, section, url)
                        for snippet in snippets:
                            await self._process_snippet(snippet)
                        await self._random_delay()
            except Exception as exc:
                logger.exception("Naver poll [%s]: %s", keyword, exc)
            await self._random_delay()

        logger.info("Naver: poll cycle complete")

    async def _start_http(self) -> None:
        self._http = NaverHttpClient(
            recency_hours=self._settings.naver_recency_hours
        )
        await self._http.start()
        self._engine = "http"
        self._status_detail = (
            f"HTTP blog/cafe KR, {len(KEYWORDS_KR)} keywords "
            f"({self._settings.naver_keywords_per_poll}/poll)"
        )
        logger.info("Naver parser ready (HTTP) — %s", self._status_detail)

    async def _start_playwright(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await create_stealth_browser(
            self._playwright, headless=True, low_memory=True
        )
        self._context = await create_stealth_context(
            self._browser,
            locale="ko-KR",
            timezone_id="Asia/Seoul",
        )
        self._engine = "playwright"
        self._status_detail = f"Playwright blog/cafe KR, recency={self._settings.naver_recency_hours}h"
        logger.info("Naver parser ready (Playwright)")

    async def start(self) -> None:
        if not self._settings.naver_enabled:
            self._status_detail = "NAVER_ENABLED=false"
            logger.info("Naver parser disabled")
            return

        if self._settings.naver_playwright:
            try:
                await self._start_playwright()
                return
            except Exception as exc:
                logger.exception("Naver Playwright failed, HTTP fallback: %s", exc)
                await self._stop_playwright()

        try:
            await self._start_http()
        except Exception as exc:
            self._status_detail = f"init failed: {exc}"
            logger.exception("Naver HTTP init failed: %s", exc)
            await self.stop()

    async def _stop_playwright(self) -> None:
        await safe_close_playwright(
            playwright=self._playwright,
            browser=self._browser,
            context=self._context,
        )
        self._playwright = None
        self._browser = None
        self._context = None

    async def stop(self) -> None:
        if self._http:
            await self._http.stop()
            self._http = None
        await self._stop_playwright()
        self._engine = "off"

    @property
    def is_active(self) -> bool:
        return self._engine != "off"
