"""PlainQuery CLI — natural language search.

Usage: python cli.py "red Honda Civic under 25k, low mileage"
       python cli.py "cheap hostel in Toronto" --schema schemas/hotels.json --data data/hotels.json
"""

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root without install
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.plainquery.engine import run


def main():
    parser = argparse.ArgumentParser(description="PlainQuery: NL → structured search")
    parser.add_argument("query", help="Natural language search query")
    parser.add_argument("--schema", default="schemas/cars.json", help="Schema file path")
    parser.add_argument("--data", default="data/cars.json", help="Data file path")
    args = parser.parse_args()

    result = run(args.query, args.schema, args.data)

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
    print("-" * 60)


if __name__ == "__main__":
    main()
