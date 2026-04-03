"""Simple in-memory TTL cache for content search results."""

import hashlib
import json
import time
from typing import Any

_cache: dict[str, tuple[float, Any]] = {}
_TTL = 60  # seconds


def _make_key(params: dict) -> str:
    return hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()


def get(params: dict) -> Any | None:
    key = _make_key(params)
    entry = _cache.get(key)
    if entry is not None:
        ts, value = entry
        if time.time() - ts < _TTL:
            return value
        del _cache[key]
    return None


def put(params: dict, value: Any) -> None:
    key = _make_key(params)
    _cache[key] = (time.time(), value)


def invalidate() -> None:
    _cache.clear()
