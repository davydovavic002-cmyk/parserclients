from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional
from urllib.parse import urljoin

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from browser_stealth import (
    create_stealth_browser,
    is_playwright_connection_error,
    safe_close_playwright,
    create_stealth_context,
    new_stealth_page,
)
from config import BEHANCE_JOB_KEYWORDS, get_settings
from filters import passes_behance_filter
from models import LeadSource, RawPost

logger = logging.getLogger(__name__)

PostHandler = Callable[[RawPost], Awaitable[None]]

JOB_CARD_SELECTORS: list[str] = [
    '[data-testid="job-card"]',
    "div.JobCard",
    "article.JobCard",
    'a[href*="/joblist/"]',
    'div[class*="JobCard"]',
    'div[class*="job-card"]',
    "li.job-list-item",
]


@dataclass
class BehanceJob:
    job_id: str
    title: str
    description: str
    company: str
    url: str


class BehanceParser:
    """Behance Joblist scraper (Playwright + stealth) — UI/UX & web design roles."""

    def __init__(self, on_post: PostHandler) -> None:
        self._settings = get_settings()
        self._on_post = on_post
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._seen_ids: set[str] = set()

    async def _random_delay(self) -> None:
        delay = random.uniform(
            self._settings.behance_delay_min,
            self._settings.behance_delay_max,
        )
        logger.debug("Behance: delay %.1f s", delay)
        await asyncio.sleep(delay)

    async def _human_scroll(self, page: Page) -> None:
        for _ in range(random.randint(3, 6)):
            await page.mouse.wheel(0, random.randint(400, 900))
            await asyncio.sleep(random.uniform(0.8, 2.0))

    @staticmethod
    def _job_id(url: str, title: str) -> str:
        match = re.search(r"/joblist/(\d+)", url)
        if match:
            return match.group(1)
        return hashlib.sha256(f"{url}:{title}".encode()).hexdigest()[:32]

    @staticmethod
    def _matches_behance_role(text: str) -> bool:
        normalized = text.casefold()
        return any(kw.casefold() in normalized for kw in BEHANCE_JOB_KEYWORDS)

    async def _extract_jobs(self, page: Page, base_url: str) -> list[BehanceJob]:
        jobs: list[BehanceJob] = []

        for selector in JOB_CARD_SELECTORS:
            cards = await page.query_selector_all(selector)
            if not cards:
                continue

            logger.info("Behance: selector '%s' → %d card(s)", selector, len(cards))

            for card in cards[:30]:
                try:
                    raw_text = (await card.inner_text()).strip()
                    if len(raw_text) < 10:
                        continue

                    if not BehanceParser._matches_behance_role(raw_text):
                        continue

                    link_el = await card.query_selector("a[href]")
                    href = await link_el.get_attribute("href") if link_el else ""
                    if not href:
                        href = await card.get_attribute("href") or ""

                    full_url = urljoin(base_url, href) if href else base_url
                    lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()]
                    title = lines[0][:200]
                    description = " ".join(lines[1:])[:2000] if len(lines) > 1 else raw_text[:2000]
                    company = lines[1][:100] if len(lines) > 1 else "behance"

                    jobs.append(
                        BehanceJob(
                            job_id=BehanceParser._job_id(full_url, title),
                            title=title,
                            description=description,
                            company=company,
                            url=full_url,
                        )
                    )
                except Exception as exc:
                    logger.debug("Behance card parse error: %s", exc)

            if jobs:
                break

        if not jobs:
            anchors = await page.query_selector_all('a[href*="/joblist/"]')
            for anchor in anchors[:25]:
                try:
                    href = await anchor.get_attribute("href") or ""
                    text = (await anchor.inner_text()).strip()
                    if not text or not BehanceParser._matches_behance_role(text):
                        continue
                    full_url = urljoin(base_url, href)
                    jobs.append(
                        BehanceJob(
                            job_id=BehanceParser._job_id(full_url, text),
                            title=text[:200],
                            description=text,
                            company="behance",
                            url=full_url,
                        )
                    )
                except Exception:
                    continue

        return jobs

    async def _scrape_joblist(self) -> list[BehanceJob]:
        assert self._context is not None
        url = self._settings.behance_joblist_url
        page = await new_stealth_page(self._context)
        jobs: list[BehanceJob] = []

        try:
            logger.info("Behance: opening joblist %s", url)
            response = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

            if response and response.status == 429:
                logger.error("Behance: HTTP 429 — skipping cycle")
                return []

            body = (await page.inner_text("body")).lower()
            if "captcha" in body[:2500] or "verify" in body[:2500]:
                logger.error("Behance: CAPTCHA detected")
                return []

            await self._random_delay()
            await self._human_scroll(page)
            await self._random_delay()

            jobs = await self._extract_jobs(page, url)
            logger.info("Behance: collected %d relevant job card(s)", len(jobs))

        except Exception as exc:
            logger.exception("Behance: scrape failed: %s", exc)
        finally:
            await page.close()

        return jobs

    async def _process_job(self, job: BehanceJob) -> None:
        if job.job_id in self._seen_ids:
            return
        self._seen_ids.add(job.job_id)

        full_text = f"Title: {job.title}\nCompany: {job.company}\nDescription: {job.description}"

        if not passes_behance_filter(full_text):
            logger.debug("Behance: pre-filter rejected '%s'", job.title[:60])
            return

        logger.info("Behance: candidate '%s' — pipeline", job.title[:80])

        post = RawPost(
            external_id=job.job_id,
            source=LeadSource.BEHANCE,
            text=full_text,
            author=job.company,
            contact=job.url,
            timestamp=datetime.now(timezone.utc),
        )
        await self._on_post(post)

    async def poll_recent(self) -> None:
        if not self._context:
            return

        try:
            jobs = await self._scrape_joblist()
            for job in jobs:
                try:
                    await self._process_job(job)
                except Exception as exc:
                    logger.error("Behance: process job error: %s", exc)
        except Exception as exc:
            logger.exception("Behance: poll error: %s", exc)
            if is_playwright_connection_error(exc):
                logger.warning("Behance: browser dead — restarting Playwright")
                await self.stop()
                await self.start()

        logger.info("Behance: poll cycle complete")

    async def start(self) -> None:
        if not self._settings.behance_enabled:
            logger.info("Behance parser disabled (BEHANCE_ENABLED=false)")
            return

        try:
            self._playwright = await async_playwright().start()
            self._browser = await create_stealth_browser(
                self._playwright,
                headless=self._settings.behance_headless,
                low_memory=True,
            )
            self._context = await create_stealth_context(
                self._browser,
                locale="en-US",
                timezone_id="America/New_York",
            )
            logger.info(
                "Behance parser ready (stealth) — %s, %d role keywords",
                self._settings.behance_joblist_url,
                len(BEHANCE_JOB_KEYWORDS),
            )
        except Exception as exc:
            logger.exception("Behance parser init failed: %s", exc)
            await self.stop()

    async def stop(self) -> None:
        await safe_close_playwright(
            playwright=self._playwright,
            browser=self._browser,
            context=self._context,
        )
        self._playwright = None
        self._browser = None
        self._context = None

    @property
    def is_active(self) -> bool:
        return self._context is not None
