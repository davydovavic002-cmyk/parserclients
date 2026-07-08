from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

NAVER_SEARCH_BASE = "https://search.naver.com/search.naver"
NAVER_RECENCY_PARAM = "nso=so:dd,p:1d"

KR_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_WS_RE = re.compile(r"\s+")


@dataclass
class NaverHttpSnippet:
    keyword: str
    section: str
    title: str
    snippet: str
    url: str
    date_hint: str


def _build_urls(keyword: str, *, recency_hours: int = 24) -> list[tuple[str, str]]:
    encoded = quote_plus(keyword)
    suffix = ""
    if recency_hours <= 24:
        suffix = f"&{NAVER_RECENCY_PARAM}"
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


def _parse_items(html: str, *, keyword: str, section: str) -> list[NaverHttpSnippet]:
    soup = BeautifulSoup(html, "html.parser")
    snippets: list[NaverHttpSnippet] = []

    for item in soup.select("li.bx, div.total_wrap, div.api_subject_bx, ul.lst_total li"):
        title_el = item.select_one(
            "a.api_txt_lines.total_tit, a.title_link, a.link_tit"
        )
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        href = title_el.get("href") or ""
        if len(title) < 5 or not href:
            continue

        desc_el = item.select_one("div.dsc_wrap, div.api_txt_lines.dsc, div.total_dsc")
        date_el = item.select_one("span.sub_time, span.sub_txt.sub_time, span.date")
        snippet_text = desc_el.get_text(strip=True) if desc_el else ""
        date_hint = date_el.get_text(strip=True) if date_el else ""

        snippets.append(
            NaverHttpSnippet(
                keyword=keyword,
                section=section,
                title=title[:300],
                snippet=_WS_RE.sub(" ", snippet_text)[:1500],
                url=href,
                date_hint=date_hint,
            )
        )
        if len(snippets) >= 20:
            break

    return snippets


class NaverHttpClient:
    """Lightweight Naver blog/cafe search — no Chromium."""

    def __init__(self, *, recency_hours: int = 24) -> None:
        self._recency_hours = recency_hours
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": KR_USER_AGENT,
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=30.0,
            follow_redirects=True,
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def is_active(self) -> bool:
        return self._client is not None

    async def search_keyword(self, keyword: str) -> tuple[list[NaverHttpSnippet], str]:
        assert self._client is not None
        all_items: list[NaverHttpSnippet] = []

        for section, url in _build_urls(keyword, recency_hours=self._recency_hours):
            try:
                resp = await self._client.get(url)
            except Exception as exc:
                logger.warning("Naver HTTP [%s] %s: %s", section, keyword, exc)
                continue

            if resp.status_code == 429:
                return [], "HTTP 429 — rate limit"
            if resp.status_code != 200:
                logger.warning(
                    "Naver HTTP [%s] %s: status %s",
                    section,
                    keyword,
                    resp.status_code,
                )
                continue

            body = resp.text.lower()
            if "captcha" in body[:3000]:
                return [], "captcha — try later"

            items = _parse_items(resp.text, keyword=keyword, section=section)
            all_items.extend(items)

        return all_items, f"ok — {len(all_items)} snippet(s)"
