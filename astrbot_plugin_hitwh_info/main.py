"""HITWH 校园信息插件 - 成绩/课表/考试/培养方案查询、语义搜索、QQ消息采集"""
from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from ._utils import extract_msg, strip_command_prefix
from .db import HitwhDB
from .embedding import Embedder
from .hierarchy import HierarchyMatcher
from .web_config import WebConfig

logger = logging.getLogger(__name__)

try:
    from astrbot.api.event import filter
    from astrbot.api.event.filter import EventMessageType
    from astrbot.api.star import Context, Star
except Exception:
    Context = Any
    class EventMessageType:
        GROUP_MESSAGE = "GroupMessage"
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
        def on_platform_loaded(*a, **kw):
            def deco(f): return f
            return deco
        @staticmethod
        def on_decorating_result(*a, **kw):
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
        logger.info("hitwh_init dsn=%s", self.config.get("postgres_dsn", "")[:50])
        self.hierarchy = HierarchyMatcher(self.db)
        self._embedder = Embedder(
            embedding_api_base=self.config.get("embedding_api_base", ""),
            embedding_api_key=self.config.get("embedding_api_key", ""),
            embedding_model=self.config.get("embedding_model", ""),
            dimension=int(self.config.get("embedding_dim", 1024)),
            rerank_api_base=self.config.get("rerank_api_base", ""),
            rerank_api_key=self.config.get("rerank_api_key", ""),
            rerank_model=self.config.get("rerank_model", ""),
        )
        self._tasks: list[asyncio.Task] = []
        self._web_config: WebConfig | None = None

    async def initialize(self) -> None:
        self._register_config_keys()
        try:
            await self.db.init_schema()
            await self.hierarchy.bootstrap(self.config.get("colleges"))
        except Exception:
            logger.warning("hitwh_db_unavailable disable db features", exc_info=True)
            self.db = None
        await self._start_web_config()
        logger.info("hitwh_plugin_ready")
        self._start_timers()

    async def _start_web_config(self) -> None:
        callbacks = {
            "grades": self._sync_grades,
            "schedule": self._sync_schedule,
            "exams": self._sync_exams,
            "plan": self._sync_plan,
            "index": self._index_knowledge,
        }
        try:
            self._web_config = WebConfig(self.config, callbacks)
            await self._web_config.start()
        except Exception:
            self._web_config = None
            logger.warning("web_config_start_failed", exc_info=True)

    def _register_config_keys(self) -> None:
        try:
            from astrbot.core.star.config import put_config
            namespace = "astrbot_plugin_hitwh_info"
            defaults = [
                ("embedding_api_base", "嵌入模型API地址", "https://api.siliconflow.cn/v1", "硅基流动等OpenAI兼容的嵌入API地址"),
                ("embedding_api_key", "嵌入模型API Key", "", "硅基流动等平台的API Key"),
                ("embedding_model", "嵌入模型名称", "BAAI/bge-m3", "例如 BAAI/bge-m3"),
                ("embedding_dim", "嵌入向量维度", 1024, "与模型匹配的向量维度"),
                ("rerank_api_base", "重排模型API地址", "https://api.siliconflow.cn/v1", "重排API地址"),
                ("rerank_api_key", "重排模型API Key", "", "重排API Key"),
                ("rerank_model", "重排模型名称", "BAAI/bge-reranker-v2-m3", "例如 BAAI/bge-reranker-v2-m3"),
            ]
            for key, name, value, desc in defaults:
                put_config(namespace, name, key, value, desc)
        except Exception:
            logger.debug("config_register_skipped")

    async def terminate(self) -> None:
        for t in self._tasks:
            if not t.done():
                t.cancel()
        if self._web_config is not None:
            await self._web_config.stop()
            self._web_config = None
        if self.db is not None:
            await self.db.close()
        logger.info("hitwh_plugin_terminated")

    def _start_timers(self) -> None:
        interval_h = int(self.config.get("sync_interval_hours", 1))
        if interval_h <= 0:
            interval_h = 1
        self._tasks = [
            asyncio.ensure_future(self._timer("grades", self._sync_grades, interval_h)),
            asyncio.ensure_future(self._timer("schedule", self._sync_schedule, interval_h)),
            asyncio.ensure_future(self._timer("exams", self._sync_exams, interval_h)),
            asyncio.ensure_future(self._timer("plan", self._sync_plan, interval_h)),
        ]
        logger.info("auto_sync_started interval_hours=%s modules=4", interval_h)

    async def _timer(self, name: str, sync_fn, interval_hours: int) -> None:
        await asyncio.sleep(5 + hash(name) % 30)
        try:
            count = await sync_fn()
            logger.info("sync_initial %s count=%s", name, count)
        except Exception:
            logger.exception("sync_initial_failed %s", name)
        while True:
            await asyncio.sleep(interval_hours * 3600)
            try:
                await sync_fn()
            except Exception:
                logger.exception("sync_failed %s", name)

    def _get_edu_config(self) -> tuple[str, str]:
        return self.config.get("token", ""), self.config.get("webvpn_base", "")

    async def _sync_grades(self) -> int:
        if self.db is None: return 0
        token, webvpn_base = self._get_edu_config()
        if not token or not webvpn_base: return 0
        from .fetchers.grades import fetch_grades
        data = await fetch_grades(webvpn_base, token)
        return await self.db.upsert_grades(data) if data else 0

    async def _sync_schedule(self) -> int:
        if self.db is None: return 0
        token, webvpn_base = self._get_edu_config()
        if not token or not webvpn_base: return 0
        from .fetchers.schedule import fetch_schedule
        data = await fetch_schedule(webvpn_base, token)
        return await self.db.upsert_schedules(data) if data else 0

    async def _sync_exams(self) -> int:
        if self.db is None: return 0
        token, webvpn_base = self._get_edu_config()
        if not token or not webvpn_base: return 0
        from .fetchers.exams import fetch_exams
        data = await fetch_exams(webvpn_base, token)
        return await self.db.upsert_exams(data) if data else 0

    async def _sync_plan(self) -> int:
        if self.db is None: return 0
        token, webvpn_base = self._get_edu_config()
        if not token or not webvpn_base: return 0
        from .fetchers.plan import fetch_plan
        data = await fetch_plan(webvpn_base, token)
        return await self.db.upsert_plan(data) if data else 0

    # ========== 命令 ==========

    @filter.command("成绩", desc="查询成绩，可选课程名过滤")
    async def cmd_grades(self, event: Any = None):
        '''查询历年成绩。支持课程名关键词过滤，如 /成绩 微积分。返回学期、课程名、分数、及格状态。'''
        keyword = strip_command_prefix(extract_msg(event), ["/成绩", "成绩"])
        token = self.config.get("token", "")
        if not token:
            yield event.plain_result("⚠️ 未配置教务网token，发 /set_token 设置"); return
        yield event.plain_result("正在拉取最新成绩...")
        count = await self._sync_grades()
        grades = await self.db.query_grades(keyword) if self.db else []
        if not grades:
            yield event.plain_result("⚠️ 没查到成绩"); return
        semesters: dict[str, list] = {}
        for g in grades:
            semesters.setdefault(g["semester"], []).append(g)
        lines = []
        for sem in sorted(semesters.keys(), reverse=True):
            lines.append(f"\n📚 {sem}")
            for g in semesters[sem]:
                score = g.get("final_score", "") or g.get("total_score", "")
                remark = f" ({g.get('score_remark','')})" if g.get("score_remark") and g["score_remark"] != "-" else ""
                makeup = f" [{g['makeup_flag']}]" if g.get("makeup_flag") else ""
                if score and score.replace(".", "").replace("旷考", "").isdigit():
                    try: icon = "✅" if float(score) >= 60 else "❌"
                    except ValueError: icon = "⚠️"
                elif score == "旷考": icon = "🚫"
                else: icon = "⚠️"
                lines.append(f"  {icon} {g['course_name']}  {score}分{makeup}{remark}")
        yield event.plain_result("📊 成绩查询结果：\n" + "\n".join(lines))

    @filter.command("课程", desc="查询本学期个人课表")
    async def cmd_schedule(self, event: Any = None):
        '''查询本学期个人课表。返回每周每天每节课的上课安排，包括教师、周次、教室。'''
        token = self.config.get("token", "")
        if not token:
            yield event.plain_result("⚠️ 未配置教务网token，发 /set_token 设置"); return
        yield event.plain_result("正在拉取课表...")
        await self._sync_schedule()
        schedules = await self.db.query_schedules() if self.db else []
        if not schedules:
            yield event.plain_result("⚠️ 没查到课程"); return
        days = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        lines = [f"📅 课表（共{len(schedules)}个时段）："]
        for s in schedules:
            day = days[s["day_of_week"]] if 0 <= s["day_of_week"] < len(days) else f"D{s['day_of_week']}"
            lines.append(f"  {day} {s['time_slot']}节  {s['raw_content'][:100]}")
        yield event.plain_result("\n".join(lines))

    @filter.command("考试", desc="查询考试安排，返回考试时间、地点、座位号。例如：/考试")
    async def cmd_exams(self, event: Any = None):
        token = self.config.get("token", "")
        if not token:
            yield event.plain_result("⚠️ 未配置教务网token，发 /set_token 设置"); return
        yield event.plain_result("正在拉取考试安排...")
        await self._sync_exams()
        exams = await self.db.query_exams() if self.db else []
        if not exams:
            yield event.plain_result("⚠️ 没查到考试安排"); return
        lines = [f"📝 考试安排（共{len(exams)}门）："]
        for e in exams:
            seat = f" 座位{e['seat_number']}" if e.get('seat_number') else ""
            lines.append(f"  {e['course_name']}")
            lines.append(f"    📍 {e.get('exam_location', '')}{seat}")
            lines.append(f"    🕐 {e['exam_time']}")
        yield event.plain_result("\n".join(lines))

    @filter.command("教学计划", desc="查询专业培养方案/教学计划，可选课程名过滤。例如：/教学计划 数据结构")
    async def cmd_plan(self, event: Any = None):
        keyword = strip_command_prefix(extract_msg(event), ["/计划", "计划"])
        token = self.config.get("token", "")
        if not token:
            yield event.plain_result("⚠️ 未配置教务网token，发 /set_token 设置"); return
        yield event.plain_result("正在拉取培养方案...")
        count = await self._sync_plan()
        if count <= 0:
            yield event.plain_result("⚠️ 拉取失败，检查token"); return
        plans = await self.db.query_plan(keyword) if self.db else []
        if not plans:
            yield event.plain_result("⚠️ 没查到培养方案"); return
        lines = [f"📘 培养方案（共{len(plans)}门）："]
        for p in plans:
            lines.append(f"  {p['school_year']} {p['semester']} {p['course_name']} ({p['credit']}学分 {p.get('hours', '')}学时)")
        yield event.plain_result("\n".join(lines))

    @filter.command("set_token", desc="设置教务网Cookie。也可在 http://localhost:8888 网页中一键捕获Cookie")
    async def cmd_set_token(self, event: Any = None):
        msg = strip_command_prefix(extract_msg(event), ["/set_token", "set_token"])
        if not msg or len(msg) < 10:
            yield event.plain_result("用法：/set_token cookie_string\n或直接编辑 data/config/astrbot_plugin_hitwh_info_config.json 中的 token 字段"); return
        self.config["token"] = msg
        yield event.plain_result("✅ 教务网token已更新！试试发 /成绩 /课程 /考试")

    @filter.command("hitwh", desc="显示插件帮助信息和所有可用命令")
    async def cmd_help(self, event: Any = None):
        yield event.plain_result(
            "📋 HITWH校园信息插件\n"
            "  /成绩 [课程名]   — 查成绩\n"
            "  /课程            — 查本学期课表\n"
            "  /考试            — 查考试安排\n"
            "  /教学计划 [课程名] — 查培养方案\n"
            "  /搜索 <关键词>    — 语义搜索知识库\n"
            "  /索引            — 重建知识库向量索引\n"
            "  /set_token <c>   — 设置教务网Cookie\n"
            "  /hitwh           — 本帮助\n"
            "\n配置: 编辑 data/config/astrbot_plugin_hitwh_info_config.json"
        )

    @filter.command("搜索", desc="语义搜索知识库。对成绩/课表/考试/培养方案进行向量检索+重排序，返回Top5相关结果")
    async def cmd_search(self, event: Any = None):
        query = strip_command_prefix(extract_msg(event), ["/搜索", "搜索"])
        if not query:
            yield event.plain_result("用法：/搜索 <关键词>\n语义搜索成绩、课程、考试、培养方案等信息"); return
        if self.db is None:
            yield event.plain_result("⚠️ 数据库不可用"); return
        yield event.plain_result("🔍 正在语义搜索...")
        try:
            q_embedding = await self._embedder.embed(query)
            candidates = await self.db.search_chunks(q_embedding)
            if not candidates:
                yield event.plain_result("⚠️ 知识库为空或未找到相关结果"); return
            seen_docs: set[int] = set()
            unique = [c for c in candidates if c["document_id"] not in seen_docs and not seen_docs.add(c["document_id"])]
            docs = [c.get("doc_content", c["content"]) for c in unique]
            if len(docs) > 1:
                reranked = await self._embedder.rerank(query, docs)
                unique = [unique[r["index"]] for r in reranked if r["index"] < len(unique)]
            self._log_rag_recall_success("cmd_search", query, candidates, unique, len(unique))
            lines = [f"🔍 「{query}」相关结果："]
            for c in unique:
                lines.append(f"\n📌 [{c['source_type']}] {c.get('doc_content', c['content'])[:300]}")
            yield event.plain_result("\n".join(lines))
            yield event.plain_result("\n".join(lines))
        except Exception:
            logger.exception("search_failed")
            yield event.plain_result("⚠️ 搜索失败")

    @filter.command("索引", desc="将成绩/课表/考试/培养方案数据拆分为原子事实，生成嵌入向量并存入知识库")
    async def cmd_index(self, event: Any = None):
        if self.db is None:
            yield event.plain_result("⚠️ 数据库不可用"); return
        yield event.plain_result("📇 正在重建知识库索引...")
        try:
            count = await self._index_knowledge()
            yield event.plain_result(f"✅ 索引完成，共 {count} 条知识")
        except Exception:
            logger.exception("index_failed")
            yield event.plain_result("⚠️ 索引失败")

    async def _index_knowledge(self) -> int:
        if self.db is None: return 0
        from .fact_splitter import FactSplitter
        splitter = FactSplitter(min_length=20)
        total = 0
        for source_type, query_fn in [
            ("grade", self.db.query_grades),
            ("schedule", self.db.query_schedules),
            ("exam", self.db.query_exams),
            ("plan", self.db.query_plan),
        ]:
            rows = await query_fn()
            for row in rows:
                text = self._row_to_text(source_type, row)
                if not text: continue
                chunks = await splitter.split(text)
                if not chunks: continue
                chunk_data = []
                for c in chunks:
                    embedding = await self._embedder.embed(c)
                    chunk_data.append({"content": c, "embedding": embedding})
                doc_id = await self.db.upsert_document(
                    title=row.get("course_name", row.get("semester", "")),
                    content=text, source_type=source_type,
                    source_name=row.get("course_name", row.get("semester", "")),
                    chunks_data=chunk_data,
                )
                total += 1
        return total

    @staticmethod
    def _row_to_text(source_type: str, row: dict[str, Any]) -> str:
        if source_type == "grade":
            return f"{row.get('semester','')} {row.get('course_name','')} {row.get('final_score','')}分 {row.get('course_nature','')}"
        elif source_type == "schedule":
            return f"{row.get('semester','')} 周{row.get('day_of_week','')} {row.get('time_slot','')}节 {row.get('raw_content','')}"
        elif source_type == "exam":
            return f"{row.get('course_name','')} 考试 {row.get('exam_time','')} {row.get('exam_location','')}"
        elif source_type == "plan":
            return f"{row.get('school_year','')} {row.get('semester','')} {row.get('course_name','')} {row.get('credit','')}学分"
        return ""

    @staticmethod
    def _log_rag_recall_success(source: str, query: str, candidates: list[dict[str, Any]],
                                unique: list[dict[str, Any]], returned_count: int) -> None:
        top_sources = ",".join(str(c.get("source_type", "unknown")) for c in unique[:5])
        logger.info(
            "rag_recall_success source=%s query=%r candidates=%s unique=%s returned=%s top_sources=%s",
            source, query, len(candidates), len(unique), returned_count, top_sources,
        )

    # ========== LLM Tools ==========

    @filter.llm_tool(name="hitwh_search", desc="语义搜索HITWH校园信息知识库。每次对话自动调用。")
    async def tool_search(self, query: str) -> str:
        """语义搜索 HITWH 校园信息知识库，适合查询通知、成绩、课表、考试、培养方案等综合问题。

        Args:
            query(string): 用户要查询的关键词或自然语言问题。
        """
        if self.db is None: return ""
        q_embedding = await self._embedder.embed(query)
        candidates = await self.db.search_chunks(q_embedding)
        if not candidates: return ""
        seen: set[int] = set()
        unique = [c for c in candidates if c["document_id"] not in seen and not seen.add(c["document_id"])]
        docs = [c.get("doc_content", c["content"]) for c in unique]
        if len(docs) > 1:
            reranked = await self._embedder.rerank(query, docs)
            unique = [unique[r["index"]] for r in reranked if r["index"] < len(unique)]
        self._log_rag_recall_success("tool_search", query, candidates, unique, len(unique))
        return "\n".join(
            f"[{c['source_type']}] {c.get('doc_content', c['content'])[:200]}"
            for c in unique
        )

    @filter.llm_tool(name="hitwh_grades", desc="精确查询HITWH学生成绩。输入课程名关键词(如'微积分'、'英语')或留空查全部。返回学期、课程名、分数、课程性质。用于查询具体课程成绩、挂科情况等。")
    async def tool_grades(self, keyword: str = "") -> str:
        """从数据库精确查询 HITWH 学生成绩，适合回答某门课多少分、是否挂科、历年成绩等问题。

        Args:
            keyword(string): 课程名关键词；留空表示查询全部成绩。
        """
        if self.db is None: return "数据库不可用"
        grades = await self.db.query_grades(keyword)
        if not grades: return "未查到成绩"
        lines = []
        for g in grades[:30]:
            score = g.get("final_score", "") or g.get("total_score", "")
            lines.append(f"{g['semester']} {g['course_name']}: {score}分 ({g.get('course_nature','')})")
        return "\n".join(lines)

    @filter.llm_tool(name="hitwh_schedule", desc="查询HITWH本学期个人课表。输入课程名或教师名关键词过滤(如'电磁场'、'周洪娟')或留空查全部。返回星期几、第几节、课程详情(含教师、周次、教室)。用于查询某天有什么课、某门课的上课时间地点等。")
    async def tool_schedule(self, query: str = "") -> str:
        """从数据库查询 HITWH 本学期个人课表，适合回答今天/某天有什么课、课程时间地点等问题。

        Args:
            query(string): 课程名、教师名、教室或其他课表关键词；留空表示查询全部课表。
        """
        if self.db is None: return "数据库不可用"
        schedules = await self.db.query_schedules()
        if not schedules: return "未查到课表"
        days = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        lines = []
        for s in schedules:
            if query and query not in s.get("raw_content", ""): continue
            day = days[s["day_of_week"]] if 0 <= s["day_of_week"] < len(days) else ""
            lines.append(f"{day} {s['time_slot']}节: {s['raw_content'][:120]}")
        return "\n".join(lines) if lines else "未找到匹配课程"

    @filter.llm_tool(name="hitwh_exams", desc="查询HITWH考试安排。输入课程名关键词过滤或留空查全部。返回课程名、考试时间、考试地点、座位号。用于查询某门课何时考试、在哪个教室、最近有哪些考试等。")
    async def tool_exams(self, query: str = "") -> str:
        """从数据库查询 HITWH 考试安排，适合回答考试时间、地点、座位号、最近考试等问题。

        Args:
            query(string): 课程名或考试安排关键词；留空表示查询全部考试安排。
        """
        if self.db is None: return "数据库不可用"
        exams = await self.db.query_exams()
        if not exams: return "未查到考试安排"
        lines = []
        for e in exams:
            if query and query not in str(e): continue
            lines.append(f"{e['course_name']}: {e['exam_time']} @ {e.get('exam_location','')} 座位{e.get('seat_number','')}")
        return "\n".join(lines) if lines else "未找到匹配考试"

    @filter.llm_tool(name="hitwh_plan", desc="查询HITWH专业培养方案/教学计划。输入课程名关键词过滤或留空查全部。返回开课学年学期、课程名、学分。用于查询培养方案中有哪些课、某门课多少学分、某学期有哪些课等。")
    async def tool_plan(self, keyword: str = "") -> str:
        """从数据库查询 HITWH 专业培养方案/教学计划，适合回答课程学分、开课学期、培养方案课程等问题。

        Args:
            keyword(string): 课程名或培养方案关键词；留空表示查询全部培养方案课程。
        """
        if self.db is None: return "数据库不可用"
        plans = await self.db.query_plan(keyword)
        if not plans: return "未查到培养方案"
        lines = []
        for p in plans[:20]:
            lines.append(f"{p['school_year']} {p['semester']} {p['course_name']} {p['credit']}学分")
        return "\n".join(lines)

    # ========== QQ 消息采集 ==========

    @filter.on_platform_loaded()
    async def _on_platform_ready(self, event: Any = None):
        pass

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def _on_group_message(self, event: Any = None):
        whitelist: list = self.config.get("group_whitelist") or []
        group_id = str(event.get_group_id() or "")
        if not group_id: return
        if self.db is None: return
        try:
            group_name = str(event.message_obj.group.group_name or "") if event.message_obj and event.message_obj.group else ""
            member_count = len(event.message_obj.group.members) if event.message_obj and event.message_obj.group and event.message_obj.group.members else 0
            if group_name:
                await self.db.upsert_qq_group(group_id, group_name, member_count)
        except Exception:
            logger.exception("qq_group_upsert_failed group_id=%s", group_id)
        if not whitelist or group_id not in whitelist: return
        try:
            await self.db.insert_qq_message(
                group_id=group_id,
                user_id=str(event.get_sender_id() or ""),
                nickname=str(event.get_sender_name() or ""),
                content=event.get_message_str() or "",
                message_time=datetime.now(timezone.utc),
            )
            self._tasks.append(asyncio.ensure_future(
                self._auto_index_message(str(event.get_sender_id() or ""), event.get_message_str() or "")
            ))
        except Exception:
            logger.exception("qq_msg_insert_failed")

    async def _auto_index_message(self, user_id: str, content: str) -> None:
        if self.db is None: return
        from .fact_splitter import FactSplitter
        splitter = FactSplitter(min_length=15)
        chunks = await splitter.split(content)
        if not chunks: return
        chunk_data = []
        for c in chunks:
            embedding = await self._embedder.embed(c)
            chunk_data.append({"content": c, "embedding": embedding})
        try:
            await self.db.upsert_document(
                title=f"QQ消息-{user_id}", content=content,
                source_type="qq_group_msg", source_name=user_id,
                chunks_data=chunk_data,
            )
        except Exception:
            logger.exception("auto_index_msg_failed")

    @filter.on_decorating_result()
    async def _on_llm_context(self, event: Any = None):
        msg = event.get_message_str() if hasattr(event, "get_message_str") else ""
        if not msg or self.db is None: return
        try:
            q_embedding = await self._embedder.embed(msg)
            candidates = await self.db.search_chunks(q_embedding)
            if not candidates: return
            seen: set[int] = set()
            unique = [c for c in candidates if c["document_id"] not in seen and not seen.add(c["document_id"])]
            context = "\n".join(c.get("doc_content", c["content"])[:200] for c in unique[:8])
            if context:
                self._log_rag_recall_success("llm_context", msg, candidates, unique, min(len(unique), 8))
                event.set_extra("hitwh_context", context)
        except Exception:
            pass
