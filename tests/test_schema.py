"""Tests for schema loader — one test per contract violation + happy path."""

import json
import pytest
from pathlib import Path
from src.plainquery.schema import load_schema, SchemaError


def _write_schema(tmp_path: Path, data: dict) -> Path:
    """Write a schema dict to a temp JSON file and return the path."""
    p = tmp_path / "schema.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


MINIMAL_VALID = {
    "vertical": "test",
    "fields": {
        "name": {"type": "string"},
    },
    "sort": ["name_asc"],
    "defaults": {"sort": "name_asc", "limit": 10},
}


def _with_field(name: str, defn: dict) -> dict:
    """Return a minimal valid schema with one field replaced/added."""
    s = {**MINIMAL_VALID, "fields": {name: defn}}
    return s


# --- Contract violations ---


def test_unknown_field_type(tmp_path):
    p = _write_schema(tmp_path, _with_field("x", {"type": "float"}))
    with pytest.raises(SchemaError, match="Field 'x'.*unknown type 'float'"):
        load_schema(p)


def test_enum_empty_values(tmp_path):
    p = _write_schema(tmp_path, _with_field("x", {"type": "enum", "values": []}))
    with pytest.raises(SchemaError, match="Field 'x'.*non-empty 'values'"):
        load_schema(p)


def test_enum_missing_values(tmp_path):
    p = _write_schema(tmp_path, _with_field("x", {"type": "enum"}))
    with pytest.raises(SchemaError, match="Field 'x'.*non-empty 'values'"):
        load_schema(p)


def test_int_missing_min(tmp_path):
    p = _write_schema(tmp_path, _with_field("x", {"type": "int", "max": 100}))
    with pytest.raises(SchemaError, match="Field 'x'.*integer 'min'"):
        load_schema(p)


def test_int_max_less_than_min(tmp_path):
    p = _write_schema(tmp_path, _with_field("x", {"type": "int", "min": 50, "max": 10}))
    with pytest.raises(SchemaError, match="Field 'x'.*min.*50.*max.*10"):
        load_schema(p)


def test_invalid_operator(tmp_path):
    p = _write_schema(tmp_path, _with_field("x", {
        "type": "int", "min": 0, "max": 100,
        "operators": ["lte", "fuzzy"],
    }))
    with pytest.raises(SchemaError, match="Field 'x'.*invalid operators.*fuzzy"):
        load_schema(p)


def test_default_sort_not_in_sort_list(tmp_path):
    schema = {
        **MINIMAL_VALID,
        "sort": ["name_asc"],
        "defaults": {"sort": "price_desc", "limit": 10},
    }
    p = _write_schema(tmp_path, schema)
    with pytest.raises(SchemaError, match="defaults.sort 'price_desc'.*not in sort"):
        load_schema(p)


def test_default_limit_zero(tmp_path):
    schema = {**MINIMAL_VALID, "defaults": {"sort": "name_asc", "limit": 0}}
    p = _write_schema(tmp_path, schema)
    with pytest.raises(SchemaError, match="defaults.limit.*positive integer"):
        load_schema(p)


def test_default_limit_negative(tmp_path):
    schema = {**MINIMAL_VALID, "defaults": {"sort": "name_asc", "limit": -5}}
    p = _write_schema(tmp_path, schema)
    with pytest.raises(SchemaError, match="defaults.limit.*positive integer"):
        load_schema(p)


# --- Happy path ---


def test_cars_schema_loads():
    s = load_schema("schemas/cars.json")
    assert s.vertical == "cars"
    assert set(s.fields.keys()) == {
        "make", "model", "year", "price", "mileage",
        "body_type", "fuel", "color",
    }
    assert s.fields["make"].type == "enum"
    assert "Honda" in s.fields["make"].values
    assert s.fields["model"].type == "string"
    assert s.fields["year"].type == "int"
    assert s.fields["year"].min == 2005
    assert s.fields["year"].max == 2026
    assert s.fields["price"].operators == ["lte", "gte", "between"]
    assert s.default_sort == "price_asc"
    assert s.default_limit == 25
