#!/usr/bin/env python3
"""Quick check: can the notification bot reach your Telegram chat?"""
from __future__ import annotations

import asyncio
import sys

import httpx

from config import get_settings


async def main() -> int:
    settings = get_settings()
    token = settings.notification_tg_bot_token.strip()
    chat_id = settings.notification_tg_chat_id.strip()

    print(f".env loaded: token={'yes' if token else 'NO'}, chat_id={chat_id or 'NO'}")

    if not token or not chat_id:
        print("Fill NOTIFICATION_TG_BOT_TOKEN and NOTIFICATION_TG_CHAT_ID in .env")
        return 1

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    text = "🧪 Тест с сервера — если видишь это, бот настроен правильно!"

    async with httpx.AsyncClient(timeout=15.0) as client:
        me = await client.get(f"https://api.telegram.org/bot{token}/getMe")
        print("getMe:", me.json())

        response = await client.post(
            url,
            data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        )
        body = response.json()
        print("sendMessage:", body)

        if not body.get("ok"):
            print("\nОшибка! Частые причины:")
            print("  • chat not found → неверный NOTIFICATION_TG_CHAT_ID")
            print("  • unauthorized → неверный NOTIFICATION_TG_BOT_TOKEN")
            print("  • напиши боту /start в личку перед тестом")
            return 1

    print("\nOK — проверь Telegram")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
