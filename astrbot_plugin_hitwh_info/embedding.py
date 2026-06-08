from __future__ import annotations

import hashlib
import logging
import math
from typing import Any

logger = logging.getLogger(__name__)


class Embedder:
    def __init__(self, provider: Any | None = None, dimension: int = 1536) -> None:
        self.provider = provider
        self.dimension = dimension

    async def embed(self, text: str) -> list[float]:
        if self.provider is not None:
            for name in ("get_embedding", "embed", "text_embedding"):
                method = getattr(self.provider, name, None)
                if method is None:
                    continue
                try:
                    result = await method(text)
                    vector = self._coerce(result)
                    if vector:
                        return self._fit(vector)
                except Exception:
                    logger.exception("provider_embedding_failed method=%s", name)
        return self._stable_hash_embedding(text)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(text) for text in texts]

    def _stable_hash_embedding(self, text: str) -> list[float]:
        values = []
        seed = text.encode("utf-8")
        for index in range(self.dimension):
            digest = hashlib.blake2b(seed + index.to_bytes(2, "big"), digest_size=4).digest()
            number = int.from_bytes(digest, "big") / 0xFFFFFFFF
            values.append(number * 2 - 1)
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]

    def _fit(self, vector: list[float]) -> list[float]:
        if len(vector) == self.dimension:
            return vector
        if len(vector) > self.dimension:
            return vector[: self.dimension]
        return vector + [0.0] * (self.dimension - len(vector))

    @staticmethod
    def _coerce(result: Any) -> list[float]:
        if isinstance(result, dict):
            result = result.get("embedding") or result.get("data")
        if isinstance(result, list) and result and isinstance(result[0], dict):
            result = result[0].get("embedding")
        return [float(x) for x in result] if isinstance(result, list) else []
