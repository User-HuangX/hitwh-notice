from __future__ import annotations

import logging
import re
import uuid
from difflib import SequenceMatcher
from typing import Any

from .sources import DEFAULT_COLLEGES, DEFAULT_SCHOOL_NAME

logger = logging.getLogger(__name__)
LEVELS = {"学校", "院系", "导员", "班级"}


class HierarchyMatcher:
    def __init__(self, db: Any, school_name: str = DEFAULT_SCHOOL_NAME) -> None:
        self.db = db
        self.school_name = school_name

    async def bootstrap(self, colleges: list[str] | None = None) -> int:
        school_id = await self.ensure_node(None, "学校", self.school_name)
        for college in colleges or DEFAULT_COLLEGES:
            await self.ensure_node(school_id, "院系", college)
        logger.info("hierarchy_bootstrap colleges=%s", len(colleges or DEFAULT_COLLEGES))
        return school_id

    async def ensure_node(self, parent_id: int | None, level: str, name: str | None = None) -> int:
        if level not in LEVELS:
            raise ValueError(f"unsupported hierarchy level: {level}")
        status = "confirmed"
        if name is None:
            name = f"待定_{uuid.uuid4().hex[:8]}"
            status = "pending"
        existing = await self.db.find_node(parent_id, name)
        if existing:
            return int(existing["id"])
        node_id = await self.db.insert_node(parent_id, level, name, status)
        logger.info("hierarchy_node_created id=%s level=%s name=%s status=%s", node_id, level, name, status)
        return int(node_id)

    async def match_source_name(self, name: str) -> int:
        college = await self._best_college(name)
        college_id = int(college["id"]) if college else await self._first_college_id()
        teacher_name = self._extract_teacher(name)
        class_name = self._extract_class(name)
        teacher_id = await self.ensure_node(college_id, "导员", teacher_name)
        class_id = await self.ensure_node(teacher_id, "班级", class_name)
        logger.info(
            "source_matched name=%s college_id=%s teacher=%s class=%s",
            name, college_id, teacher_name or "pending", class_name or "pending",
        )
        return class_id if class_name else teacher_id

    async def resolve_scope(self, scope: str, user_class: str | None = None, query: str = "") -> list[int] | None:
        scope = scope or "auto"
        if scope == "school":
            return None
        if scope == "auto":
            scope = "my_college" if re.search(r"院|学院|老师|导员|专业", query) else "my_class"
        class_node = await self._find_by_name("班级", user_class) if user_class else None
        if scope == "my_class":
            return [int(class_node["id"])] if class_node else None
        if scope == "my_college":
            if not class_node:
                return None
            ancestors = await self.db.get_ancestors(int(class_node["id"]))
            college = next((n for n in ancestors if n.get("level") == "院系"), None)
            return await self.db.get_descendants(int(college["id"])) if college else None
        raise ValueError(f"unsupported scope: {scope}")

    async def _best_college(self, text: str) -> dict[str, Any] | None:
        colleges = await self.db.list_nodes(level="院系")
        if not colleges:
            return None
        scored = sorted(((self._score_college(text, c["name"]), c) for c in colleges), reverse=True, key=lambda x: x[0])
        return scored[0][1] if scored[0][0] >= 0.18 else colleges[0]

    async def _first_college_id(self) -> int:
        school = await self.db.find_node(None, self.school_name)
        school_id = int(school["id"]) if school else await self.bootstrap([])
        colleges = await self.db.list_nodes(level="院系")
        if colleges:
            return int(colleges[0]["id"])
        return await self.ensure_node(school_id, "院系", "待匹配学院")

    async def _find_by_name(self, level: str, name: str | None) -> dict[str, Any] | None:
        if not name:
            return None
        for node in await self.db.list_nodes(level=level):
            if node.get("name") == name:
                return node
        return None

    @staticmethod
    def _extract_teacher(text: str) -> str | None:
        match = re.search(r"([\u4e00-\u9fa5]{1,4}老师|[\u4e00-\u9fa5]{1,4}导员|辅导员[\u4e00-\u9fa5]{1,3})", text)
        return match.group(1) if match else None

    @staticmethod
    def _extract_class(text: str) -> str | None:
        patterns = [
            r"([\u4e00-\u9fa5]{1,4}\d{2,4}班?)",
            r"([A-Za-z]{2,}\d{2,4}班?)",
            r"(\d{4,6}班)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                value = match.group(1)
                return value if value.endswith("班") else value
        return None

    @staticmethod
    def _score_college(text: str, college: str) -> float:
        aliases = {
            "计算机科学与技术学院": ["计算机", "计科", "软件", "网络空间"],
            "船舶与海洋工程学院": ["船舶", "海洋工程"],
            "信息科学与工程学院": ["信息", "通信", "电子"],
        }.get(college, [])
        if college in text:
            return 1.0
        if any(alias in text for alias in aliases):
            return 0.8
        return SequenceMatcher(None, text, college).ratio()
