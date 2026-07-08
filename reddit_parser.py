from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import httpx

from config import DEFAULT_REDDIT_SUBREDDITS, get_settings
from filters import passes_reddit_filter
from models import LeadSource, RawPost
from reddit_rss import RedditRssPost, fetch_subreddit_rss

logger = logging.getLogger(__name__)

PostHandler = Callable[[RawPost], Awaitable[None]]

REDDIT_JSON_URL = "https://www.reddit.com/r/{subreddit}/new.json"


@dataclass
class _RedditPost:
    id: str
    title: str
    selftext: str
    author: str
    permalink: str
    created_utc: float


class RedditParser:
    """Reddit parser — OAuth, anonymous JSON, or RSS fallback (VPS/datacenter)."""

    def __init__(self, on_post: PostHandler) -> None:
        self._settings = get_settings()
        self._on_post = on_post
        self._reddit = None
        self._http: Optional[httpx.AsyncClient] = None
        self._mode = "off"
        self._seen_ids: set[str] = set()
        self._sub_offset = 0

    @property
    def mode(self) -> str:
        return self._mode

    def _user_agent(self) -> str:
        agent = self._settings.reddit_user_agent.strip()
        if agent and "yourusername" not in agent.lower():
            return agent
        return "web:parserclients:1.0 (by /u/parserclients_bot)"

    def _subreddits(self) -> list[str]:
        return self._settings.reddit_subreddits or DEFAULT_REDDIT_SUBREDDITS

    def _build_praw_client(self):
        import praw

        return praw.Reddit(
            client_id=self._settings.reddit_client_id,
            client_secret=self._settings.reddit_client_secret,
            user_agent=self._user_agent(),
        )

    def _chunk_subreddits(self) -> list[str]:
        subs = self._subreddits()
        batch = 8
        start = self._sub_offset % max(len(subs), 1)
        chunk = subs[start : start + batch]
        if len(chunk) < batch:
            chunk = chunk + subs[: batch - len(chunk)]
        self._sub_offset = (start + batch) % max(len(subs), 1)
        return chunk

    @staticmethod
    def _from_rss(post: RedditRssPost) -> _RedditPost:
        return _RedditPost(
            id=post.id,
            title=post.title,
            selftext=post.selftext,
            author=post.author,
            permalink=post.permalink,
            created_utc=post.created_utc,
        )

    def _fetch_praw(self, limit: int) -> list[_RedditPost]:
        assert self._reddit is not None
        out: list[_RedditPost] = []
        for name in self._subreddits():
            try:
                for sub in self._reddit.subreddit(name).new(limit=limit):
                    if sub.id not in self._seen_ids:
                        out.append(
                            _RedditPost(
                                id=sub.id,
                                title=sub.title or "",
                                selftext=sub.selftext or "",
                                author=sub.author.name if sub.author else "unknown",
                                permalink=sub.permalink or "",
                                created_utc=float(sub.created_utc),
                            )
                        )
            except Exception as exc:
                logger.error("Reddit r/%s error: %s", name, exc)
        return out

    async def _fetch_json(self, limit: int) -> list[_RedditPost]:
        assert self._http is not None
        out: list[_RedditPost] = []
        for name in self._chunk_subreddits():
            url = REDDIT_JSON_URL.format(subreddit=name)
            params = {"limit": str(limit), "raw_json": "1"}
            try:
                resp = await self._http.get(url, params=params)
                if resp.status_code == 403:
                    logger.warning("Reddit JSON r/%s blocked (403) — switch to RSS", name)
                    self._mode = "rss"
                    return await self._fetch_rss(limit)
                if resp.status_code == 429:
                    logger.warning("Reddit r/%s rate-limited (429)", name)
                    await asyncio.sleep(5)
                    continue
                if resp.status_code != 200:
                    logger.warning("Reddit r/%s HTTP %s", name, resp.status_code)
                    continue
                payload = resp.json()
                for child in payload.get("data", {}).get("children", []):
                    data = child.get("data") or {}
                    post_id = data.get("id")
                    if not post_id or post_id in self._seen_ids:
                        continue
                    out.append(
                        _RedditPost(
                            id=post_id,
                            title=data.get("title") or "",
                            selftext=data.get("selftext") or "",
                            author=data.get("author") or "unknown",
                            permalink=data.get("permalink") or "",
                            created_utc=float(data.get("created_utc") or 0),
                        )
                    )
            except Exception as exc:
                logger.error("Reddit JSON r/%s error: %s", name, exc)
            await asyncio.sleep(1.5)
        return out

    async def _fetch_rss(self, limit: int) -> list[_RedditPost]:
        assert self._http is not None
        out: list[_RedditPost] = []
        for name in self._chunk_subreddits():
            try:
                rss_posts = await fetch_subreddit_rss(
                    self._http, name, limit_hint=limit
                )
                for post in rss_posts:
                    if post.id not in self._seen_ids:
                        out.append(self._from_rss(post))
            except httpx.HTTPStatusError as exc:
                logger.warning("Reddit RSS r/%s failed: %s", name, exc)
            except Exception as exc:
                logger.error("Reddit RSS r/%s error: %s", name, exc)
            await asyncio.sleep(1.5)
        return out

    async def _probe_access(self) -> str:
        """Pick json or rss — Reddit blocks unauthenticated .json from many VPS IPs."""
        assert self._http is not None
        test_sub = self._subreddits()[0]
        url = REDDIT_JSON_URL.format(subreddit=test_sub)
        try:
            resp = await self._http.get(
                url, params={"limit": "1", "raw_json": "1"}
            )
            if resp.status_code == 200:
                return "json"
        except Exception as exc:
            logger.debug("Reddit JSON probe failed: %s", exc)

        try:
            posts = await fetch_subreddit_rss(self._http, test_sub, limit_hint=1)
            if posts:
                return "rss"
        except Exception as exc:
            logger.warning("Reddit RSS probe failed: %s", exc)
        return "rss"

    async def _handle_post(self, post: _RedditPost) -> None:
        if post.id in self._seen_ids:
            return
        self._seen_ids.add(post.id)

        text = (post.selftext or post.title or "").strip()
        if not passes_reddit_filter(text):
            return

        raw = RawPost(
            external_id=post.id,
            source=LeadSource.REDDIT,
            text=text,
            author=post.author,
            contact=f"https://reddit.com{post.permalink}",
            timestamp=datetime.fromtimestamp(post.created_utc, tz=timezone.utc),
        )
        await self._on_post(raw)

    async def poll_recent(self, limit: int = 25) -> None:
        if self._mode == "off":
            return

        loop = asyncio.get_running_loop()
        try:
            if self._mode == "oauth":
                posts = await loop.run_in_executor(None, self._fetch_praw, limit)
            elif self._mode == "rss":
                posts = await self._fetch_rss(limit)
            else:
                posts = await self._fetch_json(limit)
        except Exception as exc:
            logger.exception("Reddit fetch failed: %s", exc)
            return

        logger.info("Reddit (%s): %d submission(s) to check", self._mode, len(posts))
        for post in posts:
            try:
                await self._handle_post(post)
            except Exception as exc:
                logger.error("Reddit process %s failed: %s", post.id, exc)

    async def start(self) -> None:
        cid = self._settings.reddit_client_id.strip()
        secret = self._settings.reddit_client_secret.strip()

        if cid and secret:
            self._reddit = self._build_praw_client()
            self._mode = "oauth"
            count = len(self._subreddits())
            logger.info("Reddit parser ready (OAuth) — %d subreddit(s)", count)
            return

        self._http = httpx.AsyncClient(
            headers={"User-Agent": self._user_agent()},
            timeout=20.0,
            follow_redirects=True,
        )
        self._mode = await self._probe_access()
        count = len(self._subreddits())
        logger.info(
            "Reddit parser ready (%s, no OAuth) — %d subreddit(s)",
            self._mode,
            count,
        )

    async def stop(self) -> None:
        self._reddit = None
        if self._http:
            await self._http.aclose()
            self._http = None
        self._mode = "off"

    @property
    def is_active(self) -> bool:
        return self._mode != "off"

    @property
    def status_detail(self) -> str:
        if self._mode == "oauth":
            return "OAuth PRAW"
        if self._mode == "rss":
            return "RSS (Reddit блокирует .json с VPS)"
        if self._mode == "json":
            return "anonymous JSON"
        return "выключен"
