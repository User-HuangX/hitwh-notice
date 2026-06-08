from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WebPage:
    url: str
    title: str
    text: str
    links: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SourceFact:
    fact: str
    source_type: str
    source_name: str
    hierarchy_id: int | None = None
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        return {
            "fact": self.fact,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "hierarchy_id": self.hierarchy_id,
            "embedding": self.embedding,
            "metadata": self.metadata,
        }
