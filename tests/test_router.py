"""Deterministic tests for the vertical router.

Tests context-provided mode (no LLM) and structural behavior.
Inference tests use mocking to stay offline and deterministic.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.plainquery.router import CustomerConfig, RouteResult, load_customer, route


@pytest.fixture
def customer():
    return load_customer("customers/expedia.json")


# === Context-provided mode (fully deterministic, no LLM) ===


def test_context_provided_always_wins(customer):
    """When vertical is explicitly passed, use it — no inference."""
    result = route("anything at all", customer, vertical="hotels")
    assert result.vertical == "hotels"
    assert result.mode == "provided"
    assert result.confidence == "high"
    assert result.schema_path == "schemas/hotels.json"
    assert result.data_path == "data/hotels.json"


def test_context_provided_invalid_vertical(customer):
    """An invalid explicit vertical returns ambiguous with candidates."""
    result = route("anything", customer, vertical="spaceships")
    assert result.vertical is None
    assert result.confidence == "ambiguous"
    assert set(result.candidates) == {"hotels", "flights", "car_rentals"}


def test_context_provided_flights(customer):
    """Context-provided for flights vertical."""
    result = route("blah blah", customer, vertical="flights")
    assert result.vertical == "flights"
    assert result.mode == "provided"
    assert result.schema_path == "schemas/flights.json"


def test_context_provided_car_rentals(customer):
    """Context-provided for car_rentals vertical."""
    result = route("blah blah", customer, vertical="car_rentals")
    assert result.vertical == "car_rentals"
    assert result.mode == "provided"
    assert result.schema_path == "schemas/car_rentals.json"


# === Inferred mode (mocked LLM) ===


def _mock_llm_response(text: str):
    """Create a mock Anthropic response with the given text."""
    mock_content = MagicMock()
    mock_content.text = text
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    return mock_response


@patch("src.plainquery.router.anthropic.Anthropic")
def test_inferred_unambiguous_routes_correctly(mock_anthropic_cls, customer):
    """An unambiguous LLM response routes to the correct vertical."""
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_llm_response(
        '{"vertical": "flights", "confidence": "high"}'
    )

    result = route("flight to Paris", customer)
    assert result.vertical == "flights"
    assert result.mode == "inferred"
    assert result.confidence == "high"


@patch("src.plainquery.router.anthropic.Anthropic")
def test_inferred_ambiguous_returns_no_vertical(mock_anthropic_cls, customer):
    """An ambiguous LLM response returns no vertical, not a guess."""
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_llm_response(
        '{"vertical": null, "confidence": "ambiguous"}'
    )

    result = route("something cheap in Toronto", customer)
    assert result.vertical is None
    assert result.confidence == "ambiguous"
    assert len(result.candidates) == 3


@patch("src.plainquery.router.anthropic.Anthropic")
def test_inferred_api_failure_returns_ambiguous(mock_anthropic_cls, customer):
    """API failure = fail closed = ambiguous."""
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.side_effect = Exception("API down")

    result = route("flight to Paris", customer)
    assert result.vertical is None
    assert result.confidence == "ambiguous"


@patch("src.plainquery.router.anthropic.Anthropic")
def test_inferred_invalid_vertical_in_response(mock_anthropic_cls, customer):
    """LLM returns a vertical that doesn't exist = fail closed."""
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_llm_response(
        '{"vertical": "spaceships", "confidence": "high"}'
    )

    result = route("book a rocket", customer)
    assert result.vertical is None
    assert result.confidence == "ambiguous"


@patch("src.plainquery.router.anthropic.Anthropic")
def test_inferred_malformed_json_returns_ambiguous(mock_anthropic_cls, customer):
    """Malformed JSON from LLM = fail closed."""
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_llm_response("not json at all")

    result = route("whatever", customer)
    assert result.vertical is None
    assert result.confidence == "ambiguous"
