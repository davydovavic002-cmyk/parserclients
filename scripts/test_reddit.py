#!/usr/bin/env python3
"""Test Reddit — OAuth, JSON, or RSS fallback."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import httpx

from config import DEFAULT_REDDIT_SUBREDDITS, get_settings
from reddit_rss import fetch_subreddit_rss


def _user_agent(settings) -> str:
    agent = settings.reddit_user_agent.strip()
    if agent and "yourusername" not in agent.lower():
        return agent
    return "web:parserclients:1.0 (by /u/parserclients_bot)"


def test_oauth(settings) -> int:
    import praw

    reddit = praw.Reddit(
        client_id=settings.reddit_client_id.strip(),
        client_secret=settings.reddit_client_secret.strip(),
        user_agent=_user_agent(settings),
    )
    test_sub = (settings.reddit_subreddits or DEFAULT_REDDIT_SUBREDDITS)[0]
    titles = [s.title[:70] for s in reddit.subreddit(test_sub).new(limit=3)]
    print(f"\nOK (OAuth) — r/{test_sub} latest posts:")
    for t in titles:
        print(f"  • {t}")
    return 0


def test_json(settings) -> tuple[int, bool]:
    """Returns (exit_code, blocked_403)."""
    test_sub = (settings.reddit_subreddits or DEFAULT_REDDIT_SUBREDDITS)[0]
    url = f"https://www.reddit.com/r/{test_sub}/new.json"
    headers = {"User-Agent": _user_agent(settings)}
    resp = httpx.get(url, params={"limit": "3", "raw_json": "1"}, headers=headers, timeout=20)
    if resp.status_code == 403:
        return 1, True
    if resp.status_code == 429:
        print("\nFAIL: rate-limited (429) — try again in a minute")
        return 1, False
    if resp.status_code != 200:
        print(f"\nJSON FAIL: HTTP {resp.status_code}")
        return 1, False
    children = resp.json().get("data", {}).get("children", [])
    print(f"\nOK (anonymous JSON) — r/{test_sub}:")
    for child in children:
        title = (child.get("data") or {}).get("title", "")[:70]
        print(f"  • {title}")
    return 0, False


async def _test_rss_async(settings) -> int:
    test_sub = (settings.reddit_subreddits or DEFAULT_REDDIT_SUBREDDITS)[0]
    headers = {"User-Agent": _user_agent(settings)}
    async with httpx.AsyncClient(
        headers=headers, timeout=20, follow_redirects=True
    ) as client:
        items = await fetch_subreddit_rss(client, test_sub, limit_hint=3)
    if not items:
        print("\nRSS FAIL: empty feed")
        return 1
    print(f"\nOK (RSS fallback) — r/{test_sub}:")
    for item in items:
        print(f"  • {item.title[:70]}")
    return 0


def test_rss(settings) -> int:
    return asyncio.run(_test_rss_async(settings))


def main() -> int:
    settings = get_settings()
    cid = settings.reddit_client_id.strip()
    secret = settings.reddit_client_secret.strip()

    print(f"REDDIT_CLIENT_ID: {'set' if cid else 'not set'}")
    print(f"REDDIT_CLIENT_SECRET: {'set' if secret else 'not set'}")
    print(f"REDDIT_USER_AGENT: {_user_agent(settings)}")

    if cid and secret:
        try:
            return test_oauth(settings)
        except Exception as exc:
            print(f"\nOAuth FAIL: {exc}")

    print(
        "\nReddit blocks unauthenticated .json from many VPS IPs (403)."
        "\nParser falls back to RSS — no app registration needed."
    )
    code, blocked = test_json(settings)
    if code == 0:
        return 0
    if blocked:
        print("\nJSON blocked (403) — trying RSS...")
        try:
            return test_rss(settings)
        except Exception as exc:
            print(f"\nRSS FAIL: {exc}")
            return 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
