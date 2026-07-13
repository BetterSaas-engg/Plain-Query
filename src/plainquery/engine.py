"""Pipeline orchestrator: schema → translate → validate → search.

No validation or matching logic here — just wiring.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from .backend import search
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


def run(query: str, schema_path: str, data_path: str) -> SearchResult:
    """Run the full NL → filter → search pipeline."""
    schema = load_schema(schema_path)
    data = json.loads(Path(data_path).read_text(encoding="utf-8"))

    candidate: CandidateFilter = translate(query, schema)
    vf: ValidatedFilter = validate(candidate, schema)
    rows = search(data, vf, schema)

    return SearchResult(
        rows=rows,
        total_matches=len(rows),
        validated_filter=vf.filters,
        sort=vf.sort,
        limit=vf.limit,
        unmapped=vf.unmapped,
        notes=vf.notes,
    )
