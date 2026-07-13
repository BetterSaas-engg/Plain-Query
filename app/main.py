"""PlainQuery demo API — wraps the real engine for the sales UI."""

import json
import sys
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.plainquery.schema import load_schema
from src.plainquery.translator import translate
from src.plainquery.validator import validate
from src.plainquery.backend import search
from src.plainquery.loosen import suggest, Suggestion
from src.plainquery.router import load_customer, route

app = FastAPI(title="PlainQuery Demo")

CUSTOMER_PATH = "customers/expedia.json"
customer = load_customer(CUSTOMER_PATH)


class SearchRequest(BaseModel):
    query: str
    vertical: str | None = None  # None = inferred routing


class SuggestionOut(BaseModel):
    field: str
    change: str
    match_count: int


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


@app.post("/api/search", response_model=SearchResponse)
def api_search(req: SearchRequest):
    total_start = time.perf_counter()
    resp = SearchResponse()

    # 1. Route
    route_start = time.perf_counter()
    explicit_vertical = req.vertical if req.vertical and req.vertical != "all" else None
    route_result = route(req.query, customer, vertical=explicit_vertical)
    resp.route_time_ms = (time.perf_counter() - route_start) * 1000

    resp.routed_vertical = route_result.vertical
    resp.route_mode = route_result.mode
    resp.route_confidence = route_result.confidence
    resp.route_candidates = route_result.candidates

    if route_result.vertical is None:
        resp.total_time_ms = (time.perf_counter() - total_start) * 1000
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

    resp.validated_filter = vf.filters
    resp.sort = vf.sort
    resp.limit = vf.limit
    resp.unmapped = vf.unmapped
    resp.notes = vf.notes

    # 4b. Check needs_input — precedence: not_understood > missing_essential
    all_fields = [name for name, fd in schema.fields.items() if not fd.essential]

    if not vf.filters and bool(vf.unmapped):
        resp.needs_input = True
        resp.needs_input_kind = "not_understood"
        resp.available_fields = all_fields
        resp.total_time_ms = (time.perf_counter() - total_start) * 1000
        return resp

    missing_essential = [
        name for name, fd in schema.fields.items()
        if fd.essential and name not in vf.filters
    ]
    if missing_essential:
        resp.needs_input = True
        resp.needs_input_kind = "missing_essential"
        resp.missing_essential = missing_essential
        resp.available_fields = all_fields
        resp.total_time_ms = (time.perf_counter() - total_start) * 1000
        return resp

    # 5. Search (deterministic)
    t0 = time.perf_counter()
    rows = search(data, vf, schema)
    resp.search_time_ms = (time.perf_counter() - t0) * 1000

    resp.rows = rows
    resp.total_matches = len(rows)

    # 6. Loosen if zero results
    if not rows and vf.filters:
        suggestions = suggest(data, vf, schema)
        resp.suggestions = [
            SuggestionOut(field=s.field, change=s.change, match_count=s.match_count)
            for s in suggestions
        ]

    resp.total_time_ms = (time.perf_counter() - total_start) * 1000
    return resp


@app.get("/")
def index():
    return FileResponse("app/static/index.html")


app.mount("/static", StaticFiles(directory="app/static"), name="static")
