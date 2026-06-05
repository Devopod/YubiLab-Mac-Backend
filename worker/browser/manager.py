"""Playwright browser manager for testing and deployment."""
import asyncio
import base64
from worker.config import config

_browser_instance = None
_playwright_instance = None


async def get_browser():
    global _browser_instance, _playwright_instance

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ImportError("Playwright not installed")

    _playwright_instance = await async_playwright().start()
    _browser_instance = await _playwright_instance.chromium.launch(
        headless=config.PLAYWRIGHT_HEADLESS,
        args=['--no-sandbox', '--disable-setuid-sandbox']
    )
    context = await _browser_instance.new_context(
        viewport={'width': 1280, 'height': 720}
    )
    page = await context.new_page()

    return BrowserSession(page, context)


class BrowserSession:
    def __init__(self, page, context):
        self.page = page
        self.context = context

    async def navigate(self, url):
        response = await self.page.goto(url, wait_until='networkidle', timeout=30000)
        return {
            "url": self.page.url,
            "status": response.status if response else None,
            "title": await self.page.title()
        }

    async def screenshot(self, selector=None):
        if selector:
            element = await self.page.query_selector(selector)
            if element:
                img_bytes = await element.screenshot()
            else:
                img_bytes = await self.page.screenshot(full_page=True)
        else:
            img_bytes = await self.page.screenshot(full_page=True)
        return base64.b64encode(img_bytes).decode()

    async def click(self, selector):
        await self.page.click(selector, timeout=10000)

    async def fill(self, selector, value):
        await self.page.fill(selector, value)

    async def assert_page(self, assertion_type, value, selector=None):
        if assertion_type == "visible":
            el = await self.page.query_selector(selector or value)
            return el is not None and await el.is_visible()
        elif assertion_type == "text_present":
            content = await self.page.content()
            return value in content
        elif assertion_type == "url_contains":
            return value in self.page.url
        elif assertion_type == "title_contains":
            title = await self.page.title()
            return value in title
        return False

    async def close(self):
        try:
            await self.context.close()
        except Exception:
            pass
