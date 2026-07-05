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
