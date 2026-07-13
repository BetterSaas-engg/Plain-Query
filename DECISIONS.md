# PlainQuery — Decisions Log

## Locked in planning (from SPEC.md §10)

1. Engine is schema-agnostic; verticals are declarative schema files, no per-vertical engine code.
2. LLM translates only; a deterministic Validator (fail-closed) sits between translation and search and cannot be skipped.
3. Slice order: cars (tracer) → hotels (reuse-proof) → flights (later).
4. Slice one is local synthetic data + CLI. No real-site backend, no scraping.
5. Backend for slice one is a plain in-memory filter (no search engine dependency).
6. The schema (fields/types/enums/operators) is the reusable artifact and the product's core IP.

## Decided in build chat (2026-07-04)

7. **Python version: 3.14.** Only version installed on this machine. Verified the Anthropic SDK (sole non-stdlib dep) installs cleanly on 3.14 — no wheel friction. Original plan was 3.12 for safety, but installing a second Python just for this project isn't worth it.
8. **Unmapped terms: silent drop with note.** The engine runs the search with whatever *did* map and returns an `"unmapped"` list in the output (e.g. `["fun", "sunroof"]`). No ask-back — that implies multi-turn, which is P2. Nothing is silently swallowed; the user always sees what was ignored.
9. **LLM provider: Anthropic (Claude) via the Anthropic Python SDK.** One dependency beyond stdlib.
10. **Operators are field-local** (nested in each field), not a separate top-level block — single source of truth per field.
11. **OPEN:** revisit deriving operators from type (every int implies lte/gte/between/eq) once the validator exists — would remove per-field operator repetition across schemas. Deferred until there's a consumer to design against.
12. **Structured output via JSON-mode prompting** for the tracer. Tool-use / function-calling is the P1 production upgrade (deletes the malformed-JSON failure class entirely); switching should stay cheap — the translator is the only module that touches the API.
13. **Translation model: `claude-haiku-4-5-20251001`** (Haiku — fast/cheap, sufficient for schema-bounded translation). Tech debt: model name is hardcoded in `translator.py`; move to config/env in a later pass.
14. **String fields = case-insensitive substring match (free text); enum fields = exact case-insensitive equality.** Backend semantics now match the schema's declared field types.
15. **Schema defines both searchable fields and display columns.** The `display` key lists field names to show per result row. Hotels slice revealed that searchable fields and displayable fields are separate concerns — the CLI was the one leak where car-specific presentation was hardcoded. Display is optional; if absent, all row keys are printed generically.
16. **Each deployment is single-vertical** — one customer, one schema, one dataset. "Universal" refers to the engine being vertical-neutral (one codebase serves N customers), not to a single search spanning verticals. The product is a backend: the customer's own search consumes our validated filter output. Our `backend.py` is a reference implementation / demo harness, not the customer-facing deliverable — real integrations hand the validated filter to the customer's existing search.
17. **A customer may have multiple verticals** (e.g. Expedia: hotels + flights + car rentals). A customer is therefore a set of schemas, not one schema. Each individual search still executes against exactly one schema + one dataset — this doesn't change. What's new is vertical routing: deciding which schema a query belongs to. Two modes: (1) context-provided — the customer's app already knows the vertical from its own UI state and passes it in (zero-risk, the common case, first-class); (2) inferred — a single omnibox with no context requires classifying the query, which is a new LLM decision and a new hallucination surface. An inferring router MUST fail closed: it returns a vertical or admits ambiguity, never guesses. A misroute is worse than a bad filter (confidently wrong results from the wrong catalog). Routing is a separate slice; the engine itself needs no changes to support multi-vertical customers.
18. **Car rentals is a separate vertical from cars-for-sale.** Different schema, different data, different customer. Expedia's `car_rentals` (pickup_city, vehicle_class, price_per_day, supplier) ≠ AutoTrader's `cars` (make, model, year, mileage, sale price). The cars-for-sale schema + data remain for a different customer type.
19. **Filters vs. pricing inputs: the schema declares what exists (inventory constraints).** Rental duration, hotel check-in/check-out dates, and similar are pricing/booking inputs, not search filters — they change cost, not availability. They are deliberately out of schema scope. The same Ford Explorer is available whether you rent it for 1 day or 7; duration is a downstream multiplier in the booking layer.
20. **OPEN — no bool type.** The loader supports only enum/string/int. Two verticals have now wanted booleans (`breakfast_included`, `unlimited_mileage`), both worked around as `enum ["yes","no"]`. This is a real type-system gap that every customer with yes/no amenities will hit. Revisit adding a first-class `bool` type.
21. **OPEN — unmapped completeness is not verified.** The translator asks the LLM to report terms it couldn't map, but nothing enforces that the list is complete. Confirmed: "rent an SUV in Calgary for a week" silently dropped "rent" and "for a week" from both filters and unmapped. Filters are deterministically validated (bad ones get caught); unmapped completeness is a soft prompt-level contract with no deterministic backstop. Closing this gap would require the engine to reconcile the original query text against what the LLM reported. **Update:** mitigated via an explicit accounting rule in the translator's system prompt (see translator.py). The LLM is now instructed that every meaningful term must either map to a filter/sort or appear in unmapped — pure filler words (articles, prepositions) excluded. Verified: "for a week" now surfaces as unmapped; "under 25k" and "low mileage" do not false-positive. The gap is reduced but remains soft (prompt-enforced, not deterministic).

