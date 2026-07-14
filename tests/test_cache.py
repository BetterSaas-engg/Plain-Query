"""Deterministic tests for the filter cache. No LLM calls."""

import pytest

from src.plainquery.cache import (
    CacheEntry,
    LRUFilterCache,
    make_cache_key,
    normalize_query,
    schema_fingerprint,
)


# === Normalize ===


def test_normalize_lowercase():
    assert normalize_query("Red Honda Civic") == "red honda civic"


def test_normalize_collapse_whitespace():
    assert normalize_query("  red   honda   civic  ") == "red honda civic"


def test_normalize_strips():
    assert normalize_query("  hello  ") == "hello"


def test_normalize_tabs_and_newlines():
    assert normalize_query("red\thonda\ncivic") == "red honda civic"


def test_normalize_identical_queries_match():
    assert normalize_query("cheap hotel in Toronto") == normalize_query("  Cheap  Hotel  in  TORONTO  ")


# === Cache key ===


def test_cache_key_deterministic():
    k1 = make_cache_key("Expedia", "abc123", "cheap hotel")
    k2 = make_cache_key("Expedia", "abc123", "cheap hotel")
    assert k1 == k2


def test_cache_key_different_customer():
    k1 = make_cache_key("Expedia", "abc123", "cheap hotel")
    k2 = make_cache_key("AutoTrader", "abc123", "cheap hotel")
    assert k1 != k2


def test_cache_key_different_fingerprint():
    k1 = make_cache_key("Expedia", "abc123", "cheap hotel")
    k2 = make_cache_key("Expedia", "def456", "cheap hotel")
    assert k1 != k2


def test_cache_key_different_query():
    k1 = make_cache_key("Expedia", "abc123", "cheap hotel")
    k2 = make_cache_key("Expedia", "abc123", "luxury resort")
    assert k1 != k2


def test_cache_key_normalizes_query():
    k1 = make_cache_key("Expedia", "abc123", "Cheap Hotel")
    k2 = make_cache_key("Expedia", "abc123", "  cheap  hotel  ")
    assert k1 == k2


# === LRU cache: hit / miss ===


def _entry(vertical="hotels", filters=None):
    return CacheEntry(
        vertical=vertical,
        schema_path=f"schemas/{vertical}.json",
        data_path=f"data/{vertical}.json",
        filters=filters or {"city": "Toronto"},
        sort="price_asc",
        limit=25,
        unmapped=[],
        notes=[],
    )


def test_miss_returns_none():
    cache = LRUFilterCache(max_size=10)
    assert cache.get("nonexistent") is None


def test_put_then_get():
    cache = LRUFilterCache(max_size=10)
    entry = _entry()
    cache.put("key1", entry)
    assert cache.get("key1") == entry


def test_hit_returns_same_data():
    cache = LRUFilterCache(max_size=10)
    entry = _entry(filters={"city": "Vancouver", "star_rating": {"op": "gte", "value": 4}})
    cache.put("k", entry)
    got = cache.get("k")
    assert got.filters == {"city": "Vancouver", "star_rating": {"op": "gte", "value": 4}}
    assert got.vertical == "hotels"
    assert got.sort == "price_asc"


# === LRU eviction ===


def test_eviction_removes_oldest():
    cache = LRUFilterCache(max_size=2)
    cache.put("a", _entry(filters={"city": "A"}))
    cache.put("b", _entry(filters={"city": "B"}))
    cache.put("c", _entry(filters={"city": "C"}))  # evicts "a"
    assert cache.get("a") is None
    assert cache.get("b") is not None
    assert cache.get("c") is not None


def test_access_refreshes_lru_order():
    cache = LRUFilterCache(max_size=2)
    cache.put("a", _entry(filters={"city": "A"}))
    cache.put("b", _entry(filters={"city": "B"}))
    cache.get("a")  # refresh "a" — now "b" is oldest
    cache.put("c", _entry(filters={"city": "C"}))  # evicts "b"
    assert cache.get("a") is not None
    assert cache.get("b") is None
    assert cache.get("c") is not None


# === Stats ===


def test_stats_initial():
    cache = LRUFilterCache(max_size=10)
    s = cache.stats()
    assert s["hits"] == 0
    assert s["misses"] == 0
    assert s["hit_rate"] == 0.0
    assert s["size"] == 0


def test_stats_after_hits_and_misses():
    cache = LRUFilterCache(max_size=10)
    cache.put("k", _entry())
    cache.get("k")       # hit
    cache.get("k")       # hit
    cache.get("miss1")   # miss
    s = cache.stats()
    assert s["hits"] == 2
    assert s["misses"] == 1
    assert s["hit_rate"] == pytest.approx(2 / 3, abs=0.001)
    assert s["size"] == 1


# === Per-customer isolation ===


def test_per_customer_keys_isolate():
    """Same query, same schema fingerprint, different customer → different key → miss."""
    cache = LRUFilterCache(max_size=10)
    k1 = make_cache_key("Expedia", "fp1", "cheap hotel")
    k2 = make_cache_key("Booking", "fp1", "cheap hotel")
    cache.put(k1, _entry(filters={"city": "Toronto"}))
    assert cache.get(k2) is None  # different customer → miss


# === Schema fingerprint rotation ===


def test_fingerprint_changes_with_schema(tmp_path):
    """When schema file content changes, fingerprint changes, old cache entries miss."""
    schema_file = tmp_path / "hotels.json"
    schema_file.write_text('{"vertical":"hotels","fields":{}}')

    verticals_v1 = {"hotels": {"schema": str(schema_file), "data": "data/hotels.json"}}
    fp1 = schema_fingerprint(verticals_v1)

    # "Deploy" a schema change
    schema_file.write_text('{"vertical":"hotels","fields":{"city":{"type":"string"}}}')
    fp2 = schema_fingerprint(verticals_v1)

    assert fp1 != fp2

    # Old key misses with new fingerprint
    cache = LRUFilterCache(max_size=10)
    old_key = make_cache_key("Expedia", fp1, "cheap hotel")
    cache.put(old_key, _entry())
    new_key = make_cache_key("Expedia", fp2, "cheap hotel")
    assert cache.get(new_key) is None  # rotated — old entry orphaned


def test_fingerprint_deterministic(tmp_path):
    """Same file content → same fingerprint."""
    schema_file = tmp_path / "cars.json"
    schema_file.write_text('{"vertical":"cars","fields":{}}')
    verticals = {"cars": {"schema": str(schema_file), "data": "data/cars.json"}}
    assert schema_fingerprint(verticals) == schema_fingerprint(verticals)


# === Update-in-place ===


def test_put_overwrites_existing():
    cache = LRUFilterCache(max_size=10)
    cache.put("k", _entry(filters={"city": "Toronto"}))
    cache.put("k", _entry(filters={"city": "Vancouver"}))
    assert cache.get("k").filters == {"city": "Vancouver"}
    assert cache.stats()["size"] == 1
