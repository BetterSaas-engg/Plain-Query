# PlainQuery

A schema-agnostic natural-language search backend. Natural language goes in, structured filters come out, and the customer's existing search runs them. PlainQuery does not replace the customer's search engine, index, or ranking — it sits in front, translating what users say into what the search already understands. The customer keeps their infrastructure. If PlainQuery fails, it fails to an empty filter or an honest "I don't understand" — it is architecturally incapable of making their search worse.

## The one rule

The LLM only translates. It produces a candidate filter that may be wrong. A deterministic validator sits between translation and search, enforces the schema, fails closed, and cannot be bypassed. Invalid fields are dropped with an explanation. Nothing is silently trusted.

```
query
  -> [Router]        pick the vertical (or refuse if ambiguous)
  -> [Translator]    NL -> candidate filter (one LLM call)
  -> [Validator]     deterministic: enforce schema, drop bad fields, fail closed
  -> [Search]        deterministic: filter, sort, limit against the dataset
  -> results + the filter used + what was dropped and why
```

## Schema-agnostic — and here's the proof

One engine, four verticals: cars-for-sale, hotels, flights, car rentals. Each vertical is a JSON schema file declaring fields, types, enums, operators, sorts, and defaults. Hotels, flights, and car rentals were each added after the engine was built. Each required zero engine code changes — only a schema file and a data file. The engine was modified once, to add the `date` type and `essential` field metadata, both schema-driven.

## What it refuses to do

These are the actual behaviors, tested and demonstrated. This is the product's core value proposition: a search layer that would rather say nothing than say something wrong.

**Unmappable terms — invents nothing:**
```
> "a fun car"

FILTER USED: {}
UNMAPPED TERMS: ['fun']
```
"Fun" doesn't correspond to any field. The engine surfaces it as unmapped and runs an unfiltered search. It does not hallucinate a filter for "fun."

**Out-of-range values — rejected, not clamped:**
```
> "Honda from year 3000"

FILTER USED: { "make": "Honda" }
NOTES: Field 'year': value 3000 is outside the available range 2005-2026 -- ignored.
```
Year 3000 is dropped entirely. It is not silently clamped to 2026 — that would invent intent the user never expressed.

**Ambiguous routing — refuses to guess:**
```
> "something cheap in Toronto"

ROUTING: Could not determine vertical for Expedia
Candidates: ['hotels', 'flights', 'car_rentals']
Re-run with --vertical <name> to specify.
```
"Cheap in Toronto" could be a hotel, a flight, or a car rental. The router says so and asks, rather than picking one and returning confidently wrong results from the wrong catalog.

**Nothing understood — admits it:**
```
> "a romantic place with a rooftop pool"

NOT UNDERSTOOD:
  Could not map any part of the search to our filters.
  Unmapped terms: ['romantic', 'rooftop pool']
  Available fields: ['city', 'neighborhood', 'name', 'star_rating', ...]
```
No terms matched any filter. Instead of dumping arbitrary results, the engine says what it can filter on.

**Non-temporal language — will not invent dates:**
```
> "cheap hostel in Toronto"

FILTER USED: { "city": "Toronto", "property_type": "hostel" }
NEEDS INPUT: Missing essential fields: ['check_in', 'check_out']
```
"Cheap" is a price signal, not a date. The engine maps what it understood, identifies that dates are missing, and asks — rather than fabricating today's date to fill the gap.

## Zero results — deterministic loosening

When no rows match, the engine computes which single constraint to relax and how many results that would yield. These are facts from the data, not LLM guesses:

```
> "5 star resort in Vancouver under 400 a night with great reviews"

(no matches)
Try loosening your search:
  - Drop 'price_per_night' <= 400 -> 10 matches
  - Drop 'property_type' = resort -> 1 matches
  - Raise to price_per_night <= 641 -> 1 matches
```

## Honest limitations

