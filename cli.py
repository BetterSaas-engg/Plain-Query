"""PlainQuery CLI — natural language search.

Usage:
  Single-vertical:
    python cli.py "red Honda Civic under 25k, low mileage"
    python cli.py "cheap hostel in Toronto" --schema schemas/hotels.json --data data/hotels.json

  Multi-vertical (customer mode):
    python cli.py "flight to Lisbon" --customer customers/expedia.json
    python cli.py "flight to Lisbon" --customer customers/expedia.json --vertical flights
"""

import argparse
import json
import sys
import time
from pathlib import Path

# Allow running from repo root without install
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.plainquery.engine import run, run_from_filter
from src.plainquery.router import load_customer, route
from src.plainquery.schema import load_schema
from src.plainquery.translator import translate
from src.plainquery.validator import validate, ValidatedFilter
from src.plainquery.cache import (
    LRUFilterCache, CacheEntry, make_cache_key, schema_fingerprint,
)

# Module-level cache — persists across calls in the same process.
# CLI is typically one-shot, but the cache is here for benchmarking
# (run the same query twice to see hit vs miss latency).
_cache = LRUFilterCache(max_size=1024)


def main():
    parser = argparse.ArgumentParser(description="PlainQuery: NL -> structured search")
    parser.add_argument("query", help="Natural language search query")
    parser.add_argument("--schema", default=None, help="Schema file path")
    parser.add_argument("--data", default=None, help="Data file path")
    parser.add_argument("--customer", default=None, help="Customer config file path")
    parser.add_argument("--vertical", default=None, help="Explicit vertical (context-provided)")
    parser.add_argument("--bench", action="store_true",
                        help="Run the query twice to show cache miss then hit latency")
    args = parser.parse_args()

    if args.bench:
        _run_bench(args)
    else:
        _run_once(args)


def _run_once(args):
    total_start = time.perf_counter()

    if args.customer:
        customer = load_customer(args.customer)
        fingerprint = schema_fingerprint(customer.verticals)

        # Build cache key
        key_query = args.query
        if args.vertical:
            key_query = f"{args.vertical}\x00{args.query}"
        cache_key = make_cache_key(customer.name, fingerprint, key_query)

        # Cache check
        cached = _cache.get(cache_key)
        if cached is not None:
            # Cache hit — zero LLM calls
            total_elapsed = (time.perf_counter() - total_start) * 1000
            schema = load_schema(cached.schema_path)
            data = json.loads(Path(cached.data_path).read_text(encoding="utf-8"))
            vf = ValidatedFilter(
                filters=cached.filters,
                sort=cached.sort,
                limit=cached.limit,
                unmapped=list(cached.unmapped),
                notes=list(cached.notes),
            )
            result = run_from_filter(vf, schema, data)
            total_elapsed = (time.perf_counter() - total_start) * 1000
            print(f"VERTICAL: {cached.vertical} (cached)")
            print(f"CACHE: HIT ({total_elapsed:.1f}ms total, 0 LLM calls)")
            _print_result(result)
            return

        # Cache miss — full pipeline
        route_result = route(args.query, customer, vertical=args.vertical)

        if route_result.vertical is None:
            print("=" * 60)
            print(f"ROUTING: Could not determine vertical for {customer.name}")
            print(f"Candidates: {route_result.candidates}")
            print("Re-run with --vertical <name> to specify.")
            print("=" * 60)
            return

        schema_path = route_result.schema_path
        data_path = route_result.data_path
        print(f"VERTICAL: {route_result.vertical} ({route_result.mode})")

        # Translate + validate
        schema = load_schema(schema_path)
        data = json.loads(Path(data_path).read_text(encoding="utf-8"))
        candidate = translate(args.query, schema)
        vf = validate(candidate, schema)

        # Cache on successful translation
        if not candidate.error:
            _cache.put(cache_key, CacheEntry(
                vertical=route_result.vertical,
                schema_path=schema_path,
                data_path=data_path,
                filters=vf.filters,
                sort=vf.sort,
                limit=vf.limit,
                unmapped=list(vf.unmapped),
                notes=list(vf.notes),
            ))

        result = run_from_filter(vf, schema, data)
        total_elapsed = (time.perf_counter() - total_start) * 1000
        print(f"CACHE: MISS ({total_elapsed:.1f}ms total)")
    else:
        # Single-vertical mode (backward compatible)
        schema_path = args.schema or "schemas/cars.json"
        data_path = args.data or "data/cars.json"
        result = run(args.query, schema_path, data_path)
        total_elapsed = (time.perf_counter() - total_start) * 1000
        print(f"TIME: {total_elapsed:.1f}ms")

    _print_result(result)


