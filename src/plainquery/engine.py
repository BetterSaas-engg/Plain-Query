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
    missing_essential: list[str] = field(default_factory=list)
    available_fields: list[str] = field(default_factory=list)


def _check_needs_input(vf: ValidatedFilter, schema: Schema) -> tuple[bool, list[str]]:
    """Check if essential fields are missing. Returns (needs_input, missing_fields)."""
    missing = []
    for name, field_def in schema.fields.items():
        if field_def.essential and name not in vf.filters:
            missing.append(name)
    return bool(missing), missing


def run(query: str, schema_path: str, data_path: str) -> SearchResult:
    """Run the full NL → filter → search pipeline."""
    schema = load_schema(schema_path)
    data = json.loads(Path(data_path).read_text(encoding="utf-8"))

    candidate: CandidateFilter = translate(query, schema)
    vf: ValidatedFilter = validate(candidate, schema)

    # Check if we should ask instead of searching
    needs_input, missing_essential = _check_needs_input(vf, schema)
    empty_with_unmapped = not vf.filters and bool(vf.unmapped)

    if needs_input or empty_with_unmapped:
        return SearchResult(
            validated_filter=vf.filters,
            sort=vf.sort,
            limit=vf.limit,
            unmapped=vf.unmapped,
            notes=vf.notes,
            display=schema.display,
            needs_input=True,
            missing_essential=missing_essential,
            available_fields=[
                name for name, fd in schema.fields.items() if not fd.essential
            ],
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
