"""RAG 后处理管道：MMR 多样性过滤、时间衰减权重、source-type 加权、LLM 上下文压缩、重复内容检测"""
from __future__ import annotations

import difflib
import logging
import math
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------- source-type weights ----------

SOURCE_TYPE_WEIGHTS: dict[str, float] = {
    "grade": 0.8,
    "schedule": 0.7,
    "exam": 0.9,
    "plan": 0.6,
    "qq_group_msg": 0.5,
}


def source_type_weight(source_type: str | None) -> float:
    """不同来源类型赋予不同权重，未知类型默认 0.5。"""
    return SOURCE_TYPE_WEIGHTS.get(source_type or "", 0.5)


# ---------- time decay ----------

def time_decay_weight(created_at: Any, half_life_days: float = 30) -> float:
    """指数衰减 weight = 2^(-days/half_life)。

    参数 created_at 可以是 datetime 或 None（无时间信息时返回 1.0）。
    """
    if created_at is None:
        return 1.0
    now = datetime.now(timezone.utc)
    if isinstance(created_at, datetime):
        dt = created_at
    else:
        try:
            dt = datetime.fromisoformat(str(created_at))
        except (ValueError, TypeError):
            return 1.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = (now - dt).total_seconds() / 86400.0
    if days <= 0:
        return 1.0
    return math.pow(2, -days / half_life_days)


# ---------- cosine similarity (pure Python) ----------

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------- MMR 多样性过滤 ----------

