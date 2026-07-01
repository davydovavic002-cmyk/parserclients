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
    create_stealth_context,
    new_stealth_page,
)
from config import BOARDS_URLS, get_settings
from filters import passes_boards_filter
from models import LeadSource, RawPost

logger = logging.getLogger(__name__)

PostHandler = Callable[[RawPost], Awaitable[None]]

# Per-board CSS selector strategies (tried in order)
BOARD_SELECTORS: dict[str, list[str]] = {
    "upwork_search": [
        "article.job-tile",
        "[data-test='job-tile']",
        "section.air3-card-list div.air3-card",
    ],
    "fiverr_briefs": [
        ".gig-card-layout",
        "div[data-testid='gig-card']",
        ".basic-gig-card",
    ],
    "freelancer_com": [
        "fl-project-contest-card",
        ".JobSearchCard-item",
        "div[data-project-id]",
    ],
    "guru_com": [
        ".jobRecord",
        "div.record.jobRecord",
        "a.jobTitle",
    ],
    "peopleperhour": [
        ".listings__item",
        "article.listing",
        ".project-card",
    ],
    "freelance_de": [
        ".projekt",
        "div.project-item",
        "table tr.project",
    ],
    "freelancermap": [
        ".project-card",
        "div[data-project-id]",
        ".project-list-item",
    ],
    "twago_de": [
        ".project-list-item",
        "div.project",
        "article.project",
    ],
}

BUDGET_PATTERN = re.compile(
    r"(\$|€|£|USD|EUR|GBP|budget|Budget|бюджет|honorar|preis)[:\s]*[\d\s,.]+",
    re.IGNORECASE,
)


@dataclass
class BoardCard:
    board: str
    title: str
    description: str
    budget: str
    url: str


