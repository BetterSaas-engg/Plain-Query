"""PlainQuery CLI — natural language car search.

Usage: python cli.py "red Honda Civic under 25k, low mileage"
"""

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root without install
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.plainquery.engine import run


def main():
    parser = argparse.ArgumentParser(description="PlainQuery: NL → structured car search")
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
        for row in result.rows:
            print(
                f"  {row['title']:<35} "
                f"${row['price']:>7,}  "
                f"{row['mileage']:>7,} km  "
                f"{row['color']}"
            )
    else:
        print("  (no matches)")
    print("-" * 60)


if __name__ == "__main__":
    main()
