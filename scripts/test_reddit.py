#!/usr/bin/env python3
"""Test Reddit API credentials."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import DEFAULT_REDDIT_SUBREDDITS, get_settings


def main() -> int:
    settings = get_settings()
    cid = settings.reddit_client_id.strip()
    secret = settings.reddit_client_secret.strip()
    agent = settings.reddit_user_agent.strip()

    print(f"REDDIT_CLIENT_ID: {'set' if cid else 'MISSING'}")
    print(f"REDDIT_CLIENT_SECRET: {'set' if secret else 'MISSING'}")
    print(f"REDDIT_USER_AGENT: {agent}")

    if not cid or not secret:
        print("\nFill REDDIT_* in .env — see .env.example for steps")
        return 1

    import praw

    reddit = praw.Reddit(
        client_id=cid,
        client_secret=secret,
        user_agent=agent,
    )

    subs = settings.reddit_subreddits or DEFAULT_REDDIT_SUBREDDITS
    test_sub = subs[0]
    try:
        subreddit = reddit.subreddit(test_sub)
        titles = [s.title[:70] for s in subreddit.new(limit=3)]
    except Exception as exc:
        print(f"\nFAIL: {exc}")
        return 1

    print(f"\nOK — r/{test_sub} latest posts:")
    for t in titles:
        print(f"  • {t}")
    print("\nRestart PM2 after adding keys: pm2 restart parserclients")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
