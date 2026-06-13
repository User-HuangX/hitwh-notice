from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

try:
    from pgvector.sqlalchemy import Vector
    _pgvector_available = True
except ImportError:
    Vector = None
    _pgvector_available = False

try:
    import asyncpg
    _asyncpg_available = True
except ImportError:
    _asyncpg_available = False

from ._utils import with_conn

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class HierarchyNode(Base):
    __tablename__ = "hitwh_hierarchy"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("hitwh_hierarchy.id", ondelete="CASCADE"))
    level: Mapped[str] = mapped_column()
    name: Mapped[str] = mapped_column()
    status: Mapped[str] = mapped_column(default="pending")
    path: Mapped[str] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("level IN ('学校','院系','导员','班级')"),
        CheckConstraint("status IN ('confirmed','pending')"),
        UniqueConstraint("parent_id", "name"),
        {},
    )

    children: Mapped[list["HierarchyNode"]] = relationship(
        "HierarchyNode", back_populates="parent", cascade="all, delete-orphan"
    )
    parent: Mapped["HierarchyNode | None"] = relationship(
        "HierarchyNode", back_populates="children", remote_side=[id]
    )


class Grade(Base):
    __tablename__ = "hitwh_grades"

    id: Mapped[int] = mapped_column(primary_key=True)
    semester: Mapped[str] = mapped_column()
    college: Mapped[str] = mapped_column(default="")
    course_code: Mapped[str] = mapped_column(default="")
    course_name: Mapped[str] = mapped_column()
    course_nature: Mapped[str] = mapped_column(default="")
    course_category: Mapped[str] = mapped_column(default="")
    credit: Mapped[str] = mapped_column(default="")
    is_exam: Mapped[str] = mapped_column(default="")
    count_gpa: Mapped[str] = mapped_column(default="")
    makeup_flag: Mapped[str] = mapped_column(default="")
    total_score: Mapped[str] = mapped_column(default="")
    final_score: Mapped[str] = mapped_column(default="")
    score_remark: Mapped[str] = mapped_column(default="")
    student_type: Mapped[str] = mapped_column(default="")
    submit_time: Mapped[str] = mapped_column(default="")
    grade_hash: Mapped[str] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    __table_args__ = (Index("idx_grades_hash", "grade_hash", unique=True),)


class Schedule(Base):
    __tablename__ = "hitwh_schedule"

    id: Mapped[int] = mapped_column(primary_key=True)
    semester: Mapped[str] = mapped_column()
    day_of_week: Mapped[int] = mapped_column()
    time_slot: Mapped[str] = mapped_column()
    raw_content: Mapped[str] = mapped_column(default="")
    schedule_hash: Mapped[str] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    __table_args__ = (Index("idx_schedule_hash", "schedule_hash", unique=True),)


class QQGroup(Base):
    __tablename__ = "hitwh_qq_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[str] = mapped_column(unique=True)
    group_name: Mapped[str] = mapped_column()
    member_count: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    __table_args__ = (Index("idx_qq_group_id", "group_id", unique=True),)


class QQGroupMessage(Base):
    __tablename__ = "hitwh_qq_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[str] = mapped_column()
    user_id: Mapped[str] = mapped_column()
    nickname: Mapped[str] = mapped_column(default="")
    content: Mapped[str] = mapped_column()
    message_time: Mapped[datetime] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    __table_args__ = (Index("idx_qq_msg_group", "group_id"), Index("idx_qq_msg_time", "message_time"))


class Exam(Base):
    __tablename__ = "hitwh_exams"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_code: Mapped[str] = mapped_column(default="")
    course_name: Mapped[str] = mapped_column()
    exam_location: Mapped[str] = mapped_column(default="")
    seat_number: Mapped[str] = mapped_column(default="")
    exam_time: Mapped[str] = mapped_column()
    exam_hash: Mapped[str] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    __table_args__ = (Index("idx_exams_hash", "exam_hash", unique=True),)


