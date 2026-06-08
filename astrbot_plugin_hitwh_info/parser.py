from __future__ import annotations

import logging
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .models import WebPage

logger = logging.getLogger(__name__)


class HtmlParser:
    def parse(self, html: str, url: str) -> WebPage:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        title = self._title(soup)
        text = soup.get_text("\n", strip=True)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        links = []
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"].strip())
            if href.startswith(("http://", "https://")) and href not in links:
                links.append(href)
        page = WebPage(url=url, title=title, text="\n".join(lines), links=links)
        logger.info("html_parsed url=%s title=%s chars=%s links=%s", url, title, len(page.text), len(links))
        return page

    @staticmethod
    def _title(soup: BeautifulSoup) -> str:
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        h1 = soup.find("h1")
        return h1.get_text(strip=True) if h1 else ""
