from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Awaitable, Callable, Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from googlesearch import search as google_search

from config import (
    GOOGLE_BLOCKED_URL_PARTS,
    GOOGLE_RADAR_KEYWORDS,
    GOOGLE_RADAR_PRIORITY_QUERIES,
    GOOGLE_TARGET_SITES,
    KEYWORDS_XHS,
    XHS_TRENDING_HASHTAGS,
    get_settings,
)
from filters import is_blocked_radar_url, passes_google_filter
from models import LeadSource, RawPost

logger = logging.getLogger(__name__)

PostHandler = Callable[[RawPost], Awaitable[None]]

# Hard-coded 10 s delay between every search / fetch request
REQUEST_DELAY_SECONDS = 10.0

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_WS_RE = re.compile(r"\s+")


class SearchProvider(str, Enum):
    GOOGLE = "google"
    DUCKDUCKGO = "duckduckgo"


class GoogleBlockedError(Exception):
    """Raised when Google returns 429 or CAPTCHA."""


def _is_google_blocked(exc: BaseException) -> bool:
    msg = str(exc).lower()
    blocked_signals = (
        "429",
        "too many requests",
        "captcha",
        "unusual traffic",
        "blocked",
        "rate limit",
    )
    return any(signal in msg for signal in blocked_signals)


