"""HITWH 校园信息插件 - 成绩/课表/考试/培养方案查询、语义搜索、QQ消息采集"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from ._utils import extract_msg, strip_command_prefix
from .db import HitwhDB
from .embedding import Embedder
from .hierarchy import HierarchyMatcher
from .post_process import (
    apply_composite_scores,
    compress_context,
    deduplicate_by_content,
    mmr_rerank,
    source_type_weight,
    time_decay_weight,
)
from .web_config import WebConfig, save_plugin_config

try:
    from astrbot.api import logger
    from astrbot.api.event import filter
    from astrbot.api.event.filter import EventMessageType
    from astrbot.api.star import Context, Star
except Exception:
    logger = logging.getLogger(__name__)
    Context = Any
    class EventMessageType:
        GROUP_MESSAGE = "GroupMessage"
        ALL = "AllMessage"
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
        self._bufs: dict[str, dict] = {}
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
            llm_provider=self._resolve_llm_provider(context),
        )
        self._tasks: list[asyncio.Task] = []
        self._web_config: WebConfig | None = None

    @staticmethod
    def _resolve_llm_provider(context: Any) -> Any | None:
        """Resolve an LLM provider from the AstrBot context.

        Tries several access patterns; returns None if unavailable.
        """
        for attr in ("llm_provider", "provider", "llm"):
            p = getattr(context, attr, None)
            if p is not None:
                return p
        try:
            return context.get_provider()
        except Exception:
            pass
        return None

    async def initialize(self) -> None:
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
        port = int(self.config.get("web_config_port", 8888) or 8888)
        try:
            self._web_config = WebConfig(self.config, callbacks, port=port)
            await self._web_config.start()
        except Exception:
            self._web_config = None
            logger.warning("web_config_start_failed port=%s", port, exc_info=True)

    async def terminate(self) -> None:
        for t in self._tasks:
            if not t.done():
                t.cancel()
        for gid, buf in list(self._bufs.items()):
            await self._flush_buffer(gid, buf)
        self._bufs.clear()
        if self._web_config is not None:
            await self._web_config.stop()
            self._web_config = None
        if self.db is not None:
            await self.db.close()
        logger.info("hitwh_plugin_terminated")

    def _start_timers(self) -> None:
        interval_h = int(self.config.get("sync_interval_hours", 1))
        self._tasks = []
        if interval_h <= 0:
            logger.info("edu_sync_disabled interval_hours=%s", interval_h)
        else:
            self._tasks += [
                asyncio.ensure_future(self._timer("grades", self._sync_grades, interval_h)),
                asyncio.ensure_future(self._timer("schedule", self._sync_schedule, interval_h)),
                asyncio.ensure_future(self._timer("exams", self._sync_exams, interval_h)),
                asyncio.ensure_future(self._timer("plan", self._sync_plan, interval_h)),
            ]
            logger.info("edu_sync_started interval_hours=%s modules=4", interval_h)

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

    def _has_edu_config(self) -> bool:
        token, webvpn_base = self._get_edu_config()
        return bool(token and webvpn_base)

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
        if self.db is None:
            yield event.plain_result("⚠️ 数据库不可用"); return
        grades = await self.db.query_grades(keyword)
        if not grades and self._has_edu_config():
            yield event.plain_result("首次查询，正在拉取成绩...")
            try:
                await self._sync_grades()
            except Exception as e:
                logger.exception("grades_sync_failed")
                yield event.plain_result(f"⚠️ 成绩拉取失败：{e}"); return
            grades = await self.db.query_grades(keyword)
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
        if self.db is None:
            yield event.plain_result("⚠️ 数据库不可用"); return
        schedules = await self.db.query_schedules()
        if not schedules and self._has_edu_config():
            yield event.plain_result("首次查询，正在拉取课表...")
            await self._sync_schedule()
            schedules = await self.db.query_schedules()
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
        if self.db is None:
            yield event.plain_result("⚠️ 数据库不可用"); return
        exams = await self.db.query_exams()
        if not exams and self._has_edu_config():
            yield event.plain_result("首次查询，正在拉取考试安排...")
            await self._sync_exams()
            exams = await self.db.query_exams()
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
        if self.db is None:
            yield event.plain_result("⚠️ 数据库不可用"); return
        plans = await self.db.query_plan(keyword)
        if not plans and self._has_edu_config():
            yield event.plain_result("首次查询，正在拉取培养方案...")
            await self._sync_plan()
            plans = await self.db.query_plan(keyword)
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
        save_plugin_config(self.config, self.config.get("webvpn_base", ""), msg)
        yield event.plain_result("✅ 教务网token已更新！试试发 /成绩 /课程 /考试")

    @filter.command("同步", desc="强制从教务网拉取最新数据（成绩/课表/考试/培养方案）")
    async def cmd_sync(self, event: Any = None):
        if not self._has_edu_config():
            yield event.plain_result("⚠️ 未配置教务网token或WebVPN地址"); return
        yield event.plain_result("🔄 正在同步教务数据...")
        results = []
        for name, fn in [("成绩", self._sync_grades), ("课表", self._sync_schedule),
                          ("考试", self._sync_exams), ("培养方案", self._sync_plan)]:
            try:
                n = await fn()
                results.append(f"{name}: {n}条")
            except Exception as e:
                results.append(f"{name}: 失败({e})")
        yield event.plain_result("📊 同步完成\n" + "\n".join(results))

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
            "  /hitwh_status    — 查看插件状态\n"
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
            self._log_rag_recall_start("cmd_search", query, q_embedding)
            # 使用混合检索：dense + sparse RRF 融合
            try:
                candidates = await self.db.search_hybrid(
                    q_embedding, query, min_similarity=0.3, top_k=30,
                )
            except Exception:
                logger.warning("hybrid_search_failed, fallback to dense", exc_info=True)
                candidates = await self.db.search_chunks(q_embedding, top_k=30)
            if not candidates:
                self._log_rag_recall_empty("cmd_search", query, candidates)
                yield event.plain_result("⚠️ 知识库为空或未找到相关结果"); return
            seen_docs: set[int] = set()
            unique = [c for c in candidates if c["document_id"] not in seen_docs and not seen_docs.add(c["document_id"])]
            # 内容去重
            unique = deduplicate_by_content(unique)
            # 复合得分：similarity × 时间衰减 × 来源权重
            unique = apply_composite_scores(unique)
            # MMR 多样性过滤
            unique = mmr_rerank(q_embedding, unique, lambda_param=0.7, top_k=5)
            self._log_rag_recall_success("cmd_search", query, candidates, unique, len(unique))
            lines = [f"🔍 「{query}」相关结果："]
            for c in unique:
                lines.append(f"\n📌 [{c['source_type']}] {c.get('doc_content', c['content'])[:300]}")
            yield event.plain_result("\n".join(lines))
        except Exception:
            logger.exception("search_failed")
            yield event.plain_result("⚠️ 搜索失败")

    @filter.command("hitwh_status", desc="检查 HITWH 插件状态：数据库、Token、同步与知识库数量")
    async def cmd_status(self, event: Any = None):
        token, webvpn_base = self._get_edu_config()
        lines = ["📊 HITWH 插件状态"]
        lines.append(f"数据库: {'可用' if self.db is not None else '不可用'}")
        lines.append(f"Token: {'已配置' if token else '未配置'}")
        lines.append(f"教务地址: {webvpn_base or '未配置'}")
        lines.append(f"自动同步: {self.config.get('sync_interval_hours', 1)} 小时")
        if self.db is not None:
            try:
                counts = await self.db.count_records()
                lines.append(
                    "数据量: "
                    f"成绩{counts.get('grades', 0)} / 课表{counts.get('schedules', 0)} / "
                    f"考试{counts.get('exams', 0)} / 培养方案{counts.get('plans', 0)}"
                )
                lines.append(
                    f"知识库: 文档{counts.get('documents', 0)} / 分块{counts.get('chunks', 0)}"
                )
            except Exception:
                logger.exception("status_count_failed")
                lines.append("数据量: 查询失败")
        yield event.plain_result("\n".join(lines))

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

    async def _embed_on_message(self, text: str, event) -> None:
        """缓冲 + 时间异同检测：同群消息按时序合并，时间断层 >30s 或 >300 字则 flush"""
        if self.db is None or len(text.strip()) < 2:
            return
        where = str(event.get_group_id() or "") if hasattr(event, "get_group_id") else "private"
        now = datetime.utcnow() + timedelta(hours=8)
        buf = self._bufs.get(where)
        if buf is not None:
            gap = (now - buf["last_time"]).total_seconds()
            if gap > 30 or buf["chars"] + len(text) > 300:
                await self._flush_buffer(where, buf)
                buf = None
        if buf is None:
            buf = {"lines": [], "last_time": now, "chars": 0}
            self._bufs[where] = buf
        buf["lines"].append(text)
        buf["chars"] += len(text)
        buf["last_time"] = now

    async def _flush_buffer(self, group_id: str, buf: dict) -> None:
        merged = "\n".join(buf["lines"])
        if len(merged.strip()) < 4:
            return
        try:
            embedding = await self._embedder.embed(merged)
            await self.db.upsert_document(
                title=f"QQ@{group_id}",
                content=merged,
                source_type="qq_group_msg",
                source_name=group_id,
                chunks_data=[{"content": merged, "embedding": embedding}],
            )
        except Exception:
            logger.exception("flush_buffer_failed")

    async def _index_knowledge(self) -> int:
        """手动 /索引 - 索引教务数据到知识库"""
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
                await self.db.upsert_document(
                    title=row.get("course_name", row.get("semester", "")),
                    content=text, source_type=source_type,
                    source_name=row.get("course_name", row.get("semester", "")),
                    chunks_data=chunk_data,
                    metadata={
                        "semester": row.get("semester", ""),
                        "school_year": row.get("school_year", ""),
                    },
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
    def _log_rag_recall_start(source: str, query: str, embedding: list[float]) -> None:
        logger.info(
            "rag_recall_start source=%s query=%r embedding_dim=%s",
            source, query, len(embedding),
        )

    @staticmethod
    def _log_rag_recall_empty(source: str, query: str, candidates: list[dict[str, Any]]) -> None:
        logger.info(
            "rag_recall_empty source=%s query=%r candidates=%s",
            source, query, len(candidates),
        )

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
    async def tool_search(self, event, query: str) -> str:
        """语义搜索 HITWH 校园信息知识库，适合查询通知、成绩、课表、考试、培养方案等综合问题。

        Args:
            query(string): 用户要查询的关键词或自然语言问题。
        """
        if self.db is None: return ""
        q_embedding = await self._embedder.embed(query)
        self._log_rag_recall_start("tool_search", query, q_embedding)
        try:
            candidates = await self.db.search_hybrid(
                q_embedding, query, min_similarity=0.3, top_k=30,
            )
        except Exception:
            logger.warning("tool_hybrid_search_failed, fallback to dense", exc_info=True)
            candidates = await self.db.search_chunks(q_embedding, top_k=30)
        if not candidates:
            self._log_rag_recall_empty("tool_search", query, candidates)
            return ""
        seen: set[int] = set()
        unique = [c for c in candidates if c["document_id"] not in seen and not seen.add(c["document_id"])]
        # 内容去重
        unique = deduplicate_by_content(unique)
        # 复合得分：similarity × 时间衰减 × 来源权重
        unique = apply_composite_scores(unique)
        # MMR 多样性过滤
        unique = mmr_rerank(q_embedding, unique, lambda_param=0.7, top_k=5)
        self._log_rag_recall_success("tool_search", query, candidates, unique, len(unique))
        # LLM 上下文压缩
        llm_provider = getattr(self.context, 'provider', None) or self.context
        try:
            compressed = await compress_context(query, unique, llm_provider=llm_provider, max_chars=600)
            return compressed
        except Exception:
            pass
        return "\n".join(
            f"[{c['source_type']}] {c.get('doc_content', c['content'])[:200]}"
            for c in unique
        )

    @filter.llm_tool(name="hitwh_grades", desc="精确查询HITWH学生成绩。输入课程名关键词(如'微积分'、'英语')或留空查全部。返回学期、课程名、分数、课程性质。用于查询具体课程成绩、挂科情况等。")
    async def tool_grades(self, event, keyword: str = "") -> str:
        """从数据库精确查询 HITWH 学生成绩，适合回答某门课多少分、是否挂科、历年成绩等问题。

        Args:
            keyword(string): 课程名关键词；留空表示查询全部成绩。
        """
        if self.db is None: return "数据库不可用"
        grades = await self.db.query_grades(keyword)
        if not grades and self._has_edu_config():
            await self._sync_grades()
            grades = await self.db.query_grades(keyword)
        if not grades: return "未查到成绩"
        lines = []
        for g in grades[:30]:
            score = g.get("final_score", "") or g.get("total_score", "")
            lines.append(f"{g['semester']} {g['course_name']}: {score}分 ({g.get('course_nature','')})")
        return "\n".join(lines)

    @filter.llm_tool(name="hitwh_schedule", desc="查询HITWH本学期个人课表。输入课程名或教师名关键词过滤(如'电磁场'、'周洪娟')或留空查全部。返回星期几、第几节、课程详情(含教师、周次、教室)。用于查询某天有什么课、某门课的上课时间地点等。")
    async def tool_schedule(self, event, query: str = "") -> str:
        """从数据库查询 HITWH 本学期个人课表，适合回答今天/某天有什么课、课程时间地点等问题。

        Args:
            query(string): 课程名、教师名、教室或其他课表关键词；留空表示查询全部课表。
        """
        if self.db is None: return "数据库不可用"
        schedules = await self.db.query_schedules()
        if not schedules and self._has_edu_config():
            await self._sync_schedule()
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
    async def tool_exams(self, event, query: str = "") -> str:
        """从数据库查询 HITWH 考试安排，适合回答考试时间、地点、座位号、最近考试等问题。

        Args:
            query(string): 课程名或考试安排关键词；留空表示查询全部考试安排。
        """
        if self.db is None: return "数据库不可用"
        exams = await self.db.query_exams()
        if not exams and self._has_edu_config():
            await self._sync_exams()
            exams = await self.db.query_exams()
        if not exams: return "未查到考试安排"
        lines = []
        for e in exams:
            if query and query not in str(e): continue
            lines.append(f"{e['course_name']}: {e['exam_time']} @ {e.get('exam_location','')} 座位{e.get('seat_number','')}")
        return "\n".join(lines) if lines else "未找到匹配考试"

    @filter.llm_tool(name="hitwh_plan", desc="查询HITWH专业培养方案/教学计划。输入课程名关键词过滤或留空查全部。返回开课学年学期、课程名、学分。用于查询培养方案中有哪些课、某门课多少学分、某学期有哪些课等。")
    async def tool_plan(self, event, keyword: str = "") -> str:
        """从数据库查询 HITWH 专业培养方案/教学计划，适合回答课程学分、开课学期、培养方案课程等问题。

        Args:
            keyword(string): 课程名或培养方案关键词；留空表示查询全部培养方案课程。
        """
        if self.db is None: return "数据库不可用"
        plans = await self.db.query_plan(keyword)
        if not plans and self._has_edu_config():
            await self._sync_plan()
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

    @filter.event_message_type(EventMessageType.ALL)
    async def _on_message(self, event: Any = None):
        content = event.get_message_str() or ""
        if not content.strip(): return
        if self.db is None: return
        group_id = str(event.get_group_id() or "") if hasattr(event, "get_group_id") else ""
        try:
            group = event.message_obj.group if group_id and event.message_obj and event.message_obj.group else None
            group_name = str(group.group_name or "") if group else ""
            member_count = len(group.members) if group and group.members else 0
            if group_id and group_name:
                await self.db.upsert_qq_group(group_id, group_name, member_count)
        except Exception:
            logger.exception("qq_group_upsert_failed group_id=%s", group_id)
        try:
            source_id = group_id or "private"
            await self.db.insert_qq_message(
                group_id=source_id,
                user_id=str(event.get_sender_id() or ""),
                nickname=str(event.get_sender_name() or ""),
                content=content,
                message_time=datetime.utcnow() + timedelta(hours=8),
            )
        except Exception:
            logger.exception("qq_msg_insert_failed")
        asyncio.ensure_future(self._embed_on_message(content, event))

    async def _on_group_message(self, event: Any = None):
        await self._on_message(event)

    @filter.on_decorating_result()
    async def _on_llm_context(self, event: Any = None):
        msg = event.get_message_str() if hasattr(event, "get_message_str") else ""
        if not msg or self.db is None: return
        try:
            q_embedding = await self._embedder.embed(msg)
            self._log_rag_recall_start("llm_context", msg, q_embedding)
            try:
                candidates = await self.db.search_hybrid(
                    q_embedding, msg, min_similarity=0.3, top_k=20,
                )
            except Exception:
                logger.warning("llm_hybrid_search_failed, fallback to dense", exc_info=True)
                candidates = await self.db.search_chunks(q_embedding, top_k=20)
            if not candidates:
                self._log_rag_recall_empty("llm_context", msg, candidates)
                return
            seen: set[int] = set()
            unique = [c for c in candidates if c["document_id"] not in seen and not seen.add(c["document_id"])]
            # 内容去重
            unique = deduplicate_by_content(unique)
            # 复合得分：similarity × 时间衰减 × 来源权重
            unique = apply_composite_scores(unique)
            # MMR 多样性过滤，保留 top 3
            unique = mmr_rerank(q_embedding, unique, lambda_param=0.7, top_k=3)
            # LLM 上下文压缩
            llm_provider = getattr(self.context, 'provider', None) or self.context
            try:
                context = await compress_context(msg, unique, llm_provider=llm_provider, max_chars=500)
            except Exception:
                context = "\n".join(c.get("doc_content", c["content"])[:200] for c in unique)
            if context:
                self._log_rag_recall_success("llm_context", msg, candidates, unique, min(len(unique), 3))
                event.set_extra("hitwh_context", context)
        except Exception:
            pass
