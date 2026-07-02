from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import Any, Optional

import httpx

from config import get_settings
from db import LeadDatabase
from models import INBOX_LIST_LABELS, LeadInboxList

logger = logging.getLogger(__name__)

_scout_paused = False
_bot: Optional["NotificationBot"] = None

_BUDGET_RE = re.compile(
    r"(?:бюджет|budget|зарплата|salary|₽|руб\.?|\$|€|USD|EUR)[:\s]*[\d\s,.]+",
    re.IGNORECASE,
)

WELCOME_TEXT = (
    "Привет! Я твой маленький скаут-бот 🐾\n\n"
    "Буду приносить горячие лиды с веб-площадок, а ты решаешь — "
    "в работу, в избранное или «потом».\n\n"
    "Когда прилетит лид — нажми кнопку под сообщением, "
    "и я аккуратно сложу его в нужный список 📋\n\n"
    "<b>Команды:</b>\n"
    "/start — это меню\n"
    "/status — как дела у скаута\n"
    "/lists — твои списки\n"
    "/help — подсказки"
)

HELP_TEXT = (
    "💡 <b>Подсказки</b>\n\n"
    "• Под каждым лидом — кнопки сортировки\n"
    "• <b>🔥 В работу</b> — берёшь в работу прямо сейчас\n"
    "• <b>⭐️ Избранное</b> — интересно, вернёшься позже\n"
    "• <b>📥 Позже</b> — не сейчас, но не выкидывать\n"
    "• <b>✖️ Пропустить</b> — точно не подходит\n\n"
    "Кнопки <b>⏸ Пауза</b> / <b>▶️ Запуск</b> — временно останавливают "
    "опрос источников (скаут не выключается, просто отдыхает 🛌)"
)


def is_scout_paused() -> bool:
    return _scout_paused


def set_scout_paused(value: bool) -> None:
    global _scout_paused
    _scout_paused = value


def _escape(value: Any) -> str:
    return html.escape(str(value if value is not None else "—"), quote=False)


def _extract_budget(text: str) -> str:
    match = _BUDGET_RE.search(text)
    return match.group(0).strip() if match else "не указан"


