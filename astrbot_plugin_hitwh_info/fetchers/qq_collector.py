from __future__ import annotations

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

NAPCAT_API = "http://127.0.0.1:3000"


async def fetch_qq_groups() -> list[dict[str, Any]]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{NAPCAT_API}/get_group_list", json={}, timeout=aiohttp.ClientTimeout(10)) as resp:
                data = await resp.json()
    except Exception:
        logger.exception("get_group_list_failed")
        return []

    groups = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(groups, list):
        return []
    result = []
    for g in groups:
        result.append({
            "group_id": str(g.get("group_id", "")),
            "group_name": str(g.get("group_name", "")),
            "member_count": int(g.get("member_count", 0)),
        })
    logger.info("qq_groups_fetched count=%s", len(result))
    return result


async def fetch_guild_list() -> list[dict[str, Any]]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{NAPCAT_API}/get_guild_list", json={}, timeout=aiohttp.ClientTimeout(10)) as resp:
                data = await resp.json()
    except Exception:
        logger.exception("get_guild_list_failed")
        return []

    guilds = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(guilds, list):
        return []
    result = []
    for g in guilds:
        result.append({
            "guild_id": str(g.get("guild_id", "")),
            "guild_name": str(g.get("guild_name", "")),
        })
    logger.info("guilds_fetched count=%s", len(result))
    return result
