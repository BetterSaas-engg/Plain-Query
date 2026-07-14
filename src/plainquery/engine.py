"""Pipeline orchestrator: schema → translate → validate → search.

No validation or matching logic here — just wiring.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from .backend import search
from .loosen import Suggestion, suggest
from .schema import Schema, load_schema
from .translator import CandidateFilter, translate
from .validator import ValidatedFilter, validate


@dataclass
class SearchResult:
    """Full pipeline output with transparency metadata."""
    rows: list[dict] = field(default_factory=list)
    total_matches: int = 0
    validated_filter: dict = field(default_factory=dict)
    sort: str = ""
    limit: int = 25
    unmapped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    display: list[str] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)
    needs_input: bool = False
    needs_input_kind: str = ""  # "not_understood" or "missing_essential"
    missing_essential: list[str] = field(default_factory=list)
    available_fields: list[str] = field(default_factory=list)


def run_from_filter(
    vf: ValidatedFilter, schema: Schema, data: list[dict]
) -> SearchResult:
    """Run the deterministic path from a pre-validated filter. No LLM calls.

    This is the cache-hit entry point: the filter was already translated and
    validated on a previous request, so we skip straight to needs-input checks,
    search, and loosening.
    """
    all_fields = [name for name, fd in schema.fields.items() if not fd.essential]

    # Precedence: "nothing understood" beats "missing essential"
    # 1. Empty filter with unmapped terms → we failed to comprehend the query
    if not vf.filters and bool(vf.unmapped):
        return SearchResult(
            validated_filter=vf.filters,
            sort=vf.sort,
            limit=vf.limit,
            unmapped=vf.unmapped,
            notes=vf.notes,
            display=schema.display,
            needs_input=True,
            needs_input_kind="not_understood",
            available_fields=all_fields,
        )

    # 2. Has real constraints but missing essential fields → ask for them
    missing_essential = [
        name for name, fd in schema.fields.items()
        if fd.essential and name not in vf.filters
    ]
    if missing_essential:
        return SearchResult(
            validated_filter=vf.filters,
            sort=vf.sort,
            limit=vf.limit,
            unmapped=vf.unmapped,
            notes=vf.notes,
            display=schema.display,
            needs_input=True,
            needs_input_kind="missing_essential",
            missing_essential=missing_essential,
            available_fields=all_fields,
        )

    rows = search(data, vf, schema)

    suggestions = []
    if not rows and vf.filters:
        suggestions = suggest(data, vf, schema)

    return SearchResult(
        rows=rows,
        total_matches=len(rows),
        validated_filter=vf.filters,
        sort=vf.sort,
        limit=vf.limit,
        unmapped=vf.unmapped,
        notes=vf.notes,
        display=schema.display,
        suggestions=suggestions,
    )


def run(query: str, schema_path: str, data_path: str) -> SearchResult:
    """Run the full NL → filter → search pipeline."""
    schema = load_schema(schema_path)
    data = json.loads(Path(data_path).read_text(encoding="utf-8"))

    candidate: CandidateFilter = translate(query, schema)
    vf: ValidatedFilter = validate(candidate, schema)

    return run_from_filter(vf, schema, data)
