from __future__ import annotations

import asyncio
import logging
import random
import sys
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, UserAlreadyParticipantError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.types import Channel, Chat, Message, User

from config import TG_DISCOVERY_KEYWORDS, get_settings
from quality import is_post_too_old
from db import LeadDatabase, SEED_KEYWORD
from filters import passes_tg_filter
from models import DiscoveredChat, LeadSource, RawPost

logger = logging.getLogger(__name__)

PostHandler = Callable[[RawPost], Awaitable[None]]


class TelegramParser:
    """
    Telethon parser: reads chats from discovered_chats (seeded by db.py on first run),
    global discovery for new chats, pre-filter → pipeline.
    """

    def __init__(self, db: LeadDatabase, on_post: PostHandler) -> None:
        self._db = db
        self._settings = get_settings()
        self._on_post = on_post
        self._client: Optional[TelegramClient] = None
        self._seen_ids: set[str] = set()
        self._joined: set[str] = set()
        self._status_detail: str = "не запущен"

    def _build_client(self) -> TelegramClient:
        return TelegramClient(
            self._settings.telegram_session,
            self._settings.telegram_api_id,
            self._settings.telegram_api_hash,
        )

    async def _sleep_rate(self, lo: float, hi: float) -> None:
        await asyncio.sleep(random.uniform(lo, hi))

    async def _handle_flood(self, exc: FloodWaitError) -> None:
        wait = exc.seconds + random.randint(5, 30)
        logger.warning("FloodWait — sleeping %d s", wait)
        await asyncio.sleep(wait)

    async def discover_new_channels(self) -> int:
        """Global search by TG_DISCOVERY_KEYWORDS → discovered_chats."""
        if not self._client:
            return 0

        added = 0
        for keyword in TG_DISCOVERY_KEYWORDS:
            try:
                result = await self._client(SearchRequest(q=keyword, limit=50))
                for chat in result.chats:
                    if not isinstance(chat, (Channel, Chat)):
                        continue
                    username = getattr(chat, "username", None)
                    if not username:
                        continue
                    if await self._db.add_discovered_chat(username, keyword):
                        added += 1

                await self._sleep_rate(
                    self._settings.tg_search_delay_min,
                    self._settings.tg_search_delay_max,
                )
            except FloodWaitError as exc:
                await self._handle_flood(exc)
            except Exception as exc:
                logger.error("TG discovery '%s' failed: %s", keyword, exc)

        logger.info("TG discovery: %d new chat(s)", added)
        return added

    async def _try_join(self, username: str) -> bool:
        if username in self._joined or not self._client:
            return False
        try:
            entity = await self._client.get_entity(username)
            await self._client(JoinChannelRequest(entity))
            self._joined.add(username)
            logger.info("Joined @%s", username)
            return True
        except UserAlreadyParticipantError:
            self._joined.add(username)
            return False
        except FloodWaitError as exc:
            await self._handle_flood(exc)
        except Exception as exc:
            logger.debug("Join @%s failed: %s", username, exc)
        return False

    async def _emit_message(
        self, username: str, message: Message, author: str, contact: Optional[str]
    ) -> None:
        if not message.text:
            return

        dedup = f"{username}:{message.id}"
        if dedup in self._seen_ids:
            return
        self._seen_ids.add(dedup)

        if not passes_tg_filter(message.text):
            return

        msg_time = message.date or datetime.now(timezone.utc)
        if is_post_too_old(msg_time, self._settings.max_post_age_hours):
            logger.debug(
                "TG @%s: skip old message (>%dh)",
                username,
                self._settings.max_post_age_hours,
            )
            return

        post = RawPost(
            external_id=dedup,
            source=LeadSource.TELEGRAM,
            text=message.text,
            author=author,
            contact=contact,
            timestamp=msg_time,
        )
        await self._on_post(post)

    async def _poll_chat(self, chat: DiscoveredChat, limit: int) -> bool:
        """
        Try reading messages from a chat.
        Returns True if polling succeeded, False if access denied.
        """
        assert self._client is not None
        try:
            entity = await self._client.get_entity(chat.username)
            async for message in self._client.iter_messages(entity, limit=limit):
                sender = await message.get_sender()
                author = "unknown"
                if isinstance(sender, User):
                    author = sender.username or sender.first_name or str(sender.id)
                await self._emit_message(chat.username, message, author, None)
            return True
        except FloodWaitError as exc:
            await self._handle_flood(exc)
            return False
        except Exception as exc:
            logger.debug("Poll @%s failed (will retry join): %s", chat.username, exc)
            return False

    async def _handle_realtime(self, event: events.NewMessage.Event) -> None:
        chat = await event.get_chat()
        username = getattr(chat, "username", None)
        if not username:
            return

        sender = await event.get_sender()
        author = "unknown"
        contact: Optional[str] = None
        if isinstance(sender, User):
            author = sender.username or sender.first_name or str(sender.id)
            if sender.username:
                contact = f"@{sender.username}"

        await self._emit_message(username, event.message, author, contact)

    async def poll_recent(self, limit: int = 50) -> None:
        """
        Poll all chats from DB. Seed channels (from db.py) are tried first
        without join — public channels are readable immediately.
        """
        if not self._client:
            return

        chats = await self._db.get_discovered_chats()
        if not chats:
            logger.debug("No chats in discovered_chats")
            return

        seed_count = sum(1 for c in chats if c.keyword == SEED_KEYWORD)
        logger.debug("Polling %d chat(s) (%d seed)", len(chats), seed_count)

        joins_this_cycle = 0
        daily_cap = self._settings.tg_join_daily_max

        for chat in chats:
            ok = await self._poll_chat(chat, limit)

            if not ok and chat.username not in self._joined:
                if joins_this_cycle < daily_cap:
                    if await self._try_join(chat.username):
                        joins_this_cycle += 1
                        await self._sleep_rate(
                            self._settings.tg_join_delay_min,
                            self._settings.tg_join_delay_max,
                        )
                        ok = await self._poll_chat(chat, limit)

            if not ok:
                logger.warning("Skipping @%s — no read access", chat.username)

            await self._sleep_rate(
                self._settings.tg_poll_delay_min,
                self._settings.tg_poll_delay_max,
            )

    async def run_discovery_cycle(self) -> None:
        await self.discover_new_channels()

    async def start(self) -> None:
        if not self._settings.telegram_api_id or not self._settings.telegram_api_hash:
            self._status_detail = "нет TG_API_ID / TG_API_HASH в .env"
            logger.warning("Telegram credentials missing — TG parser disabled")
            return

        self._client = self._build_client()
        try:
            await self._client.connect()
            if not await self._client.is_user_authorized():
                session_name = self._settings.telegram_session
                if sys.stdin.isatty():
                    logger.info(
                        "Telegram: сессия '%s.session' не авторизована — "
                        "введите телефон и код ниже (или: python scripts/auth_telegram.py)",
                        session_name,
                    )
                    print(
                        "\n=== Telegram: введите телефон (+79...) и код из приложения ===\n",
                        flush=True,
                    )
                    await self._client.start()
                    if not await self._client.is_user_authorized():
                        self._status_detail = "авторизация не завершена"
                        await self._client.disconnect()
                        self._client = None
                        return
                    me = await self._client.get_me()
                    logger.info(
                        "Telegram: авторизация OK — %s (@%s)",
                        me.first_name,
                        me.username or "?",
                    )
                else:
                    self._status_detail = (
                        f"сессия {session_name}.session не авторизована — "
                        "в SSH: python scripts/auth_telegram.py, затем pm2 restart"
                    )
                    logger.error(
                        "Telegram: сессия '%s.session' не авторизована. "
                        "PM2 не может ввести телефон интерактивно. "
                        "Один раз в SSH выполните: "
                        "cd parserclients && source .venv/bin/activate && "
                        "python scripts/auth_telegram.py — "
                        "введите телефон и код, затем pm2 restart parserclients. "
                        "TG-парсер пропущен, остальные источники продолжат работу.",
                        session_name,
                    )
                    await self._client.disconnect()
                    self._client = None
                    return
        except Exception as exc:
            self._status_detail = f"ошибка подключения: {exc}"
            logger.exception("Telegram parser init failed: %s", exc)
            if self._client:
                await self._client.disconnect()
            self._client = None
            return

        self._status_detail = "поиск каналов + опрос чатов"
        self._client.add_event_handler(self._handle_realtime, events.NewMessage())

        chats = await self._db.get_discovered_chats()
        seed = [c.username for c in chats if c.keyword == SEED_KEYWORD]
        logger.info(
            "Telegram parser started — %d chat(s) in DB (%d seed)",
            len(chats),
            len(seed),
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.disconnect()
            self._client = None

    @property
    def is_active(self) -> bool:
        return self._client is not None

    @property
    def status_detail(self) -> str:
        return self._status_detail if not self.is_active else "поиск каналов + опрос чатов"