class GoogleRadarParser:
    """
    Web radar: site: queries via Google, with automatic fallback to DuckDuckGo
    when Google rate-limits or shows CAPTCHA.
    """

    def __init__(self, on_post: PostHandler) -> None:
        self._settings = get_settings()
        self._on_post = on_post
        self._seen_urls: set[str] = set()
        self._http: Optional[httpx.AsyncClient] = None
        self._provider = SearchProvider.GOOGLE
        self._google_cooldown_until: float = 0.0
        self._query_offset = 0

    def _build_queries(self) -> list[str]:
        after_date = (
            datetime.now(timezone.utc)
            - timedelta(hours=self._settings.google_recency_hours)
        ).strftime("%Y-%m-%d")

        queries: list[str] = []

        # Priority EN sources — always polled first (HN, Contra, X, IH)
        for q in GOOGLE_RADAR_PRIORITY_QUERIES:
            queries.append(f"{q} after:{after_date}")

        for site in GOOGLE_TARGET_SITES:
            for keyword in GOOGLE_RADAR_KEYWORDS:
                queries.append(f'site:{site} "{keyword}" after:{after_date}')

        for hashtag in XHS_TRENDING_HASHTAGS:
            tag = hashtag.lstrip("#")
            queries.append(f'site:xiaohongshu.com "{tag}" after:{after_date}')

        for keyword in KEYWORDS_XHS:
            queries.append(f'site:xiaohongshu.com "{keyword}" after:{after_date}')

        return queries

    @staticmethod
    def _url_to_external_id(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()[:32]

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            return urlparse(url).netloc or "unknown"
        except Exception:
            return "unknown"

    def _search_google_sync(self, query: str) -> list[str]:
        urls: list[str] = []
        try:
            for url in google_search(
                query,
                num_results=self._settings.google_results_per_query,
                sleep_interval=REQUEST_DELAY_SECONDS,
                advanced=False,
            ):
                if url and url not in self._seen_urls:
                    urls.append(url)
        except Exception as exc:
            if _is_google_blocked(exc):
                raise GoogleBlockedError(str(exc)) from exc
            logger.error("Google search error for '%s': %s", query[:60], exc)
        return urls

    def _search_ddg_sync(self, query: str) -> list[str]:
        """Fallback search via ddgs (formerly duckduckgo_search)."""
        urls: list[str] = []
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS  # noqa: F401 — legacy name

            with DDGS() as ddgs:
                results = ddgs.text(
                    query,
                    max_results=self._settings.google_results_per_query,
                )
                for item in results:
                    href = item.get("href") or item.get("link")
                    if href and href not in self._seen_urls:
                        urls.append(href)
        except Exception as exc:
            logger.error("DuckDuckGo search error for '%s': %s", query[:60], exc)
        return urls

    def _activate_ddg_fallback(self, reason: str) -> None:
        if self._provider != SearchProvider.DUCKDUCKGO:
            logger.warning(
                "Switching search provider Google → DuckDuckGo (%s)", reason
            )
        self._provider = SearchProvider.DUCKDUCKGO
        # Cool down Google for 1 hour before retrying
        self._google_cooldown_until = time.monotonic() + 3600

    def _maybe_restore_google(self) -> None:
        if (
            self._provider == SearchProvider.DUCKDUCKGO
            and time.monotonic() >= self._google_cooldown_until
        ):
            logger.info("Google cooldown expired — retrying Google search")
            self._provider = SearchProvider.GOOGLE

    async def _search(self, query: str) -> list[str]:
        """Search with active provider; auto-fallback on Google block."""
        self._maybe_restore_google()
        loop = asyncio.get_running_loop()

        if self._provider == SearchProvider.GOOGLE:
            try:
                urls = await loop.run_in_executor(None, self._search_google_sync, query)
                await asyncio.sleep(REQUEST_DELAY_SECONDS)
                return urls
            except GoogleBlockedError as exc:
                self._activate_ddg_fallback(str(exc))
            except Exception as exc:
                if _is_google_blocked(exc):
                    self._activate_ddg_fallback(str(exc))
                else:
                    logger.error("Google search failed: %s", exc)
                    await asyncio.sleep(REQUEST_DELAY_SECONDS)
                    return []

        urls = await loop.run_in_executor(None, self._search_ddg_sync, query)
        await asyncio.sleep(REQUEST_DELAY_SECONDS)
        return urls

    async def _fetch_page_text(self, url: str) -> Optional[str]:
        assert self._http is not None
        try:
            response = await self._http.get(
                url,
                follow_redirects=True,
                timeout=self._settings.google_fetch_timeout,
            )

            if response.status_code == 429:
                self._activate_ddg_fallback("HTTP 429 on page fetch")
                return None

            if response.status_code >= 400:
                logger.debug("HTTP %d for %s", response.status_code, url)
                return None

            if "captcha" in response.text.lower()[:2000]:
                self._activate_ddg_fallback("CAPTCHA on page fetch")
                return None

            soup = BeautifulSoup(response.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                tag.decompose()

            parts: list[str] = []
            meta_desc = soup.find("meta", attrs={"name": "description"})
            if meta_desc and meta_desc.get("content"):
                parts.append(meta_desc["content"])

            og_desc = soup.find("meta", property="og:description")
            if og_desc and og_desc.get("content"):
                parts.append(og_desc["content"])

            body_text = soup.get_text(separator=" ", strip=True)
            if body_text:
                parts.append(body_text[:3000])

            combined = _WS_RE.sub(" ", " ".join(parts)).strip()
            return combined if len(combined) >= 40 else None

        except httpx.TimeoutException:
            logger.debug("Timeout fetching %s", url)
        except Exception as exc:
            logger.debug("Fetch error for %s: %s", url, exc)
        return None

    async def _process_url(self, url: str, query: str) -> None:
        if url in self._seen_urls:
            return
        self._seen_urls.add(url)

        if is_blocked_radar_url(url, GOOGLE_BLOCKED_URL_PARTS):
            logger.debug("Radar: blocked job-board URL %s", url[:80])
            return

        text = await self._fetch_page_text(url)
        await asyncio.sleep(REQUEST_DELAY_SECONDS)

        if not text:
            logger.debug("No extractable text: %s", url)
            return

        if not passes_google_filter(text):
            logger.debug(
                "Radar: pre-filter rejected %s (query: %s)",
                url[:60],
                query[:40],
            )
            return

        domain = self._extract_domain(url)
        post = RawPost(
            external_id=self._url_to_external_id(url),
            source=LeadSource.GOOGLE,
            text=text,
            author=domain,
            contact=url,
            timestamp=datetime.now(timezone.utc),
        )
        logger.info(
            "Radar [%s] candidate from %s (query: %s)",
            self._provider.value,
            domain,
            query[:60],
        )
        await self._on_post(post)

    async def poll_recent(self) -> None:
        if not self._http:
            return

        all_queries = self._build_queries()
        batch_size = self._settings.google_max_queries_per_poll
        start = self._query_offset % max(len(all_queries), 1)
        end = start + batch_size
        queries = all_queries[start:end]
        if end > len(all_queries):
            queries = all_queries[start:] + all_queries[: end - len(all_queries)]
        self._query_offset = (start + batch_size) % max(len(all_queries), 1)

        logger.info(
            "Radar poll — %d/%d queries via %s (delay=%ds)",
            len(queries),
            len(all_queries),
            self._provider.value,
            int(REQUEST_DELAY_SECONDS),
        )

        urls_found = 0
        for query in queries:
            try:
                urls = await self._search(query)
                urls_found += len(urls)
                logger.info(
                    "Radar [%s] '%s' → %d URL(s)",
                    self._provider.value,
                    query[:70],
                    len(urls),
                )
                for url in urls:
                    await self._process_url(url, query)
            except Exception as exc:
                logger.error("Radar query failed: %s — %s", query[:60], exc)
                await asyncio.sleep(REQUEST_DELAY_SECONDS)

        logger.info("Radar poll done — %d URL(s) this batch", urls_found)

    async def start(self) -> None:
        if not self._settings.google_radar_enabled:
            logger.info("Google radar disabled via config")
            return

        self._http = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        logger.info(
            "Radar ready — provider=%s, delay=%ds, sites=%d",
            self._provider.value,
            int(REQUEST_DELAY_SECONDS),
            len(GOOGLE_TARGET_SITES),
        )

    @property
    def is_active(self) -> bool:
        return self._http is not None

    async def stop(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None
