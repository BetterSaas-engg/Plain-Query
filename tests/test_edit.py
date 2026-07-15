"""Deterministic tests for the re-validation entry point (run_from_edit).

The edited filter is untrusted input — treated identically to an LLM candidate.
Every test here proves that the same validator that catches LLM hallucinations
catches user edit errors.
"""

import json

import pytest

from src.plainquery.schema import load_schema
from src.plainquery.engine import run_from_edit


@pytest.fixture
def schema():
    return load_schema("schemas/cars.json")


@pytest.fixture
def data():
    return json.loads(open("data/cars.json", encoding="utf-8").read())


# === Invalid edits are caught ===


def test_invalid_enum_value_dropped(schema, data):
    """User changes make from 'Honda' to 'Porsche' (not in schema enum)."""
    result = run_from_edit(
        {"make": "Porsche", "color": "red"},
        schema, data,
    )
    assert "make" not in result.validated_filter
    assert result.validated_filter["color"] == "red"
    assert any("Porsche" in n and "not valid" in n for n in result.notes)


def test_non_numeric_price_dropped(schema, data):
    """User edits price to a non-numeric value: 'banana'."""
    result = run_from_edit(
        {"make": "Honda", "price": {"op": "lte", "value": "banana"}},
        schema, data,
    )
    assert "price" not in result.validated_filter
    assert result.validated_filter["make"] == "Honda"
    assert any("banana" in n for n in result.notes)


def test_out_of_range_value_dropped(schema, data):
    """User sets year to 3000 — out of schema range."""
    result = run_from_edit(
        {"year": {"op": "eq", "value": 3000}},
        schema, data,
    )
    assert "year" not in result.validated_filter
    assert any("3000" in n and "outside" in n for n in result.notes)


def test_off_schema_field_dropped(schema, data):
    """User adds a field that doesn't exist in the schema."""
    result = run_from_edit(
        {"make": "Honda", "turbo": "yes"},
        schema, data,
    )
    assert "turbo" not in result.validated_filter
    assert result.validated_filter["make"] == "Honda"
    assert any("turbo" in n and "not in the schema" in n for n in result.notes)


def test_invalid_operator_dropped(schema, data):
    """User uses an operator not allowed for the field."""
    result = run_from_edit(
        {"price": {"op": "eq", "value": 20000}},
        schema, data,
    )
    # price only allows lte, gte, between — not eq
    assert "price" not in result.validated_filter
    assert any("'eq' is not allowed" in n for n in result.notes)


def test_inverted_between_dropped(schema, data):
    """User sets low > high in a between range."""
    result = run_from_edit(
        {"year": {"op": "between", "low": 2024, "high": 2018}},
        schema, data,
    )
    assert "year" not in result.validated_filter
    assert any("inverted" in n for n in result.notes)


# === Valid edits pass through ===


def test_valid_edit_passes(schema, data):
    """A clean edit produces the expected filter and results."""
    result = run_from_edit(
        {"make": "Honda", "color": "red", "price": {"op": "lte", "value": 25000}},
        schema, data, sort="mileage_asc",
    )
    assert result.validated_filter == {
        "make": "Honda",
        "color": "red",
        "price": {"op": "lte", "value": 25000},
    }
    assert result.sort == "mileage_asc"
    assert result.notes == []
    assert result.total_matches > 0


def test_valid_edit_with_limit(schema, data):
    """Caller can override the limit."""
    result = run_from_edit(
        {"make": "Honda"},
        schema, data, limit=5,
    )
    assert result.limit == 5
    assert len(result.rows) <= 5


def test_empty_edit_returns_all(schema, data):
    """An empty filter returns unfiltered results (up to limit)."""
    result = run_from_edit({}, schema, data)
    assert result.total_matches == result.limit  # capped at default 25


# === Partial failure: good fields survive, bad fields dropped ===


def test_partial_edit_good_survives(schema, data):
    """One good field + one bad field: good survives, bad is dropped with note."""
    result = run_from_edit(
        {"make": "Toyota", "mileage": {"op": "lte", "value": "not_a_number"}},
        schema, data,
    )
    assert result.validated_filter["make"] == "Toyota"
    assert "mileage" not in result.validated_filter
    assert any("not_a_number" in n for n in result.notes)
    assert result.total_matches > 0


# === Review is populated on edit results ===


def test_edit_result_has_review(schema, data):
    """The edit result includes a structured review for the next round."""
    result = run_from_edit(
        {"make": "Honda", "price": {"op": "lte", "value": 25000}},
        schema, data,
    )
    assert result.review is not None
    assert result.review.status == "ready"
    field_names = [f.name for f in result.review.fields]
    assert "make" in field_names
    assert "price" in field_names
    # Review includes schema context for re-editing
    make_field = next(f for f in result.review.fields if f.name == "make")
    assert "Honda" in make_field.enum_values


# === Same validator, same behavior as LLM path ===


def test_edit_matches_validator_behavior(schema, data):
    """Prove the edit path uses the same validator as the LLM path:
    identical input → identical notes."""
    from src.plainquery.translator import CandidateFilter
    from src.plainquery.validator import validate

    bad_filter = {"make": "Porsche", "year": {"op": "eq", "value": 3000}}

    # LLM path
    candidate = CandidateFilter(filters=dict(bad_filter), unmapped=[])
    vf = validate(candidate, schema)

    # Edit path
    result = run_from_edit(bad_filter, schema, data)

    # Same validator → same notes
    assert result.notes == vf.notes
    assert result.validated_filter == vf.filters
