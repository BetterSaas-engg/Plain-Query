"""Filter cache: skip router + translator on repeat queries.

Caches the validated filter keyed on (customer, schema_fingerprint, normalized_query).
A cache hit means zero LLM calls — the request runs through the deterministic path
only (validate is already done; search + loosening are stateless pure functions).

Implementation: in-process LRU behind a Protocol so a shared store (Redis) can
drop in later without touching the engine or callers.

NOTE: In-process means each process has its own cache. Hit rates do not hold
across horizontally scaled instances. A shared backend (Redis, Memcached) is
required for that — swap LRUFilterCache for a shared implementation of
FilterCacheBackend when the time comes.

No TTL: a validated filter is a pure function of (query_text, schema). It cannot
go stale. When a schema changes, the schema_fingerprint changes, the cache key
rotates, and old entries are naturally orphaned (never served, eventually evicted).

Per-customer keying: globally-keyed would be correct (same query + same schema =
same filter regardless of customer) and higher-hit-rate, but enterprise security
teams object to any cross-tenant shared surface. Deliberate trade: correctness-
neutral, hit-rate-negative, sale-positive.
"""

import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CacheEntry:
    """Everything needed to skip both router and translator on a cache hit."""
    vertical: str
    schema_path: str
    data_path: str
    filters: dict = field(default_factory=dict)
    sort: str = ""
    limit: int = 25
    unmapped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def normalize_query(text: str) -> str:
    """Lowercase, strip, collapse whitespace. Deterministic normalization."""
    return " ".join(text.lower().split())


def schema_fingerprint(customer_verticals: dict[str, dict]) -> str:
    """Hash all schema file contents for a customer. Rotates when any schema changes.

    The fingerprint is part of the cache key. When a customer updates a schema
    file, the fingerprint changes, every cached entry for that customer becomes
    a miss, and stale filters (which may reference dropped fields or removed
    enum values) are never served.
    """
    h = hashlib.sha256()
    # Sort by vertical name for deterministic ordering
    for v_name in sorted(customer_verticals.keys()):
        schema_path = customer_verticals[v_name]["schema"]
        content = Path(schema_path).read_bytes()
        h.update(v_name.encode("utf-8"))
        h.update(b"\x00")
        h.update(content)
        h.update(b"\x00")
    return h.hexdigest()[:16]  # 16 hex chars = 64 bits, sufficient for key rotation


def make_cache_key(customer_name: str, fingerprint: str, query: str) -> str:
    """Build the cache lookup key. Deterministic for identical inputs."""
    normalized = normalize_query(query)
    raw = f"{customer_name}\x00{fingerprint}\x00{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@runtime_checkable
class FilterCacheBackend(Protocol):
    """Abstract cache interface. Implement for Redis, Memcached, etc."""
    def get(self, key: str) -> CacheEntry | None: ...
    def put(self, key: str, entry: CacheEntry) -> None: ...
    def stats(self) -> dict: ...


class LRUFilterCache:
    """In-process LRU cache.

    Not thread-safe — sufficient for single-process deployments and the demo.
    A production multi-worker deployment would use a shared backend instead.
    """

    def __init__(self, max_size: int = 1024):
        self._max_size = max_size
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> CacheEntry | None:
        entry = self._store.get(key)
        if entry is not None:
            self._store.move_to_end(key)
            self._hits += 1
            return entry
        self._misses += 1
        return None

    def put(self, key: str, entry: CacheEntry) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = entry
        if len(self._store) > self._max_size:
            self._store.popitem(last=False)

    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 4) if total > 0 else 0.0,
            "size": len(self._store),
            "max_size": self._max_size,
        }
