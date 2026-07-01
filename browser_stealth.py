from __future__ import annotations

import logging

from playwright.async_api import Browser, BrowserContext, Page, Playwright

logger = logging.getLogger(__name__)

# Current Chrome on Windows — realistic, not HeadlessChrome default
REALISTIC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
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


async def new_stealth_page(context: BrowserContext) -> Page:
    page = await context.new_page()
    await apply_stealth(page)
    return page