def mmr_rerank(
    query_embedding: list[float],
    candidates: list[dict[str, Any]],
    lambda_param: float = 0.7,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """标准 MMR (Maximal Marginal Relevance) 算法。

    Args:
        query_embedding: 用户查询的向量
        candidates: 候选文档列表，每个候选需包含 'embedding' 字段
        lambda_param: 0.7 → 70% 相关性 + 30% 多样性
        top_k: 返回文档数

    Returns:
        按 MMR 得分降序排列的 top_k 文档
    """
    if not candidates:
        return []

    n = len(candidates)
    if n <= top_k:
        return list(candidates)

    # 预提取 embeddings
    embeddings: list[list[float] | None] = [
        c.get("embedding") for c in candidates
    ]
    # 预计算候选与 query 的相似度（优先使用 _composite_score）
    relevance_scores: list[float] = []
    for i, c in enumerate(candidates):
        if "_composite_score" in c:
            relevance_scores.append(float(c["_composite_score"]))
        elif embeddings[i] is not None and query_embedding:
            relevance_scores.append(_cosine_similarity(query_embedding, embeddings[i]))  # type: ignore[arg-type]
        else:
            relevance_scores.append(float(c.get("similarity", 0.0)))

    selected: list[int] = []
    remaining: set[int] = set(range(n))

    while remaining and len(selected) < top_k:
        best_score = -float("inf")
        best_idx = -1

        for idx in remaining:
            rel = relevance_scores[idx]

            # 多样性惩罚：与已选中文档的最大余弦相似度
            diversity = 0.0
            if selected:
                max_sim = 0.0
                emb_i = embeddings[idx]
                for sel_idx in selected:
                    emb_s = embeddings[sel_idx]
                    if emb_i is not None and emb_s is not None:
                        sim = _cosine_similarity(emb_i, emb_s)
                        if sim > max_sim:
                            max_sim = sim
                diversity = max_sim

            mmr = lambda_param * rel - (1.0 - lambda_param) * diversity
            if mmr > best_score:
                best_score = mmr
                best_idx = idx

        if best_idx < 0:
            break
        selected.append(best_idx)
        remaining.discard(best_idx)

    return [candidates[i] for i in selected]


# ---------- 重复内容检测 ----------

def deduplicate_by_content(
    documents: list[dict[str, Any]], threshold: float = 0.85
) -> list[dict[str, Any]]:
    """使用 difflib.SequenceMatcher 检测内容相似的文档，保留得分更高的。

    Args:
        documents: 文档列表，每个文档需有 'doc_content' 或 'content' 字段
        threshold: 相似度阈值，超过即视为重复

    Returns:
        去重后的文档列表
    """
    if not documents:
        return []

    # 按 similarity 降序排列，优先保留高分文档
    sorted_docs = sorted(
        documents, key=lambda d: float(d.get("similarity", 0.0)), reverse=True
    )
    result: list[dict[str, Any]] = []

    for doc in sorted_docs:
        content = doc.get("doc_content") or doc.get("content", "")
        is_dup = False
        for existing in result:
            existing_content = existing.get("doc_content") or existing.get("content", "")
            ratio = difflib.SequenceMatcher(None, content, existing_content).ratio()
            if ratio >= threshold:
                is_dup = True
                break
        if not is_dup:
            result.append(doc)

    return result


# ---------- LLM 上下文压缩 ----------

_COMPRESS_PROMPT = (
    "从以下文档中提取与查询「{query}」最相关的关键信息，"
    "以简洁的要点形式输出，每条一行。总字数不超过 {max_chars} 字。"
    "只输出提取结果，不要解释，不要评价。\n\n"
    "文档：\n{docs_text}"
)


async def compress_context(
    query: str,
    documents: list[dict[str, Any]],
    llm_provider: Any = None,
    max_chars: int = 800,
) -> str:
    """用 LLM 对检索到的多个文档做抽取式摘要压缩。

    Args:
        query: 用户原始查询
        documents: 待压缩的文档列表
        llm_provider: LLM 提供者（需有 text_chat / ask / chat 方法）
        max_chars: 压缩后最大字符数

    Returns:
        压缩后的文本；若 LLM 不可用则返回原始文档拼接
    """
    if not documents:
        return ""

    if llm_provider is None:
        # 无 LLM 时的降级：直接拼接
        return "\n".join(
            f"[{d.get('source_type', '')}] {d.get('doc_content', d.get('content', ''))[:200]}"
            for d in documents
        )

    # 构建文档文本
    parts: list[str] = []
    for i, d in enumerate(documents):
        source = d.get("source_type", "unknown")
        text = d.get("doc_content") or d.get("content", "")
        parts.append(f"[{i + 1}] [{source}] {text}")
    docs_text = "\n\n---\n\n".join(parts)

    prompt = _COMPRESS_PROMPT.format(
        query=query, max_chars=max_chars, docs_text=docs_text
    )

    try:
        from ._utils import call_provider_method

        response = await call_provider_method(
            llm_provider, ["text_chat", "ask", "chat"], prompt=prompt
        )
        result = str(response).strip()
        logger.info("compress_context success chars=%s→%s", len(docs_text), len(result))
        return result[:max_chars]
    except Exception:
        logger.exception("compress_context_failed")
        return "\n".join(
            f"[{d.get('source_type', '')}] {d.get('doc_content', d.get('content', ''))[:200]}"
            for d in documents
        )


# ---------- 组合管道 ----------

def apply_composite_scores(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """为每个候选计算复合得分 = similarity × time_decay × source_weight。"""
    for c in candidates:
        sim = float(c.get("similarity", 0.0))
        c["_composite_score"] = (
            sim
            * time_decay_weight(c.get("created_at"))
            * source_type_weight(c.get("source_type"))
        )
    return candidates


async def post_process_pipeline(
    query_embedding: list[float],
    candidates: list[dict[str, Any]],
    *,
    query: str = "",
    llm_provider: Any = None,
    top_k: int = 5,
    lambda_param: float = 0.7,
    dedup_threshold: float = 0.85,
    compress: bool = False,
    max_chars: int = 800,
) -> str | list[dict[str, Any]]:
    """一站式 RAG 后处理管道。

    Steps:
        1. 按 document_id 去重（保留首次出现的最高相似度）
        2. 内容相似度去重
        3. 计算复合得分（similarity × time_decay × source_weight）
        4. MMR 多样性过滤
        5. 可选 LLM 压缩

    Returns:
        若 compress=False 返回文档列表，否则返回压缩后的字符串
    """
    if not candidates:
        return "" if compress else []

    # Step 1: document_id 去重（保留首次-最高相似度）
    seen: set[int] = set()
    unique: list[dict[str, Any]] = [
        c
        for c in candidates
        if c["document_id"] not in seen and not seen.add(c["document_id"])
    ]

    # Step 2: 内容去重
    unique = deduplicate_by_content(unique, threshold=dedup_threshold)

    # Step 3: 复合得分
    unique = apply_composite_scores(unique)

    # Step 4: MMR 多样性过滤
    unique = mmr_rerank(query_embedding, unique, lambda_param=lambda_param, top_k=top_k)

    # Step 5: 可选压缩
    if compress:
        return await compress_context(query, unique, llm_provider=llm_provider, max_chars=max_chars)

    return unique
