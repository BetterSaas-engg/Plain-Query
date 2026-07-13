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
from pathlib import Path

# Allow running from repo root without install
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.plainquery.engine import run
from src.plainquery.router import load_customer, route


def main():
    parser = argparse.ArgumentParser(description="PlainQuery: NL -> structured search")
    parser.add_argument("query", help="Natural language search query")
    parser.add_argument("--schema", default=None, help="Schema file path")
    parser.add_argument("--data", default=None, help="Data file path")
    parser.add_argument("--customer", default=None, help="Customer config file path")
    parser.add_argument("--vertical", default=None, help="Explicit vertical (context-provided)")
    args = parser.parse_args()

    if args.customer:
        # Multi-vertical: route first
        customer = load_customer(args.customer)
        route_result = route(args.query, customer, vertical=args.vertical)

        if route_result.vertical is None:
            # Ambiguous — ask the user
            print("=" * 60)
            print(f"ROUTING: Could not determine vertical for {customer.name}")
            print(f"Candidates: {route_result.candidates}")
            print("Re-run with --vertical <name> to specify.")
            print("=" * 60)
            return

        schema_path = route_result.schema_path
        data_path = route_result.data_path

        # Transparency: show routing decision
        print(f"VERTICAL: {route_result.vertical} ({route_result.mode})")
    else:
        # Single-vertical mode (backward compatible)
        schema_path = args.schema or "schemas/cars.json"
        data_path = args.data or "data/cars.json"

    result = run(args.query, schema_path, data_path)

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

    # 3. Results
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
