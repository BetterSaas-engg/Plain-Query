"""Zero-results loosening: suggest constraint relaxations backed by real data.

Explain and suggest, never auto-execute. Every suggestion is a fact computed
from the dataset — which constraint to drop or widen, and exactly how many
rows that would yield. No LLM calls, no guesses.
"""

from dataclasses import dataclass

from .backend import search
from .schema import Schema
from .validator import ValidatedFilter


@dataclass
class Suggestion:
    """One actionable relaxation the user could try."""
    field: str
    change: str          # human-readable description of the relaxation
    match_count: int     # real count from the dataset


def suggest(data: list[dict], vf: ValidatedFilter, schema: Schema) -> list[Suggestion]:
    """Given a zero-result search, suggest constraint relaxations that yield matches.

    For each constraint, tries:
    1. Dropping it entirely.
    2. For numeric lte/gte, widening to the nearest threshold that produces matches.

    Returns suggestions sorted by fewest constraints removed, then highest match count.
    """
    if not vf.filters:
        return []

    suggestions: list[Suggestion] = []

    for field_name, constraint in vf.filters.items():
        # Try dropping this one constraint
        drop_count = _count_without(data, vf, schema, field_name)
        if drop_count > 0:
            label = _describe_constraint(field_name, constraint)
            suggestions.append(Suggestion(
                field=field_name,
                change=f"Drop {label}",
                match_count=drop_count,
            ))

        # For numeric constraints, try widening
        if isinstance(constraint, dict):
            widen = _try_widen(data, vf, schema, field_name, constraint)
            if widen:
                suggestions.append(widen)

    # Sort: highest match count first (most helpful suggestion on top)
    suggestions.sort(key=lambda s: -s.match_count)
    return suggestions


def _count_without(
    data: list[dict], vf: ValidatedFilter, schema: Schema, skip_field: str
) -> int:
    """Count matches with one constraint removed."""
    reduced_filters = {k: v for k, v in vf.filters.items() if k != skip_field}
    reduced_vf = ValidatedFilter(
        filters=reduced_filters,
        sort=vf.sort,
        limit=len(data),  # count all, don't cap
        unmapped=vf.unmapped,
        notes=vf.notes,
    )
    return len(search(data, reduced_vf, schema))


def _try_widen(
    data: list[dict], vf: ValidatedFilter, schema: Schema,
    field_name: str, constraint: dict,
) -> Suggestion | None:
    """For numeric lte/gte, find the smallest widening that yields matches."""
    op = constraint.get("op")
    value = constraint.get("value")
    if op not in ("lte", "gte") or not isinstance(value, int):
        return None

    # Collect all values for this field from rows that match ALL OTHER constraints
    other_filters = {k: v for k, v in vf.filters.items() if k != field_name}
    other_vf = ValidatedFilter(
        filters=other_filters,
        sort=vf.sort,
        limit=len(data),
        unmapped=vf.unmapped,
        notes=vf.notes,
    )
    candidates = search(data, other_vf, schema)
    if not candidates:
        return None

    # Extract numeric values for this field from candidate rows
    field_values = []
    for row in candidates:
        raw = row.get(field_name)
        if raw is None:
            continue
        try:
            field_values.append(int(raw))
        except (ValueError, TypeError):
            continue

    if not field_values:
        return None

    if op == "lte":
        # Find the smallest value above the current threshold
        above = sorted(set(v for v in field_values if v > value))
        if not above:
            return None
        nearest = above[0]
        # Count matches at this threshold
        widened_constraint = {"op": "lte", "value": nearest}
        count = _count_with_replaced(data, vf, schema, field_name, widened_constraint)
        if count > 0:
            return Suggestion(
                field=field_name,
                change=f"Raise to {field_name} <= {nearest}",
                match_count=count,
            )

    elif op == "gte":
        # Find the largest value below the current threshold
        below = sorted(set(v for v in field_values if v < value), reverse=True)
        if not below:
            return None
        nearest = below[0]
        widened_constraint = {"op": "gte", "value": nearest}
        count = _count_with_replaced(data, vf, schema, field_name, widened_constraint)
        if count > 0:
            return Suggestion(
                field=field_name,
                change=f"Lower to {field_name} >= {nearest}",
                match_count=count,
            )

    return None


def _count_with_replaced(
    data: list[dict], vf: ValidatedFilter, schema: Schema,
    field_name: str, new_constraint: dict,
) -> int:
    """Count matches with one constraint replaced."""
    replaced_filters = {**vf.filters, field_name: new_constraint}
    replaced_vf = ValidatedFilter(
        filters=replaced_filters,
        sort=vf.sort,
        limit=len(data),
        unmapped=vf.unmapped,
        notes=vf.notes,
    )
    return len(search(data, replaced_vf, schema))


def _describe_constraint(field_name: str, constraint) -> str:
    """Human-readable label for a constraint."""
    if isinstance(constraint, dict):
        op = constraint.get("op", "")
        if op == "between":
            return f"'{field_name}' between {constraint.get('low')}–{constraint.get('high')}"
        val = constraint.get("value", "")
        op_labels = {"eq": "=", "lte": "<=", "gte": ">="}
        return f"'{field_name}' {op_labels.get(op, op)} {val}"
    return f"'{field_name}' = {constraint}"
