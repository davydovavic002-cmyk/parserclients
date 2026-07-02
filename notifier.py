"""Backward-compatible re-export — use telegram_bot for full bot API."""

from telegram_bot import send_lead_notification

__all__ = ["send_lead_notification"]
