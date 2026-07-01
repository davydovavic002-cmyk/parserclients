from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

import aiohttp

from config import KEYWORDS_RU, VK_TARGET_COMMUNITIES, get_settings
from filters import has_stop_words, passes_vk_filter
from models import LeadSource, RawPost

logger = logging.getLogger(__name__)

PostHandler = Callable[[RawPost], Awaitable[None]]
VK_API = "https://api.vk.com/method"


class VKParser:
    """
    VK wall.get via aiohttp.
    Filters posts by RU keywords + GLOBAL_STOP_WORDS, sends to AI pipeline.
    """

    def __init__(self, on_post: PostHandler) -> None:
        self._settings = get_settings()
        self._on_post = on_post
        self._session: Optional[aiohttp.ClientSession] = None
        self._seen_ids: set[str] = set()
        self._group_ids: dict[str, int] = {}

    async def _call(self, method: str, **params: Any) -> Any:
        assert self._session is not None
        params["access_token"] = self._settings.vk_api_token
        params["v"] = self._settings.vk_api_version

        async with self._session.get(f"{VK_API}/{method}", params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
            if "error" in data:
                raise RuntimeError(data["error"].get("error_msg", str(data["error"])))
            return data.get("response")

    async def _group_id(self, screen_name: str) -> Optional[int]:
        if screen_name in self._group_ids:
            return self._group_ids[screen_name]
        try:
            groups = await self._call("groups.getById", group_id=screen_name)
            if not groups:
                logger.warning("VK: community '%s' not found", screen_name)
                return None
            gid = int(groups[0]["id"])
            self._group_ids[screen_name] = gid
            return gid
        except Exception as exc:
            logger.error("VK groups.getById '%s': %s", screen_name, exc)
            return None

    async def _poll_wall(self, community: str) -> int:
        gid = await self._group_id(community)
        if not gid:
            return 0

        try:
            wall = await self._call(
                "wall.get",
                owner_id=-gid,
                count=self._settings.vk_posts_per_community,
                filter="owner",
            )
        except Exception as exc:
            logger.error("VK wall.get '%s': %s", community, exc)
            return 0

        items = wall.get("items", []) if isinstance(wall, dict) else []
        processed = 0

        logger.info("VK [%s]: fetched %d post(s)", community, len(items))

        for item in items:
            post_id = item.get("id")
            text = (item.get("text") or "").strip()
            if post_id is None or not text:
                continue

            ext_id = f"{gid}_{post_id}"
            if ext_id in self._seen_ids:
                continue

            if has_stop_words(text):
                logger.debug("VK [%s]: stop-word rejected post %s", community, post_id)
                continue

            if not passes_vk_filter(text):
                logger.debug("VK [%s]: no RU keyword match post %s", community, post_id)
                continue

            self._seen_ids.add(ext_id)
            processed += 1

            logger.info("VK [%s]: candidate post %s — pipeline", community, post_id)

            post = RawPost(
                external_id=ext_id,
                source=LeadSource.VK,
                text=text,
                author=str(item.get("from_id", "unknown")),
                contact=f"https://vk.com/wall-{gid}_{post_id}",
                timestamp=datetime.fromtimestamp(
                    item.get("date", 0), tz=timezone.utc
                ),
            )
            await self._on_post(post)

        return processed

    async def poll_recent(self) -> None:
        if not self._session:
            return

        logger.info("VK: polling %d communit(ies)", len(VK_TARGET_COMMUNITIES))
        total = 0

        for community in VK_TARGET_COMMUNITIES:
            try:
                count = await self._poll_wall(community)
                total += count
            except Exception as exc:
                logger.exception("VK poll '%s' error (continuing): %s", community, exc)
            await asyncio.sleep(self._settings.vk_poll_delay)

        logger.info("VK: poll complete — %d candidate(s) sent", total)

    async def start(self) -> None:
        if not self._settings.vk_api_token:
            logger.warning("VK_API_TOKEN missing — VK parser disabled")
            return
        timeout = aiohttp.ClientTimeout(total=20)
        self._session = aiohttp.ClientSession(timeout=timeout)
        logger.info(
            "VK parser ready — %d communities, RU keywords (%d phrases)",
            len(VK_TARGET_COMMUNITIES),
            len(KEYWORDS_RU),
        )

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    @property
    def is_active(self) -> bool:
        return self._session is not None
