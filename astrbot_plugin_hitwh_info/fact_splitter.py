from __future__ import annotations

import logging
import math
import re
from typing import Any

from more_itertools import unique_everseen

from ._utils import call_provider_method

logger = logging.getLogger(__name__)


class FactSplitter:
    def __init__(
        self,
        llm_provider: Any | None = None,
        min_length: int = 12,
        prompt_template: str | None = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.min_length = min_length
        self.prompt_template = prompt_template or (
            "将以下文本拆成一条条独立的原子事实，每条一行。"
            "只输出事实列表，不要解释，确保每条事实脱离上下文仍可理解。\n\n{text}"
        )

    async def split(self, text: str) -> list[str]:
        clean_text = self._normalize_text(text)
        if not clean_text:
            return []
        if self.llm_provider is not None:
            try:
                response = await call_provider_method(
                    self.llm_provider, ["text_chat", "ask", "chat"], prompt=self.prompt_template.format(text=clean_text)
                )
                facts = self._parse_lines(str(response))
                if facts:
                    logger.info("llm_fact_split count=%s", len(facts))
                    return facts
            except Exception:
                logger.exception("llm_fact_split_failed fallback=rule")
        facts = self._rule_split(clean_text)
        logger.info("rule_fact_split count=%s", len(facts))
        return facts

    def _rule_split(self, text: str) -> list[str]:
        chunks = re.split(r"(?<=[。！？!?；;])\s*|[\r\n]+", text)
        return list(unique_everseen(chunk.strip() for chunk in chunks if self._valid(chunk)))

    def _parse_lines(self, text: str) -> list[str]:
        lines = []
        for line in re.split(r"[\r\n]+", text):
            line = re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", line).strip()
            if self._valid(line):
                lines.append(line)
        return list(unique_everseen(lines))

    def _valid(self, sentence: str) -> bool:
        sentence = sentence.strip()
        if len(sentence) < self.min_length:
            return False
        if sentence in {"了解更多", "查看更多", "点击查看", "无关"}:
            return False
        return bool(re.search(r"[\u4e00-\u9fffA-Za-z0-9]", sentence))

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"[ \t]+", " ", text or "").strip()


