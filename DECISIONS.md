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
