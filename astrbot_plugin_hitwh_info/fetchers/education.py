from __future__ import annotations

import logging
from collections.abc import Iterable

import aiohttp

from ..models import WebPage
from ..parser import HtmlParser
from .base import BaseFetcher

logger = logging.getLogger(__name__)

DEFAULT_EDUCATION_URLS = [
    "http://jwts.hitwh.edu.cn/cjcx/queryQmcj",
    "http://jwts.hitwh.edu.cn/pubtzgg/queryTzggByMxxs",
]

LOGIN_PAGE_KEYWORDS = ["用户登录", "统一身份认证", "loginCAS"]


class EducationFetcher(BaseFetcher):
    """带 token 认证的本科教育网抓取器（固定抓取成绩和通知）。"""

    def __init__(
        self,
        token: str,
        urls: Iterable[str] | None = None,
        timeout: int = 20,
        parser: HtmlParser | None = None,
    ) -> None:
        self.token = token
        self._token_expired = False
        self.urls = list(urls or DEFAULT_EDUCATION_URLS)
        self.timeout = timeout
        self.parser = parser or HtmlParser()

    @property
    def is_token_expired(self) -> bool:
        """标记上次抓取时 token 是否过期，由 fetch() 设置。"""
        return self._token_expired

    async def fetch(self) -> list[WebPage]:
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        pages = []
        self._token_expired = False
        headers = {
            "Cookie": self.token,
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        }
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            for url in self.urls:
                try:
                    async with session.get(url, allow_redirects=True) as response:
                        html = await response.text()
                        
                        # 检测是否跳到了登录页（token失效）
                        if any(kw in html for kw in LOGIN_PAGE_KEYWORDS):
                            logger.warning(
                                "education_token_expired url=%s", url,
                            )
                            self._token_expired = True
                            continue
                        
                        page = self.parser.parse(html, url)
                        if page.text.strip():
                            pages.append(page)
                            logger.info(
                                "education_fetch_ok url=%s chars=%s",
                                url, len(page.text),
                            )
                        else:
                            logger.warning(
                                "education_empty url=%s", url,
                            )
                except Exception:
                    logger.exception("education_fetch_error url=%s", url)
        logger.info(
            "education_pages_fetched count=%s expired=%s",
            len(pages), self._token_expired,
        )
        return pages
