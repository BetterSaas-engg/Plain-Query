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
19. **~~Filters vs. pricing inputs~~ REVISED: Dates belong in the schema; availability matching is the customer's concern.** The original claim that dates aren't filters because they don't change what exists was wrong — real availability is date-dependent. However, PlainQuery is a backend that emits structured filters into the customer's search system. Dates (check_in, check_out, depart_date, pickup_date, dropoff_date) are now schema fields with a `date` type and `essential: true`. We translate and emit them; matching them against real inventory calendars is the customer's system's job. Our reference backend (`backend.py`) skips date filters since our demo data is date-free — this is honest, not a gap. Rental duration remains out of scope: it affects total pricing, not what inventory exists.
20. **OPEN — no bool type.** The loader supports only enum/string/int. Two verticals have now wanted booleans (`breakfast_included`, `unlimited_mileage`), both worked around as `enum ["yes","no"]`. This is a real type-system gap that every customer with yes/no amenities will hit. Revisit adding a first-class `bool` type.
21. **OPEN — unmapped completeness is not verified.** The translator asks the LLM to report terms it couldn't map, but nothing enforces that the list is complete. Confirmed: "rent an SUV in Calgary for a week" silently dropped "rent" and "for a week" from both filters and unmapped. Filters are deterministically validated (bad ones get caught); unmapped completeness is a soft prompt-level contract with no deterministic backstop. Closing this gap would require the engine to reconcile the original query text against what the LLM reported. **Update:** mitigated via an explicit accounting rule in the translator's system prompt (see translator.py). The LLM is now instructed that every meaningful term must either map to a filter/sort or appear in unmapped — pure filler words (articles, prepositions) excluded. Verified: "for a week" now surfaces as unmapped; "under 25k" and "low mileage" do not false-positive. The gap is reduced but remains soft (prompt-enforced, not deterministic).

## 22. Essential fields and the ask-instead-of-search rule

**Schema fields can be marked `essential: true`.** A vertical that can't meaningfully search without certain fields (hotels without dates, flights without a departure date) declares them essential. The engine checks after validation: if any essential field is missing from the validated filter, or if the filter is empty while unmapped terms exist, the engine returns a `needs_input` result instead of running the search. This is deterministic — no LLM call to decide whether to ask. The UI renders a clean prompt ("When are you traveling?") instead of dumping arbitrary results when nothing was understood.

Three flaws closed by one mechanism:
- "a romantic place with a rooftop pool" no longer dumps 25 cheap hostels — it admits it understood nothing useful and shows what can be searched.
- Hotels, flights, and car rentals now have date fields (check_in/check_out, depart_date, pickup_date/dropoff_date) and require them.
- The `date` type is a new first-class type in the schema loader (ISO YYYY-MM-DD), validated fail-fast like every other type.

**Scope boundary:** We translate and emit date filters. Matching them against real availability calendars is the customer's system's job. Our reference backend skips date filters since our demo data is date-free.

## 23. Scaling: LLM-per-query is the architecture's binding constraint — cache-first is the answer

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

### Open questions (resolved in Decision #24)

- ~~Cache store~~ → in-process LRU. See #24.
- ~~TTL policy~~ → none. See #24.
- ~~Per-customer or global~~ → per-customer. See #24.
- Measure the real repeat-query rate before promising a hit-rate number to anyone. (Still open — requires production traffic.)

## 24. Filter cache: implemented — in-process LRU, per-customer, no TTL

**Status:** Built and tested. 21 deterministic tests. Cache hit skips both router and translator — zero LLM calls on hit. Measured latency: see README.md performance table.

### What was built

`src/plainquery/cache.py` — an in-process LRU cache behind an abstract `FilterCacheBackend` protocol. The engine, CLI, and API never touch the cache implementation directly; they go through the protocol. A shared store (Redis, Memcached) can drop in by implementing the same interface — no changes to callers or engine.

**Cache hit path:** normalize query → hash key → lookup → reconstruct `ValidatedFilter` → `run_from_filter()` (deterministic search + loosening). Zero LLM calls. The ~10ms path.

**Cache miss path:** route (LLM) → translate (LLM) → validate (deterministic) → store in cache → search. Same as before, plus one cache write.

**Key structure:** `sha256(customer_name + schema_fingerprint + normalized_query)`.
- `customer_name` — per-customer isolation (see below).
- `schema_fingerprint` — `sha256` of all schema file contents for the customer. Rotates automatically when any schema file changes.
- `normalized_query` — lowercased, trimmed, whitespace-collapsed.

### Resolved: store — in-process LRU

In-process `OrderedDict`-based LRU, max 1024 entries (configurable). Simple, zero-dependency, sufficient for single-process and demo.

**Limitation acknowledged:** in-process means each process/instance has its own cache. Hit rates do not hold across horizontally scaled instances. When multi-instance deployment is needed, swap `LRUFilterCache` for a `Redis`-backed implementation of `FilterCacheBackend`. The interface is designed for this — the swap is one line.

### Resolved: TTL — none

A validated filter is a pure function of `(query_text, schema_version)`. The same input always produces the same output. It cannot go stale — the translation is deterministic given the same prompt and schema, and the validator is fully deterministic.

Schema changes are handled by key rotation, not TTL: the `schema_fingerprint` is part of the cache key. When a customer deploys a schema change (new fields, changed enums, removed operators), the fingerprint changes, every existing key becomes a miss, and stale entries are naturally orphaned — they sit in the LRU until evicted, never served.

TTL would add complexity (expiry threads, configuration surface) to solve a problem that doesn't exist. If a customer later demands a ceiling for compliance reasons, adding TTL to the `FilterCacheBackend` interface is trivial.

### Resolved: scope — per-customer keying

