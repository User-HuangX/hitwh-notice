from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..models import WebPage
from ..parser import HtmlParser
from .base import BaseFetcher

logger = logging.getLogger(__name__)

DEFAULT_EDUCATION_URLS = [
    ("POST", "/cjcx/queryQmcj", "pjsfkf=&pageXnxq=&pageBkcxbj=&pageSfjg=&pageKcmc="),
    ("GET", "/pubtzgg/queryTzggByMxxs", ""),
]

LOGIN_PAGE_KEYWORDS = ["用户登录", "统一身份认证", "loginCAS", "vpn_the_connection"]

RETRYABLE_EXCEPTIONS = (
    aiohttp.ClientError,
    asyncio.TimeoutError,
)


def _is_login_page(html: str) -> bool:
    return any(kw in html for kw in LOGIN_PAGE_KEYWORDS)


class EducationFetcher(BaseFetcher):

    def __init__(
        self,
        token: str,
        urls: Iterable[tuple[str, str, str]] | None = None,
        timeout: int = 20,
        parser: HtmlParser | None = None,
        webvpn_base: str = "",
    ) -> None:
        self.token = token
        self._token_expired = False
        self._url_defs = list(urls or DEFAULT_EDUCATION_URLS)
        self.timeout = timeout
        self.parser = parser or HtmlParser()
        self._base = webvpn_base.rstrip("/") if webvpn_base else "http://jwts.hitwh.edu.cn"

    @property
    def is_token_expired(self) -> bool:
        return self._token_expired

    async def fetch(self) -> list[WebPage]:
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        pages = []
        self._token_expired = False
        headers = {
            "Cookie": self.token,
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        }
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            await self._sync_vpn_cookies(session)
            for method, path, data in self._url_defs:
                url = f"{self._base}{path}"
                try:
                    if "queryQmcj" in path:
                        pages.extend(await self._fetch_grade_pages(session, url))
                    else:
                        page = await self._fetch_single(session, method, url, data, path)
                        if page:
                            pages.append(page)
                except Exception:
                    logger.exception("education_fetch_error url=%s", url)
        logger.info("education_pages_fetched count=%s expired=%s", len(pages), self._token_expired)
        return pages

    @retry(retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS), stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=1, max=10), reraise=True)
    async def _fetch_single(self, session: aiohttp.ClientSession, method: str,
                            url: str, data: str, path: str) -> WebPage | None:
        if method == "POST":
            async with session.post(url, data=data, allow_redirects=True) as response:
                html = await response.text()
        else:
            async with session.get(url, allow_redirects=True) as response:
                html = await response.text()

        if _is_login_page(html):
            logger.warning("education_token_expired url=%s", url)
            self._token_expired = True
            return None

        page = self.parser.parse(html, path)
        page.raw_html = html
        if not page.text.strip():
            return None
        logger.info("education_fetch_ok url=%s chars=%s", path, len(page.text))
        return page

    async def _sync_vpn_cookies(self, session: aiohttp.ClientSession) -> None:
        vpn_host = self._base.split("/http/")[0] if "/http/" in self._base else "https://webvpn.hitwh.edu.cn"
        ts = int(time.time() * 1000)
        cookie_url = f"{vpn_host}/wengine-vpn/cookie?method=get&host=jwts.hitwh.edu.cn&scheme=http&path=/cjcx/queryQmcj&vpn_timestamp={ts}"
        try:
            async with session.get(cookie_url) as resp:
                await resp.text()
            logger.info("vpn_cookies_synced")
        except Exception:
            logger.warning("vpn_cookies_sync_failed")

    async def _fetch_grade_pages(self, session: aiohttp.ClientSession, base_url: str) -> list[WebPage]:
        pages = []
        page_no = 1
        page_count = 1
        while page_no <= page_count:
            post_data = f"pageNo={page_no}&pageSize=20&pageCount={page_count}&pageXnxq=&pageBkcxbj=&pageSfjg=&pageKcmc="
            try:
                async with session.post(base_url, data=post_data, allow_redirects=True) as response:
                    html = await response.text()
                if _is_login_page(html):
                    logger.warning("education_token_expired url=%s", base_url)
                    self._token_expired = True
                    return pages
                page = self.parser.parse(html, base_url)
                page.raw_html = html
                pages.append(page)
                if page_no == 1:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(html, "html.parser")
                    pc = soup.find("input", id="pageCount")
                    if pc and pc.get("value"):
                        page_count = int(pc["value"])
                logger.info("education_fetch_ok url=%s page=%s/%s chars=%s", base_url, page_no, page_count, len(page.text))
            except Exception:
                logger.exception("education_fetch_error url=%s page=%s", base_url, page_no)
            page_no += 1
        return pages
