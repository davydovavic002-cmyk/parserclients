from __future__ import annotations

import json
import logging
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, Playwright

logger = logging.getLogger(__name__)

# Current Chrome on Windows — realistic, not HeadlessChrome default
REALISTIC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

XHS_MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 "
    "Mobile/15E148 Safari/604.1"
)

STEALTH_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-setuid-sandbox",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--no-first-run",
    "--disable-features=TranslateUI,site-per-process",
]

VPS_LIGHT_ARGS = [
    "--single-process",
    "--js-flags=--max-old-space-size=256",
]


async def apply_stealth(page: Page) -> None:
    """Mask automation fingerprints via playwright-stealth."""
    try:
        from playwright_stealth import stealth_async

        await stealth_async(page)
    except ImportError:
        logger.warning(
            "playwright-stealth not installed — running without stealth patches"
        )
    except Exception as exc:
        logger.warning("stealth_async failed: %s", exc)


async def create_stealth_browser(
    playwright: Playwright,
    *,
    headless: bool = True,
    low_memory: bool = False,
) -> Browser:
    args = list(STEALTH_LAUNCH_ARGS)
    if low_memory:
        args.extend(VPS_LIGHT_ARGS)
    return await playwright.chromium.launch(
        headless=headless,
        args=args,
    )


async def create_stealth_context(
    browser: Browser,
    *,
    locale: str = "en-US",
    timezone_id: str = "Europe/Berlin",
) -> BrowserContext:
    context = await browser.new_context(
        user_agent=REALISTIC_USER_AGENT,
        locale=locale,
        timezone_id=timezone_id,
        viewport={"width": 1920, "height": 1080},
        screen={"width": 1920, "height": 1080},
        color_scheme="light",
        extra_http_headers={
            "Accept-Language": f"{locale},en;q=0.9",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
        },
    )
    await context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        window.chrome = { runtime: {} };
        """
    )
    return context


async def create_xhs_context(
    browser: Browser,
    *,
    storage_state_path: str = "",
    mobile: bool = False,
) -> BrowserContext:
    """
    XHS browser context with optional saved cookies.

    Default desktop UA — lighter on VPS and matches cookies from auth_xhs.py.
    Set mobile=True only if desktop gets blocked.
    """
    path = Path(storage_state_path) if storage_state_path else None
    cookies = _cookies_from_storage_state(path) if path and path.is_file() else []

    if mobile:
        kwargs: dict = {
            "user_agent": XHS_MOBILE_USER_AGENT,
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "viewport": {"width": 390, "height": 844},
            "screen": {"width": 390, "height": 844},
            "is_mobile": True,
            "has_touch": True,
            "color_scheme": "light",
            "extra_http_headers": {
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        }
        context = await browser.new_context(**kwargs)
        lang_script = "['zh-CN', 'zh']"
    else:
        context = await create_stealth_context(
            browser,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        lang_script = "['zh-CN', 'zh', 'en']"

    if cookies:
        try:
            await context.add_cookies(cookies)
            logger.info("XHS: injected %d cookies from %s", len(cookies), path)
        except Exception as exc:
            logger.warning("XHS: add_cookies failed: %s", exc)
    elif path and path.is_file():
        logger.warning("XHS: no cookies in storage state %s", path)

    await context.add_init_script(
        f"""
        Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
        Object.defineProperty(navigator, 'languages', {{ get: () => {lang_script} }});
        """
    )
    return context


async def configure_lightweight_page(page: Page) -> None:
    """Block heavy assets — prevents OOM / Page crashed on small VPS."""

    async def _route(route, request):
        if request.resource_type in ("image", "media", "font", "stylesheet"):
            await route.abort()
        else:
            await route.continue_()

    await page.route("**/*", _route)


def _cookies_from_storage_state(path: Path) -> list[dict]:
    """
    Read cookies from Playwright storage_state JSON.

    Do NOT pass storage_state= to new_context on headless VPS — Playwright
    navigates every origin (rednote.com, xiaohongshu.com) and often aborts.
    Injecting cookies avoids that bootstrap navigation.
    """
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


async def new_stealth_page(context: BrowserContext) -> Page:
    page = await context.new_page()
    await apply_stealth(page)
    return page
