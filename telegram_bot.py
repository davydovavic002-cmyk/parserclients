from __future__ import annotations

import asyncio
import html
import json
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
    "/lists — твои списки (нажми папку, чтобы открыть)\n"
    "/help — подсказки"
)

HELP_TEXT = (
    "💡 <b>Подсказки</b>\n\n"
    "• Под каждым лидом — кнопки сортировки\n"
    "• <b>📋 Списки</b> — обзор папок, нажми на папку чтобы открыть\n"
    "• <b>🔥 В работу</b> — берёшь в работу прямо сейчас\n"
    "• <b>⭐️ Избранное</b> — интересно, вернёшься позже\n"
    "• <b>📥 Позже</b> — не сейчас, но не выкидывать\n"
    "• <b>✖️ Пропустить</b> — точно не подходит\n\n"
    "Кнопки <b>⏸ Пауза</b> / <b>▶️ Запуск</b> — временно останавливают "
    "опрос источников (скаут не выключается, просто отдыхает 🛌)"
)

INBOX_NEW_KEY = "new"
INBOX_MENU_KEY = "menu"
INBOX_PAGE_SIZE = 5

INBOX_MENU_LABELS: dict[str, str] = {
    INBOX_NEW_KEY: "📬 Новые",
    **INBOX_LIST_LABELS,
}


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


def _resolve_inbox_key(key: str) -> Optional[str]:
    """Map callback key to DB inbox_list value (None = uncategorized)."""
    if key == INBOX_NEW_KEY:
        return None
    if key in INBOX_LIST_LABELS:
        return key
    return None


def _inbox_menu_label(key: str) -> str:
    return INBOX_MENU_LABELS.get(key, key)


def _truncate_button(text: str, max_len: int = 42) -> str:
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _lists_overview_keyboard(
    counts: dict[str, int], uncategorized: int
) -> dict:
    rows: list[list[dict]] = [
        [
            {
                "text": f"📬 Новые ({uncategorized})",
                "callback_data": f"inbox:{INBOX_NEW_KEY}:0",
            }
        ],
        [
            {
                "text": f"{INBOX_LIST_LABELS[LeadInboxList.ACTIVE.value]} ({counts.get(LeadInboxList.ACTIVE.value, 0)})",
                "callback_data": f"inbox:{LeadInboxList.ACTIVE.value}:0",
            },
            {
                "text": f"{INBOX_LIST_LABELS[LeadInboxList.FAVORITES.value]} ({counts.get(LeadInboxList.FAVORITES.value, 0)})",
                "callback_data": f"inbox:{LeadInboxList.FAVORITES.value}:0",
            },
        ],
        [
            {
                "text": f"{INBOX_LIST_LABELS[LeadInboxList.LATER.value]} ({counts.get(LeadInboxList.LATER.value, 0)})",
                "callback_data": f"inbox:{LeadInboxList.LATER.value}:0",
            },
            {
                "text": f"{INBOX_LIST_LABELS[LeadInboxList.SKIPPED.value]} ({counts.get(LeadInboxList.SKIPPED.value, 0)})",
                "callback_data": f"inbox:{LeadInboxList.SKIPPED.value}:0",
            },
        ],
    ]
    return {"inline_keyboard": rows}


def _inbox_page_keyboard(
    list_key: str, page: int, total: int
) -> dict:
    total_pages = max(1, (total + INBOX_PAGE_SIZE - 1) // INBOX_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    rows: list[list[dict]] = []

    nav: list[dict] = []
    if page > 0:
        nav.append(
            {
                "text": "◀️",
                "callback_data": f"inbox:{list_key}:{page - 1}",
            }
        )
    nav.append(
        {
            "text": f"{page + 1}/{total_pages}",
            "callback_data": f"inbox:{list_key}:{page}",
        }
    )
    if page < total_pages - 1:
        nav.append(
            {
                "text": "▶️",
                "callback_data": f"inbox:{list_key}:{page + 1}",
            }
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            {
                "text": "« К спискам",
                "callback_data": f"inbox:{INBOX_MENU_KEY}:0",
            }
        ]
    )
    return {"inline_keyboard": rows}


_BUDGET_LABELS = {
    "High": "High ($1,500+)",
    "Medium": "Medium ($800–$1,500)",
    "Low": "Low (<$800)",
    "Unknown": "Unknown",
}


