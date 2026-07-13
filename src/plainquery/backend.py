"""Deterministic in-memory search backend.

Takes a ValidatedFilter, schema, and dataset (list of dicts), applies filters,
sorts, limits, and returns matching rows. No LLM involvement — pure logic.
"""

from .schema import Schema
from .validator import ValidatedFilter


def search(data: list[dict], vf: ValidatedFilter, schema: Schema) -> list[dict]:
    """Filter, sort, and limit data according to the validated filter."""
    results = [row for row in data if _matches(row, vf.filters, schema)]
    results = _sort(results, vf.sort)
    return results[: vf.limit]


def _matches(row: dict, filters: dict, schema: Schema) -> bool:
    """Return True if row satisfies all filters."""
    for field_name, constraint in filters.items():
        value = row.get(field_name)
        if value is None:
            return False

        if isinstance(constraint, dict):
            # Numeric filter with operator
            if not _match_numeric(value, constraint):
                return False
        else:
            # Determine match strategy from schema field type
            field_def = schema.fields.get(field_name)
            if field_def and field_def.type == "string":
                # String field — case-insensitive substring (free text match)
                if str(constraint).lower() not in str(value).lower():
                    return False
            else:
                # Enum — exact case-insensitive equality
                if str(value).lower() != str(constraint).lower():
                    return False

    return True


def _match_numeric(value, constraint: dict) -> bool:
    """Evaluate a numeric constraint against a row value."""
    try:
        value = int(value)
    except (ValueError, TypeError):
        return False

    op = constraint["op"]
    if op == "eq":
        return value == constraint["value"]
    elif op == "lte":
        return value <= constraint["value"]
    elif op == "gte":
        return value >= constraint["value"]
    elif op == "between":
        return constraint["low"] <= value <= constraint["high"]
    return False


def _sort(rows: list[dict], sort: str) -> list[dict]:
    """Sort rows by the sort key. Format: '<field>_<direction>'."""
    if not sort or not rows:
        return rows

    # Parse sort string: "price_asc", "year_desc", "mileage_asc"
    parts = sort.rsplit("_", 1)
    if len(parts) != 2:
        return rows

    field_name, direction = parts
    reverse = direction == "desc"

    # Partition: rows with a valid numeric sort value vs rows without
    sortable = []
    unsortable = []
    for row in rows:
        val = row.get(field_name)
        if val is None:
            unsortable.append(row)
            continue
        try:
            numeric = int(val)
        except (ValueError, TypeError):
            unsortable.append(row)
            continue
        sortable.append((numeric, row))

    sortable.sort(key=lambda pair: pair[0], reverse=reverse)
    return [row for _, row in sortable] + unsortable