class Plan(Base):
    __tablename__ = "hitwh_plan"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_code: Mapped[str] = mapped_column(default="")
    course_name: Mapped[str] = mapped_column()
    course_name_en: Mapped[str] = mapped_column(default="")
    school_year: Mapped[str] = mapped_column(default="")
    semester: Mapped[str] = mapped_column(default="")
    college: Mapped[str] = mapped_column(default="")
    course_nature: Mapped[str] = mapped_column(default="")
    course_category: Mapped[str] = mapped_column(default="")
    credit: Mapped[str] = mapped_column(default="")
    hours: Mapped[str] = mapped_column(default="")
    is_exam: Mapped[str] = mapped_column(default="")
    plan_hash: Mapped[str] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    __table_args__ = (Index("idx_plan_hash", "plan_hash", unique=True),)


class Document(Base):
    __tablename__ = "hitwh_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(default="")
    content: Mapped[str] = mapped_column()
    source_type: Mapped[str] = mapped_column()
    source_name: Mapped[str] = mapped_column(default="")
    chunk_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    chunks: Mapped[list["Chunk"]] = relationship("Chunk", back_populates="document",
                                                   cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_doc_source", "source_type", "source_name"),
    )


class Chunk(Base):
    __tablename__ = "hitwh_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("hitwh_documents.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(default=0)
    content: Mapped[str] = mapped_column()
    embedding: Mapped[list[float]] = mapped_column(Vector(1024) if Vector else String(4096))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    document: Mapped["Document"] = relationship("Document", back_populates="chunks")

    __table_args__ = (
        Index("idx_chunk_doc_id", "document_id"),
        Index("idx_chunk_doc_content", text("md5(content)"), unique=True),
        *([Index("idx_chunk_embedding", "embedding", postgresql_using="ivfflat")] if Vector else []),
    )


def _hash(columns: list[str], item: dict[str, Any]) -> str:
    return hashlib.md5("|".join(str(item.get(c, "")) for c in columns).encode()).hexdigest()


