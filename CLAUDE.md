# PlainQuery — Claude Code working context

## What this is
A schema-agnostic natural-language search engine. NL text → structured filter → deterministic
search → results. One engine; each vertical is a declarative schema file. Slice one = cars,
local synthetic data, CLI only.

## The rule that must never break
The LLM ONLY translates NL into a candidate filter object keyed to the active schema.
It never searches and its output is never trusted directly. A deterministic Validator
(src/plainquery/validator.py) sits between translation and search, enforces the schema,
FAILS CLOSED, and cannot be bypassed. Invalid/off-schema/out-of-range → reject, clamp, or
flag as unmapped. Never silently hallucinate a filter.

## Build discipline (owner's standards)
- Tracer bullet: thin end-to-end slice working before widening. Cars first, fully, then stop.
- One small step at a time; stop and wait for confirmation. No pre-empting next steps.
- DRY + orthogonality; keep code easy to delete. Reversibility is a design constraint.
- Flag every assumption, estimate, and opinion explicitly. Prove, don't assume.
- Highest-value tests are on the deterministic Validator — cover happy path, off-schema,
  out-of-range, and unmappable-term cases.
- Externalize decisions to DECISIONS.md as they're made.

## Slice-one definition of done
See SPEC.md §6–§7. Cars pipeline runnable via `python cli.py "<sentence>"`, prints results
AND the validated filter used, golden-query tests pass.

## Explicitly NOT in slice one
Scraping, real sites, semantic/vector search, multi-turn, auth, UI. (SPEC.md §8.)
