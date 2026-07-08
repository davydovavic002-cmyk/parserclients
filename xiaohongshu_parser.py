from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import quote

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from browser_stealth import (
    create_stealth_browser,
    create_xhs_context,
    new_stealth_page,
)
from config import KEYWORDS_XHS, XHS_TRENDING_HASHTAGS, get_settings
from filters import passes_xhs_filter
from models import LeadSource, RawPost

logger = logging.getLogger(__name__)

PostHandler = Callable[[RawPost], Awaitable[None]]

XHS_SEARCH_BASE = "https://www.xiaohongshu.com/search_result"

_LOGIN_MARKERS = (
    "captcha",
    "验证",
    "请登录",
    "登录后",
    "扫码登录",
    "login",
    "security verification",
)


@dataclass
class XhsNote:
    note_id: str
    text: str
    url: str
    hashtag: str


class XiaohongshuParser:
    """Playwright scraper for Xiaohongshu — mobile UA + optional saved login."""

    def __init__(self, on_post: PostHandler) -> None:
        self._settings = get_settings()
        self._on_post = on_post
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._seen_ids: set[str] = set()
        self._status_detail: str = "не запущен"
        self._last_notes: int = 0

    def _search_targets(self) -> list[tuple[str, str]]:
        """Hashtags + keyword searches."""
        targets: list[tuple[str, str]] = []
        seen_urls: set[str] = set()

        for tag in XHS_TRENDING_HASHTAGS:
            keyword = tag.lstrip("#")
            url = f"{XHS_SEARCH_BASE}?keyword={quote(keyword)}"
            if url not in seen_urls:
                targets.append((tag, url))
                seen_urls.add(url)

        for kw in KEYWORDS_XHS[:10]:
            url = f"{XHS_SEARCH_BASE}?keyword={quote(kw)}"
            if url not in seen_urls:
                targets.append((f"kw:{kw}", url))
                seen_urls.add(url)

        return targets

    async def _random_delay(self) -> None:
        delay = random.uniform(
            self._settings.boards_delay_min,
            self._settings.boards_delay_max,
        )
        await asyncio.sleep(delay)

    async def _human_scroll(self, page: Page) -> None:
        for _ in range(random.randint(4, 7)):
            await page.mouse.wheel(0, random.randint(350, 900))
            await asyncio.sleep(random.uniform(0.8, 2.0))

    @staticmethod
    def _note_id_from_href(href: str, fallback: str) -> str:
        for pattern in (
            r"/explore/([a-f0-9]+)",
            r"/discovery/item/([a-f0-9]+)",
            r"noteId=([a-f0-9]+)",
        ):
            match = re.search(pattern, href)
            if match:
                return match.group(1)
        return hashlib.sha256(fallback.encode()).hexdigest()[:32]

    @staticmethod
    def _looks_like_login_wall(body: str) -> bool:
        sample = body[:4000].lower()
        return any(m in sample for m in _LOGIN_MARKERS)

    async def _extract_notes(self, page: Page, label: str) -> list[XhsNote]:
        notes: list[XhsNote] = []

        selectors = [
            "section.note-item",
            "div.feeds-page div.note",
            "a[href*='/explore/']",
            "a[href*='/discovery/item/']",
            "div.note-item",
            "[class*='note-item']",
        ]

        for selector in selectors:
            if selector.startswith("a["):
                anchors = await page.query_selector_all(selector)
                for i, anchor in enumerate(anchors[:30]):
                    try:
                        href = await anchor.get_attribute("href") or ""
                        text = (await anchor.inner_text()).strip()
                        if len(text) < 8:
                            continue
                        note_id = self._note_id_from_href(
                            href, f"{label}-{i}-{text[:40]}"
                        )
                        full_url = (
                            f"https://www.xiaohongshu.com{href}"
                            if href.startswith("/")
                            else href
                        )
                        notes.append(
                            XhsNote(
                                note_id=note_id,
                                text=text,
                                url=full_url or page.url,
                                hashtag=label,
                            )
                        )
                    except Exception:
                        continue
            else:
                cards = await page.query_selector_all(selector)
                for i, card in enumerate(cards[:25]):
                    try:
                        text = (await card.inner_text()).strip()
                        link = await card.query_selector("a[href]")
                        href = await link.get_attribute("href") if link else ""
                        if len(text) < 10:
                            continue
                        note_id = self._note_id_from_href(
                            href or text, f"{label}-{i}-{text[:40]}"
                        )
                        full_url = (
                            f"https://www.xiaohongshu.com{href}"
                            if href and href.startswith("/")
                            else href or page.url
                        )
                        notes.append(
                            XhsNote(
                                note_id=note_id,
                                text=text,
                                url=full_url,
                                hashtag=label,
                            )
                        )
                    except Exception:
                        continue

            if notes:
                logger.info(
                    "XHS [%s]: selector '%s' → %d note(s)",
                    label,
                    selector,
                    len(notes),
                )
                break

        if not notes:
            body = await page.inner_text("body")
            for kw in KEYWORDS_XHS:
                idx = body.find(kw)
                if idx >= 0:
                    snippet = body[max(0, idx - 120) : idx + 200].strip()
                    notes.append(
                        XhsNote(
                            note_id=hashlib.sha256(snippet.encode()).hexdigest()[:32],
                            text=snippet,
                            url=page.url,
                            hashtag=label,
                        )
                    )
                    break

        return notes

    async def _scrape_search(self, label: str, url: str) -> list[XhsNote]:
        assert self._context is not None
        page = await new_stealth_page(self._context)
        notes: list[XhsNote] = []

        try:
            logger.info("XHS: opening [%s] %s", label, url)
            response = await page.goto(
                url, wait_until="domcontentloaded", timeout=60_000
            )

            if response and response.status == 429:
                self._status_detail = "HTTP 429 — rate limit"
                logger.error("XHS [%s]: HTTP 429", label)
                return []

            await asyncio.sleep(self._settings.xhs_page_delay)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass

            body = await page.inner_text("body")
            if self._looks_like_login_wall(body):
                self._status_detail = (
                    "нужен логин — python scripts/auth_xhs.py, см. XHS_STORAGE_STATE"
                )
                logger.error(
                    "XHS [%s]: login wall — run scripts/auth_xhs.py on desktop",
                    label,
                )
                return []

            await self._human_scroll(page)
            await self._random_delay()

            notes = await self._extract_notes(page, label)
            logger.info("XHS [%s]: collected %d note(s)", label, len(notes))

        except Exception as exc:
            self._status_detail = f"ошибка scrape: {exc}"
            logger.exception("XHS scrape [%s] failed: %s", label, exc)
        finally:
            await page.close()

        return notes

    async def _process_note(self, note: XhsNote) -> None:
        if note.note_id in self._seen_ids:
            return
        self._seen_ids.add(note.note_id)

        if not passes_xhs_filter(note.text):
            return

        logger.info("XHS [%s]: candidate → pipeline", note.hashtag)

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

        targets = self._search_targets()
        logger.info("XHS: polling %d search target(s)", len(targets))
        total_notes = 0

        for label, url in targets:
            try:
                notes = await self._scrape_search(label, url)
                total_notes += len(notes)
                for note in notes:
                    try:
                        await self._process_note(note)
                    except Exception as exc:
                        logger.error("XHS note process error: %s", exc)
            except Exception as exc:
                logger.exception("XHS poll [%s] error: %s", label, exc)

            await asyncio.sleep(self._settings.xhs_poll_delay)

        self._last_notes = total_notes
        if total_notes:
            self._status_detail = f"работает — {total_notes} note(s) last cycle"
        elif "логин" not in self._status_detail:
            self._status_detail = "работает — 0 notes (login wall or empty)"
        logger.info("XHS: poll done — %d note(s)", total_notes)

    async def start(self) -> None:
        if not self._settings.xhs_enabled:
            self._status_detail = "XHS_ENABLED=false"
            logger.info("XHS parser disabled (XHS_ENABLED=false)")
            return

        storage = self._settings.xhs_storage_state.strip()
        if storage and not Path(storage).is_file():
            logger.warning(
                "XHS_STORAGE_STATE=%s not found — scraping without login",
                storage,
            )

        try:
            self._playwright = await async_playwright().start()
            self._browser = await create_stealth_browser(
                self._playwright,
                headless=self._settings.xhs_headless,
            )
            self._context = await create_xhs_context(
                self._browser,
                storage_state_path=storage,
            )
            login_hint = "with cookies" if storage and Path(storage).is_file() else "no cookies"
            self._status_detail = f"mobile UA, {login_hint}"
            logger.info(
                "XHS parser ready — %d targets, headless=%s, %s",
                len(self._search_targets()),
                self._settings.xhs_headless,
                login_hint,
            )
        except Exception as exc:
            self._status_detail = f"init failed: {exc}"
            logger.exception("XHS parser init failed: %s", exc)
            await self.stop()

    async def stop(self) -> None:
        try:
            if self._context:
                await self._context.close()
        except Exception as exc:
            logger.debug("XHS context close: %s", exc)
        finally:
            self._context = None
        try:
            if self._browser:
                await self._browser.close()
        except Exception as exc:
            logger.debug("XHS browser close: %s", exc)
        finally:
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as exc:
                logger.debug("XHS playwright stop: %s", exc)
            self._playwright = None

    @property
    def is_active(self) -> bool:
        return self._context is not None

    @property
    def status_detail(self) -> str:
        if not self.is_active:
            return self._status_detail
        return self._status_detail
