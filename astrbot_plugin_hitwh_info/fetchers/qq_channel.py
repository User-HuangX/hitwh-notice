from __future__ import annotations

import logging
from typing import Any

from .base import BaseFetcher

logger = logging.getLogger(__name__)


class QQChannelFetcher(BaseFetcher):
    def __init__(self, channels: list[dict[str, Any]] | None = None, client: Any | None = None) -> None:
        self.channels = channels or []
        self.client = client

    async def fetch(self) -> list[dict[str, Any]]:
        if self.client and hasattr(self.client, "fetch_channels"):
            channels = await self.client.fetch_channels()
        else:
            channels = self.channels
        logger.info("qq_channels_fetched count=%s", len(channels))
        return list(channels)

    def to_facts(self, channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
        facts = []
        for channel in channels:
            guild_id = str(channel.get("guild_id") or channel.get("id") or channel.get("source_name"))
            guild_name = str(channel.get("guild_name") or channel.get("name") or guild_id)
            subchannels = [str(x) for x in channel.get("channels", [])]
            if subchannels:
                quoted = "、".join(f"\"{item}\"" for item in subchannels)
                fact = f"QQ频道\"{guild_name}\"包含子频道{quoted}。"
            else:
                fact = f"QQ频道\"{guild_name}\"是一个校园信息频道。"
            facts.append({
                "fact": fact,
                "source_type": "qq_channel",
                "source_name": guild_id,
                "metadata": {"guild_name": guild_name, "channels": subchannels},
            })
        return facts
