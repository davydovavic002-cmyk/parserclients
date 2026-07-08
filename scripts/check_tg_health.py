#!/usr/bin/env python3
"""Check Telegram source (Telethon) and notification bot on the server."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import httpx
from telethon import TelegramClient

from config import STARTING_TELEGRAM_CHANNELS, get_settings
from db import LeadDatabase


async def check_telethon() -> tuple[bool, str]:
    settings = get_settings()
    api_id = settings.telegram_api_id
    api_hash = settings.telegram_api_hash.strip()

    if not api_id or not api_hash:
        return False, "нет TG_API_ID / TG_API_HASH в .env"

    session = settings.telegram_session
    session_path = Path(f"{session}.session")
    if not session_path.exists():
        return False, f"файл {session}.session не найден — запусти scripts/auth_telegram.py"

    client = TelegramClient(session, api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return False, (
                f"сессия {session}.session не авторизована — "
                "python scripts/auth_telegram.py"
            )

        me = await client.get_me()
        username = f"@{me.username}" if me.username else "(без username)"

        readable = 0
        for channel in STARTING_TELEGRAM_CHANNELS[:3]:
            try:
                entity = await client.get_entity(channel)
                messages = await client.get_messages(entity, limit=1)
                if messages:
                    readable += 1
            except Exception:
                pass

        return True, (
            f"авторизован как {me.first_name or '?'} {username}, "
            f"seed-каналов читается: {readable}/3"
        )
    except Exception as exc:
        return False, f"ошибка подключения: {exc}"
    finally:
        await client.disconnect()


async def check_notification_bot(*, send_test: bool) -> tuple[bool, str]:
    settings = get_settings()
    token = settings.notification_tg_bot_token.strip()
    chat_id = settings.notification_tg_chat_id.strip()

    if not token or not chat_id:
        return False, "нет NOTIFICATION_TG_BOT_TOKEN / NOTIFICATION_TG_CHAT_ID"

    async with httpx.AsyncClient(timeout=15.0) as client:
        me_resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
        me_body = me_resp.json()
        if not me_body.get("ok"):
            return False, f"getMe failed: {me_body.get('description', me_body)}"

        bot_name = me_body["result"].get("username", "?")

        if not send_test:
            return True, f"бот @{bot_name} OK (без отправки сообщения)"

        msg_resp = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": "🩺 Health check — бот уведомлений работает",
            },
        )
        msg_body = msg_resp.json()
        if not msg_body.get("ok"):
            return False, f"sendMessage failed: {msg_body.get('description', msg_body)}"

        return True, f"бот @{bot_name} — тестовое сообщение отправлено"


async def check_db_chats() -> str:
    settings = get_settings()
    db = LeadDatabase(settings.db_path)
    await db.connect()
    try:
        chats = await db.get_discovered_chats()
        return f"discovered_chats в БД: {len(chats)}"
    finally:
        await db.close()


async def main() -> int:
    send_test = "--send" in sys.argv

    print("=== Telegram health check ===\n")

    tg_ok, tg_detail = await check_telethon()
    print(f"{'✅' if tg_ok else '❌'} Telethon (источник): {tg_detail}")

    bot_ok, bot_detail = await check_notification_bot(send_test=send_test)
    print(f"{'✅' if bot_ok else '❌'} Bot API (уведомления): {bot_detail}")

    try:
        db_info = await check_db_chats()
        print(f"ℹ️  {db_info}")
    except Exception as exc:
        print(f"⚠️  БД: {exc}")

    print()
    if tg_ok and bot_ok:
        print("OK — оба Telegram-компонента работают")
        return 0

    print("FAIL — см. строки выше")
    if not tg_ok:
        print("\nФикс источника:")
        print("  cd /home/deploy/parserclients")
        print("  source .venv/bin/activate")
        print("  python scripts/auth_telegram.py")
        print("  pm2 restart parserclients")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