class SemanticChunker:
    """语义分块器：基于嵌入向量相似度的断点检测，支持重叠。

    当相邻句子的 embedding 相似度低于阈值时断开。
    每个 chunk 与前一个 chunk 重叠 overlap_size 句。
    """

    SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])\s*|\n+")

    def __init__(
        self,
        embedder: Any | None = None,
        similarity_threshold: float = 0.65,
        overlap_size: int = 1,
        min_chunk_size: int = 40,
        max_chunk_size: int = 1000,
    ) -> None:
        """
        Args:
            embedder: Embedder 实例，需有 embed(text) -> list[float] 方法。
            similarity_threshold: 相邻句子相似度低于此值时断开（余弦相似度，0~1）。
            overlap_size: 每个 chunk 与前一个 chunk 重叠的句子数。
            min_chunk_size: 最小块字符数，低于此值尝试与相邻块合并。
            max_chunk_size: 最大块字符数，超过此值强制断开。
        """
        self.embedder = embedder
        self.similarity_threshold = similarity_threshold
        self.overlap_size = overlap_size
        self.min_chunk_size = min_chunk_size
        self.max_chunk_size = max_chunk_size

    async def chunk(self, text: str) -> list[str]:
        """将文本拆分为语义块。

        当 embedder 不可用时，降级为简单按句号分块。
        """
        sentences = self._split_sentences(text)
        if not sentences:
            return []

        if self.embedder is None:
            return self._fallback_chunk(sentences)

        # 获取每个句子的嵌入向量
        try:
            embeddings = await self.embedder.embed_many(sentences)
        except Exception:
            logger.exception("semantic_chunk_embed_failed, fallback to rule")
            return self._fallback_chunk(sentences)

        # 计算相邻句子相似度，确定断点
        breakpoints = self._find_breakpoints(sentences, embeddings)

        # 根据断点分块
        chunks = self._build_chunks(sentences, breakpoints)

        logger.info("semantic_chunk count=%s sentences=%s", len(chunks), len(sentences))
        return chunks

    def _split_sentences(self, text: str) -> list[str]:
        """将文本按句子分割，保留有意义的内容。"""
        parts = self.SENTENCE_SPLIT_RE.split(text)
        sentences: list[str] = []
        for part in parts:
            part = part.strip()
            if part and len(part) >= 2:
                sentences.append(part)
        return sentences

    def _find_breakpoints(
        self, sentences: list[str], embeddings: list[list[float]]
    ) -> list[int]:
        """根据相邻句子 embedding 的余弦相似度，找出断点索引。

        返回值是断点位置列表，表示在这些句子之后断开。
        例如 [3, 7] 表示在第 3 句和第 7 句之后断开。
        """
        breakpoints: list[int] = []
        cumulative_len = len(sentences[0]) if sentences else 0

        for i in range(len(sentences) - 1):
            sim = self._cosine_similarity(embeddings[i], embeddings[i + 1])
            cumulative_len += len(sentences[i + 1])

            # 相似度低于阈值 → 断点
            if sim < self.similarity_threshold:
                breakpoints.append(i)
            # 累计长度超过 max_chunk_size → 强制断点
            elif cumulative_len >= self.max_chunk_size:
                breakpoints.append(i)
                cumulative_len = len(sentences[i + 1])

        return breakpoints

    def _build_chunks(
        self, sentences: list[str], breakpoints: list[int]
    ) -> list[str]:
        """根据断点构建 chunk，并处理重叠和最小长度。"""
        if not breakpoints:
            # 没有断点，整个文本作为一个 chunk
            return ["".join(sentences)]

        # 切分句子组
        groups: list[list[str]] = []
        start = 0
        for bp in sorted(breakpoints):
            groups.append(sentences[start:bp + 1])
            start = bp + 1
        if start < len(sentences):
            groups.append(sentences[start:])

        # 合并过短的 group
        groups = self._merge_short_groups(groups)

        # 构建最终 chunks，包含重叠
        chunks: list[str] = []
        for i, group in enumerate(groups):
            chunk_text = "".join(group)

            # 添加重叠：将前一个 chunk 的最后 overlap_size 句加入
            if i > 0 and self.overlap_size > 0:
                prev_group = groups[i - 1]
                overlap_sentences = prev_group[-self.overlap_size:] if len(prev_group) >= self.overlap_size else prev_group
                chunk_text = "".join(overlap_sentences) + chunk_text

            chunks.append(chunk_text)

        return chunks

    def _merge_short_groups(self, groups: list[list[str]]) -> list[list[str]]:
        """将长度不足 min_chunk_size 的 group 合并到相邻 group。"""
        merged: list[list[str]] = []
        i = 0
        while i < len(groups):
            group = groups[i]
            group_text = "".join(group)
            if len(group_text) < self.min_chunk_size and i + 1 < len(groups):
                # 与下一个 group 合并
                groups[i + 1] = group + groups[i + 1]
                i += 1
            elif len(group_text) < self.min_chunk_size and merged:
                # 与上一个 chunk 合并
                merged[-1].extend(group)
                i += 1
            else:
                merged.append(group)
                i += 1
        return merged

    def _fallback_chunk(self, sentences: list[str]) -> list[str]:
        """降级方案：简单按句子顺序分块，确保每块不超过 max_chunk_size。"""
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0
        for s in sentences:
            if current_len + len(s) > self.max_chunk_size and current:
                chunks.append("".join(current))
                # 重叠
                if self.overlap_size > 0 and len(current) >= self.overlap_size:
                    current = current[-self.overlap_size:]
                    current_len = sum(len(x) for x in current)
                else:
                    current = []
                    current_len = 0
            current.append(s)
            current_len += len(s)
        if current:
            chunks.append("".join(current))
        return chunks

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
