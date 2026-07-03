from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Awaitable, Callable, Optional
from urllib.parse import quote_plus, urljoin
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from config import HABR_CAREER_BASE, HABR_SEARCH_QUERIES, get_settings
from filters import passes_habr_filter
from models import LeadSource, RawPost

logger = logging.getLogger(__name__)

PostHandler = Callable[[RawPost], Awaitable[None]]

_BUDGET_RE = re.compile(
    r"(бюджет|budget|зарплата|salary|₽|руб\.?|rub|\$|€)[:\s]*[\d\s,.]+",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class HabrTask:
    task_id: str
    title: str
    description: str
    budget: str
    url: str
    author: str
    timestamp: datetime


class HabrParser:
    """
    Habr job feed parser.

    Habr Freelance (RSS) closed in Feb 2025 — parser uses Habr Career vacancies
    via HTTP + BeautifulSoup. Optionally tries legacy RSS first and falls back.
    """

    def __init__(self, on_post: PostHandler) -> None:
        self._settings = get_settings()
        self._on_post = on_post
        self._http: Optional[httpx.AsyncClient] = None
        self._seen_ids: set[str] = set()

    @staticmethod
    def _strip_html(raw: str) -> str:
        text = _TAG_RE.sub(" ", raw)
        return unescape(" ".join(text.split()))

    @staticmethod
    def _extract_budget(text: str) -> str:
        match = _BUDGET_RE.search(text)
        return match.group(0).strip() if match else ""

    @staticmethod
    def _parse_pub_date(raw: str) -> datetime:
        try:
            return parsedate_to_datetime(raw).astimezone(timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)

    @staticmethod
    def _item_id(link: str, guid: str = "") -> str:
        match = re.search(r"/vacancies/(\d+)", link)
        if match:
            return match.group(1)
        if guid:
            return hashlib.sha256(guid.encode()).hexdigest()[:32]
        return hashlib.sha256(link.encode()).hexdigest()[:32]

    def _parse_rss(self, xml_text: str) -> list[HabrTask]:
        root = ET.fromstring(xml_text)
        tasks: list[HabrTask] = []

        for item in root.findall(".//item")[: self._settings.habr_max_items]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            guid = (item.findtext("guid") or link).strip()
            description_raw = item.findtext("description") or ""
            pub_date = item.findtext("pubDate") or ""

            if not title or not link:
                continue

            description = self._strip_html(description_raw)
            budget = self._extract_budget(f"{title} {description}")

            tasks.append(
                HabrTask(
                    task_id=self._item_id(link, guid),
                    title=title,
                    description=description,
                    budget=budget,
                    url=link,
                    author="habr_freelance",
                    timestamp=self._parse_pub_date(pub_date),
                )
            )

        return tasks

    def _parse_career_html(self, html: str) -> list[HabrTask]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".vacancy-card")
        tasks: list[HabrTask] = []

        for card in cards[: self._settings.habr_max_items]:
            title_el = card.select_one(".vacancy-card__title-link")
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            href = title_el.get("href") or ""
            url = urljoin(HABR_CAREER_BASE, href)

            company_el = card.select_one(".vacancy-card__company a")
            company = company_el.get_text(strip=True) if company_el else "habr_career"

            salary_el = card.select_one(".vacancy-card__salary")
            salary = salary_el.get_text(" ", strip=True) if salary_el else ""
            budget = self._extract_budget(salary) or salary

            time_el = card.select_one("time[datetime]")
            timestamp = datetime.now(timezone.utc)
            if time_el and time_el.get("datetime"):
                try:
                    timestamp = datetime.fromisoformat(
                        time_el["datetime"].replace("Z", "+00:00")
                    ).astimezone(timezone.utc)
                except ValueError:
                    pass

            description = f"{title}. Компания: {company}."
            if salary:
                description += f" Зарплата: {salary}."

            tasks.append(
                HabrTask(
                    task_id=self._item_id(url),
                    title=title,
                    description=description,
                    budget=budget,
                    url=url,
                    author=company,
                    timestamp=timestamp,
                )
            )

        return tasks

    async def _fetch_rss(self) -> list[HabrTask]:
        assert self._http is not None
        url = self._settings.habr_rss_url
        logger.info("Habr: trying legacy Freelance RSS %s", url)

        response = await self._http.get(url)
        if response.status_code == 410:
            logger.warning(
                "Habr Freelance RSS returned 410 Gone (service closed) — using Career"
            )
            return []
        response.raise_for_status()
        tasks = self._parse_rss(response.text)
        logger.info("Habr: parsed %d task(s) from RSS", len(tasks))
        return tasks

    async def _fetch_career(self) -> list[HabrTask]:
        assert self._http is not None
        queries = HABR_SEARCH_QUERIES
        merged: list[HabrTask] = []
        seen_ids: set[str] = set()

        for query in queries:
            url = (
                f"{HABR_CAREER_BASE}/vacancies"
                f"?q={quote_plus(query)}&type=all"
            )
            logger.info("Habr Career: fetching %s", url)
            try:
                response = await self._http.get(url)
                response.raise_for_status()
                batch = self._parse_career_html(response.text)
                for task in batch:
                    if task.task_id not in seen_ids:
                        seen_ids.add(task.task_id)
                        merged.append(task)
            except httpx.HTTPError as exc:
                logger.warning("Habr Career fetch failed for %r: %s", query, exc)

        logger.info(
            "Habr Career: %d unique vacancy card(s) from %d queries",
            len(merged),
            len(queries),
        )
        return merged[: self._settings.habr_max_items]

    async def _fetch_tasks(self) -> list[HabrTask]:
        tasks: list[HabrTask] = []

        if self._settings.habr_try_rss:
            try:
                tasks = await self._fetch_rss()
            except ET.ParseError as exc:
                logger.warning("Habr RSS parse error: %s", exc)
            except httpx.HTTPError as exc:
                logger.warning("Habr RSS fetch failed: %s", exc)

        if not tasks:
            tasks = await self._fetch_career()

        return tasks

    async def _process_task(self, task: HabrTask) -> None:
        if task.task_id in self._seen_ids:
            return
        self._seen_ids.add(task.task_id)

        full_text = f"Title: {task.title}\n"
        if task.budget:
            full_text += f"Budget/Salary: {task.budget}\n"
        full_text += f"Description: {task.description}"

        if not passes_habr_filter(full_text):
            logger.debug("Habr: pre-filter rejected '%s'", task.title[:60])
            return

        logger.info("Habr: candidate '%s' — pipeline", task.title[:80])

        post = RawPost(
            external_id=task.task_id,
            source=LeadSource.HABR,
            text=full_text,
            author=task.author,
            contact=task.url,
            timestamp=task.timestamp,
        )
        await self._on_post(post)

    async def poll_recent(self) -> None:
        if not self._http:
            return

        try:
            tasks = await self._fetch_tasks()
            for task in tasks:
                try:
                    await self._process_task(task)
                except Exception as exc:
                    logger.error("Habr: process task error: %s", exc)
            logger.info("Habr: poll done — %d vacancy card(s) scanned", len(tasks))
        except httpx.HTTPError as exc:
            logger.error("Habr Career fetch failed: %s", exc)
        except Exception as exc:
            logger.exception("Habr: poll error: %s", exc)

    async def start(self) -> None:
        if not self._settings.habr_enabled:
            logger.info("Habr parser disabled (HABR_ENABLED=false)")
            return

        self._http = httpx.AsyncClient(
            timeout=self._settings.habr_fetch_timeout,
            headers={
                "User-Agent": "WebDevScoutBot/1.0 (+https://github.com/)",
                "Accept": "text/html,application/rss+xml,application/xml",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            },
            follow_redirects=True,
        )
        logger.info(
            "Habr parser ready — Career %s (Freelance RSS fallback: %s)",
            self._settings.habr_career_url,
            self._settings.habr_try_rss,
        )

    async def stop(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    @property
    def is_active(self) -> bool:
        return self._http is not None