- **Dates are emitted, not matched.** PlainQuery translates date filters (check-in, departure, pickup) and includes them in the validated output. Matching against real availability calendars is the customer's system's job — we don't model inventory.
- **No bool type.** The schema supports `enum`, `string`, `int`, and `date`. Two verticals needed booleans (`breakfast_included`, `unlimited_mileage`), both worked around as `enum ["yes", "no"]`. A first-class `bool` type is an open item.
- **Synthetic demo data.** The four datasets (~2,000 rows each) are seeded synthetic data for demonstration. Real deployments would connect to the customer's catalog.
- **The filter cache is designed but not built.** [Decision #23](DECISIONS.md) describes caching validated filters keyed on `(normalized_query, schema_version)` to eliminate the LLM call on repeat queries. It is not yet implemented — there is no customer traffic to justify it. The cache hit rate must be measured, not promised.
- **Unmapped completeness is prompt-enforced, not deterministic.** The translator is instructed to report every term it couldn't map, but nothing guarantees completeness. Mitigated by an explicit accounting rule; the gap is reduced but soft.

## Performance and cost

Measured on the demo (Haiku model, single-instance, no cache):

| Layer | Latency |
|-------|---------|
| Router (LLM, warm) | 600-1400ms typical |
| Translator (LLM, warm) | 600-1400ms typical |
| Validator (deterministic) | < 1ms |
| Search (deterministic) | < 15ms |
| Loosening (deterministic) | < 50ms |

All latency and cost live in the model calls. The deterministic layers are stateless pure functions.

**Two independent cost levers** ([details in DECISIONS.md](DECISIONS.md)):

1. **PlainQuery filter cache** eliminates the LLM call entirely on repeat queries (cache hit ≈ 7ms measured, zero tokens). Converts cost from per-query to per-unique-query. The savings scale with the customer's repeat-query rate, which must be measured against production traffic.

2. **Anthropic prompt caching** reduces the token cost of cache *misses* (where the LLM call still happens). The translator's system prompt (schema, field definitions, rules) is identical across all queries for a vertical. Anthropic caches this prefix and bills reuse at ~10% of the standard input rate (5-min/1-hr TTL, 1.25x–2x write premium on population). Requires sustained traffic to keep the provider cache warm — applies at production volume, not low-traffic demos.

These levers are orthogonal: lever 1 controls *how many* LLM calls happen, lever 2 controls *what each call costs*. Neither should be quoted as a combined discount — both depend on the customer's traffic pattern and must be measured independently.

## Running it

**Requirements:** Python 3.14 (built and tested on 3.14 only; earlier versions may work but are not verified), an [Anthropic API key](https://console.anthropic.com/).

```bash
# Setup
python -m venv .venv
source .venv/bin/activate        # or .venv\Scripts\activate on Windows
pip install anthropic python-dotenv fastapi uvicorn

# API key
echo "ANTHROPIC_API_KEY=sk-..." > .env

# CLI
python cli.py "red Honda Civic under 25k, low mileage"
python cli.py "cheap hostel in Toronto" --customer customers/expedia.json
python cli.py "flight to Lisbon in June under 900" --customer customers/expedia.json

# Demo UI
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# Open http://127.0.0.1:8000

# Tests (44 tests, all deterministic, no LLM calls)
python -m pytest -v
```

## Repo map

```
src/plainquery/
  schema.py        Schema loader. Fail-fast validation of field types, enums, ranges, operators.
  translator.py    NL -> candidate filter via one LLM call. Does NOT validate.
  validator.py     Deterministic middleware. Enforces schema, drops bad fields, fails closed.
  backend.py       Reference in-memory search. Filter, sort, limit. Customer replaces this.
  engine.py        Pipeline orchestrator. Wires translate -> validate -> search. Checks essential fields.
  loosen.py        Zero-results suggestions. Deterministic: tries relaxations, reports real match counts.
  router.py        Vertical routing. Context-provided (no LLM) or inferred (one LLM call, fail-closed).

schemas/           Declarative schema files (cars, hotels, flights, car_rentals).
data/              Synthetic datasets (~2,000 rows each, seeded generators in scripts/).
customers/         Customer configs mapping verticals to schemas + data.
app/               FastAPI demo app + single-page frontend.
cli.py             CLI entry point.
tests/             44 deterministic tests (validator, loosen, router, engine behavior).
```

See [SPEC.md](SPEC.md) for the original build spec and [DECISIONS.md](DECISIONS.md) for every architectural decision and its rationale.
