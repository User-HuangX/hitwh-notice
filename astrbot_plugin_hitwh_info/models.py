from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class WebPage(BaseModel):
    url: str
    title: str
    text: str
    links: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw_html: str = ""


class SourceFact(BaseModel):
    fact: str
    source_type: str
    source_name: str
    hierarchy_id: int | None = None
    embedding: list[float] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        return self.model_dump()
