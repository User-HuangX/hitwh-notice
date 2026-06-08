from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseFetcher(ABC):
    @abstractmethod
    async def fetch(self) -> list[Any]:
        raise NotImplementedError
