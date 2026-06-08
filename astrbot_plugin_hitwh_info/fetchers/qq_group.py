from __future__ import annotations

import logging
from typing import Any

from .base import BaseFetcher

logger = logging.getLogger(__name__)


class QQGroupFetcher(BaseFetcher):
    def __init__(self, groups: list[dict[str, Any]] | None = None, client: Any | None = None) -> None:
        self.groups = groups or []
        self.client = client

    async def fetch(self) -> list[dict[str, Any]]:
        if self.client and hasattr(self.client, "fetch_groups"):
            groups = await self.client.fetch_groups()
        else:
            groups = self.groups
        logger.info("qq_groups_fetched count=%s", len(groups))
        return list(groups)

    def to_facts(self, groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        facts = []
        for group in groups:
            group_id = str(group.get("group_id") or group.get("id") or group.get("source_name"))
            group_name = str(group.get("group_name") or group.get("name") or group_id)
            member_count = group.get("member_count") or group.get("members")
            suffix = f"，共{member_count}名成员" if member_count is not None else ""
            facts.append({
                "fact": f"QQ群\"{group_name}\"(群号:{group_id})是一个校园信息群{suffix}。",
                "source_type": "qq_group",
                "source_name": group_id,
                "metadata": {"group_name": group_name, "member_count": member_count},
            })
        return facts