def _run_bench(args):
    """Run the same query twice: first is a cache miss, second is a cache hit."""
    if not args.customer:
        print("--bench requires --customer (cache needs a customer config)")
        return

    customer = load_customer(args.customer)
    fingerprint = schema_fingerprint(customer.verticals)

    key_query = args.query
    if args.vertical:
        key_query = f"{args.vertical}\x00{args.query}"
    cache_key = make_cache_key(customer.name, fingerprint, key_query)

    # --- Run 1: cache miss ---
    print("=" * 60)
    print("RUN 1 — CACHE MISS (router + translator + validator + search)")
    print("=" * 60)
    t0 = time.perf_counter()

    route_result = route(args.query, customer, vertical=args.vertical)
    route_ms = (time.perf_counter() - t0) * 1000

    if route_result.vertical is None:
        print(f"ROUTING: Could not determine vertical for {customer.name}")
        print(f"Candidates: {route_result.candidates}")
        return

    schema = load_schema(route_result.schema_path)
    data = json.loads(Path(route_result.data_path).read_text(encoding="utf-8"))

    t1 = time.perf_counter()
    candidate = translate(args.query, schema)
    translate_ms = (time.perf_counter() - t1) * 1000

    t2 = time.perf_counter()
    vf = validate(candidate, schema)
    validate_ms = (time.perf_counter() - t2) * 1000

    # Cache the result
    if not candidate.error:
        _cache.put(cache_key, CacheEntry(
            vertical=route_result.vertical,
            schema_path=route_result.schema_path,
            data_path=route_result.data_path,
            filters=vf.filters,
            sort=vf.sort,
            limit=vf.limit,
            unmapped=list(vf.unmapped),
            notes=list(vf.notes),
        ))

    t3 = time.perf_counter()
    result = run_from_filter(vf, schema, data)
    search_ms = (time.perf_counter() - t3) * 1000

    miss_total = (time.perf_counter() - t0) * 1000

    print(f"  Route:     {route_ms:>8.1f}ms")
    print(f"  Translate: {translate_ms:>8.1f}ms")
    print(f"  Validate:  {validate_ms:>8.1f}ms")
    print(f"  Search:    {search_ms:>8.1f}ms")
    print(f"  TOTAL:     {miss_total:>8.1f}ms")
    print(f"  Results:   {result.total_matches} matches")
    _print_result(result)

    # --- Run 2: cache hit ---
    print()
    print("=" * 60)
    print("RUN 2 — CACHE HIT (search only, 0 LLM calls)")
    print("=" * 60)
    t0 = time.perf_counter()

    cached = _cache.get(cache_key)
    cache_lookup_ms = (time.perf_counter() - t0) * 1000

    schema = load_schema(cached.schema_path)
    data = json.loads(Path(cached.data_path).read_text(encoding="utf-8"))
    vf = ValidatedFilter(
        filters=cached.filters,
        sort=cached.sort,
        limit=cached.limit,
        unmapped=list(cached.unmapped),
        notes=list(cached.notes),
    )

    t1 = time.perf_counter()
    result = run_from_filter(vf, schema, data)
    search_ms = (time.perf_counter() - t1) * 1000

    hit_total = (time.perf_counter() - t0) * 1000

    print(f"  Cache:     {cache_lookup_ms:>8.3f}ms")
    print(f"  Search:    {search_ms:>8.1f}ms")
    print(f"  TOTAL:     {hit_total:>8.1f}ms")
    print(f"  Results:   {result.total_matches} matches")

    # --- Comparison ---
    print()
    print("=" * 60)
    print("COMPARISON")
    print("=" * 60)
    print(f"  Miss: {miss_total:>8.1f}ms")
    print(f"  Hit:  {hit_total:>8.1f}ms")
    if hit_total > 0:
        print(f"  Speedup: {miss_total / hit_total:.0f}x")
    print(f"  Cache stats: {_cache.stats()}")


def _print_result(result):
    # 1. Filter used
    print("=" * 60)
    print("FILTER USED:")
    print(json.dumps(result.validated_filter, indent=2))
    print(f"Sort: {result.sort}  |  Limit: {result.limit}")
    print("=" * 60)

    # 2. Notes and unmapped
    if result.notes:
        print("\nNOTES:")
        for note in result.notes:
            print(f"  - {note}")

    if result.unmapped:
        print(f"\nUNMAPPED TERMS: {result.unmapped}")

    # 3. Needs input — ask instead of searching
    if result.needs_input:
        if result.needs_input_kind == "not_understood":
            print("\nNOT UNDERSTOOD:")
            print("  Could not map any part of the search to our filters.")
            print(f"  Unmapped terms: {result.unmapped}")
            print(f"  Available fields: {result.available_fields}")
        elif result.needs_input_kind == "missing_essential":
            print("\nNEEDS INPUT:")
            print(f"  Missing essential fields: {result.missing_essential}")
        print("-" * 60)
        return

    # 4. Results
    print(f"\nRESULTS ({result.total_matches} matches):")
    print("-" * 60)
    if result.rows:
        # Use schema display list, or fall back to all keys
        columns = result.display if result.display else list(result.rows[0].keys())
        for row in result.rows:
            parts = []
            for col in columns:
                val = row.get(col, "")
                parts.append(str(val))
            print(f"  {' | '.join(parts)}")
    else:
        print("  (no matches)")
        if result.suggestions:
            print()
            print("Try loosening your search:")
            for s in result.suggestions:
                print(f"  - {s.change} -> {s.match_count} matches")
    print("-" * 60)


if __name__ == "__main__":
    main()
