from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


async def setup_browser(webvpn_base: str, cookie_str: str):
    import playwright.async_api as pw

    domain = urlparse(webvpn_base).hostname or "webvpn.hitwh.edu.cn"

    playwright = await pw.async_playwright().start()
    browser = await playwright.chromium.launch(headless=True, args=["--ignore-certificate-errors"])
    context = await browser.new_context(ignore_https_errors=True)

    for cookie_part in cookie_str.split("; "):
        if "=" in cookie_part:
            name, _, value = cookie_part.partition("=")
            await context.add_cookies([{
                "name": name.strip(),
                "value": value.strip(),
                "domain": domain,
                "path": "/",
            }])

    page = await context.new_page()
    return playwright, browser, page
