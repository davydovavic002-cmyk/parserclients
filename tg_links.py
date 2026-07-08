from __future__ import annotations

import re
from typing import Optional

_TG_HANDLE_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_]{3,})")


def build_tg_message_link(external_id: str) -> Optional[str]:
    """Build https://t.me/channel/123 from external_id '{username}:{message_id}'."""
    if ":" not in external_id:
        return None
    username, msg_id = external_id.split(":", 1)
    username = username.lstrip("@").strip()
    if username and msg_id.isdigit():
        return f"https://t.me/{username}/{msg_id}"
    return None


def extract_tg_contacts(
    text: str,
    *,
    author: str = "",
    channel_username: str = "",
    sender_contact: Optional[str] = None,
) -> str:
    """Collect @handles from author, sender and post text."""
    parts: list[str] = []
    channel_lower = channel_username.lstrip("@").lower()

    def _add(value: str) -> None:
        value = value.strip()
        if not value or value in parts:
            return
        parts.append(value)

    if sender_contact and sender_contact.startswith("@"):
        _add(sender_contact)

    if author and author != "unknown":
        if author.isdigit():
            pass
        elif author.startswith("@"):
            _add(author)
        elif re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,}", author):
            _add(f"@{author}")
        else:
            _add(author)

    for match in _TG_HANDLE_RE.finditer(text or ""):
        handle = f"@{match.group(1)}"
        if handle.lstrip("@").lower() == channel_lower:
            continue
        _add(handle)
        if len(parts) >= 4:
            break

    if parts:
        return ", ".join(parts)
    return "— (контакт в посте — открой ссылку)"


def resolve_tg_lead_urls(
    external_id: str,
    text: str,
    *,
    author: str = "",
    stored_contact: Optional[str] = None,
) -> tuple[str, str]:
    """Return (contact_display, message_link) for Telegram leads."""
    channel_username = external_id.split(":", 1)[0] if ":" in external_id else ""
    link = build_tg_message_link(external_id) or "—"
    contact = extract_tg_contacts(
        text,
        author=author,
        channel_username=channel_username,
        sender_contact=stored_contact,
    )
    return contact, link
