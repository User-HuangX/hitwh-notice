from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

DDL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS hitwh_hierarchy (
    id          SERIAL PRIMARY KEY,
    parent_id   INT REFERENCES hitwh_hierarchy(id) ON DELETE CASCADE,
    level       TEXT NOT NULL CHECK (level IN ('学校','院系','导员','班级')),
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('confirmed','pending')),
    path        TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(parent_id, name)
);

CREATE TABLE IF NOT EXISTS hitwh_facts (
    id            SERIAL PRIMARY KEY,
    fact          TEXT NOT NULL,
    fact_hash     TEXT NOT NULL,
    embedding     vector(1536),
    hierarchy_id  INT REFERENCES hitwh_hierarchy(id),
    source_type   TEXT NOT NULL CHECK (source_type IN ('website','qq_group','qq_channel')),
    source_name   TEXT NOT NULL,
    metadata      JSONB DEFAULT '{}',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_hash ON hitwh_facts (fact_hash);
CREATE INDEX IF NOT EXISTS idx_facts_hierarchy ON hitwh_facts (hierarchy_id);
CREATE INDEX IF NOT EXISTS idx_hierarchy_level_name ON hitwh_hierarchy (level, name);
CREATE INDEX IF NOT EXISTS idx_hierarchy_name_trgm ON hitwh_hierarchy USING gin (name gin_trgm_ops);
"""

VECTOR_INDEX_DDL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_facts_embedding') THEN
        CREATE INDEX idx_facts_embedding ON hitwh_facts
        USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
    END IF;
END $$;
"""


class HitwhDB:
    def __init__(self, dsn: str | None = None, **connect_kwargs: Any) -> None:
        self.dsn = dsn
        self.connect_kwargs = connect_kwargs
        self._pool = None

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _get_pool(self):
        if self._pool is None:
            try:
                import asyncpg
            except ImportError as exc:
                raise RuntimeError("asyncpg is required for database operations") from exc
            if self.dsn:
                self._pool = await asyncpg.create_pool(dsn=self.dsn, **self.connect_kwargs)
            else:
                self._pool = await asyncpg.create_pool(**self.connect_kwargs)
            logger.info("db_pool_created")
        return self._pool

    async def init_schema(self) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(DDL)
            await conn.execute(VECTOR_INDEX_DDL)
        logger.info("db_schema_ready")

    async def insert_node(self, parent_id: int | None, level: str, name: str, status: str) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            parent_path = None
            if parent_id is not None:
                parent_path = await conn.fetchval("SELECT path FROM hitwh_hierarchy WHERE id=$1", parent_id)
            path = f"{parent_path}/{name}" if parent_path else name
            row = await conn.fetchrow(
                """
                INSERT INTO hitwh_hierarchy (parent_id, level, name, status, path)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (parent_id, name) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """,
                parent_id,
                level,
                name,
                status,
                path,
            )
            return int(row["id"])

    async def find_node(self, parent_id: int | None, name: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, parent_id, level, name, status, path
                FROM hitwh_hierarchy
                WHERE parent_id IS NOT DISTINCT FROM $1 AND name = $2
                """,
                parent_id,
                name,
            )
            return dict(row) if row else None

    async def list_nodes(self, level: str | None = None) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            if level:
                rows = await conn.fetch("SELECT * FROM hitwh_hierarchy WHERE level=$1 ORDER BY id", level)
            else:
                rows = await conn.fetch("SELECT * FROM hitwh_hierarchy ORDER BY id")
            return [dict(row) for row in rows]

    async def resolve_pending(self, parent_id: int, level: str, real_name: str) -> int | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE hitwh_hierarchy
                SET name=$1, status='confirmed', path = regexp_replace(path, '[^/]+$', $1)
                WHERE id = (
                    SELECT id FROM hitwh_hierarchy
                    WHERE parent_id=$2 AND level=$3 AND status='pending'
                    ORDER BY id LIMIT 1
                )
                RETURNING id
                """,
                real_name,
                parent_id,
                level,
            )
            if row:
                logger.info("pending_node_resolved id=%s level=%s name=%s", row["id"], level, real_name)
            return int(row["id"]) if row else None

    async def insert_facts(self, facts: list[dict[str, Any]]) -> int:
        if not facts:
            return 0
        pool = await self._get_pool()
        inserted = 0
        async with pool.acquire() as conn:
            for fact in facts:
                embedding = self._vector_literal(fact.get("embedding"))
                result = await conn.execute(
                    """
                    INSERT INTO hitwh_facts
                        (fact, fact_hash, embedding, hierarchy_id, source_type, source_name, metadata)
                    VALUES ($1, md5($1), $2::vector, $3, $4, $5, $6::jsonb)
                    ON CONFLICT (fact_hash) DO NOTHING
                    """,
                    fact["fact"],
                    embedding,
                    fact.get("hierarchy_id"),
                    fact["source_type"],
                    fact["source_name"],
                    json.dumps(fact.get("metadata", {}), ensure_ascii=False),
                )
                inserted += int(result == "INSERT 0 1")
        logger.info("facts_inserted count=%s submitted=%s", inserted, len(facts))
        return inserted

    async def search_facts(self, query_emb: Iterable[float], hierarchy_ids: list[int] | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        query_vector = self._vector_literal(query_emb)
        async with pool.acquire() as conn:
            if hierarchy_ids:
                rows = await conn.fetch(
                    """
                    SELECT f.fact, f.source_type, f.source_name, f.metadata,
                           h.name AS hierarchy_name, h.level,
                           1 - (f.embedding <=> $1::vector) AS similarity
                    FROM hitwh_facts f
                    LEFT JOIN hitwh_hierarchy h ON f.hierarchy_id = h.id
                    WHERE f.hierarchy_id = ANY($2::int[])
                    ORDER BY f.embedding <=> $1::vector
                    LIMIT $3
                    """,
                    query_vector,
                    hierarchy_ids,
                    top_k,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT f.fact, f.source_type, f.source_name, f.metadata,
                           h.name AS hierarchy_name, h.level,
                           1 - (f.embedding <=> $1::vector) AS similarity
                    FROM hitwh_facts f
                    LEFT JOIN hitwh_hierarchy h ON f.hierarchy_id = h.id
                    ORDER BY f.embedding <=> $1::vector
                    LIMIT $2
                    """,
                    query_vector,
                    top_k,
                )
            return [dict(row) for row in rows]

    async def get_descendants(self, node_id: int) -> list[int]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH RECURSIVE tree AS (
                    SELECT id FROM hitwh_hierarchy WHERE id = $1
                    UNION ALL
                    SELECT h.id FROM hitwh_hierarchy h JOIN tree t ON h.parent_id = t.id
                ) SELECT id FROM tree
                """,
                node_id,
            )
            return [int(row["id"]) for row in rows]

    async def get_ancestors(self, node_id: int) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH RECURSIVE tree AS (
                    SELECT id, parent_id, level, name, status, path FROM hitwh_hierarchy WHERE id = $1
                    UNION ALL
                    SELECT h.id, h.parent_id, h.level, h.name, h.status, h.path
                    FROM hitwh_hierarchy h JOIN tree t ON h.id = t.parent_id
                ) SELECT * FROM tree
                """,
                node_id,
            )
            return [dict(row) for row in rows]

    @staticmethod
    def _vector_literal(values: Iterable[float] | None) -> str | None:
        if values is None:
            return None
        return "[" + ",".join(f"{float(v):.8f}" for v in values) + "]"
