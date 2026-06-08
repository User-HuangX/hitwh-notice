"""HITWH 校园信息插件 - 以本科教育网成绩查询为核心"""

from __future__ import annotations
import asyncio
import logging
import re
from typing import Any

from .db import HitwhDB
from .embedding import Embedder
from .fact_splitter import FactSplitter
from .fetchers.education import EducationFetcher
from .fetchers.website import WebsiteFetcher
from .hierarchy import HierarchyMatcher

logger = logging.getLogger(__name__)

try:
    from astrbot.api.event import filter
    from astrbot.api.star import Context, Star
except Exception:
    Context = Any
    class Star:
        def __init__(self, context: Any = None, config: dict[str, Any] | None = None) -> None:
            self.context = context
            self.config = config or {}

    class _Filter:
        @staticmethod
        def command(*a, **kw):
            def deco(f): return f
            return deco
        @staticmethod
        def event_message_type(*a, **kw):
            def deco(f): return f
            return deco
        @staticmethod
        def llm_tool(*a, **kw):
            def deco(f): return f
            return deco
    filter = _Filter()


class HitwhInfoPlugin(Star):
    def __init__(self, context: Context, config: dict[str, Any] | None = None) -> None:
        super().__init__(context, config)
        self.config = config or {}
        self.db = HitwhDB(self.config.get("postgres_dsn"))
        self.hierarchy = HierarchyMatcher(self.db)
        self.embedder = Embedder(self._llm_provider())
        self.splitter = FactSplitter(self._llm_provider())
        self.website_fetcher = WebsiteFetcher(self.config.get("website_urls"))
        self.education_fetcher = EducationFetcher(token=self.config.get("token", ""))
        self._sync_task = None
        
        # 教育网API地址
        self._edu_base = "http://jwts.hitwh.edu.cn"
        self._edu_headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    async def initialize(self) -> None:
        await self.db.init_schema()
        await self.hierarchy.bootstrap(self.config.get("colleges"))
        logger.info("hitwh_plugin_ready")

    async def terminate(self) -> None:
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
        await self.db.close()
        logger.info("hitwh_plugin_terminated")

    # ========== 教育网直连查询 ==========

    async def _edu_request(self, path: str, data: str = "", method: str = "POST") -> str:
        """带Cookie直接请求教育网"""
        token = self.config.get("token", "")
        if not token:
            return ""
        headers = {**self._edu_headers, "Cookie": token}
        try:
            import httpx
            if method == "GET":
                resp = await httpx.AsyncClient(timeout=15).get(
                    f"{self._edu_base}{path}", headers=headers
                )
            else:
                resp = await httpx.AsyncClient(timeout=15).post(
                    f"{self._edu_base}{path}", headers=headers, data=data
                )
            if "用户登录" in resp.text or "loginCAS" in resp.text:
                logger.warning("education_token_expired")
                return "__TOKEN_EXPIRED__"
            return resp.text
        except Exception:
            logger.exception("edu_request_failed path=%s", path)
            return ""

    async def query_grades(self) -> list[dict]:
        """直接查询教育网期末成绩"""
        html = await self._edu_request("/cjcx/queryQmcj", "xnxqid=&kcCode=&ksxz=0&sfjg=")
        if not html or html == "__TOKEN_EXPIRED__":
            return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        grades = []
        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 12:
                grades.append({
                    "学期": cells[1].get_text(strip=True),
                    "院系": cells[2].get_text(strip=True),
                    "课程": cells[4].get_text(strip=True),
                    "学分": cells[7].get_text(strip=True),
                    "成绩": cells[10].get_text(strip=True),
                    "备注": cells[11].get_text(strip=True) if len(cells) > 11 else "",
                })
        return grades

    async def query_notices(self) -> str:
        """直接查询教育网通知公告"""
        html = await self._edu_request("/pubtzgg/queryTzggByMxxs", method="GET")
        if not html or html == "__TOKEN_EXPIRED__":
            return ""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text(separator="\n", strip=True)

    # ========== 命令 ==========

    @filter.command("成绩")
    async def cmd_grades(self, event: Any = None):
        """查询个人成绩 / 成绩 微积分"""
        keyword = ""
        msg = event.get_message_str() if hasattr(event, "get_message_str") else ""
        for pre in ["/成绩", "成绩"]:
            if msg.startswith(pre):
                keyword = msg[len(pre):].strip()
                break
        
        token = self.config.get("token", "")
        if not token:
            if hasattr(event, "plain_result"):
                yield event.plain_result("⚠️ 未配置教务网token，发 /set_token 设置")
            else:
                yield "no_token"
            return
        
        yield event.plain_result("正在查询成绩，稍等...")
        
        grades = await self.query_grades()
        if not grades:
            yield event.plain_result("⚠️ 没查到成绩，可能是token过期了，发 /set_token 更新")
            return
        
        # 如果有关键字就筛选
        if keyword:
            grades = [g for g in grades if keyword in g["课程"]]
            if not grades:
                yield event.plain_result(f"没找到「{keyword}」相关的成绩")
                return
        
        # 按学期分组
        semesters = {}
        for g in grades:
            semesters.setdefault(g["学期"], []).append(g)
        
        lines = []
        for sem in sorted(semesters.keys(), reverse=True):
            lines.append(f"\n📚 {sem}")
            for g in semesters[sem]:
                score = g["成绩"]
                remark = f" ({g['备注']})" if g["备注"] else ""
                if score and score.isdigit():
                    score_i = int(score)
                    icon = "✅" if score_i >= 60 else "❌"
                else:
                    icon = "⚠️"
                lines.append(f"  {icon} {g['课程']}  {score}分{remark}")
        
        yield event.plain_result("📊 成绩查询结果：\n" + "\n".join(lines))

    @filter.command("通知")
    async def cmd_notices(self, event: Any = None):
        """查询教育网最新通知"""
        yield event.plain_result("正在查询最新通知...")
        text = await self.query_notices()
        if not text:
            yield event.plain_result("⚠️ 未配置token或已过期，发 /set_token 设置")
        else:
            yield event.plain_result(f"📰 教务网通知：\n{text[:500]}")

    @filter.command("hitwh_sync")
    async def cmd_sync(self, event: Any = None):
        """手动同步所有数据源"""
        yield event.plain_result("正在同步，这可能需要一分钟...")
        result = await self.sync_all()
        parts = []
        if result.get("education", 0) > 0:
            parts.append(f"教育网 {result['education']}条")
        if result.get("website", 0) > 0:
            parts.append(f"官网 {result['website']}条")
        msg = "同步完成：" + "、".join(parts) if parts else "没有新数据"
        if result.get("_token_warn"):
            msg += f"\n⚠️ {result['_token_warn']}"
        yield event.plain_result(msg)

    @filter.command("set_token")
    async def cmd_set_token(self, event: Any = None):
        """设置教务网Cookie"""
        msg = event.get_message_str() if hasattr(event, "get_message_str") else ""
        for pre in ["/set_token", "set_token"]:
            if msg.startswith(pre):
                msg = msg[len(pre):].strip()
                break
        if not msg or len(msg) < 10:
            yield event.plain_result("用法：/set_token name=value; JSESSIONID=xxx; HIT=xxx\n浏览器F12拷的Cookie贴过来")
            return
        self.config["token"] = msg
        yield event.plain_result("✅ 教务网token已更新！试试发 /成绩 或 /通知")

    @filter.command("hitwh")
    async def cmd_help(self, event: Any = None):
        """查看命令帮助"""
        yield event.plain_result(
            "📋 HITWH校园信息插件\n"
            "  /成绩 [课程名]  - 查成绩，可加课程名筛选\n"
            "  /通知           - 查教务网最新通知\n"
            "  /set_token <c>  - 设置教务网Cookie\n"
            "  /hitwh_sync     - 手动同步所有数据\n"
            "  /hitwh          - 本帮助\n"
            "\n问「我的微积分成绩」也会自动查"
        )

    # ========== 数据同步 ==========

    async def sync_all(self) -> dict[str, Any]:
        await self.db.init_schema()
        
        # 1. 教育网优先
        education_count = 0
        token_warn = None
        if self.config.get("token"):
            edu_pages = await self.education_fetcher.fetch()
            if self.education_fetcher._token_expired:
                token_warn = "token已失效，发 /set_token 更新"
            elif edu_pages:
                education_count = await self._sync_facts(edu_pages, "education")
        else:
            token_warn = "未配置token"
        
        # 2. 官网作为补充
        pages = await self.website_fetcher.fetch()
        colleges = self.website_fetcher.extract_colleges(pages)
        await self.hierarchy.bootstrap(colleges)
        website_count = await self._sync_facts(pages, "website")
        
        return {"education": education_count, "website": website_count, "_token_warn": token_warn}

    async def _sync_facts(self, pages: list[Any], source_type: str) -> int:
        records = []
        for page in pages:
            facts = await self.splitter.split(page.text)
            for fact in facts:
                emb = await self.embedder.embed(fact)
                hierarchy_id = await self.hierarchy.match_source_name(fact)
                records.append({
                    "fact": fact, "embedding": emb,
                    "hierarchy_id": hierarchy_id,
                    "source_type": source_type,
                    "source_name": page.url,
                    "metadata": {"title": page.title},
                })
        return await self.db.insert_facts(records)

    @filter.llm_tool(name="search_school_info")
    async def search_school_info(self, query: str, scope: str = "auto", **kwargs: Any) -> str:
        """LLM工具：搜索校园信息"""
        user_class = kwargs.get("user_class") or self.config.get("my_class")
        hierarchy_ids = await self.hierarchy.resolve_scope(scope, user_class=user_class, query=query)
        emb = await self.embedder.embed(query)
        facts = await self.db.search_facts(emb, hierarchy_ids=hierarchy_ids, top_k=5)
        return self._format_facts(facts)

    def _llm_provider(self) -> Any | None:
        if hasattr(self.context, "get_using_provider"):
            try:
                return self.context.get_using_provider()
            except Exception:
                pass
        return None

    @staticmethod
    def _format_facts(facts: list[dict]) -> str:
        if not facts:
            return "未检索到相关信息。"
        return "\n".join(
            f"{i}. {r['fact']}（来源:{r.get('source_type','?')}）"
            for i, r in enumerate(facts, 1)
        )
