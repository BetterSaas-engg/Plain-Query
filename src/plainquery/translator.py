"""Translate natural language into a candidate filter object via one LLM call.

The translator does NOT validate. It passes the model's candidate through as-is.
Validation (enums, ranges, types) is the validator's job.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date

import anthropic
from dotenv import load_dotenv

from .schema import Schema


@dataclass
class CandidateFilter:
    """Raw candidate from the LLM. Not yet validated."""
    filters: dict = field(default_factory=dict)
    sort: str | None = None
    unmapped: list[str] = field(default_factory=list)
    error: str | None = None


MODEL = "claude-haiku-4-5-20251001"


def _build_system_prompt(schema: Schema) -> str:
    today = date.today().isoformat()
    lines = [
        f"You are a search filter translator for a {schema.vertical} search engine.",
        f"Today's date is {today}. Resolve all relative dates relative to this date.",
        "The user will give you a natural language search query.",
        "Your job: extract structured filters from the query using ONLY the fields below.",
        "",
        "## Available fields",
    ]

    for name, f in schema.fields.items():
        essential_tag = " [REQUIRED]" if f.essential else ""
        if f.type == "enum":
            lines.append(f"- {name} (enum): one of {f.values}{essential_tag}")
        elif f.type == "string":
            lines.append(f"- {name} (string): free text match{essential_tag}")
        elif f.type == "date":
            lines.append(
                f"- {name} (date, ISO YYYY-MM-DD): operators {f.operators}{essential_tag}"
            )
        elif f.type == "int":
            range_parts = []
            if f.min is not None:
                range_parts.append(f"min {f.min}")
            if f.max is not None:
                range_parts.append(f"max {f.max}")
            unit = f" ({f.unit})" if f.unit else ""
            range_str = f", range: {', '.join(range_parts)}" if range_parts else ""
            lines.append(
                f"- {name} (integer{unit}): operators {f.operators}{range_str}{essential_tag}"
            )

    lines += [
        "",
        "## Sort options",
        f"Available: {schema.sort_options}",
        f"Default (omit if no preference): {schema.default_sort}",
        "",
        "## Output rules",
        "Return ONLY a JSON object. No markdown fences, no explanation, no prose.",
        "The JSON object has these keys:",
        '- For each matched field: the field name as key. For enum/string fields, the value is a string. For int fields, the value is an object like {"op": "<operator>", "value": <int>} or {"op": "between", "low": <int>, "high": <int>}. For date fields, same structure but with ISO date strings: {"op": "eq", "value": "2026-06-15"} or {"op": "between", "low": "2026-06-12", "high": "2026-06-15"}.',
        '- "sort": a string from the sort options, only if the query implies a sort preference. Omit if no preference.',
        '- "unmapped": an array of user terms you could NOT map to any field. Always include this key (empty array if everything mapped).',
        "",
        "## Important",
        "- Only use field names and enum values listed above.",
        '- Prefer sort over invented thresholds. E.g. "low mileage" → sort by mileage_asc, NOT mileage < some guess.',
        "- If a user term doesn't match any field, put it in unmapped. Never invent fields.",
        "- For numeric values, interpret common shorthands: 25k = 25000, 50K = 50000, etc.",
        f'- For date fields, convert mentions to ISO YYYY-MM-DD relative to today ({today}). "next weekend" → the upcoming Saturday/Sunday. "in June" → June of the current or next year, whichever is in the future. All dates must be today or later. If a date is genuinely ambiguous or absent, do NOT invent one — leave the field out.',
        "- A term must NEVER appear in both a filter and in unmapped. If you mapped it to a filter, it is accounted for — do not also list it as unmapped.",
        "",
        "## Accounting rule",
        "Every meaningful term or phrase in the user's query must be accounted for.",
        "It either (a) maps to a field filter or sort, or (b) appears in unmapped.",
        "Never silently discard a term because it seems like context or because you",
        "understood its intent. If a phrase is meaningful but doesn't fit any field",
        '— e.g. a rental duration, a date range, an amenity not in the schema —',
        "it MUST go in unmapped. Ignore only pure filler words (articles, prepositions,",
        '"please", conjunctions, etc.).',
    ]

    return "\n".join(lines)


def _parse_response(text: str) -> CandidateFilter:
    """Parse the LLM response into a CandidateFilter. Fail-closed: never raise."""
    # Strip markdown fences if the model wrapped its output
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError) as e:
        return CandidateFilter(error=f"JSON parse failed: {e}", unmapped=[text.strip()])

    if not isinstance(data, dict):
        return CandidateFilter(error=f"Expected JSON object, got {type(data).__name__}")

    unmapped = data.pop("unmapped", [])
    if not isinstance(unmapped, list):
        unmapped = [str(unmapped)]

    sort = data.pop("sort", None)
    if sort is not None and not isinstance(sort, str):
        sort = None

    return CandidateFilter(filters=data, sort=sort, unmapped=unmapped)


def translate(text: str, schema: Schema) -> CandidateFilter:
    """Translate a natural language query into a candidate filter via one LLM call."""
    load_dotenv()
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    system_prompt = _build_system_prompt(schema)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": text}],
        )
    except Exception as e:
        return CandidateFilter(error=f"API call failed: {e}")

    if not response.content or not hasattr(response.content[0], "text"):
        return CandidateFilter(error="empty or unexpected API response")

    return _parse_response(response.content[0].text)