## 22. Scaling: LLM-per-query is the architecture's binding constraint — cache-first is the answer

**Status:** Constraint acknowledged and answered in design. Implementation deferred until there is customer traffic to justify it. (Do not build the cache pre-customer — it would optimize a system nobody is using.)

### The constraint (honest statement of the problem)

Every query currently makes at least one LLM call (translation), and up to two when routing is inferred. At consumer-search volumes this does not hold:

- **Latency is the deal-killer, not cost.** An LLM call is ~300ms–1s. Traditional faceted search returns in ~10–50ms. Naively deployed, PlainQuery would make a customer's search 10–50x slower. Search latency is directly tied to conversion, so this is the objection that ends the conversation in the room.
- **Cost.** A mid-size travel site at ~10 sustained searches/sec is ~864K queries/day. Even at Haiku pricing, an LLM call per query is a material line item — plausibly more than the customer's entire existing search infrastructure.

No amount of infrastructure fixes this: nothing makes an LLM call as fast as a hash lookup. The answer must be architectural.

### What already scales (unchanged)

The deterministic layers — `schema.py`, `validator.py`, `backend.py`, `loosen.py` — are pure functions with no I/O. They are stateless and horizontally scalable. Nothing in that design fights scale.

### The answer: cache the validated filter, call the LLM only on miss

The translator is a **pure function of `(normalized_query_text, schema_version)` -> validated filter.** Same sentence, same schema, same answer. Real search traffic follows a brutal power law: a large fraction of queries are exact repeats ("cheap hotel in Toronto", typed by thousands of people).

Therefore:

1. **Normalize** the query (lowercase, trim, collapse whitespace).
2. **Key** on `hash(normalized_text + schema_version)`.
3. **Cache the validated filter** (not the raw LLM candidate, and not the search results — results go stale as inventory changes; the *filter* does not).
4. **On hit:** skip the LLM entirely. The request runs through the deterministic path only — millisecond latency, zero marginal model cost.
5. **On miss:** call the LLM, validate, store, serve.

Expectation (flagged as an estimate, not a measurement): the majority of production traffic should be served without ever calling a model. This is what makes the product commercially viable at consumer scale — it converts LLM cost from per-query to per-*unique*-query.

**Cache invalidation is keyed on schema version.** When a customer changes their schema, the key changes, and stale filters (which may reference dropped fields or removed enum values) are naturally orphaned rather than served. This is why `schema_version` is part of the key and not an afterthought.

### Why our architecture earns this cheaply

The cache works **only because the translator is stateless and side-effect-free**, and because validation is a separate deterministic stage. A cached filter is safe to replay precisely because the validator already guaranteed it is schema-valid. The discipline held throughout the build (LLM translates, validator judges, no hidden state) is what buys this.

### Routing is already largely free

Context-provided routing is first-class (Decision on vertical routing): real customers pass the vertical from their own UI state (the tab the user is on). That is **zero LLM calls for routing** in the common case. Inferred routing — the single-omnibox case — is the only path that adds a second call, and it is cacheable on the same basis.

### Explicitly rejected / deferred

- **A deterministic "fast path"** that bypasses the LLM for simple queries: rejected for now. It would create two divergent translation paths that could disagree, which is a correctness risk for a marginal gain the cache already captures.
- **Caching search results** rather than filters: rejected. Inventory changes; filters do not.
- **Building the cache now:** deferred. Pre-customer, this is premature optimization. The requirement at this stage is a *credible answer*, not code — a technical buyer will ask "what's your p99?" and "what does this cost per query?", and the answer above is what that conversation needs.

### Open questions (for when this is built)

- Cache store: in-process (per instance) vs. shared (Redis)? Shared is required for hit rates to hold across horizontally scaled instances.
- TTL policy, if any — filters do not go stale, but customers may want a ceiling.
- Do we cache per-customer or globally? (Same sentence + same schema = same filter, so a global cache keyed on schema version is sound — but customers may object to any shared surface. Likely per-customer for the enterprise sale, even at the cost of hit rate.)
- Measure the real repeat-query rate before promising a hit-rate number to anyone.