class BoardsParser:
    """
    Playwright scraper for public freelance board listing pages.
    Human-like scroll + random delays, 6-language keyword filter, AI pipeline.
    """

    def __init__(self, on_post: PostHandler) -> None:
        self._settings = get_settings()
        self._on_post = on_post
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._seen_ids: set[str] = set()

    async def _random_delay(self) -> None:
        delay = random.uniform(
            self._settings.boards_delay_min,
            self._settings.boards_delay_max,
        )
        logger.debug("Boards: human delay %.1f s", delay)
        await asyncio.sleep(delay)

    async def _human_scroll(self, page: Page) -> None:
        logger.debug("Boards: scrolling page to load lazy content")
        for _ in range(random.randint(3, 6)):
            scroll_px = random.randint(400, 900)
            await page.mouse.wheel(0, scroll_px)
            await asyncio.sleep(random.uniform(0.8, 2.0))
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(random.uniform(0.5, 1.2))

    @staticmethod
    def _extract_budget(text: str) -> str:
        match = BUDGET_PATTERN.search(text)
        return match.group(0).strip() if match else ""

    @staticmethod
    def _card_external_id(board: str, url: str, title: str) -> str:
        raw = f"{board}:{url or title}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    async def _extract_cards_generic(
        self, page: Page, board: str, base_url: str
    ) -> list[BoardCard]:
        """Fallback: grab linked blocks with substantial text."""
        cards: list[BoardCard] = []
        anchors = await page.query_selector_all("a[href]")
        seen_hrefs: set[str] = set()

        for anchor in anchors:
            try:
                href = await anchor.get_attribute("href") or ""
                if not href or href.startswith("#") or href in seen_hrefs:
                    continue
                if not any(
                    kw in href.lower()
                    for kw in ("job", "project", "gig", "brief", "projekt", "work")
                ):
                    continue

                full_url = urljoin(base_url, href)
                seen_hrefs.add(href)
                text = (await anchor.inner_text()).strip()
                if len(text) < 20:
                    continue

                lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
                title = lines[0][:200]
                description = " ".join(lines[1:])[:1500] if len(lines) > 1 else text[:1500]
                budget = self._extract_budget(text)

                cards.append(
                    BoardCard(
                        board=board,
                        title=title,
                        description=description,
                        budget=budget,
                        url=full_url,
                    )
                )
            except Exception:
                continue

        return cards[:30]

    async def _extract_cards(self, page: Page, board: str, url: str) -> list[BoardCard]:
        cards: list[BoardCard] = []
        selectors = BOARD_SELECTORS.get(board, [])

        for selector in selectors:
            elements = await page.query_selector_all(selector)
            if not elements:
                continue

            logger.info(
                "Boards [%s]: selector '%s' matched %d element(s)",
                board,
                selector,
                len(elements),
            )

            for el in elements[:25]:
                try:
                    raw_text = (await el.inner_text()).strip()
                    if len(raw_text) < 15:
                        continue

                    lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()]
                    title = lines[0][:200]
                    description = (
                        " ".join(lines[1:])[:2000] if len(lines) > 1 else raw_text[:2000]
                    )
                    budget = self._extract_budget(raw_text)

                    link_el = await el.query_selector("a[href]")
                    href = await link_el.get_attribute("href") if link_el else ""
                    full_url = urljoin(url, href) if href else url

                    cards.append(
                        BoardCard(
                            board=board,
                            title=title,
                            description=description,
                            budget=budget,
                            url=full_url,
                        )
                    )
                except Exception as exc:
                    logger.debug("Boards card parse error [%s]: %s", board, exc)

            if cards:
                break

        if not cards:
            logger.warning(
                "Boards [%s]: no selector matches — using generic link extraction",
                board,
            )
            cards = await self._extract_cards_generic(page, board, url)

        return cards

    async def _scrape_board(self, board: str, url: str) -> list[BoardCard]:
        assert self._context is not None
        page = await new_stealth_page(self._context)
        cards: list[BoardCard] = []

        try:
            logger.info("Boards: opening [%s] %s", board, url)
            response = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

            if response and response.status == 429:
                logger.error("Boards [%s]: HTTP 429 — skipping this cycle", board)
                return []

            if response and response.status >= 400:
                logger.error(
                    "Boards [%s]: HTTP %d — skipping",
                    board,
                    response.status,
                )
                return []

            body_text = (await page.inner_text("body")).lower()
            if "captcha" in body_text[:3000] or "verify you are human" in body_text[:3000]:
                logger.error("Boards [%s]: CAPTCHA detected — skipping", board)
                return []

            await self._random_delay()
            await self._human_scroll(page)
            await self._random_delay()

            cards = await self._extract_cards(page, board, url)
            logger.info("Boards [%s]: collected %d card(s)", board, len(cards))

        except Exception as exc:
            logger.exception("Boards [%s]: scrape failed: %s", board, exc)
        finally:
            await page.close()

        return cards

    async def _process_card(self, card: BoardCard) -> None:
        full_text = f"Title: {card.title}\n"
        if card.budget:
            full_text += f"Budget: {card.budget}\n"
        full_text += f"Description: {card.description}"

        ext_id = self._card_external_id(card.board, card.url, card.title)
        if ext_id in self._seen_ids:
            return
        self._seen_ids.add(ext_id)

        if not passes_boards_filter(full_text):
            logger.debug("Boards [%s]: pre-filter rejected '%s'", card.board, card.title[:60])
            return

        logger.info(
            "Boards [%s]: candidate '%s' — sending to pipeline",
            card.board,
            card.title[:80],
        )

        post = RawPost(
            external_id=ext_id,
            source=LeadSource.BOARDS,
            text=full_text,
            author=card.board,
            contact=card.url,
            timestamp=datetime.now(timezone.utc),
        )
        await self._on_post(post)

    async def poll_recent(self) -> None:
        if not self._context:
            return

        logger.info("Boards: starting poll of %d board(s)", len(BOARDS_URLS))

        for board, url in BOARDS_URLS.items():
            try:
                cards = await self._scrape_board(board, url)
                for card in cards:
                    try:
                        await self._process_card(card)
                    except Exception as exc:
                        logger.error(
                            "Boards [%s]: card processing error: %s",
                            board,
                            exc,
                        )
            except Exception as exc:
                logger.exception("Boards [%s]: poll error (continuing): %s", board, exc)

            await self._random_delay()

        logger.info("Boards: poll cycle complete")

    async def start(self) -> None:
        if not self._settings.boards_enabled:
            logger.info("Boards parser disabled (BOARDS_ENABLED=false)")
            return

        try:
            self._playwright = await async_playwright().start()
            self._browser = await create_stealth_browser(
                self._playwright,
                headless=self._settings.boards_headless,
            )
            self._context = await create_stealth_context(
                self._browser,
                locale="en-US",
                timezone_id="America/New_York",
            )
            logger.info(
                "Boards parser ready (stealth) — %d URL(s), delay %d–%d s",
                len(BOARDS_URLS),
                int(self._settings.boards_delay_min),
                int(self._settings.boards_delay_max),
            )
        except Exception as exc:
            logger.exception("Boards parser init failed: %s", exc)
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
