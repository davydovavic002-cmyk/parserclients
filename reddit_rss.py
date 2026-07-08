from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx

logger = logging.getLogger(__name__)

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
_POST_ID_RE = re.compile(r"/comments/([a-z0-9]+)/")
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class RedditRssPost:
    id: str
    title: str
    selftext: str
    author: str
    permalink: str
    created_utc: float


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text)).strip()


def _post_id_from_link(link: str) -> str:
    match = _POST_ID_RE.search(link)
    return match.group(1) if match else ""


def parse_reddit_atom(xml_text: str) -> list[RedditRssPost]:
    posts: list[RedditRssPost] = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        logger.warning("Reddit RSS parse error: %s", exc)
        return []

    for entry in root.findall("atom:entry", ATOM_NS):
        title = (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").strip()
        link_el = entry.find("atom:link", ATOM_NS)
        link = link_el.get("href", "") if link_el is not None else ""
        content = entry.findtext("atom:content", default="", namespaces=ATOM_NS) or ""
        summary = entry.findtext("atom:summary", default="", namespaces=ATOM_NS) or ""
        author_el = entry.find("atom:author", ATOM_NS)
        author = ""
        if author_el is not None:
            author = (author_el.findtext("atom:name", default="", namespaces=ATOM_NS) or "").strip()

        updated = entry.findtext("atom:updated", default="", namespaces=ATOM_NS) or ""
        try:
            created = parsedate_to_datetime(updated) if updated else datetime.now(timezone.utc)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            created_utc = created.timestamp()
        except (TypeError, ValueError, OverflowError):
            created_utc = datetime.now(timezone.utc).timestamp()

        post_id = _post_id_from_link(link)
        if not post_id or not title:
            continue

        body = _strip_html(content or summary)
        permalink = link.replace("https://www.reddit.com", "") if link.startswith("https://") else link

        posts.append(
            RedditRssPost(
                id=post_id,
                title=title,
                selftext=body,
                author=author or "unknown",
                permalink=permalink,
                created_utc=created_utc,
            )
        )
    return posts


async def fetch_subreddit_rss(
    client: httpx.AsyncClient,
    subreddit: str,
    *,
    sort: str = "new",
    limit_hint: int = 25,
) -> list[RedditRssPost]:
    url = f"https://www.reddit.com/r/{subreddit}/{sort}/.rss"
    params = {"limit": str(min(limit_hint, 100))}
    resp = await client.get(url, params=params)
    if resp.status_code == 429:
        raise httpx.HTTPStatusError("rate limited", request=resp.request, response=resp)
    if resp.status_code != 200:
        raise httpx.HTTPStatusError(
            f"HTTP {resp.status_code}", request=resp.request, response=resp
        )
    return parse_reddit_atom(resp.text)
