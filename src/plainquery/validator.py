"""Deterministic validator: candidate filter → guaranteed schema-valid filter.

Fails closed. Drops individual bad fields (never rejects the whole query).
Never invents or alters user intent — out-of-range values are dropped, not clamped.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from .schema import FieldDef, Schema
from .translator import CandidateFilter

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class ValidatedFilter:
    """Guaranteed schema-valid. Everything here can go straight to the backend."""
    filters: dict = field(default_factory=dict)
    sort: str = ""
    limit: int = 25
    unmapped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def validate(candidate: CandidateFilter, schema: Schema) -> ValidatedFilter:
    """Validate a candidate filter against the schema. Returns only clean data."""
    result = ValidatedFilter(
        sort=schema.default_sort or "",
        limit=schema.default_limit,
        unmapped=list(candidate.unmapped),
    )

    # If translation itself failed, return empty filter with notes
    if candidate.error:
        result.notes.append(f"Translation error: {candidate.error}")
        return result

    # Validate sort
    if candidate.sort is not None:
        _validate_sort(candidate.sort, schema, result)

    # Validate each filter field
    for field_name, value in candidate.filters.items():
        _validate_field(field_name, value, schema, result)

    # Invariant: a term must never appear in both filters and unmapped.
    # If the LLM mapped a term to a filter, remove it from unmapped.
    if result.filters and result.unmapped:
        filter_values = set()
        for v in result.filters.values():
            if isinstance(v, str):
                filter_values.add(v.lower())
            elif isinstance(v, dict):
                for sub in (v.get("value"), v.get("low"), v.get("high")):
                    if isinstance(sub, str):
                        filter_values.add(sub.lower())
        result.unmapped = [
            term for term in result.unmapped
            if not any(fv in term.lower() or term.lower() in fv for fv in filter_values)
        ]

    return result


def _validate_sort(sort: str, schema: Schema, result: ValidatedFilter) -> None:
    """Validate sort option; fall back to default if invalid."""
    # Case-insensitive match
    sort_lower = sort.lower()
    for option in schema.sort_options:
        if option.lower() == sort_lower:
            result.sort = option
            return
    # Invalid sort
    result.notes.append(
        f"Sort '{sort}' is not available — using default '{schema.default_sort}'. "
        f"Valid options: {schema.sort_options}"
    )
    # result.sort already set to default_sort


def _validate_field(
    field_name: str, value, schema: Schema, result: ValidatedFilter
) -> None:
    """Validate a single filter field against the schema."""
    field_def = schema.fields.get(field_name)
    if field_def is None:
        result.notes.append(
            f"Field '{field_name}' is not in the schema — ignored."
        )
        return

    if field_def.type == "enum":
        _validate_enum(field_name, value, field_def, result)
    elif field_def.type == "string":
        _validate_string(field_name, value, result)
    elif field_def.type == "int":
        _validate_int(field_name, value, field_def, result)
    elif field_def.type == "date":
        _validate_date(field_name, value, field_def, result)


def _validate_enum(
    name: str, value, field_def: FieldDef, result: ValidatedFilter
) -> None:
    """Validate an enum value — case-insensitive, store canonical casing."""
    if not isinstance(value, str):
        result.notes.append(
            f"Field '{name}': expected a string value, got {type(value).__name__} — ignored."
        )
        return

    value_lower = value.lower()
    for canonical in field_def.values:
        if canonical.lower() == value_lower:
            result.filters[name] = canonical
            return

    result.notes.append(
        f"Field '{name}': value '{value}' is not valid. "
        f"Options: {field_def.values} — ignored."
    )


def _validate_string(name: str, value, result: ValidatedFilter) -> None:
    """Validate a string field."""
    if isinstance(value, str) and value.strip():
        result.filters[name] = value.strip()
    else:
        result.notes.append(
            f"Field '{name}': expected a non-empty string — ignored."
        )


def _validate_int(
    name: str, value, field_def: FieldDef, result: ValidatedFilter
) -> None:
    """Validate an int filter (operator + value(s))."""
    bare_coercion = False
    if not isinstance(value, dict):
        # Try coercing a bare value to {"op": "eq", "value": X}
        coerced = _try_coerce_int(value)
        if coerced is None:
            result.notes.append(
                f"Field '{name}': expected an object like "
                f'{{\"op\": \"...\", \"value\": ...}}, got {type(value).__name__} — ignored.'
            )
            return
        value = {"op": "eq", "value": coerced}
        bare_coercion = True

    op = value.get("op")
    if not isinstance(op, str):
        result.notes.append(
            f"Field '{name}': missing or invalid 'op' — ignored."
        )
        return

    # Check operator is allowed for this field
    if field_def.operators and op not in field_def.operators:
        if bare_coercion:
            result.notes.append(
                f"Field '{name}': bare value given without a comparison — "
                f"this field supports: {field_def.operators} — ignored."
            )
        else:
            result.notes.append(
                f"Field '{name}': operator '{op}' is not allowed. "
                f"Valid operators: {field_def.operators} — ignored."
            )
        return

    # Validate value(s) depending on operator
    if op == "between":
        low = _try_coerce_int(value.get("low"))
        high = _try_coerce_int(value.get("high"))
        if low is None or high is None:
            result.notes.append(
                f"Field '{name}': 'between' requires numeric 'low' and 'high' — ignored."
            )
            return
        # Invariant: low <= high
        if low > high:
            result.notes.append(
                f"Field '{name}': between range {low}–{high} is inverted — "
                f"'low' must be less than or equal to 'high' — ignored."
            )
            return
        # Range check both
        low_err = _check_range(low, field_def)
        high_err = _check_range(high, field_def)
        if low_err or high_err:
            msg = low_err or high_err
            result.notes.append(
                f"Field '{name}': {msg} — ignored."
            )
            return
        result.filters[name] = {"op": "between", "low": low, "high": high}
    else:
        raw_val = value.get("value")
        coerced_val = _try_coerce_int(raw_val)
        if coerced_val is None:
            result.notes.append(
                f"Field '{name}': value '{raw_val}' is not a valid number — ignored."
            )
            return
        range_err = _check_range(coerced_val, field_def)
        if range_err:
            result.notes.append(f"Field '{name}': {range_err} — ignored.")
            return
        result.filters[name] = {"op": op, "value": coerced_val}


def _try_coerce_int(value) -> int | None:
    """Try to coerce a value to int. Handles numeric strings like '25000'."""
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == int(value):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _check_date_not_past(name: str, date_str: str, result: ValidatedFilter) -> bool:
    """Return True if date is today or future. Adds note and returns False if past."""
    today = date.today().isoformat()
    if date_str < today:
        result.notes.append(
            f"Field '{name}': date {date_str} is in the past "
            f"(today is {today}) — ignored."
        )
        return False
    return True


def _validate_date(
    name: str, value, field_def: FieldDef, result: ValidatedFilter
) -> None:
    """Validate a date filter (ISO YYYY-MM-DD strings with operators)."""
    if isinstance(value, str):
        # Bare date string — treat as eq
        if not _ISO_DATE_RE.match(value):
            result.notes.append(
                f"Field '{name}': '{value}' is not a valid ISO date (YYYY-MM-DD) — ignored."
            )
            return
        if not _check_date_not_past(name, value, result):
            return
        if field_def.operators and "eq" not in field_def.operators:
            result.notes.append(
                f"Field '{name}': bare date given without a comparison — "
                f"this field supports: {field_def.operators} — ignored."
            )
            return
        result.filters[name] = {"op": "eq", "value": value}
        return

    if not isinstance(value, dict):
        result.notes.append(
            f"Field '{name}': expected a date string or operator object — ignored."
        )
        return

    op = value.get("op")
    if not isinstance(op, str):
        result.notes.append(f"Field '{name}': missing or invalid 'op' — ignored.")
        return

    if field_def.operators and op not in field_def.operators:
        result.notes.append(
            f"Field '{name}': operator '{op}' is not allowed. "
            f"Valid operators: {field_def.operators} — ignored."
        )
        return

    if op == "between":
        low = value.get("low")
        high = value.get("high")
        if not isinstance(low, str) or not _ISO_DATE_RE.match(low):
            result.notes.append(
                f"Field '{name}': 'between' requires ISO date 'low' — ignored."
            )
            return
        if not isinstance(high, str) or not _ISO_DATE_RE.match(high):
            result.notes.append(
                f"Field '{name}': 'between' requires ISO date 'high' — ignored."
            )
            return
        if low > high:
            result.notes.append(
                f"Field '{name}': date range {low} to {high} is inverted — ignored."
            )
            return
        if not _check_date_not_past(name, low, result):
            return
        result.filters[name] = {"op": "between", "low": low, "high": high}
    else:
        val = value.get("value")
        if not isinstance(val, str) or not _ISO_DATE_RE.match(val):
            result.notes.append(
                f"Field '{name}': '{val}' is not a valid ISO date (YYYY-MM-DD) — ignored."
            )
            return
        if not _check_date_not_past(name, val, result):
            return
        result.filters[name] = {"op": op, "value": val}


def _check_range(value: int, field_def: FieldDef) -> str | None:
    """Check if value is within schema range. Returns error message or None."""
    below = field_def.min is not None and value < field_def.min
    above = field_def.max is not None and value > field_def.max
    if not below and not above:
        return None
    parts = []
    if field_def.min is not None:
        parts.append(str(field_def.min))
    if field_def.max is not None:
        parts.append(str(field_def.max))
    range_str = "–".join(parts)
    return f"value {value} is outside the available range {range_str}"
