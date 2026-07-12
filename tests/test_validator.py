"""Deterministic tests for the validator against the cars schema.

All candidates are built directly as CandidateFilter objects — no LLM calls.
"""

import pytest

from src.plainquery.schema import load_schema
from src.plainquery.translator import CandidateFilter
from src.plainquery.validator import validate


@pytest.fixture
def schema():
    return load_schema("schemas/cars.json")


# === DROPS ===


def test_drop_off_schema_field(schema):
    candidate = CandidateFilter(filters={"sunroof": True, "make": "Honda"})
    result = validate(candidate, schema)
    assert "sunroof" not in result.filters
    assert result.filters["make"] == "Honda"
    assert any("sunroof" in n and "not in the schema" in n for n in result.notes)


def test_drop_bad_enum_value_lists_options(schema):
    candidate = CandidateFilter(filters={"color": "maroon", "make": "Toyota"})
    result = validate(candidate, schema)
    assert "color" not in result.filters
    assert result.filters["make"] == "Toyota"
    note = next(n for n in result.notes if "maroon" in n)
    assert "Options:" in note
    assert "red" in note  # at least one valid option listed


def test_drop_out_of_range_high_not_clamped(schema):
    candidate = CandidateFilter(filters={"year": {"op": "eq", "value": 3000}})
    result = validate(candidate, schema)
    assert "year" not in result.filters  # NOT clamped to 2026
    assert any("3000" in n and "outside" in n for n in result.notes)


def test_drop_out_of_range_low_not_clamped(schema):
    candidate = CandidateFilter(filters={"year": {"op": "eq", "value": 1990}})
    result = validate(candidate, schema)
    assert "year" not in result.filters  # NOT clamped to 2005
    assert any("1990" in n and "outside" in n for n in result.notes)


def test_drop_bad_operator(schema):
    """price doesn't allow 'eq'; only lte, gte, between."""
    candidate = CandidateFilter(filters={"price": {"op": "eq", "value": 20000}})
    result = validate(candidate, schema)
    assert "price" not in result.filters
    assert any("'eq'" in n and "not allowed" in n for n in result.notes)


def test_drop_inverted_between(schema):
    candidate = CandidateFilter(
        filters={"year": {"op": "between", "low": 2020, "high": 2010}}
    )
    result = validate(candidate, schema)
    assert "year" not in result.filters
    assert any("inverted" in n for n in result.notes)


def test_drop_uncoercible_value(schema):
    candidate = CandidateFilter(filters={"price": {"op": "lte", "value": "cheap"}})
    result = validate(candidate, schema)
    assert "price" not in result.filters
    assert any("cheap" in n and "not a valid number" in n for n in result.notes)


def test_drop_bad_sort_falls_back_to_default(schema):
    candidate = CandidateFilter(filters={"make": "Honda"}, sort="color_asc")
    result = validate(candidate, schema)
    assert result.sort == "price_asc"  # schema default
    assert any("color_asc" in n and "not available" in n for n in result.notes)


# === KEEPS ===


def test_keep_enum_wrong_casing_stores_canonical(schema):
    candidate = CandidateFilter(filters={"make": "honda"})
    result = validate(candidate, schema)
    assert result.filters["make"] == "Honda"
    assert result.notes == []


def test_keep_numeric_string_coercion(schema):
    candidate = CandidateFilter(filters={"price": {"op": "lte", "value": "25000"}})
    result = validate(candidate, schema)
    assert result.filters["price"] == {"op": "lte", "value": 25000}
    assert result.notes == []


def test_keep_valid_between(schema):
    candidate = CandidateFilter(
        filters={"year": {"op": "between", "low": 2015, "high": 2020}}
    )
    result = validate(candidate, schema)
    assert result.filters["year"] == {"op": "between", "low": 2015, "high": 2020}
    assert result.notes == []


def test_candidate_error_returns_empty_filters_with_note(schema):
    candidate = CandidateFilter(error="JSON parse failed: unexpected token")
    result = validate(candidate, schema)
    assert result.filters == {}
    assert any("Translation error" in n for n in result.notes)


# === PARTIAL FAILURE (the key test) ===


def test_partial_failure_good_survives_bad_dropped(schema):
    """A mix of good and bad fields: good survives, bad are dropped and noted."""
    candidate = CandidateFilter(
        filters={
            "make": "Honda",          # valid
            "color": "maroon",        # bad enum
            "year": {"op": "eq", "value": 3000},  # out of range
        }
    )
    result = validate(candidate, schema)
    # Good field survives
    assert result.filters["make"] == "Honda"
    # Bad fields are absent
    assert "color" not in result.filters
    assert "year" not in result.filters
    # Both bad fields are noted
    assert len(result.notes) == 2
    assert any("maroon" in n for n in result.notes)
    assert any("3000" in n for n in result.notes)
