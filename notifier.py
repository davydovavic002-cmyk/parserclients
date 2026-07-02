from __future__ import annotations

import html
import logging
import re
from typing import Any

import httpx

from config import get_settings

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

_BUDGET_RE = re.compile(
    r"(?:бюджет|budget|зарплата|salary|₽|руб\.?|\$|€|USD|EUR)[:\s]*[\d\s,.]+",
    re.IGNORECASE,
)


def _escape(value: Any) -> str:
    return html.escape(str(value if value is not None else "—"), quote=False)


def _extract_budget(text: str) -> str:
    match = _BUDGET_RE.search(text)
    return match.group(0).strip() if match else "не указан"


def _format_message(lead_data: dict) -> str:
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
        link_line = f'🔍 Ссылка: <a href="{html.escape(str(link), quote=True)}">{link_escaped}</a>'
    else:
        link_line = f"🔍 Ссылка: {link_escaped}"

    return (
        "🚀 <b>НАЙДЕН НОВЫЙ ЛИД!</b>\n\n"
        f"📁 Источник: <b>{source}</b>\n"
        f"💰 Бюджет: {html.escape(str(budget), quote=False)}\n"
        f"👤 Автор/Контакт: {contact}\n"
        f"📝 Суть задачи: {summary}\n"
        f"{link_line}\n"
        f"🧠 Почему подходит (ИИ): <i>{reason}</i>"
    )


async def send_lead_notification(lead_data: dict) -> bool:
    """
    Send a qualified-lead alert to the configured Telegram bot chat.

    Expected keys: source, contact, summary, link, reason.
    Optional: budget, text (used to auto-detect budget if budget is missing).
    """
    settings = get_settings()
    token = settings.notification_tg_bot_token.strip()
    chat_id = settings.notification_tg_chat_id.strip()

    if not token or not chat_id:
        logger.debug("Telegram notifications disabled — bot token or chat id missing")
        return False

    url = _TELEGRAM_API.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": _format_message(lead_data),
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            body = response.json()
            if not body.get("ok"):
                logger.error("Telegram API error: %s", body)
                return False
        logger.info("Lead notification sent to chat %s", chat_id)
        return True
    except httpx.HTTPError as exc:
        logger.error("Failed to send Telegram notification: %s", exc)
        return False
