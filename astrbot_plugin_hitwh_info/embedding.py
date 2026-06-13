from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from difflib import SequenceMatcher
from typing import Any

logger = logging.getLogger(__name__)


class Embedder:
    def __init__(self,
                 embedding_api_base: str = "",
                 embedding_api_key: str = "",
                 embedding_model: str = "",
                 dimension: int = 1024,
                 rerank_api_base: str = "",
                 rerank_api_key: str = "",
                 rerank_model: str = "",
                 ) -> None:
        self.emb_api_base = embedding_api_base.rstrip("/")
        self.emb_api_key = embedding_api_key
        self.emb_model = embedding_model
        self.dimension = dimension
        self.rerank_api_base = rerank_api_base.rstrip("/")
        self.rerank_api_key = rerank_api_key
        self.rerank_model = rerank_model

    @property
    def _has_embedding_api(self) -> bool:
        return bool(self.emb_api_base and self.emb_api_key and self.emb_model)

    @property
    def _has_rerank_api(self) -> bool:
        return bool(self.rerank_api_base and self.rerank_api_key and self.rerank_model)

    async def embed(self, text: str) -> list[float]:
        if self._has_embedding_api:
            try:
                return await self._api_embed(text)
            except Exception:
                logger.exception("api_embedding_failed")
        return self._stable_hash_embedding(text)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        if self._has_embedding_api and texts:
            try:
                return await self._api_embed_batch(texts)
            except Exception:
                logger.exception("api_embed_batch_failed")
        return [await self.embed(text) for text in texts]

    async def _api_embed(self, text: str) -> list[float]:
        results = await self._api_embed_batch([text])
        return results[0] if results else []

    async def _api_embed_batch(self, texts: list[str]) -> list[list[float]]:
        import aiohttp
        url = f"{self.emb_api_base}/embeddings"
        headers = {"Authorization": f"Bearer {self.emb_api_key}", "Content-Type": "application/json"}
        payload = {"model": self.emb_model, "input": texts}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers,
                                    timeout=aiohttp.ClientTimeout(60)) as resp:
                data = await resp.json()
        embeddings: list[list[float] | None] = [None] * len(texts)
        for item in data.get("data", []):
            idx = item.get("index", 0)
            if idx < len(embeddings):
                embeddings[idx] = [float(x) for x in item["embedding"]]
        return [e if e else self._stable_hash_embedding(t) for e, t in zip(embeddings, texts)]

    async def rerank(self, query: str, documents: list[str]) -> list[dict[str, Any]]:
        if not documents:
            return []
        if self._has_rerank_api:
            try:
                return await self._api_rerank(query, documents)
            except Exception:
                logger.exception("rerank_api_failed")
        return self._simple_rerank(query, documents)

    async def _api_rerank(self, query: str, documents: list[str]) -> list[dict[str, Any]]:
        import aiohttp
        url = f"{self.rerank_api_base}/rerank"
        headers = {"Authorization": f"Bearer {self.rerank_api_key}", "Content-Type": "application/json"}
        payload = {"model": self.rerank_model, "query": query, "documents": documents}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers,
                                    timeout=aiohttp.ClientTimeout(30)) as resp:
                data = await resp.json()
        results = data.get("results", [])
        return [{"index": r.get("index", i), "score": r.get("relevance_score", 0)}
                for i, r in enumerate(sorted(results, key=lambda x: x.get("relevance_score", 0), reverse=True))]

    def _simple_rerank(self, query: str, documents: list[str]) -> list[dict[str, Any]]:
        results = []
        for i, doc in enumerate(documents):
            score = SequenceMatcher(None, query.lower(), doc.lower()).ratio()
            results.append({"index": i, "score": score})
        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def _stable_hash_embedding(self, text: str) -> list[float]:
        values = []
        seed = text.encode("utf-8")
        for index in range(self.dimension):
            digest = hashlib.blake2b(seed + index.to_bytes(2, "big"), digest_size=4).digest()
            number = int.from_bytes(digest, "big") / 0xFFFFFFFF
            values.append(number * 2 - 1)
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]
