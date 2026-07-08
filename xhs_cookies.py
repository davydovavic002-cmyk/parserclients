from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def cookies_from_storage_state(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("XHS: could not read storage state %s: %s", path, exc)
        return []

    raw = data.get("cookies") or []
    cookies: list[dict] = []
    for item in raw:
        name = item.get("name")
        if not name or "value" not in item:
            continue
        domain = item.get("domain")
        if not domain:
            continue
        cookie: dict = {
            "name": name,
            "value": item["value"],
            "domain": domain,
            "path": item.get("path") or "/",
        }
        expires = item.get("expires")
        if isinstance(expires, (int, float)) and expires > 0:
            cookie["expires"] = expires
        if "httpOnly" in item:
            cookie["httpOnly"] = bool(item["httpOnly"])
        if "secure" in item:
            cookie["secure"] = bool(item["secure"])
        same_site = item.get("sameSite")
        if same_site in ("Strict", "Lax", "None"):
            cookie["sameSite"] = same_site
        cookies.append(cookie)
    return cookies


def cookie_dict_from_storage_state(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in cookies_from_storage_state(path):
        out[item["name"]] = item["value"]
    return out
