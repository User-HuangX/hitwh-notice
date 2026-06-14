from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
from typing import Any

import numpy as np

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
                 llm_provider: Any | None = None,
                 ) -> None:
        self.emb_api_base = embedding_api_base.rstrip("/")
        self.emb_api_key = embedding_api_key
        self.emb_model = embedding_model
        self.dimension = dimension
        self.rerank_api_base = rerank_api_base.rstrip("/")
        self.rerank_api_key = rerank_api_key
        self.rerank_model = rerank_model
        self.llm_provider = llm_provider

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

    # ------------------------------------------------------------------
    # Rerank pipeline
    # ------------------------------------------------------------------

    async def rerank(self, query: str, documents: list[str]) -> list[dict[str, Any]]:
        """Main rerank entry point.

        1. Compress documents (TF-IDF sentence extraction)
        2. Try API rerank → LLM listwise rerank → TF-IDF fallback
        3. Calibrate scores (min-max norm, threshold filter)
        """
        if not documents:
            return []

        # Compress: keep only the most relevant 2-3 sentences per doc
        compressed, idx_map = self._compress_documents(query, documents)

        results = None
        # 1) API rerank
        if self._has_rerank_api:
            try:
                results = await self._api_rerank(query, compressed)
            except Exception:
                logger.exception("rerank_api_failed")

        # 2) LLM listwise rerank (fallback when API unavailable/failed)
        if results is None and self.llm_provider is not None:
            try:
                results = await self._llm_rerank(query, compressed)
            except Exception:
                logger.exception("llm_rerank_failed")

        # 3) TF-IDF cosine similarity (final fallback)
        if results is None:
            results = self._tfidf_rerank(query, compressed)

        # Map compressed indices back to original
        results = [
            {"index": idx_map[r["index"]], "score": r["score"]}
            for r in results
            if r["index"] < len(idx_map)
        ]

        # Calibrate: normalize + threshold
        return self._calibrate_scores(results)

    # ------------------------------------------------------------------
    # API rerank
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # LLM listwise reranking (CompRank-inspired)
    # ------------------------------------------------------------------

    async def _llm_rerank(self, query: str, documents: list[str]) -> list[dict[str, Any]]:
        """Use the project LLM provider to perform listwise reranking.

        The LLM receives the query + candidate documents and outputs a
        ranked list of indices.  Inspired by CompRank (arXiv:2606.11700).
        """
        from ._utils import call_provider_method

        docs_text = "\n".join(
            f"[{i}] {doc[:500]}" for i, doc in enumerate(documents)
        )
        prompt = (
            "你是一个文档排序助手。给定一个查询和一组候选文档，请根据文档与查询的相关性从高到低排序。\n\n"
            f"查询：{query}\n\n"
            f"候选文档：\n{docs_text}\n\n"
            "请按相关性从高到低输出文档编号列表，每行一个数字，只输出数字，不要其他内容。\n"
            "例如：\n3\n0\n5\n1\n..."
        )

        response = await call_provider_method(
            self.llm_provider, ["text_chat", "ask", "chat"], prompt=prompt,
        )
        indices = self._parse_rank_indices(str(response), len(documents))
        # Assign descending scores: best = 1.0, descending linearly
        n = len(indices)
        return [
            {"index": idx, "score": (n - i) / n if n > 0 else 0.0}
            for i, idx in enumerate(indices)
        ]

    @staticmethod
    def _parse_rank_indices(text: str, num_docs: int) -> list[int]:
        """Parse LLM output like '3\\n0\\n5\\n1' into ordered index list."""
        indices: list[int] = []
        seen: set[int] = set()
        for token in re.split(r"[\s,;\n]+", text.strip()):
            token = token.strip()
            if token.lstrip("-").isdigit():
                idx = int(token)
                if 0 <= idx < num_docs and idx not in seen:
                    indices.append(idx)
                    seen.add(idx)
        # Append any missing indices at the end
        for i in range(num_docs):
            if i not in seen:
                indices.append(i)
        return indices

    # ------------------------------------------------------------------
    # Document compression (CompRank-inspired token-level compression)
    # ------------------------------------------------------------------

    def _compress_documents(
        self, query: str, documents: list[str], topk: int = 3,
    ) -> tuple[list[str], list[int]]:
        """Extract top-K most relevant sentences from each document using TF-IDF.

        Returns (compressed_docs, idx_map) where idx_map[i] is the original
        document index for compressed_docs[i].
        """
        if not documents:
            return [], []

        # Split each document into sentences
        all_sents: list[tuple[int, str]] = []  # (doc_idx, sentence)
        for i, doc in enumerate(documents):
            for sent in self._split_sentences(doc):
                if len(sent.strip()) >= 6:
                    all_sents.append((i, sent))

        if not all_sents:
            return list(documents), list(range(len(documents)))

        # Use jieba + TfidfVectorizer for sentence scoring
        try:
            jieba_cut = self._get_jieba_cut()
            tokenized = [" ".join(jieba_cut(s)) for _, s in all_sents]
            query_tok = " ".join(jieba_cut(query))
            all_texts = [query_tok] + tokenized

            vectorizer = self._get_tfidf_vectorizer()
            tfidf = vectorizer.fit_transform(all_texts)
            query_vec = tfidf[0]
            sent_vecs = tfidf[1:]

            from sklearn.metrics.pairwise import cosine_similarity
            scores = cosine_similarity(query_vec, sent_vecs).flatten()
        except Exception:
            logger.warning("tfidf_compression_failed using first sentences")
            # Fallback: take first topk sentences from each doc
            compressed: list[str] = []
            idx_map: list[int] = []
            for i, doc in enumerate(documents):
                sents = self._split_sentences(doc)
                compressed.append(" ".join(sents[:topk]) or doc[:500])
                idx_map.append(i)
            return compressed, idx_map

        # For each document, collect its sentences + scores
        doc_sents: dict[int, list[tuple[float, str]]] = {}
        for (doc_idx, sent), score in zip(all_sents, scores):
            doc_sents.setdefault(doc_idx, []).append((float(score), sent))

        compressed_docs: list[str] = []
        idx_map: list[int] = []
        for doc_idx in sorted(doc_sents):
            pairs = sorted(doc_sents[doc_idx], key=lambda x: x[0], reverse=True)
            top_sents = [s for _, s in pairs[:topk]]
            compressed_docs.append(" ".join(top_sents))
            idx_map.append(doc_idx)

        return compressed_docs, idx_map

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences (Chinese + English aware)."""
        if not text:
            return []
        # Split on Chinese/English sentence boundaries
        parts = re.split(
            r"(?<=[。！？!?；;])\s*|(?<=[.])\s+(?=[A-Z])|[\r\n]+",
            text,
        )
        return [p.strip() for p in parts if p.strip()]

    @staticmethod
    def _get_jieba_cut():
        """Lazy-load jieba."""
        try:
            import jieba
            return jieba.cut
        except ImportError:
            # Fallback: character-level tokenization for Chinese
            def _char_cut(text: str):
                return list(text.replace(" ", ""))
            return _char_cut

    @staticmethod
    def _get_tfidf_vectorizer():
        """Lazy-load TfidfVectorizer with Chinese-friendly settings."""
        from sklearn.feature_extraction.text import TfidfVectorizer
        return TfidfVectorizer(
            analyzer="word",
            token_pattern=r"(?u)\b\w+\b",
            max_features=5000,
        )

    # ------------------------------------------------------------------
    # Score calibration (min-max normalization + threshold filtering)
    # ------------------------------------------------------------------

    @staticmethod
    def _calibrate_scores(
        results: list[dict[str, Any]], threshold: float = 0.3,
    ) -> list[dict[str, Any]]:
        """Normalize scores to [0, 1] via min-max and filter below threshold."""
        if not results:
            return []
        scores = np.array([r["score"] for r in results], dtype=float)
        s_min, s_max = float(scores.min()), float(scores.max())
        if s_max - s_min < 1e-8:
            # All scores identical — keep all at 1.0 if above threshold, else filtered
            if s_max < threshold:
                return []
            return [{"index": r["index"], "score": 1.0} for r in results]

        normalized = (scores - s_min) / (s_max - s_min)
        return [
            {"index": r["index"], "score": float(ns)}
            for r, ns in zip(results, normalized)
            if ns >= threshold
        ]

    # ------------------------------------------------------------------
    # TF-IDF fallback rerank (replaces old SequenceMatcher fallback)
    # ------------------------------------------------------------------

    def _tfidf_rerank(self, query: str, documents: list[str]) -> list[dict[str, Any]]:
        """TF-IDF + cosine similarity fallback reranker.

        Uses jieba for Chinese tokenization and sklearn's TfidfVectorizer.
        """
        if not documents:
            return []

        jieba_cut = self._get_jieba_cut()
        tokenized = [" ".join(jieba_cut(doc)) for doc in documents]
        query_tok = " ".join(jieba_cut(query))
        all_texts = [query_tok] + tokenized

        try:
            vectorizer = self._get_tfidf_vectorizer()
            tfidf = vectorizer.fit_transform(all_texts)
            query_vec = tfidf[0]
            doc_vecs = tfidf[1:]

            from sklearn.metrics.pairwise import cosine_similarity
            scores = cosine_similarity(query_vec, doc_vecs).flatten()
        except Exception:
            logger.exception("tfidf_rerank_failed")
            # Last resort: return in original order with uniform scores
            return [{"index": i, "score": 0.5} for i in range(len(documents))]

        results = [
            {"index": i, "score": float(s)}
            for i, s in enumerate(scores)
        ]
        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    # ------------------------------------------------------------------
    # Deterministic hash embedding
    # ------------------------------------------------------------------

    def _stable_hash_embedding(self, text: str) -> list[float]:
        values = []
        seed = text.encode("utf-8")
        for index in range(self.dimension):
            digest = hashlib.blake2b(seed + index.to_bytes(2, "big"), digest_size=4).digest()
            number = int.from_bytes(digest, "big") / 0xFFFFFFFF
            values.append(number * 2 - 1)
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]
