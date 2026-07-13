"""Vertical router: decide which schema a query belongs to.

Two modes:
1. Context-provided — caller passes the vertical explicitly. No LLM, no risk.
2. Inferred — one LLM call classifies the query against the customer's verticals.

Fails closed: on ambiguity, returns no vertical rather than guessing.
A misroute is worse than a bad filter.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from .schema import load_schema

MODEL = "claude-haiku-4-5-20251001"


@dataclass
class CustomerConfig:
    """A customer's set of verticals."""
    name: str
    verticals: dict[str, dict]  # vertical_name -> {"schema": path, "data": path}


@dataclass
class RouteResult:
    """Result of vertical routing."""
    vertical: str | None = None        # chosen vertical name, or None if ambiguous
    schema_path: str | None = None
    data_path: str | None = None
    mode: str = ""                     # "provided" or "inferred"
    confidence: str = ""               # "high" or "ambiguous"
    candidates: list[str] = field(default_factory=list)  # on ambiguity, the options


def load_customer(path: str | Path) -> CustomerConfig:
    """Load a customer config file."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return CustomerConfig(
        name=raw["name"],
        verticals=raw["verticals"],
    )


def route(text: str, customer: CustomerConfig, vertical: str | None = None) -> RouteResult:
    """Route a query to the right vertical.

    If vertical is provided, use it directly (context-provided mode).
    Otherwise, infer from the query text via one LLM call.
    """
    if vertical is not None:
        # Context-provided: no inference needed
        if vertical not in customer.verticals:
            return RouteResult(
                confidence="ambiguous",
                candidates=list(customer.verticals.keys()),
            )
        v = customer.verticals[vertical]
        return RouteResult(
            vertical=vertical,
            schema_path=v["schema"],
            data_path=v["data"],
            mode="provided",
            confidence="high",
        )

    # Inferred mode: classify via LLM
    return _infer(text, customer)


_client = None
_prompt_cache: dict[str, str] = {}


def _get_client():
    global _client
    if _client is None:
        load_dotenv()
        _client = anthropic.Anthropic()
    return _client


def _get_router_prompt(customer: CustomerConfig) -> str:
    """Build and cache the router system prompt per customer."""
    cache_key = customer.name
    if cache_key in _prompt_cache:
        return _prompt_cache[cache_key]

    vertical_descriptions = []
    for v_name, v_config in customer.verticals.items():
        schema = load_schema(v_config["schema"])
        field_names = list(schema.fields.keys())
        vertical_descriptions.append(
            f"- {v_name}: searches by {', '.join(field_names)}"
        )

    prompt = (
        f"You are a query router for {customer.name}. "
        f"The customer has these search verticals:\n"
        + "\n".join(vertical_descriptions)
        + "\n\n"
        "Given a user search query, decide which ONE vertical it belongs to.\n\n"
        "Rules:\n"
        "- Return ONLY a JSON object with two keys: \"vertical\" and \"confidence\".\n"
        "- \"vertical\": the vertical name (one of: "
        + ", ".join(f'"{v}"' for v in customer.verticals)
        + ") or null if genuinely ambiguous.\n"
        '- "confidence": "high" if clearly one vertical, "ambiguous" if the query '
        "could reasonably belong to multiple verticals.\n"
        "- If ambiguous, set vertical to null. Do NOT guess. A wrong route is worse "
        "than no route.\n"
        "- No markdown fences, no explanation, just the JSON object.\n"
    )
    _prompt_cache[cache_key] = prompt
    return prompt


def _infer(text: str, customer: CustomerConfig) -> RouteResult:
    """Classify a query into a vertical via one LLM call. Fail closed on ambiguity."""
    client = _get_client()
    system_prompt = _get_router_prompt(customer)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=64,
            system=system_prompt,
            messages=[{"role": "user", "content": text}],
        )
    except Exception:
        # API failure = ambiguous (fail closed)
        return RouteResult(
            confidence="ambiguous",
            candidates=list(customer.verticals.keys()),
        )

    if not response.content or not hasattr(response.content[0], "text"):
        return RouteResult(
            confidence="ambiguous",
            candidates=list(customer.verticals.keys()),
        )

    return _parse_route_response(response.content[0].text, customer)


def _parse_route_response(text: str, customer: CustomerConfig) -> RouteResult:
    """Parse the LLM routing response. Fail closed on any parse issue."""
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    cleaned = cleaned.strip()

    candidates = list(customer.verticals.keys())

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return RouteResult(confidence="ambiguous", candidates=candidates)

    if not isinstance(data, dict):
        return RouteResult(confidence="ambiguous", candidates=candidates)

    vertical = data.get("vertical")
    confidence = data.get("confidence", "ambiguous")

    # Fail closed: if confidence isn't high, or vertical is null/missing, return ambiguous
    if confidence != "high" or vertical is None:
        return RouteResult(confidence="ambiguous", candidates=candidates)

    # Validate the vertical exists
    if vertical not in customer.verticals:
        return RouteResult(confidence="ambiguous", candidates=candidates)

    v = customer.verticals[vertical]
    return RouteResult(
        vertical=vertical,
        schema_path=v["schema"],
        data_path=v["data"],
        mode="inferred",
        confidence="high",
        candidates=candidates,
    )
