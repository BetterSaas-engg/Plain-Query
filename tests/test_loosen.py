"""Deterministic tests for zero-results loosening suggestions.

All tests use inline data and ValidatedFilter objects directly — no LLM calls.
"""

import pytest

from src.plainquery.schema import load_schema
from src.plainquery.validator import ValidatedFilter
from src.plainquery.loosen import suggest


@pytest.fixture
def hotel_schema():
    return load_schema("schemas/hotels.json")


def _make_hotels():
    """Small inline dataset for controlled testing."""
    return [
        {"id": 1, "name": "Budget Inn", "city": "Toronto", "star_rating": 3,
         "price_per_night": 120, "guest_rating": 7, "property_type": "hotel",
         "breakfast_included": "yes", "neighborhood": "Downtown"},
        {"id": 2, "name": "Luxury Resort", "city": "Vancouver", "star_rating": 5,
         "price_per_night": 600, "guest_rating": 9, "property_type": "resort",
         "breakfast_included": "yes", "neighborhood": "Coal Harbour"},
        {"id": 3, "name": "Mid Hotel", "city": "Vancouver", "star_rating": 4,
         "price_per_night": 300, "guest_rating": 8, "property_type": "hotel",
         "breakfast_included": "no", "neighborhood": "Downtown"},
        {"id": 4, "name": "Backpackers Vancouver", "city": "Vancouver", "star_rating": 2,
         "price_per_night": 50, "guest_rating": 6, "property_type": "hostel",
         "breakfast_included": "no", "neighborhood": "Gastown"},
        {"id": 5, "name": "Premium Resort", "city": "Vancouver", "star_rating": 5,
         "price_per_night": 520, "guest_rating": 10, "property_type": "resort",
         "breakfast_included": "yes", "neighborhood": "West End"},
    ]


def test_single_constraint_drop_yields_matches(hotel_schema):
    """Dropping star_rating from a zero-result search should yield matches."""
    data = _make_hotels()
    # 5-star hotel in Vancouver — no 5-star hotels exist, only resorts
    vf = ValidatedFilter(
        filters={
            "city": "Vancouver",
            "star_rating": {"op": "eq", "value": 5},
            "property_type": "hotel",
        },
        sort="price_per_night_asc",
        limit=25,
    )
    suggestions = suggest(data, vf, hotel_schema)
    assert len(suggestions) > 0
    # Dropping property_type should yield matches (2 Vancouver 5-star resorts)
    drop_type = next((s for s in suggestions if s.field == "property_type"), None)
    assert drop_type is not None
    assert drop_type.match_count == 2
    # Dropping star_rating should also yield matches (1 Vancouver hotel)
    drop_star = next((s for s in suggestions if s.field == "star_rating"
                      and s.change.startswith("Drop")), None)
    assert drop_star is not None
    assert drop_star.match_count > 0


def test_numeric_widening_finds_nearest_threshold(hotel_schema):
    """Widening price_per_night should suggest the nearest working value."""
    data = _make_hotels()
    # 5-star resort in Vancouver under 400 — none exist (cheapest is 520)
    vf = ValidatedFilter(
        filters={
            "city": "Vancouver",
            "star_rating": {"op": "eq", "value": 5},
            "property_type": "resort",
            "price_per_night": {"op": "lte", "value": 400},
        },
        sort="price_per_night_asc",
        limit=25,
    )
    suggestions = suggest(data, vf, hotel_schema)
    # Should suggest widening price to 520 (the nearest value above 400)
    widen = next((s for s in suggestions if s.field == "price_per_night"
                  and "520" in s.change), None)
    assert widen is not None
    assert widen.match_count >= 1


def test_impossible_search_returns_no_suggestions(hotel_schema):
    """When no single-constraint relaxation helps, return empty list."""
    data = _make_hotels()
    # Every constraint individually excludes all data:
    # city=Halifax (no rows), property_type=bnb (no rows)
    # Dropping either one still leaves the other blocking everything.
    vf = ValidatedFilter(
        filters={"city": "Halifax", "property_type": "bnb"},
        sort="price_per_night_asc",
        limit=25,
    )
    suggestions = suggest(data, vf, hotel_schema)
    assert suggestions == []
