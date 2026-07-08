from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx

from browser_stealth import REALISTIC_USER_AGENT
from xhs_cookies import cookie_dict_from_storage_state

logger = logging.getLogger(__name__)

XHS_SEARCH_URL = "https://www.xiaohongshu.com/search_result"

_LOGIN_MARKERS = (
    "请登录",
    "扫码登录",
    "登录后",
    "security verification",
    "captcha",
)

_NOTE_ID_RE = re.compile(r"/explore/([a-f0-9]{16,24})")
_JSON_TITLE_RE = re.compile(r'"display_title"\s*:\s*"((?:\\.|[^"\\])*)"')
_JSON_DESC_RE = re.compile(r'"desc"\s*:\s*"((?:\\.|[^"\\])*)"')


@dataclass
class XhsHttpNote:
    note_id: str
    text: str
    url: str


def _decode_json_str(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return raw.replace("\\n", "\n").replace('\\"', '"')


def _looks_like_login_wall(text: str) -> bool:
    sample = text[:8000]
    return any(m in sample for m in _LOGIN_MARKERS)


def parse_notes_from_html(html: str, *, label: str, page_url: str) -> list[XhsHttpNote]:
    if _looks_like_login_wall(html):
        return []

    notes: list[XhsHttpNote] = []
    seen: set[str] = set()

    titles = [_decode_json_str(t) for t in _JSON_TITLE_RE.findall(html)]
    descs = [_decode_json_str(d) for d in _JSON_DESC_RE.findall(html)]
    note_ids = list(dict.fromkeys(_NOTE_ID_RE.findall(html)))

    for i, note_id in enumerate(note_ids[:30]):
        if note_id in seen:
            continue
        seen.add(note_id)
        title = titles[i] if i < len(titles) else ""
        desc = descs[i] if i < len(descs) else ""
        text = f"{title}\n{desc}".strip() or title or desc
        if len(text) < 8:
            continue
        notes.append(
            XhsHttpNote(
                note_id=note_id,
                text=text[:2000],
                url=f"https://www.xiaohongshu.com/explore/{note_id}",
            )
        )

    if not notes and len(html) > 500:
        for kw in ("网页设计", "网站开发", "独立站", "MVP", "UI设计"):
            idx = html.find(kw)
            if idx >= 0:
                snippet = re.sub(r"\s+", " ", html[max(0, idx - 80) : idx + 180])
                notes.append(
                    XhsHttpNote(
                        note_id=f"snippet-{label}-{idx}",
                        text=snippet[:500],
                        url=page_url,
                    )
                )
                break

    return notes


class XhsHttpClient:
    """Lightweight XHS fetch — no Chromium, uses cookies from auth_xhs.py."""

    def __init__(self, storage_state_path: str = "") -> None:
        self._storage_path = Path(storage_state_path) if storage_state_path else None
        self._client: Optional[httpx.AsyncClient] = None
        self._cookies: dict[str, str] = {}

    async def start(self) -> None:
        if self._storage_path and self._storage_path.is_file():
            self._cookies = cookie_dict_from_storage_state(self._storage_path)
            logger.info(
                "XHS HTTP: loaded %d cookies from %s",
                len(self._cookies),
                self._storage_path,
            )
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": REALISTIC_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://www.xiaohongshu.com/explore",
            },
            cookies=self._cookies,
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

    async def search(self, keyword: str, *, label: str = "") -> tuple[list[XhsHttpNote], str]:
        assert self._client is not None
        url = f"{XHS_SEARCH_URL}?keyword={quote(keyword)}"
        try:
            resp = await self._client.get(url)
        except Exception as exc:
            return [], f"HTTP error: {exc}"

        if resp.status_code == 429:
            return [], "HTTP 429 — rate limit"
        if resp.status_code >= 400:
            return [], f"HTTP {resp.status_code}"

        html = resp.text
        if _looks_like_login_wall(html):
            return [], "login wall — refresh xhs_storage.json"

        notes = parse_notes_from_html(html, label=label or keyword, page_url=str(resp.url))
        return notes, f"ok — {len(notes)} note(s), {len(html)} bytes"