Globally-keyed caching would be correct: `(same query + same schema = same filter)` regardless of which customer owns the schema. It would also yield higher hit rates (two customers with identical schemas would share cache entries).

**Rejected in favor of per-customer keying.** Enterprise security teams object to any cross-tenant shared surface, even when the shared data is a search filter derived from a public schema. The objection is not technical — it's organizational. The trade:
- **Correctness:** neutral. Per-customer produces identical filters to global.
- **Hit rate:** negative. Two customers with identical schemas maintain separate caches.
- **Sale:** positive. "Your data never touches another customer's cache" is a sentence that ends the security review faster.

This is a deliberate trade: we lose some cache efficiency to remove a sales objection. The hit-rate cost is marginal (most customers have unique schemas anyway), and the sales cost of the alternative is real.

### What is NOT cached

- **Search results.** Inventory changes; filters do not. (Unchanged from #23.)
- **Translation errors.** A failed API call may be transient. Caching the failure would serve a bad result on retry.
- **Ambiguous routing.** If the router can't classify a query, that's not cached — the user will re-submit with a vertical hint, which is a different cache key.

### Two independent cost levers

The system has two independent mechanisms that reduce LLM cost. They are not additive in a simple way — each applies to a different subset of traffic — and neither should be quoted as a single combined-savings number. Both must be measured against production traffic.

**Lever 1: PlainQuery filter cache (ours).** Eliminates the LLM call entirely on repeat queries. A cache hit costs zero tokens. The savings scale with the customer's repeat-query rate, which follows a power law in real search traffic but must be measured, not assumed.

**Lever 2: Anthropic prompt caching (provider-side).** The translator's system prompt is identical for every query within a vertical — it contains the schema, field definitions, rules, and output format. On a PlainQuery cache *miss* (where the LLM call actually happens), Anthropic's prompt caching can serve the schema-prefix portion from their cache at ~10% of the standard input token rate. Write premium is 1.25x (5-min TTL) to 2x (1-hr TTL) on the first request that populates the cache. This reduces the per-miss cost, not the per-hit cost (hits don't call the LLM at all).

**Caveat:** Prompt-cache savings require sustained traffic to keep Anthropic's cache warm within the TTL window. At production volume with steady query flow, the prompt cache stays warm and nearly every miss benefits. At low-traffic or bursty volumes, the prompt cache expires between requests and the savings don't materialize. This lever applies at scale, not during demos.

**How they compose:** Lever 1 determines *how many* LLM calls happen (repeat-query rate). Lever 2 determines *what each call costs* (prompt-cache hit rate at the provider). They are orthogonal — improving one does not affect the other. A customer's total cost depends on both their repeat-query distribution and their traffic volume/steadiness. Quote them as separate levers to be measured, not as a combined discount.

### Engine changes

`engine.py` now exposes `run_from_filter(vf, schema, data)` — the deterministic-only entry point that cache hits use. `run()` (the full pipeline) calls `run_from_filter` internally after translation and validation. No duplication.

## 25. Deferred feature backlog (v2 / v3)

**Status:** Evaluated, deliberately not built. The product is buyer-ready. The scarce resource at this stage is a signed pilot, not more features. These are logged so they're not lost, sorted by when to revisit.

**Guiding note:** these are deferred on purpose. The risk at this stage is building cool features instead of selling a ready product.

### Part of the pitch now (build nothing)

**Query-to-inventory gap analytics.** Every zero-result validated query is a structured record of demand the catalog can't satisfy. Near-zero engineering — the validated filter + zero-result signal already exist; the only work is logging them to a store. Potentially a second revenue line / data product independent of the search integration. Mention in sales conversations as a byproduct; build the logging when a customer wants the data.

**Cross-vertical bundling** ("weekend trip to Vancouver" → flight + hotel + car). High demo/vision value, high build cost (multiple filters, date coherence across verticals, re-opens the routing ambiguity problem that Decision #17 carefully closed). Use as a vision slide. Do NOT build speculatively.

### v2 — build once there's a paying customer or to strengthen a specific demo

**Conversational refinement.** Multi-turn: "actually under $20k" mutates the prior validated filter instead of re-translating from scratch. Cheap for numeric mutations (deterministic patch to the cached filter); fuzzy changes ("blue instead") still need an LLM call to resolve what "instead" refers to — do not claim zero re-translation. Strongest candidate to build before a big demo, only if it doesn't delay the meeting.

**Saved-search alerts.** The validated filter becomes a stored subscription predicate; new inventory is checked against stored filters and the user is notified on match. The concept is a small lift (reuse the filter as a predicate). The PRODUCTION system is not — needs a matching pipeline running on every inventory update, notification/delivery infrastructure, unsubscribe flow, and a subscription store. High retention leverage once deployed. Best v2 feature, but scope it honestly — the filter reuse is trivial; everything around it is real engineering.

### v3 — after a customer and their real data exist

**Voice input.** Speech-to-text upstream of the existing pipeline. The validator matters more here (noisy transcripts produce worse candidates, and the fail-closed behavior is what makes it safe). Trivial to add to the architecture; low marginal value until there's a customer whose users actually want voice search.

**Intent classification** ("browse" vs "buy" vs "compare"). A lightweight classifier before translation that changes default sort, layout, or result count without touching filter logic. Useful for conversion optimization; irrelevant until there's real traffic to optimize.

**Seller-side tagging assist.** Feed common unmapped terms back to merchants to close the demand-language/supply-metadata gap. If users keep searching "pet-friendly" and the schema doesn't have it, surface that signal to the merchant so they can add the field. Mainly relevant to marketplaces (a conditional target) — less useful for single-brand catalogs where the schema is internally controlled.
