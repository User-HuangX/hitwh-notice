from __future__ import annotations

import logging
from typing import Any

from .db import HitwhDB
from .embedding import Embedder
from .fact_splitter import FactSplitter
from .fetchers.qq_channel import QQChannelFetcher
from .fetchers.qq_group import QQGroupFetcher
from .fetchers.website import WebsiteFetcher
from .hierarchy import HierarchyMatcher

logger = logging.getLogger(__name__)

try:
    from astrbot.api.event import filter
    from astrbot.api.star import Context, Star, register
except Exception:
    Context = Any

    class Star:  # type: ignore[no-redef]
        def __init__(self, context: Any = None, config: dict[str, Any] | None = None) -> None:
            self.context = context
            self.config = config or {}

    def register(*_args: Any, **_kwargs: Any):
        def deco(cls):
            return cls
        return deco

    class _Filter:
        @staticmethod
        def command(*_args: Any, **_kwargs: Any):
            def deco(func):
                return func
            return deco

        @staticmethod
        def llm_tool(*_args: Any, **_kwargs: Any):
            def deco(func):
                return func
            return deco

    filter = _Filter()


@register("astrbot_plugin_hitwh_info", "hx", "HITWH校园信息检索插件", "0.1.0")
class HitwhInfoPlugin(Star):
    def __init__(self, context: Context, config: dict[str, Any] | None = None) -> None:
        super().__init__(context, config)
        self.config = config or {}
        self.db = HitwhDB(self.config.get("postgres_dsn"))
        self.hierarchy = HierarchyMatcher(self.db)
        self.embedder = Embedder(self._llm_provider())
        self.splitter = FactSplitter(self._llm_provider())
        self.website_fetcher = WebsiteFetcher(self.config.get("website_urls"))
        self.group_fetcher = QQGroupFetcher(self.config.get("qq_groups", []))
        self.channel_fetcher = QQChannelFetcher(self.config.get("qq_channels", []))

    async def initialize(self) -> None:
        await self.db.init_schema()
        await self.hierarchy.bootstrap(self.config.get("colleges"))
        logger.info("hitwh_info_plugin_initialized")

    async def terminate(self) -> None:
        await self.db.close()
        logger.info("hitwh_info_plugin_terminated")

    @filter.command("hitwh_sync")
    async def hitwh_sync(self, event: Any = None):
        result = await self.sync_all()
        message = (
            f"HITWH同步完成：官网{result['website']}条，"
            f"QQ群{result['qq_group']}条，QQ频道{result['qq_channel']}条。"
        )
        if event is not None and hasattr(event, "plain_result"):
            yield event.plain_result(message)
        else:
            yield message

    @filter.llm_tool(name="search_school_info")
    async def search_school_info(self, query: str, scope: str = "auto", **kwargs: Any) -> str:
        user_class = kwargs.get("user_class") or self.config.get("my_class")
        hierarchy_ids = await self.hierarchy.resolve_scope(scope or "auto", user_class=user_class, query=query)
        emb = await self.embedder.embed(query)
        facts = await self.db.search_facts(emb, hierarchy_ids=hierarchy_ids, top_k=int(kwargs.get("top_k", 5)))
        return self._format_facts(facts)

    async def sync_all(self) -> dict[str, int]:
        await self.db.init_schema()
        pages = await self.website_fetcher.fetch()
        colleges = self.website_fetcher.extract_colleges(pages)
        await self.hierarchy.bootstrap(colleges)
        website_count = await self._sync_website_facts(pages)
        group_count = await self._sync_named_sources(self.group_fetcher, "group_name")
        channel_count = await self._sync_named_sources(self.channel_fetcher, "guild_name")
        return {"website": website_count, "qq_group": group_count, "qq_channel": channel_count}

    async def _sync_website_facts(self, pages: list[Any]) -> int:
        records = []
        for page in pages:
            facts = await self.splitter.split(page.text)
            for fact in facts:
                emb = await self.embedder.embed(fact)
                hierarchy_id = await self.hierarchy.match_source_name(fact)
                records.append({
                    "fact": fact,
                    "embedding": emb,
                    "hierarchy_id": hierarchy_id,
                    "source_type": "website",
                    "source_name": page.url,
                    "metadata": {"title": page.title},
                })
        return await self.db.insert_facts(records)

    async def _sync_named_sources(self, fetcher: Any, name_key: str) -> int:
        items = await fetcher.fetch()
        records = fetcher.to_facts(items)
        for item, record in zip(items, records, strict=False):
            display_name = str(item.get(name_key) or item.get("name") or record["source_name"])
            record["hierarchy_id"] = await self.hierarchy.match_source_name(display_name)
            record["embedding"] = await self.embedder.embed(record["fact"])
        return await self.db.insert_facts(records)

    def _llm_provider(self) -> Any | None:
        if hasattr(self.context, "get_using_provider"):
            try:
                return self.context.get_using_provider()
            except Exception:
                logger.exception("llm_provider_lookup_failed")
        return None

    @staticmethod
    def _format_facts(facts: list[dict[str, Any]]) -> str:
        if not facts:
            return "未检索到相关校园信息。"
        lines = []
        for index, row in enumerate(facts, 1):
            source = f"{row.get('source_type', '')}:{row.get('source_name', '')}"
            level = row.get("level") or "未分层"
            hierarchy_name = row.get("hierarchy_name") or "未知"
            similarity = row.get("similarity")
            sim_text = f" 相似度:{similarity:.3f}" if isinstance(similarity, (float, int)) else ""
            lines.append(f"{index}. {row['fact']}（{level}/{hierarchy_name} 来源:{source}{sim_text}）")
        return "\n".join(lines)
