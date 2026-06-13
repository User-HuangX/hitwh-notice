from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable
from urllib.parse import urljoin, urlparse

import aiohttp

from ..models import WebPage
from ..parser import HtmlParser
from ..sources import DEFAULT_COLLEGES, DEFAULT_WEBSITE_URLS
from .base import BaseFetcher

logger = logging.getLogger(__name__)

_NEWS_PATTERNS = [
    re.compile(r"/(?:info|news|article|xwzx|xyxw|tzgg|xsdt)/\d+"),
    re.compile(r"/\d{4}/\d{4,6}"),
    re.compile(r"/(?:content|detail)\.(?:jsp|html|s?htm)\?"),
    re.compile(r"/\w+/\d+\.html?"),
]


def _looks_like_article(url: str) -> bool:
    for pat in _NEWS_PATTERNS:
        if pat.search(url):
            return True
    return False


class WebsiteFetcher(BaseFetcher):
    def __init__(self, urls: Iterable[str] | None = None, timeout: int = 20,
                 parser: HtmlParser | None = None, max_articles: int = 30,
                 fetch_articles: bool = True) -> None:
        self.urls = list(urls or DEFAULT_WEBSITE_URLS)
        self.timeout = timeout
        self.parser = parser or HtmlParser()
        self.max_articles = max_articles
        self.fetch_articles = fetch_articles

    async def fetch(self) -> list[WebPage]:
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        pages: list[WebPage] = []
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for url in self.urls:
                try:
                    page = await self._fetch_page(session, url)
                    if page:
                        pages.append(page)
                        if self.fetch_articles:
                            articles = await self._fetch_articles(session, page)
                            pages.extend(articles)
                except Exception:
                    logger.exception("website_fetch_failed url=%s", url)
        logger.info("website_pages_fetched count=%s", len(pages))
        return pages

    async def _fetch_page(self, session: aiohttp.ClientSession, url: str) -> WebPage | None:
        async with session.get(url) as response:
            response.raise_for_status()
            html = await response.text()
        return self.parser.parse(html, url)

    async def _fetch_articles(self, session: aiohttp.ClientSession, homepage: WebPage) -> list[WebPage]:
        article_urls: list[str] = []
        base_domain = urlparse(homepage.url).netloc
        for link in homepage.links:
            parsed = urlparse(link)
            if parsed.netloc and parsed.netloc != base_domain:
                continue
            if _looks_like_article(link) and link not in article_urls:
                article_urls.append(link)
        article_urls = article_urls[:self.max_articles]
        if not article_urls:
            return []

        tasks = [self._fetch_page(session, u) for u in article_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        pages: list[WebPage] = []
        for result in results:
            if isinstance(result, WebPage) and result.text.strip():
                pages.append(result)
            elif isinstance(result, Exception):
                logger.warning("article_fetch_failed %s", result)
        logger.info("articles_fetched count=%s candidates=%s", len(pages), len(article_urls))
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
