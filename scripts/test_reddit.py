#!/usr/bin/env python3
"""Test Reddit — OAuth if keys set, else anonymous public JSON."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import httpx

from config import DEFAULT_REDDIT_SUBREDDITS, get_settings


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


def test_anonymous(settings) -> int:
    test_sub = (settings.reddit_subreddits or DEFAULT_REDDIT_SUBREDDITS)[0]
    url = f"https://www.reddit.com/r/{test_sub}/new.json"
    headers = {"User-Agent": _user_agent(settings)}
    resp = httpx.get(url, params={"limit": "3", "raw_json": "1"}, headers=headers, timeout=20)
    if resp.status_code == 429:
        print("\nFAIL: rate-limited (429) — try again in a minute")
        return 1
    if resp.status_code != 200:
        print(f"\nFAIL: HTTP {resp.status_code}")
        return 1
    children = resp.json().get("data", {}).get("children", [])
    print(f"\nOK (anonymous JSON, no app registration) — r/{test_sub}:")
    for child in children:
        title = (child.get("data") or {}).get("title", "")[:70]
        print(f"  • {title}")
    return 0


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
            print("Falling back to anonymous JSON test...")
            return test_anonymous(settings)

    print(
        "\nReddit closed self-service app registration (~2025)."
        "\nParser uses anonymous .json API — no REDDIT_CLIENT_ID needed."
    )
    try:
        return test_anonymous(settings)
    except Exception as exc:
        print(f"\nFAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
