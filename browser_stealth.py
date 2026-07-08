from __future__ import annotations

import logging

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
) -> Browser:
    return await playwright.chromium.launch(
        headless=headless,
        args=STEALTH_LAUNCH_ARGS,
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
) -> BrowserContext:
    """Mobile-like context — XHS blocks desktop headless more often."""
    from pathlib import Path

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
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
        },
    }
    path = Path(storage_state_path) if storage_state_path else None
    if path and path.is_file():
        kwargs["storage_state"] = str(path)
        logger.info("XHS: loaded storage state from %s", path)

    context = await browser.new_context(**kwargs)
    await context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
        """
    )
    return context


async def new_stealth_page(context: BrowserContext) -> Page:
    page = await context.new_page()
    await apply_stealth(page)
    return page
