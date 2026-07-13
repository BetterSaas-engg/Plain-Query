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


# === DATE VALIDATION ===


@pytest.fixture
def hotel_schema():
    return load_schema("schemas/hotels.json")


def test_date_past_rejected_not_clamped(hotel_schema):
    """A past date must be rejected, not clamped to today."""
    candidate = CandidateFilter(
        filters={"check_in": {"op": "eq", "value": "2020-01-01"}}
    )
    result = validate(candidate, hotel_schema)
    assert "check_in" not in result.filters
    assert any("past" in n for n in result.notes)


def test_date_valid_future_accepted(hotel_schema):
    """A future date passes validation."""
    candidate = CandidateFilter(
        filters={"check_in": {"op": "eq", "value": "2027-06-15"}}
    )
    result = validate(candidate, hotel_schema)
    assert result.filters["check_in"] == {"op": "eq", "value": "2027-06-15"}
    assert result.notes == []


def test_date_malformed_rejected(hotel_schema):
    """Non-ISO date string is rejected."""
    candidate = CandidateFilter(
        filters={"check_in": {"op": "eq", "value": "June 15"}}
    )
    result = validate(candidate, hotel_schema)
    assert "check_in" not in result.filters
    assert any("not a valid ISO date" in n for n in result.notes)


def test_no_term_in_both_filter_and_unmapped(schema):
    """A term must never appear in both filters and unmapped."""
    candidate = CandidateFilter(
        filters={"make": "Honda"},
        unmapped=["Honda"],  # LLM contradiction
    )
    result = validate(candidate, schema)
    assert result.filters["make"] == "Honda"
    # "Honda" must be removed from unmapped since it's in the filter
    assert "Honda" not in result.unmapped


# === NEEDS INPUT PRECEDENCE (engine-level, tested via run()) ===

from src.plainquery.engine import run as engine_run
from unittest.mock import patch


def _mock_translate_empty_with_unmapped(text, schema):
    """Simulate LLM returning nothing mapped, terms in unmapped."""
    return CandidateFilter(filters={}, unmapped=["romantic", "rooftop pool"])


def _mock_translate_partial(text, schema):
    """Simulate LLM mapping some fields but not dates."""
    return CandidateFilter(filters={"city": "Toronto", "property_type": "hostel"})


@patch("src.plainquery.engine.translate")
def test_empty_filter_with_unmapped_returns_not_understood(mock_translate):
    """Empty filter + unmapped terms → not_understood, not a date prompt."""
    mock_translate.side_effect = _mock_translate_empty_with_unmapped
    result = engine_run("a romantic place", "schemas/hotels.json", "data/hotels.json")
    assert result.needs_input is True
    assert result.needs_input_kind == "not_understood"
    assert result.missing_essential == []  # should NOT list dates


@patch("src.plainquery.engine.translate")
def test_partial_filter_missing_essential_asks_for_dates(mock_translate):
    """Has real constraints but missing essential → missing_essential."""
    mock_translate.side_effect = _mock_translate_partial
    result = engine_run("hostel in Toronto", "schemas/hotels.json", "data/hotels.json")
    assert result.needs_input is True
    assert result.needs_input_kind == "missing_essential"
    assert "check_in" in result.missing_essential
    assert "check_out" in result.missing_essential


# === REGRESSION: non-temporal queries must NOT produce date fields ===


def _mock_translate_cheap_hostel(text, schema):
    """Correct behavior: 'cheap hostel in Toronto' maps city + type, no dates."""
    return CandidateFilter(filters={"city": "Toronto", "property_type": "hostel"})


def _mock_translate_hostel_toronto(text, schema):
    """Correct behavior: 'hostel in Toronto' maps city + type, no dates."""
    return CandidateFilter(filters={"city": "Toronto", "property_type": "hostel"})


def _mock_translate_nice_hotel_vancouver(text, schema):
    """Correct behavior: 'nice hotel in Vancouver' maps city + type, no dates."""
    return CandidateFilter(filters={"city": "Vancouver", "property_type": "hotel"})


@patch("src.plainquery.engine.translate")
def test_cheap_hostel_no_invented_dates(mock_translate):
    """'cheap hostel in Toronto' must not produce date fields — asks for dates."""
    mock_translate.side_effect = _mock_translate_cheap_hostel
    result = engine_run("cheap hostel in Toronto", "schemas/hotels.json", "data/hotels.json")
    assert "check_in" not in result.validated_filter
    assert "check_out" not in result.validated_filter
    assert result.needs_input is True
    assert result.needs_input_kind == "missing_essential"


@patch("src.plainquery.engine.translate")
def test_hostel_toronto_no_invented_dates(mock_translate):
    """'hostel in Toronto' must not produce date fields — asks for dates."""
    mock_translate.side_effect = _mock_translate_hostel_toronto
    result = engine_run("hostel in Toronto", "schemas/hotels.json", "data/hotels.json")
    assert "check_in" not in result.validated_filter
    assert "check_out" not in result.validated_filter
    assert result.needs_input is True
    assert result.needs_input_kind == "missing_essential"


@patch("src.plainquery.engine.translate")
def test_nice_hotel_vancouver_no_invented_dates(mock_translate):
    """'nice hotel in Vancouver' must not produce date fields — asks for dates."""
    mock_translate.side_effect = _mock_translate_nice_hotel_vancouver
    result = engine_run("nice hotel in Vancouver", "schemas/hotels.json", "data/hotels.json")
    assert "check_in" not in result.validated_filter
    assert "check_out" not in result.validated_filter
    assert result.needs_input is True
    assert result.needs_input_kind == "missing_essential"
