"""PlainQuery demo API — wraps the real engine for the sales UI."""

import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.plainquery.schema import load_schema
from src.plainquery.translator import translate
from src.plainquery.validator import validate, ValidatedFilter
from src.plainquery.backend import search
from src.plainquery.loosen import suggest, Suggestion
from src.plainquery.engine import run_from_filter, run_from_edit, FilterReview, ReviewField
from src.plainquery.router import load_customer, route, _get_client, MODEL
from src.plainquery.cache import (
    LRUFilterCache, CacheEntry, make_cache_key, schema_fingerprint,
)

logger = logging.getLogger("plainquery")

CUSTOMER_PATH = "customers/expedia.json"
customer = load_customer(CUSTOMER_PATH)
_fingerprint = schema_fingerprint(customer.verticals)
_cache = LRUFilterCache(max_size=1024)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm the Anthropic client connection at startup."""
    try:
        t0 = time.perf_counter()
        client = _get_client()
        client.messages.create(
            model=MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
        elapsed = (time.perf_counter() - t0) * 1000
        logger.info(f"Client warm-up complete ({elapsed:.0f}ms)")
    except Exception as e:
        logger.warning(f"Client warm-up failed (non-fatal): {e}")
    yield


app = FastAPI(title="PlainQuery Demo", lifespan=lifespan)


class SearchRequest(BaseModel):
    query: str
    vertical: str | None = None  # None = inferred routing


class SuggestionOut(BaseModel):
    field: str
    change: str
    match_count: int


class ReviewFieldOut(BaseModel):
    name: str
    type: str
    value: str | dict = ""
    enum_values: list[str] = []
    operators: list[str] = []
    min: int | None = None
    max: int | None = None
    unit: str | None = None
    essential: bool = False


class FilterReviewOut(BaseModel):
    """Structured reviewable filter — first-class API output.

    Everything an API consumer needs to build a review/edit step:
    per-field what-we-understood with schema context, unmapped terms,
    dropped fields with reasons, and search-readiness status.
    """
    fields: list[ReviewFieldOut] = []
    sort: str = ""
    sort_options: list[str] = []
    limit: int = 25
    unmapped: list[str] = []
    dropped: list[str] = []       # Validator notes — each explains a rejection
    status: str = "ready"         # "ready" | "needs_input" | "not_understood"
    missing_essential: list[str] = []
    available_fields: list[str] = []


class SearchResponse(BaseModel):
    # Routing
    routed_vertical: str | None = None
    route_mode: str = ""
    route_confidence: str = ""
    route_candidates: list[str] = []

    # Filter
    validated_filter: dict = {}
    sort: str = ""
    limit: int = 25
    unmapped: list[str] = []
    notes: list[str] = []
    display: list[str] = []

    # Reviewable filter — structured for customer review/edit flows
    review: FilterReviewOut | None = None

    # Results
    rows: list[dict] = []
    total_matches: int = 0
    suggestions: list[SuggestionOut] = []

    # Needs input
    needs_input: bool = False
    needs_input_kind: str = ""
    missing_essential: list[str] = []
    available_fields: list[str] = []

    # Timing
    route_time_ms: float = 0
    translate_time_ms: float = 0
    validate_time_ms: float = 0
    search_time_ms: float = 0
    total_time_ms: float = 0

    # Cache
    cache_hit: bool = False
    cache_stats: dict = {}


def _to_review_out(review: FilterReview | None) -> FilterReviewOut | None:
    if review is None:
        return None
    return FilterReviewOut(
        fields=[
            ReviewFieldOut(
                name=f.name, type=f.type, value=f.value,
                enum_values=f.enum_values, operators=f.operators,
                min=f.min, max=f.max, unit=f.unit, essential=f.essential,
            )
            for f in review.fields
        ],
        sort=review.sort,
        sort_options=review.sort_options,
        limit=review.limit,
        unmapped=review.unmapped,
        dropped=review.dropped,
        status=review.status,
        missing_essential=review.missing_essential,
        available_fields=review.available_fields,
    )


@app.post("/api/search", response_model=SearchResponse)
def api_search(req: SearchRequest):
    total_start = time.perf_counter()
    resp = SearchResponse()

    # --- Cache check: skip router + translator on hit ---
    explicit_vertical = req.vertical if req.vertical and req.vertical != "all" else None
    cache_key = make_cache_key(customer.name, _fingerprint, req.query)
    # For context-provided vertical, include it in the key so "hotels" and "flights"
    # for the same query text don't collide.
    if explicit_vertical:
        cache_key = make_cache_key(
            customer.name, _fingerprint, f"{explicit_vertical}\x00{req.query}"
        )

    cached = _cache.get(cache_key)
    if cached is not None:
        # Cache hit — zero LLM calls. Deterministic path only.
        resp.cache_hit = True
        resp.routed_vertical = cached.vertical
        resp.route_mode = "cached"
        resp.route_confidence = "high"

        schema = load_schema(cached.schema_path)
        data = json.loads(Path(cached.data_path).read_text(encoding="utf-8"))

        vf = ValidatedFilter(
            filters=cached.filters,
            sort=cached.sort,
            limit=cached.limit,
            unmapped=list(cached.unmapped),
            notes=list(cached.notes),
        )

        t0 = time.perf_counter()
        result = run_from_filter(vf, schema, data)
        resp.search_time_ms = (time.perf_counter() - t0) * 1000

        resp.validated_filter = result.validated_filter
        resp.sort = result.sort
        resp.limit = result.limit
        resp.unmapped = result.unmapped
        resp.notes = result.notes
        resp.display = result.display
        resp.rows = result.rows
        resp.total_matches = result.total_matches
        resp.needs_input = result.needs_input
        resp.needs_input_kind = result.needs_input_kind
        resp.missing_essential = result.missing_essential
        resp.available_fields = result.available_fields
        resp.suggestions = [
            SuggestionOut(field=s.field, change=s.change, match_count=s.match_count)
            for s in result.suggestions
        ]
        resp.review = _to_review_out(result.review)
        resp.total_time_ms = (time.perf_counter() - total_start) * 1000
        resp.cache_stats = _cache.stats()
        return resp

    # --- Cache miss: full pipeline (router + translator + validator) ---

    # 1. Route
    route_start = time.perf_counter()
    route_result = route(req.query, customer, vertical=explicit_vertical)
    resp.route_time_ms = (time.perf_counter() - route_start) * 1000

    resp.routed_vertical = route_result.vertical
    resp.route_mode = route_result.mode
    resp.route_confidence = route_result.confidence
    resp.route_candidates = route_result.candidates

    if route_result.vertical is None:
        resp.total_time_ms = (time.perf_counter() - total_start) * 1000
        resp.cache_stats = _cache.stats()
        return resp

    # 2. Load schema + data
    schema = load_schema(route_result.schema_path)
    data = json.loads(Path(route_result.data_path).read_text(encoding="utf-8"))
    resp.display = schema.display

    # 3. Translate (LLM call)
    t0 = time.perf_counter()
    candidate = translate(req.query, schema)
    resp.translate_time_ms = (time.perf_counter() - t0) * 1000

    # 4. Validate (deterministic)
    t0 = time.perf_counter()
    vf = validate(candidate, schema)
    resp.validate_time_ms = (time.perf_counter() - t0) * 1000

    # Don't cache translation errors — they may be transient (API timeout)
    if not candidate.error:
        _cache.put(cache_key, CacheEntry(
            vertical=route_result.vertical,
            schema_path=route_result.schema_path,
            data_path=route_result.data_path,
            filters=vf.filters,
            sort=vf.sort,
            limit=vf.limit,
            unmapped=list(vf.unmapped),
            notes=list(vf.notes),
        ))

    resp.validated_filter = vf.filters
    resp.sort = vf.sort
    resp.limit = vf.limit
    resp.unmapped = vf.unmapped
    resp.notes = vf.notes

    # 5. Run deterministic path (needs_input checks + search + loosening)
    t0 = time.perf_counter()
    result = run_from_filter(vf, schema, data)
    resp.search_time_ms = (time.perf_counter() - t0) * 1000

    resp.rows = result.rows
    resp.total_matches = result.total_matches
    resp.needs_input = result.needs_input
    resp.needs_input_kind = result.needs_input_kind
    resp.missing_essential = result.missing_essential
    resp.available_fields = result.available_fields
    resp.suggestions = [
        SuggestionOut(field=s.field, change=s.change, match_count=s.match_count)
        for s in result.suggestions
    ]
    resp.review = _to_review_out(result.review)

    resp.total_time_ms = (time.perf_counter() - total_start) * 1000
    resp.cache_stats = _cache.stats()
    return resp


class EditRequest(BaseModel):
    """User-edited filter submitted for re-validation and search."""
    vertical: str                  # Which vertical's schema to validate against
    filters: dict                  # The edited filter — untrusted input
    sort: str | None = None
    limit: int | None = None


@app.post("/api/search/edit", response_model=SearchResponse)
def api_search_edit(req: EditRequest):
    """Re-validate an edited filter and search. The review/edit entry point.

    The edited filter is untrusted — it goes through the same validator that
    sits between the LLM and search. Invalid fields are dropped, not passed
    through. A user editing "price ≤ 25000" to "price ≤ banana" is caught.
    """
    total_start = time.perf_counter()
    resp = SearchResponse()

    if req.vertical not in customer.verticals:
        resp.notes = [f"Vertical '{req.vertical}' not found."]
        resp.total_time_ms = (time.perf_counter() - total_start) * 1000
        return resp

    v = customer.verticals[req.vertical]
    resp.routed_vertical = req.vertical
    resp.route_mode = "edit"
    resp.route_confidence = "high"

    schema = load_schema(v["schema"])
    data = json.loads(Path(v["data"]).read_text(encoding="utf-8"))
    resp.display = schema.display

    t0 = time.perf_counter()
    result = run_from_edit(req.filters, schema, data, sort=req.sort, limit=req.limit)
    resp.search_time_ms = (time.perf_counter() - t0) * 1000

    resp.validated_filter = result.validated_filter
    resp.sort = result.sort
    resp.limit = result.limit
    resp.unmapped = result.unmapped
    resp.notes = result.notes
    resp.rows = result.rows
    resp.total_matches = result.total_matches
    resp.needs_input = result.needs_input
    resp.needs_input_kind = result.needs_input_kind
    resp.missing_essential = result.missing_essential
    resp.available_fields = result.available_fields
    resp.suggestions = [
        SuggestionOut(field=s.field, change=s.change, match_count=s.match_count)
        for s in result.suggestions
    ]
    resp.review = _to_review_out(result.review)

    resp.total_time_ms = (time.perf_counter() - total_start) * 1000
    return resp


@app.get("/")
def index():
    return FileResponse("app/static/index.html")


app.mount("/static", StaticFiles(directory="app/static"), name="static")
