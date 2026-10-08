from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any


class TTLCache:
    """Tiny in-memory async TTL cache (single-instance; fine for low-traffic drill-downs)."""

    def __init__(self, ttl: float = 900.0, maxsize: int = 512) -> None:
        self.ttl = ttl
        self.maxsize = maxsize
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get_or_set(self, key: str, factory: Callable[[], Awaitable[Any]]) -> Any:
        now = time.monotonic()
        hit = self._store.get(key)
        if hit and hit[0] > now:
            return hit[1]
        value = await factory()
        async with self._lock:
            if len(self._store) >= self.maxsize:
                self._store = {k: v for k, v in self._store.items() if v[0] > now}
                while len(self._store) >= self.maxsize:
                    self._store.pop(next(iter(self._store)), None)
            self._store[key] = (now + self.ttl, value)
        return value
