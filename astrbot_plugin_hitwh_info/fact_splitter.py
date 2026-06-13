from __future__ import annotations

import logging
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