def _main_keyboard(paused: bool) -> dict:
    pause_btn = "▶️ Запуск" if paused else "⏸ Пауза"
    return {
        "keyboard": [
            [{"text": "📊 Статус"}, {"text": "📋 Списки"}],
            [{"text": pause_btn}, {"text": "💡 Помощь"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def _lead_inline_keyboard(lead_id: int) -> dict:
    prefix = f"lead:{lead_id}:"
    return {
        "inline_keyboard": [
            [
                {
                    "text": INBOX_LIST_LABELS[LeadInboxList.ACTIVE.value],
                    "callback_data": f"{prefix}{LeadInboxList.ACTIVE.value}",
                },
                {
                    "text": INBOX_LIST_LABELS[LeadInboxList.FAVORITES.value],
                    "callback_data": f"{prefix}{LeadInboxList.FAVORITES.value}",
                },
            ],
            [
                {
                    "text": INBOX_LIST_LABELS[LeadInboxList.LATER.value],
                    "callback_data": f"{prefix}{LeadInboxList.LATER.value}",
                },
                {
                    "text": INBOX_LIST_LABELS[LeadInboxList.SKIPPED.value],
                    "callback_data": f"{prefix}{LeadInboxList.SKIPPED.value}",
                },
            ],
        ]
    }


def _format_lead_message(lead_data: dict, inbox_note: str = "") -> str:
    budget = lead_data.get("budget")
    if not budget and lead_data.get("text"):
        budget = _extract_budget(str(lead_data["text"]))
    budget = budget or "не указан"

    source = _escape(lead_data.get("source", "—"))
    contact = _escape(lead_data.get("contact", "—"))
    summary = _escape(lead_data.get("summary", "—"))
    reason = _escape(lead_data.get("reason", "—"))
    link = lead_data.get("link") or "—"
    link_escaped = _escape(link)

    if link != "—" and str(link).startswith(("http://", "https://")):
        link_line = (
            f'🔍 Ссылка: <a href="{html.escape(str(link), quote=True)}">'
            f"{link_escaped}</a>"
        )
    else:
        link_line = f"🔍 Ссылка: {link_escaped}"

    header = "🚀 <b>НАЙДЕН НОВЫЙ ЛИД!</b>"
    if inbox_note:
        header = f"{header}\n{inbox_note}"

    footer = (
        "\n\n<i>👇 Куда положить этого лида?</i>"
        if not inbox_note
        else ""
    )

    return (
        f"{header}\n\n"
        f"📁 Источник: <b>{source}</b>\n"
        f"💰 Бюджет: {html.escape(str(budget), quote=False)}\n"
        f"👤 Автор/Контакт: {contact}\n"
        f"📝 Суть задачи: {summary}\n"
        f"{link_line}\n"
        f"🧠 Почему подходит (ИИ): <i>{reason}</i>"
        f"{footer}"
    )


class NotificationBot:
    """Personal Telegram bot: welcome menu, scout pause/resume, lead sorting."""

    def __init__(self, db: LeadDatabase) -> None:
        self._db = db
        self._settings = get_settings()
        self._token = self._settings.notification_tg_bot_token.strip()
        self._chat_id = self._settings.notification_tg_chat_id.strip()
        self._http: Optional[httpx.AsyncClient] = None
        self._running = False
        self._offset = 0
        self._active_parsers: list[str] = []

    @property
    def is_active(self) -> bool:
        """Polling works when token + HTTP client are ready."""
        return bool(self._token and self._http)

    @property
    def can_notify(self) -> bool:
        """Lead push requires both token and chat id."""
        return bool(self._token and self._chat_id and self._http)

    def set_active_parsers(self, names: list[str]) -> None:
        self._active_parsers = names

    def _api(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self._token}/{method}"

    def _authorized(self, chat_id: Any) -> bool:
        return str(chat_id) == str(self._chat_id)

    async def setup(self) -> None:
        if not self._token:
            logger.warning(
                "Notification bot DISABLED — set NOTIFICATION_TG_BOT_TOKEN in .env"
            )
            return

        self._http = httpx.AsyncClient(timeout=35.0)
        try:
            me = await self._call("getMe", {})
            username = me.get("result", {}).get("username", "?")
        except Exception as exc:
            logger.error("Invalid NOTIFICATION_TG_BOT_TOKEN: %s", exc)
            await self._http.aclose()
            self._http = None
            return

        try:
            await self._http.post(self._api("deleteWebhook"))
        except httpx.HTTPError as exc:
            logger.warning("deleteWebhook failed: %s", exc)

        if not self._chat_id:
            logger.warning(
                "NOTIFICATION_TG_CHAT_ID is empty — bot listens, "
                "write @%s /start to get your chat id",
                username,
            )
        else:
            logger.info(
                "Notification bot ready — @%s, chat_id=%s",
                username,
                self._chat_id,
            )

    async def stop(self) -> None:
        self._running = False
        if self._http:
            await self._http.aclose()
            self._http = None

    async def _call(self, method: str, payload: dict) -> dict:
        assert self._http is not None
        response = await self._http.post(self._api(method), json=payload)
        if response.status_code == 409:
            logger.error(
                "Telegram 409 Conflict: this bot token is already used elsewhere "
                "(second PM2 instance, local run, or webhook). Stop duplicates."
            )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            logger.error("Telegram %s error: %s", method, body)
        return body

    async def _send_to_chat(
        self,
        chat_id: Any,
        text: str,
        *,
        reply_markup: Optional[dict] = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        await self._call("sendMessage", payload)

    async def send_text(
        self,
        text: str,
        *,
        reply_markup: Optional[dict] = None,
        parse_mode: str = "HTML",
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        await self._call("sendMessage", payload)

    async def send_lead(self, lead_data: dict) -> bool:
        if not self.can_notify:
            return False

        lead_id = lead_data.get("lead_id")
        markup = _lead_inline_keyboard(int(lead_id)) if lead_id else None

        try:
            await self.send_text(
                _format_lead_message(lead_data),
                reply_markup=markup,
            )
            logger.info("Lead notification sent (id=%s)", lead_id)
            return True
        except httpx.HTTPError as exc:
            logger.error("Failed to send lead notification: %s", exc)
            return False

    async def _status_text(self) -> str:
        total = await self._db.count_qualified_leads()
        uncategorized = await self._db.count_uncategorized_qualified()
        paused = is_scout_paused()
        state = "⏸ на паузе" if paused else "🟢 работает"
        parsers = ", ".join(self._active_parsers) or "—"

        return (
            f"📊 <b>Статус скаута</b>\n\n"
            f"Состояние: <b>{state}</b>\n"
            f"Источники: {html.escape(parsers)}\n"
            f"Квалифицированных лидов: <b>{total}</b>\n"
            f"Ждут сортировки: <b>{uncategorized}</b> 📬"
        )

    async def _lists_text(self) -> str:
        counts = await self._db.get_inbox_counts()
        uncategorized = await self._db.count_uncategorized_qualified()
        lines = [
            "📋 <b>Твои списки</b>\n",
            f"📬 Новые (без папки): <b>{uncategorized}</b>",
        ]
        for key, label in INBOX_LIST_LABELS.items():
            lines.append(f"{label}: <b>{counts.get(key, 0)}</b>")
        return "\n".join(lines)

    async def _handle_start(self) -> None:
        await self.send_text(
            WELCOME_TEXT,
            reply_markup=_main_keyboard(is_scout_paused()),
        )

    async def _handle_help(self) -> None:
        await self.send_text(
            HELP_TEXT,
            reply_markup=_main_keyboard(is_scout_paused()),
        )

    async def _handle_status(self) -> None:
        await self.send_text(
            await self._status_text(),
            reply_markup=_main_keyboard(is_scout_paused()),
        )

    async def _handle_lists(self) -> None:
        await self.send_text(
            await self._lists_text(),
            reply_markup=_main_keyboard(is_scout_paused()),
        )

    async def _toggle_pause(self, resume: bool) -> None:
        set_scout_paused(not resume)
        if is_scout_paused():
            text = "⏸ Скаут прилёг отдохнуть. Нажми <b>▶️ Запуск</b>, когда будешь готов."
        else:
            text = "▶️ Скаут снова в деле! Побежал искать лиды 🐾"
        await self.send_text(text, reply_markup=_main_keyboard(is_scout_paused()))

    async def _handle_setup_hint(self, chat_id: Any) -> None:
        if not self._chat_id:
            text = (
                "👋 Привет! Я скаут-бот, но меня ещё не до конца настроили.\n\n"
                f"Твой chat ID:\n<code>{chat_id}</code>\n\n"
                "Добавь на сервере в файл <code>.env</code>:\n"
                f"<code>NOTIFICATION_TG_CHAT_ID={chat_id}</code>\n\n"
                "Затем перезапусти:\n<code>pm2 restart parserclients</code>\n\n"
                "После этого снова напиши /start 🐾"
            )
        else:
            text = (
                "⚠️ Chat ID не совпадает с настройками на сервере.\n\n"
                f"Твой ID: <code>{chat_id}</code>\n"
                f"В .env указано: <code>{self._chat_id}</code>\n\n"
                "Исправь <code>NOTIFICATION_TG_CHAT_ID</code> в .env "
                "и выполни <code>pm2 restart parserclients</code>."
            )
        logger.warning(
            "Unauthorized bot message: chat_id=%s (expected=%s)",
            chat_id,
            self._chat_id or "(empty)",
        )
        await self._send_to_chat(chat_id, text)

    async def _handle_message(self, message: dict) -> None:
        chat_id = message.get("chat", {}).get("id")
        if chat_id is None:
            return

        if not self._authorized(chat_id):
            await self._handle_setup_hint(chat_id)
            return

        text = (message.get("text") or "").strip()
        if not text:
            return

        if text.startswith("/start"):
            await self._handle_start()
        elif text.startswith("/help"):
            await self._handle_help()
        elif text.startswith("/status"):
            await self._handle_status()
        elif text.startswith("/lists"):
            await self._handle_lists()
        elif text in ("📊 Статус", "Статус"):
            await self._handle_status()
        elif text in ("📋 Списки", "Списки"):
            await self._handle_lists()
        elif text in ("💡 Помощь", "Помощь"):
            await self._handle_help()
        elif text in ("⏸ Пауза", "Пауза"):
            await self._toggle_pause(resume=False)
        elif text in ("▶️ Запуск", "Запуск"):
            await self._toggle_pause(resume=True)
        else:
            await self.send_text(
                "Не понял 🤔 Нажми /start или выбери кнопку в меню.",
                reply_markup=_main_keyboard(is_scout_paused()),
            )

    async def _handle_callback(self, callback: dict) -> None:
        query_id = callback.get("id")
        data = callback.get("data") or ""
        message = callback.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")

        if not self._authorized(chat_id):
            return

        if not data.startswith("lead:"):
            await self._call(
                "answerCallbackQuery",
                {"callback_query_id": query_id, "text": "Неизвестная кнопка"},
            )
            return

        parts = data.split(":", 2)
        if len(parts) != 3:
            return

        _, lead_id_raw, list_name = parts
        try:
            lead_id = int(lead_id_raw)
        except ValueError:
            return

        if list_name not in INBOX_LIST_LABELS:
            return

        saved = await self._db.set_lead_inbox_list(lead_id, list_name)
        label = INBOX_LIST_LABELS[list_name]
        toast = f"✅ Лид в списке «{label}»" if saved else "Лид уже был отсортирован"

        await self._call(
            "answerCallbackQuery",
            {"callback_query_id": query_id, "text": toast, "show_alert": False},
        )

        if saved and message_id:
            inbox_note = f"📂 <b>Список:</b> {label}"
            original_text = message.get("text") or message.get("caption") or ""
            # Rebuild from stored message is fragile; append note instead
            new_text = original_text
            if "📂" not in original_text:
                new_text = original_text.replace(
                    "🚀 <b>НАЙДЕН НОВЫЙ ЛИД!</b>",
                    f"🚀 <b>НАЙДЕН НОВЫЙ ЛИД!</b>\n{inbox_note}",
                    1,
                )
                new_text = new_text.replace(
                    "\n\n<i>👇 Куда положить этого лида?</i>",
                    "",
                )

            try:
                await self._call(
                    "editMessageText",
                    {
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": new_text,
                        "parse_mode": "HTML",
                        "reply_markup": {"inline_keyboard": []},
                    },
                )
            except httpx.HTTPError as exc:
                logger.debug("editMessageText failed: %s", exc)

    async def _handle_update(self, update: dict) -> None:
        if "message" in update:
            await self._handle_message(update["message"])
        elif "callback_query" in update:
            await self._handle_callback(update["callback_query"])

    async def run_polling(self) -> None:
        if not self.is_active:
            return

        self._running = True
        logger.info("Notification bot polling started")

        while self._running:
            try:
                body = await self._call(
                    "getUpdates",
                    {
                        "offset": self._offset,
                        "timeout": 25,
                        "allowed_updates": ["message", "callback_query"],
                    },
                )
                for update in body.get("result", []):
                    self._offset = update["update_id"] + 1
                    try:
                        await self._handle_update(update)
                    except Exception as exc:
                        logger.exception("Bot update error: %s", exc)
            except asyncio.CancelledError:
                break
            except httpx.HTTPError as exc:
                logger.error("Bot polling HTTP error: %s", exc)
                await asyncio.sleep(5)
            except Exception as exc:
                logger.exception("Bot polling error: %s", exc)
                await asyncio.sleep(5)

        logger.info("Notification bot polling stopped")


async def start_notification_bot(db: LeadDatabase) -> Optional[NotificationBot]:
    global _bot
    bot = NotificationBot(db)
    await bot.setup()
    if bot.is_active:
        _bot = bot
        return bot
    return None


async def send_lead_notification(lead_data: dict) -> bool:
    if _bot and _bot.can_notify:
        return await _bot.send_lead(lead_data)
    return False