def _format_lead_message(lead_data: dict, inbox_note: str = "") -> str:
    ai_budget = lead_data.get("estimated_budget")
    if ai_budget:
        budget = _BUDGET_LABELS.get(str(ai_budget), str(ai_budget))
    else:
        budget = lead_data.get("budget")
        if not budget and lead_data.get("text"):
            budget = _extract_budget(str(lead_data["text"]))
        budget = budget or "не указан"

    score = lead_data.get("score")
    score_line = f"📊 Score: <b>{score}/100</b>\n" if score is not None else ""

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
        f"{score_line}"
        f"💰 Бюджет (ИИ): {html.escape(str(budget), quote=False)}\n"
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

    @staticmethod
    def _encode_payload(payload: dict) -> dict[str, Any]:
        """Telegram Bot API expects form fields; nested objects as JSON strings."""
        encoded: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                encoded[key] = json.dumps(value)
            elif value is not None:
                encoded[key] = value
        return encoded

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
        response = await self._http.post(
            self._api(method),
            data=self._encode_payload(payload),
        )
        if response.status_code == 409:
            logger.error(
                "Telegram 409 Conflict: this bot token is already used elsewhere "
                "(second PM2 instance, local run, or webhook). Stop duplicates."
            )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            desc = body.get("description", body)
            logger.error("Telegram %s error: %s", method, desc)
        return body

    async def send_startup_ping(self, parser_names: list[str]) -> None:
        """Confirm bot → chat delivery right after PM2 start."""
        if not self.can_notify:
            logger.warning(
                "Startup ping skipped — set NOTIFICATION_TG_BOT_TOKEN and "
                "NOTIFICATION_TG_CHAT_ID in .env next to main.py"
            )
            return

        parsers_line = html.escape(
            ", ".join(parser_names) if parser_names else "подключаются…"
        )
        text = (
            "🐾 <b>Скаут запущен!</b>\n\n"
            f"Источники: {parsers_line}\n"
            "Напиши /start — покажу меню\n"
            "Или /test — пришлю пробный лид"
        )
        try:
            await self.send_text(text, reply_markup=_main_keyboard(is_scout_paused()))
            logger.info("Startup ping sent to chat %s", self._chat_id)
        except Exception as exc:
            logger.error(
                "Startup ping FAILED — проверь NOTIFICATION_TG_CHAT_ID=%s: %s",
                self._chat_id,
                exc,
            )

    async def _handle_test_lead(self) -> None:
        await self.send_lead(
            {
                "lead_id": 0,
                "source": "test",
                "text": "Budget: $500 — тестовый лид",
                "contact": "@test_client",
                "summary": "Нужен лендинг для стартапа (тест)",
                "link": "https://example.com",
                "reason": "Пробное сообщение — бот работает ✅",
            }
        )
        await self.send_text(
            "🧪 Пробный лид отправлен! Если видишь карточку выше — уведомления работают.",
            reply_markup=_main_keyboard(is_scout_paused()),
        )

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

    async def _edit_message(
        self,
        chat_id: Any,
        message_id: int,
        text: str,
        *,
        reply_markup: Optional[dict] = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        await self._call("editMessageText", payload)

    def _lead_to_message_data(self, record) -> dict:
        return {
            "lead_id": record.id,
            "source": record.source.value,
            "text": record.text,
            "contact": record.contact or record.author or "—",
            "summary": record.summary or record.reason or "—",
            "link": record.contact or "—",
            "reason": record.reason or "—",
        }

    async def _lists_overview_text(self) -> str:
        counts = await self._db.get_inbox_counts()
        uncategorized = await self._db.count_uncategorized_qualified()
        lines = [
            "📋 <b>Твои списки</b>\n",
            "Нажми на папку, чтобы открыть лиды внутри 👇\n",
            f"📬 Новые (без папки): <b>{uncategorized}</b>",
        ]
        for key, label in INBOX_LIST_LABELS.items():
            lines.append(f"{label}: <b>{counts.get(key, 0)}</b>")
        return "\n".join(lines)

    async def _inbox_page_text(
        self, list_key: str, page: int
    ) -> tuple[str, dict, int]:
        db_list = _resolve_inbox_key(list_key)
        if list_key != INBOX_MENU_KEY and list_key not in INBOX_MENU_LABELS:
            list_key = INBOX_NEW_KEY
            db_list = None

        if list_key == INBOX_MENU_KEY:
            counts = await self._db.get_inbox_counts()
            uncategorized = await self._db.count_uncategorized_qualified()
            return (
                await self._lists_overview_text(),
                _lists_overview_keyboard(counts, uncategorized),
                0,
            )

        total = await self._db.count_qualified_by_inbox(db_list)
        total_pages = max(1, (total + INBOX_PAGE_SIZE - 1) // INBOX_PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        offset = page * INBOX_PAGE_SIZE
        records = await self._db.get_qualified_by_inbox(
            db_list, limit=INBOX_PAGE_SIZE, offset=offset
        )

        label = _inbox_menu_label(list_key)
        lines = [
            f"{label}\n",
            f"Всего: <b>{total}</b> · страница <b>{page + 1}/{total_pages}</b>\n",
        ]

        if not records:
            lines.append("<i>Пока пусто — перетащи сюда лиды кнопками под карточками.</i>")
        else:
            lines.append("<i>Нажми на лид ниже, чтобы открыть карточку:</i>")

        keyboard_rows: list[list[dict]] = []
        for index, record in enumerate(records, start=1):
            global_index = offset + index
            preview = record.summary or record.reason or record.text
            lines.append(
                f"\n<b>{global_index}.</b> "
                f"[{_escape(record.source.value)}] "
                f"{_escape(preview[:100])}"
            )
            if record.id:
                keyboard_rows.append(
                    [
                        {
                            "text": _truncate_button(
                                f"{global_index}. {preview}"
                            ),
                            "callback_data": f"openlead:{record.id}",
                        }
                    ]
                )

        markup = _inbox_page_keyboard(list_key, page, total)
        if keyboard_rows:
            markup["inline_keyboard"] = keyboard_rows + markup["inline_keyboard"]

        return "\n".join(lines), markup, total

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
        stats = await self._db.get_pipeline_stats()
        settings = get_settings()
        gemini_ok = bool(settings.gemini_api_key.strip())
        uncategorized = await self._db.count_uncategorized_qualified()
        unnotified = await self._db.count_unnotified_qualified()
        paused = is_scout_paused()
        state = "⏸ на паузе" if paused else "🟢 работает"
        parsers = ", ".join(self._active_parsers) or "—"

        lines = [
            "📊 <b>Статус скаута</b>\n",
            f"Состояние: <b>{state}</b>",
            f"Источники: {html.escape(parsers)}",
            f"Gemini API: {'✅ ключ задан' if gemini_ok else '❌ GEMINI_API_KEY пустой'}",
            "",
            "<b>Воронка (вся история):</b>",
            f"📥 Записей в базе: <b>{stats['total_rows']}</b>",
            f"⏳ В очереди (pending): <b>{stats['pending']}</b>",
            f"🤖 Проверено Gemini: <b>{stats['checked_by_ai']}</b>",
            f"✅ Квалифицировано: <b>{stats['qualified']}</b>",
            f"❌ Отклонено: <b>{stats['rejected']}</b>",
            f"📬 Ждут сортировки: <b>{uncategorized}</b>",
            f"📨 Не отправлено в TG: <b>{unnotified}</b>",
        ]

        if unnotified > 0:
            lines.append(
                f"\n💡 <i>Есть {unnotified} лид(ов) без уведомления — "
                "напиши /push чтобы прислать.</i>"
            )

        if stats["by_source"]:
            lines.append("\n<b>По источникам:</b>")
            for src, counts in sorted(stats["by_source"].items()):
                q = counts.get("qualified", 0)
                r = counts.get("rejected", 0)
                p = counts.get("pending", 0)
                lines.append(f"• {html.escape(src)}: ✅{q} ❌{r} ⏳{p}")

        if stats["total_rows"] == 0:
            lines.append(
                "\n⚠️ <i>Парсеры ещё не отправили ни одного поста. "
                "Подожди 1–2 цикла (5–10 мин) после обновления.</i>"
            )
        elif not gemini_ok:
            lines.append("\n⚠️ <i>GEMINI_API_KEY не задан в .env на сервере.</i>")
        elif stats["checked_by_ai"] == 0 and stats["pending"] > 0:
            lines.append("\n⚠️ <i>Записи есть, но Gemini не вызывался.</i>")
        elif stats["checked_by_ai"] == 0:
            lines.append(
                "\n⚠️ <i>Gemini ещё не вызывался — смотри pm2 logs.</i>"
            )
        elif stats["qualified"] == 0 and stats["rejected"] > 0:
            lines.append("\n<b>Последние отказы ИИ:</b>")
            for item in stats["recent_rejections"]:
                reason = html.escape(item["reason"][:120])
                lines.append(f"• [{html.escape(item['source'])}] {reason}")

        return "\n".join(lines)

    async def _handle_lists(self) -> None:
        counts = await self._db.get_inbox_counts()
        uncategorized = await self._db.count_uncategorized_qualified()
        await self.send_text(
            await self._lists_overview_text(),
            reply_markup=_lists_overview_keyboard(counts, uncategorized),
        )
        """Send Telegram cards for qualified leads that were never notified."""
        if not self.can_notify:
            return 0

        records = await self._db.get_unnotified_qualified_leads()
        sent = 0
        for record in records:
            try:
                ok = await self.send_lead(
                    {
                        "lead_id": record.id,
                        "source": record.source.value,
                        "text": record.text,
                        "contact": record.contact or record.author or "—",
                        "summary": record.summary or record.reason or "—",
                        "link": record.contact or "—",
                        "reason": record.reason or "Квалифицирован",
                    }
                )
                if ok:
                    await self._db.mark_lead_notified(
                        record.external_id, record.source
                    )
                    sent += 1
                    await asyncio.sleep(0.4)
            except Exception as exc:
                logger.error("push lead %s failed: %s", record.external_id, exc)
        return sent

    async def _handle_push(self) -> None:
        count = await self.push_unnotified_leads()
        if count:
            text = f"📨 Отправила <b>{count}</b> лид(ов) — проверь сообщения выше 👆"
        else:
            text = "📭 Нет неотправленных лидов (или уведомления уже были)."
        await self.send_text(text, reply_markup=_main_keyboard(is_scout_paused()))

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

    async def push_unnotified_leads(self) -> int:
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
        elif text.startswith("/push"):
            await self._handle_push()
        elif text.startswith("/test"):
            await self._handle_test_lead()
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

    async def _handle_inbox_callback(
        self,
        query_id: str,
        chat_id: Any,
        message_id: Optional[int],
        data: str,
    ) -> None:
        parts = data.split(":")
        if len(parts) != 3:
            return

        _, list_key, page_raw = parts
        try:
            page = int(page_raw)
        except ValueError:
            page = 0

        text, markup, _ = await self._inbox_page_text(list_key, page)

        await self._call(
            "answerCallbackQuery",
            {"callback_query_id": query_id},
        )

        if message_id:
            try:
                await self._edit_message(
                    chat_id, message_id, text, reply_markup=markup
                )
            except httpx.HTTPError as exc:
                logger.debug("inbox edit failed, sending new message: %s", exc)
                await self._send_to_chat(chat_id, text, reply_markup=markup)

    async def _handle_open_lead_callback(
        self, query_id: str, chat_id: Any, data: str
    ) -> None:
        try:
            lead_id = int(data.split(":", 1)[1])
        except (IndexError, ValueError):
            return

        record = await self._db.get_lead_by_id(lead_id)
        await self._call(
            "answerCallbackQuery",
            {"callback_query_id": query_id},
        )
        if not record:
            await self._send_to_chat(chat_id, "Лид не найден в базе.")
            return

        folder_note = ""
        if record.inbox_list:
            folder_note = (
                f"📂 <b>Список:</b> {INBOX_LIST_LABELS.get(record.inbox_list, record.inbox_list)}\n\n"
            )

        await self._send_to_chat(
            chat_id,
            folder_note + _format_lead_message(self._lead_to_message_data(record)),
            reply_markup=_lead_inline_keyboard(lead_id),
        )

    async def _handle_callback(self, callback: dict) -> None:
        query_id = callback.get("id")
        data = callback.get("data") or ""
        message = callback.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")

        if not self._authorized(chat_id):
            return

        if data.startswith("inbox:"):
            await self._handle_inbox_callback(
                query_id, chat_id, message_id, data
            )
            return

        if data.startswith("openlead:"):
            await self._handle_open_lead_callback(query_id, chat_id, data)
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
