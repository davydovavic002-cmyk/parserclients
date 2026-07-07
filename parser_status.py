from __future__ import annotations

import html
from dataclasses import dataclass

PARSER_ORDER: tuple[str, ...] = (
    "Telegram",
    "XHS",
    "Naver",
    "Boards",
    "GoogleRadar",
    "Reddit",
    "Behance",
)

_registry: dict[str, "ParserStatus"] = {}


@dataclass
class ParserStatus:
    name: str
    active: bool
    detail: str


def set_parser_status(name: str, active: bool, detail: str = "") -> None:
    if active and not detail:
        detail = "работает"
    _registry[name] = ParserStatus(name=name, active=active, detail=detail)


def get_parser_statuses() -> list[ParserStatus]:
    ordered: list[ParserStatus] = []
    for name in PARSER_ORDER:
        if name in _registry:
            ordered.append(_registry[name])
    for name, status in _registry.items():
        if name not in PARSER_ORDER:
            ordered.append(status)
    return ordered


def format_status_lines_html() -> list[str]:
    lines: list[str] = []
    for item in get_parser_statuses():
        icon = "✅" if item.active else "❌"
        detail = html.escape(item.detail) if item.detail else "—"
        lines.append(f"{icon} <b>{item.name}</b> — {detail}")
    return lines
