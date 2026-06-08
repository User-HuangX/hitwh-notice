from __future__ import annotations

import logging
import re
from collections.abc import Iterable

import aiohttp

from ..models import WebPage
from ..parser import HtmlParser
from ..sources import DEFAULT_COLLEGES, DEFAULT_WEBSITE_URLS
from .base import BaseFetcher

logger = logging.getLogger(__name__)


class WebsiteFetcher(BaseFetcher):
    def __init__(self, urls: Iterable[str] | None = None, timeout: int = 20, parser: HtmlParser | None = None) -> None:
        self.urls = list(urls or DEFAULT_WEBSITE_URLS)
        self.timeout = timeout
        self.parser = parser or HtmlParser()

    async def fetch(self) -> list[WebPage]:
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        pages = []
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for url in self.urls:
                try:
                    async with session.get(url) as response:
                        response.raise_for_status()
                        html = await response.text()
                    pages.append(self.parser.parse(html, url))
                except Exception:
                    logger.exception("website_fetch_failed url=%s", url)
        logger.info("website_pages_fetched count=%s", len(pages))
        return pages

    def extract_colleges(self, pages: list[WebPage]) -> list[str]:
        found = []
        text = "\n".join(page.text for page in pages)
        for college in DEFAULT_COLLEGES:
            if college in text and college not in found:
                found.append(college)
        for match in re.findall(r"[\u4e00-\u9fa5]{2,20}学院", text):
            if match not in found and len(match) <= 20:
                found.append(match)
        logger.info("colleges_extracted count=%s", len(found))
        return found or DEFAULT_COLLEGES
