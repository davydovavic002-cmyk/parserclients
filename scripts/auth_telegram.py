#!/usr/bin/env python3
"""One-time Telethon login — creates/updates the .session file."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from telethon import TelegramClient

from config import get_settings


async def main() -> int:
    settings = get_settings()
    api_id = settings.telegram_api_id
    api_hash = settings.telegram_api_hash.strip()

    if not api_id or not api_hash:
        print("ERROR: задайте TG_API_ID и TG_API_HASH в .env")
        return 1

    if not sys.stdin.isatty():
        print(
            "ERROR: нужен интерактивный терминал (SSH с TTY).\n"
            "Не запускайте через PM2 — только вручную в SSH."
        )
        return 1

    session = settings.telegram_session
    client = TelegramClient(session, api_id, api_hash)

    print(f"Сессия: {session}.session")
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        name = me.first_name or "?"
        username = f"@{me.username}" if me.username else "(без username)"
        print(f"Уже авторизован: {name} {username}")
        await client.disconnect()
        return 0

    print()
    print("=== Вход в Telegram (Telethon) ===")
    print("Сейчас спросят номер телефона (+79...) и код из приложения Telegram.")
    print()

    await client.start()

    if not await client.is_user_authorized():
        print("ERROR: авторизация не завершена")
        await client.disconnect()
        return 1

    me = await client.get_me()
    name = me.first_name or "?"
    username = f"@{me.username}" if me.username else "(без username)"
    print()
    print(f"OK — вошли как {name} {username}")
    print(f"Файл сессии: {session}.session")
    print("Дальше: pm2 restart parserclients")
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
