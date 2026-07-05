"""Load and validate a PlainQuery schema file. Fail-fast on any violation."""

import json
from dataclasses import dataclass, field
from pathlib import Path

VALID_TYPES = {"enum", "string", "int"}
VALID_OPERATORS = {"eq", "lte", "gte", "between"}


class SchemaError(Exception):
    """Raised when a schema file violates the contract."""


@dataclass(frozen=True)
class FieldDef:
    name: str
    type: str
    values: list[str] = field(default_factory=list)  # enum only
    min: int | None = None  # int only
    max: int | None = None  # int only
    unit: str | None = None  # int only
    operators: list[str] = field(default_factory=list)  # int only


@dataclass(frozen=True)
class Schema:
    vertical: str
    fields: dict[str, FieldDef]
    sort_options: list[str]
    default_sort: str
    default_limit: int


def _fail(field_name: str, problem: str) -> None:
    raise SchemaError(f"Field '{field_name}': {problem}")


def _validate_field(name: str, raw: dict) -> FieldDef:
    ftype = raw.get("type")
    if ftype not in VALID_TYPES:
        _fail(name, f"unknown type '{ftype}' (allowed: {sorted(VALID_TYPES)})")

    if ftype == "enum":
        values = raw.get("values")
        if not values or not isinstance(values, list):
            _fail(name, "enum field must have a non-empty 'values' list")
        return FieldDef(name=name, type="enum", values=values)

    if ftype == "string":
        return FieldDef(name=name, type="string")

    # int
    fmin = raw.get("min")
    fmax = raw.get("max")
    if fmin is None or not isinstance(fmin, int):
        _fail(name, "int field must have an integer 'min'")
    if fmax is None:
        # price/mileage have no max in schema — that's fine, skip the min<=max check
        pass
    else:
        if not isinstance(fmax, int):
            _fail(name, "'max' must be an integer")
        if fmin > fmax:
            _fail(name, f"min ({fmin}) > max ({fmax})")

    operators = raw.get("operators", [])
    if not isinstance(operators, list):
        _fail(name, "'operators' must be a list")
    bad = set(operators) - VALID_OPERATORS
    if bad:
        _fail(name, f"invalid operators {sorted(bad)} (allowed: {sorted(VALID_OPERATORS)})")

    return FieldDef(
        name=name, type="int",
        min=fmin, max=fmax,
        unit=raw.get("unit"),
        operators=operators,
    )


def load_schema(path: str | Path) -> Schema:
    """Load a schema JSON file, validate every contract, return a Schema."""
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))

    vertical = raw.get("vertical")
    if not vertical or not isinstance(vertical, str):
        raise SchemaError("Schema must have a non-empty 'vertical' string")

    raw_fields = raw.get("fields")
    if not raw_fields or not isinstance(raw_fields, dict):
        raise SchemaError("Schema must have a non-empty 'fields' object")

    fields = {name: _validate_field(name, defn) for name, defn in raw_fields.items()}

    sort_options = raw.get("sort", [])
    if not isinstance(sort_options, list):
        raise SchemaError("'sort' must be a list")

    defaults = raw.get("defaults", {})
    default_sort = defaults.get("sort", sort_options[0] if sort_options else None)
    if default_sort and default_sort not in sort_options:
        raise SchemaError(
            f"defaults.sort '{default_sort}' is not in sort options {sort_options}"
        )

    default_limit = defaults.get("limit", 25)
    if not isinstance(default_limit, int) or default_limit < 1:
        raise SchemaError(f"defaults.limit must be a positive integer, got {default_limit}")

    return Schema(
        vertical=vertical,
        fields=fields,
        sort_options=sort_options,
        default_sort=default_sort,
        default_limit=default_limit,
    )