class HitwhDB:
    def __init__(self, dsn: str | None = None, **connect_kwargs: Any) -> None:
        self.dsn = dsn
        self.connect_kwargs = connect_kwargs
        self._engine = None
        self._sessionmaker = None

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None

    async def _ensure_engine(self):
        if self._engine is None:
            if not _asyncpg_available:
                raise RuntimeError("asyncpg is required for database operations")
            dsn = self.dsn or self.connect_kwargs.pop("dsn", "")
            if not dsn:
                dsn = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
            if not dsn.startswith("postgresql+asyncpg"):
                if dsn.startswith("postgresql://"):
                    dsn = "postgresql+asyncpg://" + dsn[len("postgresql://"):]
                elif dsn.startswith("postgresql+psycopg"):
                    dsn = dsn.replace("postgresql+psycopg", "postgresql+asyncpg")
            self._engine = create_async_engine(dsn, echo=False, **self.connect_kwargs)
            self._sessionmaker = async_sessionmaker(self._engine, class_=AsyncSession, expire_on_commit=False)
            logger.info("db_engine_created")
        return self._sessionmaker

    async def _session(self):
        sm = await self._ensure_engine()
        return sm()

    async def init_schema(self) -> None:
        await self._ensure_engine()
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("db_schema_ready")

    # ======== hierarchy ========

    async def insert_node(self, parent_id: int | None, level: str, name: str, status: str) -> int:
        async with await self._session() as session, session.begin():
            parent_path = None
            if parent_id is not None:
                parent_path = await session.scalar(
                    select(HierarchyNode.path).where(HierarchyNode.id == parent_id)
                )
            path = f"{parent_path}/{name}" if parent_path else name

            stmt = (
                pg_insert(HierarchyNode)
                .values(parent_id=parent_id, level=level, name=name, status=status, path=path)
                .on_conflict_do_update(
                    constraint="hitwh_hierarchy_parent_id_name_key",
                    set_={"name": name},
                )
                .returning(HierarchyNode.id)
            )
            result = await session.execute(stmt)
            node_id = result.scalar_one()
            logger.info("hierarchy_node_created id=%s level=%s name=%s status=%s", node_id, level, name, status)
            return node_id

    async def find_node(self, parent_id: int | None, name: str) -> dict[str, Any] | None:
        async with await self._session() as session:
            stmt = select(HierarchyNode).where(
                HierarchyNode.parent_id == parent_id if parent_id is not None else HierarchyNode.parent_id.is_(None),
                HierarchyNode.name == name,
            )
            result = await session.execute(stmt)
            node = result.scalar_one_or_none()
            return _node_to_dict(node) if node else None

    async def list_nodes(self, level: str | None = None) -> list[dict[str, Any]]:
        async with await self._session() as session:
            stmt = select(HierarchyNode).order_by(HierarchyNode.id)
            if level:
                stmt = stmt.where(HierarchyNode.level == level)
            result = await session.execute(stmt)
            return [_node_to_dict(n) for n in result.scalars().all()]

    async def resolve_pending(self, parent_id: int, level: str, real_name: str) -> int | None:
        async with await self._session() as session, session.begin():
            subq = (
                select(HierarchyNode.id)
                .where(
                    HierarchyNode.parent_id == parent_id,
                    HierarchyNode.level == level,
                    HierarchyNode.status == "pending",
                )
                .order_by(HierarchyNode.id)
                .limit(1)
                .scalar_subquery()
            )
            stmt = (
                update(HierarchyNode)
                .where(HierarchyNode.id == subq)
                .values(
                    name=real_name,
                    status="confirmed",
                    path=text("regexp_replace(path, '[^/]+$', :newname)").bindparams(newname=real_name),
                )
                .returning(HierarchyNode.id)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row:
                logger.info("pending_node_resolved id=%s level=%s name=%s", row, level, real_name)
            return row

    async def get_descendants(self, node_id: int) -> list[int]:
        async with await self._session() as session:
            tree = (
                select(HierarchyNode.id)
                .where(HierarchyNode.id == node_id)
                .cte(name="tree", recursive=True)
            )
            tree = tree.union_all(
                select(HierarchyNode.id).where(HierarchyNode.parent_id == tree.c.id)
            )
            result = await session.execute(select(tree.c.id))
            return [int(row[0]) for row in result.all()]

    async def get_ancestors(self, node_id: int) -> list[dict[str, Any]]:
        async with await self._session() as session:
            tree = (
                select(HierarchyNode)
                .where(HierarchyNode.id == node_id)
                .cte(name="tree", recursive=True)
            )
            tree = tree.union_all(
                select(HierarchyNode).where(HierarchyNode.id == tree.c.parent_id)
            )
            result = await session.execute(
                select(HierarchyNode).where(HierarchyNode.id.in_(select(tree.c.id)))
            )
            return [_node_to_dict(n) for n in result.scalars().all()]

    # ======== grades ========

    async def upsert_grades(self, grades: list[dict[str, Any]]) -> int:
        async with await self._session() as session, session.begin():
            inserted = 0
            for g in grades:
                h = _hash(["semester", "course_code", "final_score"], g)
                stmt = (
                    pg_insert(Grade)
                    .values(
                        semester=g.get("semester", ""),
                        college=g.get("college", ""),
                        course_code=g.get("course_code", ""),
                        course_name=g.get("course_name", ""),
                        course_nature=g.get("course_nature", ""),
                        course_category=g.get("course_category", ""),
                        credit=g.get("credit", ""),
                        is_exam=g.get("is_exam", ""),
                        count_gpa=g.get("count_gpa", ""),
                        makeup_flag=g.get("makeup_flag", ""),
                        total_score=g.get("total_score", ""),
                        final_score=g.get("final_score", ""),
                        score_remark=g.get("score_remark", ""),
                        student_type=g.get("student_type", ""),
                        submit_time=g.get("submit_time", ""),
                        grade_hash=h,
                    )
                    .on_conflict_do_nothing(index_elements=[Grade.grade_hash])
                )
                result = await session.execute(stmt)
                inserted += int(result.rowcount == 1)
            logger.info("grades_inserted count=%s submitted=%s", inserted, len(grades))
            return inserted

    async def query_grades(self, keyword: str = "") -> list[dict[str, Any]]:
        async with await self._session() as session:
            stmt = select(Grade).order_by(Grade.semester.desc(), Grade.id)
            if keyword:
                stmt = stmt.where(Grade.course_name.ilike(f"%{keyword}%"))
            result = await session.execute(stmt)
            return [_grade_to_dict(g) for g in result.scalars().all()]

    # ======== schedule ========

    async def upsert_schedules(self, schedules: list[dict[str, Any]]) -> int:
        async with await self._session() as session, session.begin():
            inserted = 0
            for s in schedules:
                h = _hash(["semester", "day_of_week", "time_slot"], s)
                stmt = (
                    pg_insert(Schedule)
                    .values(
                        semester=s.get("semester", ""),
                        day_of_week=s.get("day_of_week", 0),
                        time_slot=s.get("time_slot", ""),
                        raw_content=s.get("raw_content", ""),
                        schedule_hash=h,
                    )
                    .on_conflict_do_nothing(index_elements=[Schedule.schedule_hash])
                )
                result = await session.execute(stmt)
                inserted += int(result.rowcount == 1)
            logger.info("schedules_inserted count=%s submitted=%s", inserted, len(schedules))
            return inserted

    async def query_schedules(self, semester: str = "") -> list[dict[str, Any]]:
        async with await self._session() as session:
            stmt = select(Schedule).order_by(Schedule.day_of_week, Schedule.time_slot)
            if semester:
                stmt = stmt.where(Schedule.semester == semester)
            result = await session.execute(stmt)
            return [_schedule_to_dict(s) for s in result.scalars().all()]

    # ======== exams ========

    async def upsert_exams(self, exams: list[dict[str, Any]]) -> int:
        async with await self._session() as session, session.begin():
            inserted = 0
            for e in exams:
                h = _hash(["course_code", "exam_time"], e)
                stmt = (
                    pg_insert(Exam)
                    .values(
                        course_code=e.get("course_code", ""),
                        course_name=e.get("course_name", ""),
                        exam_location=e.get("exam_location", ""),
                        seat_number=e.get("seat_number", ""),
                        exam_time=e.get("exam_time", ""),
                        exam_hash=h,
                    )
                    .on_conflict_do_nothing(index_elements=[Exam.exam_hash])
                )
                result = await session.execute(stmt)
                inserted += int(result.rowcount == 1)
            logger.info("exams_inserted count=%s submitted=%s", inserted, len(exams))
            return inserted

    async def query_exams(self) -> list[dict[str, Any]]:
        async with await self._session() as session:
            result = await session.execute(select(Exam).order_by(Exam.exam_time))
            return [_exam_to_dict(e) for e in result.scalars().all()]

    # ======== plan ========

    async def upsert_plan(self, plans: list[dict[str, Any]]) -> int:
        async with await self._session() as session, session.begin():
            inserted = 0
            for p in plans:
                h = _hash(["course_code", "course_name", "school_year", "semester"], p)
                stmt = (
                    pg_insert(Plan)
                    .values(
                        course_code=p.get("course_code", ""),
                        course_name=p.get("course_name", ""),
                        course_name_en=p.get("course_name_en", ""),
                        school_year=p.get("school_year", ""),
                        semester=p.get("semester", ""),
                        college=p.get("college", ""),
                        course_nature=p.get("course_nature", ""),
                        course_category=p.get("course_category", ""),
                        credit=p.get("credit", ""),
                        hours=p.get("hours", ""),
                        is_exam=p.get("is_exam", ""),
                        plan_hash=h,
                    )
                    .on_conflict_do_nothing(index_elements=[Plan.plan_hash])
                )
                result = await session.execute(stmt)
                inserted += int(result.rowcount == 1)
            logger.info("plan_inserted count=%s submitted=%s", inserted, len(plans))
            return inserted

    async def query_plan(self, keyword: str = "") -> list[dict[str, Any]]:
        async with await self._session() as session:
            stmt = select(Plan).order_by(Plan.school_year, Plan.semester, Plan.id)
            if keyword:
                stmt = stmt.where(Plan.course_name.ilike(f"%{keyword}%"))
            result = await session.execute(stmt)
            return [_plan_to_dict(p) for p in result.scalars().all()]

    # ======== qq groups ========

    async def upsert_qq_group(self, group_id: str, group_name: str, member_count: int = 0) -> int:
        async with await self._session() as session, session.begin():
            stmt = (
                pg_insert(QQGroup)
                .values(group_id=group_id, group_name=group_name, member_count=member_count)
                .on_conflict_do_update(
                    index_elements=[QQGroup.group_id],
                    set_={"group_name": group_name, "member_count": member_count}
                )
            )
            await session.execute(stmt)
            return 1

    @with_conn
    async def query_qq_groups(self, conn) -> list[dict[str, Any]]:
        rows = await conn.fetch("SELECT * FROM hitwh_qq_groups ORDER BY id")
        return [dict(row) for row in rows]

    # ======== qq messages ========

    async def insert_qq_message(self, group_id: str, user_id: str, nickname: str, content: str, message_time: datetime) -> None:
        async with await self._session() as session, session.begin():
            msg = QQGroupMessage(
                group_id=group_id, user_id=user_id, nickname=nickname,
                content=content, message_time=message_time,
            )
            session.add(msg)

    @with_conn
    async def query_qq_messages(self, conn, group_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        if group_id:
            rows = await conn.fetch(
                "SELECT * FROM hitwh_qq_messages WHERE group_id=$1 ORDER BY message_time DESC LIMIT $2",
                group_id, limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM hitwh_qq_messages ORDER BY message_time DESC LIMIT $1", limit,
            )
            return [dict(row) for row in rows]

    # ======== documents & chunks ========

    async def upsert_document(self, title: str, content: str, source_type: str,
                              source_name: str, chunks_data: list[dict[str, Any]]) -> int:
        async with await self._session() as session, session.begin():
            doc = Document(
                title=title, content=content,
                source_type=source_type, source_name=source_name,
                chunk_count=len(chunks_data),
            )
            session.add(doc)
            await session.flush()

            doc_id = doc.id
            for i, cd in enumerate(chunks_data):
                session.add(Chunk(
                    document_id=doc_id, chunk_index=i,
                    content=cd["content"], embedding=cd.get("embedding", []),
                ))
            logger.info("doc_upserted id=%s title=%s chunks=%s", doc_id, title[:40], len(chunks_data))
            return doc_id

    @with_conn
    async def search_chunks(self, conn, embedding: list[float], min_similarity: float = 0.3) -> list[dict[str, Any]]:
        rows = await conn.fetch(
            """SELECT c.id, c.content, c.document_id, c.chunk_index,
                      d.title, d.content AS doc_content, d.source_type, d.source_name,
                      1 - (c.embedding <=> $1) AS similarity
               FROM hitwh_chunks c
               JOIN hitwh_documents d ON c.document_id = d.id
               WHERE 1 - (c.embedding <=> $1) > $2
               ORDER BY c.embedding <=> $1""",
            embedding, min_similarity,
        )
        return [dict(row) for row in rows]

    @with_conn
    async def get_document_chunks(self, conn, document_id: int) -> list[dict[str, Any]]:
        rows = await conn.fetch(
            "SELECT * FROM hitwh_chunks WHERE document_id=$1 ORDER BY chunk_index",
            document_id,
        )
        return [dict(row) for row in rows]


def _node_to_dict(node: HierarchyNode) -> dict[str, Any]:
    return {"id": node.id, "parent_id": node.parent_id, "level": node.level,
            "name": node.name, "status": node.status, "path": node.path}


def _grade_to_dict(g: Grade) -> dict[str, Any]:
    return {c.name: getattr(g, c.name) for c in g.__table__.columns}


def _course_to_dict(c: Schedule) -> dict[str, Any]:
    return {col.name: getattr(c, col.name) for col in c.__table__.columns}


def _schedule_to_dict(s: Schedule) -> dict[str, Any]:
    return {col.name: getattr(s, col.name) for col in s.__table__.columns}


def _plan_to_dict(p: Plan) -> dict[str, Any]:
    return {col.name: getattr(p, col.name) for col in p.__table__.columns}


def _exam_to_dict(e: Exam) -> dict[str, Any]:
    return {col.name: getattr(e, col.name) for col in e.__table__.columns}
