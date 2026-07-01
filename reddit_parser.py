from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import praw
from praw.models import Submission

from config import DEFAULT_REDDIT_SUBREDDITS, get_settings
from filters import passes_reddit_filter
from models import LeadSource, RawPost

logger = logging.getLogger(__name__)

PostHandler = Callable[[RawPost], Awaitable[None]]


class RedditParser:
    """Async Reddit parser (praw in executor) — EN/DE keywords + stop words."""

    def __init__(self, on_post: PostHandler) -> None:
        self._settings = get_settings()
        self._on_post = on_post
        self._reddit: Optional[praw.Reddit] = None
        self._seen_ids: set[str] = set()

    def _build_client(self) -> praw.Reddit:
        return praw.Reddit(
            client_id=self._settings.reddit_client_id,
            client_secret=self._settings.reddit_client_secret,
            user_agent=self._settings.reddit_user_agent,
        )

    def _fetch_new(self, limit: int) -> list[Submission]:
        assert self._reddit is not None
        out: list[Submission] = []
        subreddits = self._settings.reddit_subreddits or DEFAULT_REDDIT_SUBREDDITS

        for name in subreddits:
            try:
                for sub in self._reddit.subreddit(name).new(limit=limit):
                    if sub.id not in self._seen_ids:
                        out.append(sub)
            except Exception as exc:
                logger.error("Reddit r/%s error: %s", name, exc)
        return out

    async def _handle_submission(self, sub: Submission) -> None:
        if sub.id in self._seen_ids:
            return
        self._seen_ids.add(sub.id)

        text = (sub.selftext or sub.title or "").strip()
        if not passes_reddit_filter(text):
            return

        author = sub.author.name if sub.author else "unknown"
        post = RawPost(
            external_id=sub.id,
            source=LeadSource.REDDIT,
            text=text,
            author=author,
            contact=f"https://reddit.com{sub.permalink}",
            timestamp=datetime.fromtimestamp(sub.created_utc, tz=timezone.utc),
        )
        await self._on_post(post)

    async def poll_recent(self, limit: int = 25) -> None:
        if not self._reddit:
            return

        loop = asyncio.get_running_loop()
        try:
            submissions = await loop.run_in_executor(None, self._fetch_new, limit)
        except Exception as exc:
            logger.exception("Reddit fetch failed: %s", exc)
            return

        logger.info("Reddit: %d new submission(s) to check", len(submissions))
        for sub in submissions:
            try:
                await self._handle_submission(sub)
            except Exception as exc:
                logger.error("Reddit process %s failed: %s", sub.id, exc)

    async def start(self) -> None:
        if not self._settings.reddit_client_id or not self._settings.reddit_client_secret:
            logger.warning("Reddit credentials missing — Reddit parser disabled")
            return
        self._reddit = self._build_client()
        count = len(self._settings.reddit_subreddits or DEFAULT_REDDIT_SUBREDDITS)
        logger.info("Reddit parser ready — %d subreddit(s)", count)

    async def stop(self) -> None:
        self._reddit = None

    @property
    def is_active(self) -> bool:
        return self._reddit is not None
