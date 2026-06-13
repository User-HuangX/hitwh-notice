from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import Any


async def call_provider_method(provider: Any, method_names: list[str], **kwargs: Any) -> Any:
    method = _find_provider_method(provider, method_names)
    if method is None:
        raise TypeError(f"provider must expose one of: {', '.join(method_names)}")
    try:
        return await method(**kwargs)
    except TypeError:
        return await method(*kwargs.values())


def _find_provider_method(
    provider: Any, method_names: list[str]
) -> Callable[..., Awaitable[Any]] | None:
    for name in method_names:
        candidate = getattr(provider, name, None)
        if candidate is not None:
            return candidate
    return None


def with_conn(func):
    sig = inspect.signature(func)
    params = list(sig.parameters.keys())

    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await func(self, conn, *args, **kwargs)

    return wrapper


def extract_msg(event: Any) -> str:
    return event.get_message_str() if hasattr(event, "get_message_str") else ""


def strip_command_prefix(msg: str, prefixes: list[str]) -> str:
    for pre in prefixes:
        if msg.startswith(pre):
            return msg[len(pre):].strip()
    return msg.strip()
