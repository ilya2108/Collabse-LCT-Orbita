"""In-process кэш с TTL (без KeyDB/Redis).

Один экземпляр backend на стенде, поэтому кэш живёт в памяти процесса: графы навыков из
YAML, JWKS, результаты дорогих чистых вычислений. Правила:
- ключи с префиксом `fsp:v1:`;
- инвалидация `delete` строго после коммита транзакции;
- расшифрованные ПДн в кэш не попадают никогда;
- интерфейс оставлен async и совместим с кэшем «Альмы», чтобы при необходимости
  заменить реализацию на внешнюю без правки вызывающего кода.
"""

from __future__ import annotations

import time
from typing import Any

KEY_PREFIX = "fsp:v1:"


def cache_key(suffix: str) -> str:
    return f"{KEY_PREFIX}{suffix}"


class Cache:
    def __init__(self) -> None:
        # key → (expires_at_monotonic, value)
        self._store: dict[str, tuple[float, Any]] = {}

    def _get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= time.monotonic():
            self._store.pop(key, None)
            return None
        return value

    async def get_json(self, suffix: str) -> Any | None:
        return self._get(cache_key(suffix))

    async def set_json(self, suffix: str, value: Any, ttl_seconds: int) -> bool:
        self._store[cache_key(suffix)] = (time.monotonic() + ttl_seconds, value)
        return True

    async def delete(self, *suffixes: str) -> bool:
        for suffix in suffixes:
            self._store.pop(cache_key(suffix), None)
        return True

    async def clear(self) -> None:
        self._store.clear()

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self._store.clear()


_cache: Cache | None = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache


async def close_cache() -> None:
    global _cache
    if _cache is not None:
        await _cache.close()
    _cache = None
