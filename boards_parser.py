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
    is_playwright_connection_error,
    new_stealth_page,
    safe_close_playwright,
)
from config import BOARDS_URLS, get_settings
from filters import passes_boards_filter
from models import LeadSource, RawPost
from quality import parse_proposal_count, should_skip_board_listing

logger = logging.getLogger(__name__)

PostHandler = Callable[[RawPost], Awaitable[None]]

# Per-board CSS selector strategies (tried in order)
BOARD_SELECTORS: dict[str, list[str]] = {
    "upwork_design": [
        "article.job-tile",
        "[data-test='job-tile']",
        "section.air3-card-list div.air3-card",
    ],
    "upwork_fullstack": [
        "article.job-tile",
        "[data-test='job-tile']",
        "section.air3-card-list div.air3-card",
    ],
    "upwork_landing": [
        "article.job-tile",
        "[data-test='job-tile']",
        "section.air3-card-list div.air3-card",
    ],
    "upwork_nextjs": [
        "article.job-tile",
        "[data-test='job-tile']",
        "section.air3-card-list div.air3-card",
    ],
    "upwork_figma": [
        "article.job-tile",
        "[data-test='job-tile']",
        "section.air3-card-list div.air3-card",
    ],
    "upwork_react": [
        "article.job-tile",
        "[data-test='job-tile']",
        "section.air3-card-list div.air3-card",
    ],
    "upwork_brand": [
        "article.job-tile",
        "[data-test='job-tile']",
        "section.air3-card-list div.air3-card",
    ],
    "upwork_mvp": [
        "article.job-tile",
        "[data-test='job-tile']",
        "section.air3-card-list div.air3-card",
    ],
    "upwork_saas": [
        "article.job-tile",
        "[data-test='job-tile']",
        "section.air3-card-list div.air3-card",
    ],
    "freelancer_design": [
        "fl-project-contest-card",
        ".JobSearchCard-item",
        "div[data-project-id]",
    ],
    "freelancer_fullstack": [
        "fl-project-contest-card",
        ".JobSearchCard-item",
        "div[data-project-id]",
    ],
    "freelancer_mvp": [
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
    "peopleperhour_design": [
        ".listings__item",
        "article.listing",
        ".project-card",
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
    proposals: Optional[int] = None


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
        self._board_offset = 0

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
                        proposals=parse_proposal_count(text),
                    )
                )
            except Exception:
                continue

        return cards[:30]

    @staticmethod
    def _selectors_for_board(board: str) -> list[str]:
        if board in BOARD_SELECTORS:
            return BOARD_SELECTORS[board]
        if board.startswith("upwork_"):
            return BOARD_SELECTORS["upwork_design"]
        if board.startswith("freelancer_"):
            return BOARD_SELECTORS["freelancer_design"]
        return []

    async def _extract_cards(self, page: Page, board: str, url: str) -> list[BoardCard]:
        cards: list[BoardCard] = []
        selectors = self._selectors_for_board(board)

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
                            proposals=parse_proposal_count(raw_text),
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
            if is_playwright_connection_error(exc):
                raise
        finally:
            await page.close()

        return cards

    async def _process_card(self, card: BoardCard) -> None:
        full_text = f"Title: {card.title}\n"
        if card.budget:
            full_text += f"Budget: {card.budget}\n"
        if card.proposals is not None:
            full_text += f"Proposals: {card.proposals}\n"
        full_text += f"Description: {card.description}"

        skip_reason = should_skip_board_listing(
            full_text,
            max_proposals=self._settings.max_proposals,
            max_post_age_hours=self._settings.max_post_age_hours,
        )
        if skip_reason:
            logger.debug(
                "Boards [%s]: skip '%s' — %s",
                card.board,
                card.title[:60],
                skip_reason,
            )
            return

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
            if not self._settings.boards_enabled:
                return
            logger.warning("Boards: browser inactive — restarting")
            await self.start()
            if not self._context:
                return

        board_items = list(BOARDS_URLS.items())
        batch_size = max(1, self._settings.boards_max_boards_per_poll)
        start = self._board_offset % len(board_items)
        end = start + batch_size
        if end <= len(board_items):
            batch = board_items[start:end]
        else:
            batch = board_items[start:] + board_items[: end - len(board_items)]
        self._board_offset = (start + batch_size) % len(board_items)

        logger.info(
            "Boards: batch %d/%d board(s) this cycle",
            len(batch),
            len(board_items),
        )

        for board, url in batch:
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
                if is_playwright_connection_error(exc):
                    logger.warning("Boards: browser dead — restarting Playwright")
                    await self.stop()
                    await self.start()
                    if not self._context:
                        break

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
                low_memory=True,
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
