"""Pipeline orchestrator: schema → translate → validate → search.

No validation or matching logic here — just wiring.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from .backend import search
from .loosen import Suggestion, suggest
from .schema import FieldDef, Schema, load_schema
from .translator import CandidateFilter, translate
from .validator import ValidatedFilter, validate


# ---------------------------------------------------------------------------
# Reviewable filter — first-class structured output for customer API consumers
# ---------------------------------------------------------------------------

@dataclass
class ReviewField:
    """One field in the reviewable filter: what was understood + schema context.

    Provides everything a customer's UI needs to render an editable review form:
    the value we extracted, the field type, and the constraints the schema allows
    (enum options, numeric range, valid operators). All derived from existing
    schema + validated-filter data — no new computation.
    """
    name: str
    type: str                                    # "enum", "string", "int", "date"
    value: str | dict = ""                       # The extracted constraint
    # Schema context for the reviewer
    enum_values: list[str] = field(default_factory=list)  # Valid options (enum only)
    operators: list[str] = field(default_factory=list)     # Valid operators (int/date)
    min: int | None = None                       # Range floor (int only)
    max: int | None = None                       # Range ceiling (int only)
    unit: str | None = None                      # "CAD", "km", etc. (int only)
    essential: bool = False


@dataclass
class FilterReview:
    """Structured reviewable filter — everything an API consumer needs to build
    a review/edit step. Every field here is derived from data the engine already
    computes; nothing is invented.
    """
    # Per-field breakdown of what was understood
    fields: list[ReviewField] = field(default_factory=list)
    sort: str = ""
    sort_options: list[str] = field(default_factory=list)
    limit: int = 25

    # What wasn't understood
    unmapped: list[str] = field(default_factory=list)

    # What was dropped and why (validator notes — each explains a rejection)
    dropped: list[str] = field(default_factory=list)

    # Status: "ready" | "needs_input" | "not_understood"
    status: str = "ready"
    missing_essential: list[str] = field(default_factory=list)
    available_fields: list[str] = field(default_factory=list)


def build_filter_review(
    vf: ValidatedFilter, schema: Schema,
    *, needs_input: bool = False, needs_input_kind: str = "",
    missing_essential: list[str] | None = None,
) -> FilterReview:
    """Build a structured review from a validated filter + schema.

    Pairs each filter field with its schema metadata so any consumer can
    render an editable review form without needing to re-read the schema.
    """
    review_fields = []
    for fname, constraint in vf.filters.items():
        fd = schema.fields.get(fname)
        if fd is None:
            continue
        review_fields.append(_review_field(fname, constraint, fd))

    # Include missing essential fields as empty entries so the UI can render
    # them as required-empty inputs (date pickers, etc.) without a separate
    # prompt screen.
    for ename in (missing_essential or []):
        if ename not in vf.filters:
            fd = schema.fields.get(ename)
            if fd is not None:
                review_fields.append(_review_field(ename, "", fd))

    all_fields = [name for name, fd in schema.fields.items() if not fd.essential]

    status = "ready"
    if needs_input:
        status = needs_input_kind or "needs_input"

    return FilterReview(
        fields=review_fields,
        sort=vf.sort,
        sort_options=schema.sort_options,
        limit=vf.limit,
        unmapped=list(vf.unmapped),
        dropped=list(vf.notes),
        status=status,
        missing_essential=list(missing_essential or []),
        available_fields=all_fields,
    )


def _review_field(name: str, value, fd: FieldDef) -> ReviewField:
    """Build a ReviewField from a filter entry + its schema definition."""
    return ReviewField(
        name=name,
        type=fd.type,
        value=value,
        enum_values=list(fd.values) if fd.values else [],
        operators=list(fd.operators) if fd.operators else [],
        min=fd.min,
        max=fd.max,
        unit=fd.unit,
        essential=fd.essential,
    )


# ---------------------------------------------------------------------------
# SearchResult — full pipeline output
# ---------------------------------------------------------------------------

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
    # Structured reviewable filter
    review: FilterReview | None = None


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
        review = build_filter_review(
            vf, schema, needs_input=True, needs_input_kind="not_understood",
        )
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
            review=review,
        )

    # 2. Has real constraints but missing essential fields → ask for them
    missing_essential = [
        name for name, fd in schema.fields.items()
        if fd.essential and name not in vf.filters
    ]
    if missing_essential:
        review = build_filter_review(
            vf, schema, needs_input=True, needs_input_kind="missing_essential",
            missing_essential=missing_essential,
        )
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
            review=review,
        )

    rows = search(data, vf, schema)

    suggestions = []
    if not rows and vf.filters:
        suggestions = suggest(data, vf, schema)

    review = build_filter_review(vf, schema)
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
        review=review,
    )


def run_from_edit(
    edited_filters: dict,
    schema: Schema,
    data: list[dict],
    sort: str | None = None,
    limit: int | None = None,
) -> SearchResult:
    """Re-validate an edited filter and search. The edit/review entry point.

    The edited filter is untrusted input — a user reviewed the mapping and
    changed it. Treat it exactly like a translator candidate: validate every
    field, drop/reject bad values, fail closed, surface notes. No edited
    filter reaches the search without re-validation.

    This is the same validator that sits between the LLM and search.
    A user editing "price ≤ 25000" to "price ≤ banana" is caught identically
    to an LLM hallucinating "price ≤ banana".
    """
    candidate = CandidateFilter(
        filters=dict(edited_filters),
        sort=sort,
        unmapped=[],  # User edits have no unmapped terms
    )
    vf = validate(candidate, schema)

    # Override limit if the caller specified one
    if limit is not None and isinstance(limit, int) and limit > 0:
        vf = ValidatedFilter(
            filters=vf.filters,
            sort=vf.sort,
            limit=limit,
            unmapped=vf.unmapped,
            notes=vf.notes,
        )

    return run_from_filter(vf, schema, data)


def run(query: str, schema_path: str, data_path: str) -> SearchResult:
    """Run the full NL → filter → search pipeline."""
    schema = load_schema(schema_path)
    data = json.loads(Path(data_path).read_text(encoding="utf-8"))

    candidate: CandidateFilter = translate(query, schema)
    vf: ValidatedFilter = validate(candidate, schema)

    return run_from_filter(vf, schema, data)
