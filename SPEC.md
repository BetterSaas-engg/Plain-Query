# NL-Search Engine — Build Spec (Slice One Seed)

> Working codename: **PlainQuery** (placeholder — rename freely).
> This document is the handoff seed for the Claude Code build session. Paste it into the repo as `SPEC.md`, and use the `CLAUDE.md` block at the bottom as the repo's `CLAUDE.md`.

---

## 1. One-line pitch

Natural language in → structured filters out → run against the vertical's existing faceted search → results back. One **schema-agnostic engine**; each vertical (cars, hotels, flights) is just a schema plugged in.

Market reality (eyes open): conversational/NL search already ships at Hilton, Wyndham, Perplexity, and as middleware from Algolia/Coveo/Meilisearch/Klevu. This is a **red ocean**, entered deliberately. The wedge being tested: a **lighter, schema-driven overlay** that maps NL onto a site's *existing* filters without re-indexing its catalog, starting in the **deterministic, spec-driven** car vertical that the ecommerce-search incumbents serve least well.

---

## 2. The one architectural principle (non-negotiable)

**The LLM never searches. It only translates NL into a structured filter object that conforms to a declarative schema.** Everything after translation is deterministic:

```
user text
  → [LLM Translator]  (NL → candidate filter JSON, keyed to the active schema)
  → [Validator]       (deterministic middleware: coerce types, enforce enums/ranges,
                       drop/flag unknown fields — the model cannot skip this)
  → [Search Backend]  (deterministic query against the dataset)
  → results
```

This mirrors the shim-broker standard: the Validator is middleware **outside model control**, it **fails closed** (an invalid filter is rejected or safely narrowed, never silently trusted), and every translation + rejection is logged. The Translator is the risky/novel part; the backend is boring on purpose.

Why this matters commercially: the **schema definition is the reusable, sellable artifact.** Prove the engine reads *any* schema, and "plug and play" stops being a slogan.

---

## 3. The declarative schema (the crux)

Each vertical is one declarative file the engine consumes. No engine code changes per vertical. Example (cars, illustrative — trim to what the synthetic data supports):

```json
{
  "vertical": "cars",
  "fields": {
    "make":      { "type": "enum",  "values": ["Honda", "Toyota", "Ford", "BMW", "..."] },
    "model":     { "type": "string" },
    "trim":      { "type": "string" },
    "year":      { "type": "int",   "min": 1990, "max": 2026 },
    "price":     { "type": "int",   "unit": "CAD", "min": 0 },
    "mileage":   { "type": "int",   "unit": "km",  "min": 0 },
    "body_type": { "type": "enum",  "values": ["sedan","suv","truck","hatchback","coupe"] },
    "fuel":      { "type": "enum",  "values": ["gas","hybrid","ev","diesel"] },
    "color":    { "type": "enum",  "values": ["red","blue","black","white","silver","..."] }
  },
  "operators": {
    "price":   ["lte","gte","between"],
    "mileage": ["lte","gte","between"],
    "year":    ["eq","gte","lte","between"]
  },
  "sort": ["price_asc","price_desc","mileage_asc","year_desc"],
  "defaults": { "sort": "price_asc", "limit": 25 }
}
```

A validated filter object for *"red Honda Civic under 25k, low mileage"* should come out roughly:

```json
{
  "make": "Honda", "model": "Civic", "color": "red",
  "price": { "op": "lte", "value": 25000 },
  "sort": "mileage_asc"
}
```

Note the reasoning to preserve: "low mileage" → `sort: mileage_asc`, not an invented numeric threshold. The Translator should prefer sorts/known enums over hallucinated cutoffs, and surface anything it couldn't map (see acceptance criteria).

---

## 4. Repo layout (tracer-bullet)

```
plain-query/
├── CLAUDE.md
├── SPEC.md                  # this file
├── DECISIONS.md             # append decisions as we lock them
├── schemas/
│   └── cars.json            # slice-one schema
├── data/
│   └── cars.json            # synthetic car rows (a few thousand)
├── src/plainquery/
│   ├── translator.py        # NL → candidate filter (LLM call)
│   ├── validator.py         # deterministic middleware, fail-closed
│   ├── backend.py           # deterministic search over the dataset
│   ├── schema.py            # load + parse a schema file
│   └── engine.py            # wire translate → validate → search
├── tests/
│   ├── test_validator.py    # the highest-value tests (deterministic)
│   └── test_engine_e2e.py   # golden NL queries → expected filters/results
└── cli.py                   # `python cli.py "red honda civic under 25k"`
```

---

## 5. Slice plan (sequencing IS the demo)

- **Slice 1 — CARS (tracer bullet).** Full pipeline end to end against synthetic local data. CLI only. Done = acceptance criteria below pass.
- **Slice 2 — HOTELS (reuse-proof).** Whole job: drop in `schemas/hotels.json` + `data/hotels.json` and show the *same engine code* works with near-zero new logic. If Slice 2 needs engine changes, Slice 1's abstraction failed — that's the real test.
- **Slice 3 — FLIGHTS (later).** Third schema. Deferred until 1–2 are solid.

---

## 6. Slice-one requirements

**P0 (cannot ship without):**
- Load a schema file and a dataset file.
- Translate an NL string into a candidate filter object via one LLM call.
- Validate deterministically: coerce types, enforce enums/ranges/operators, **reject or flag** anything off-schema. Fail closed.
- Run the validated filter against the dataset and return ranked rows.
- A CLI that takes a sentence and prints results + the filter it used (transparency).
- Golden-query tests: ~15 NL inputs with expected validated-filter outputs.

**P1 (fast follow, not slice one):** synonyms/aliases in schema; "no results → loosen suggestion"; a minimal web UI.

**P2 (design for, don't build):** real-site backend adapter; semantic/vector matching for fuzzy queries; multi-turn refinement ("actually make it blue").

## 7. Acceptance criteria (slice one done)

- Given the cars schema and dataset, when I run `python cli.py "red Honda Civic under 25k, low mileage"`, then results are all red Civics ≤ $25,000 sorted by mileage ascending, and the printed filter matches.
- Given an unmappable term (e.g. *"a fun car"*), when translated, then the Validator does **not** invent a filter — the engine returns either an unfiltered/defaulted result set **or** a clear "couldn't map: 'fun'" note. Never a silently hallucinated field.
- Given an out-of-range value (*"year 3000"*), when validated, then it's rejected/clamped per schema, never passed through.
- Given an off-schema field (*"with a sunroof"* when `sunroof` isn't in the schema), then it's dropped and surfaced as unmapped, not crashed on.

---

## 8. Non-goals (slice one)

Scraping or hitting any real site; semantic/vector search; multi-turn conversation; auth/identity; UI polish; more than one vertical. These are deferred by design, not forgotten (they live in P1/P2).

---

## 9. Tech + environment

- Windows 11 + PowerShell + VS Code + Claude Code.
- **Python version — flag (opinion + risk):** you have 3.14 installed. 3.14 is very new; some libraries may not yet ship wheels for it. Recommend building slice one in a **3.12 or 3.13 venv** to avoid dependency friction, unless a quick check shows your needed libs install cleanly on 3.14. Decide before writing code.
- Dependencies deliberately minimal: standard lib + the Anthropic SDK for the one LLM call. No search framework in slice one (the backend is a plain in-memory filter over a few thousand rows — fast enough, and it isolates the thing under test).

---

## 10. DECISIONS.md — seed entries (locked in this planning chat)

1. Engine is schema-agnostic; verticals are declarative schema files, no per-vertical engine code.
2. LLM translates only; a deterministic Validator (fail-closed) sits between translation and search and cannot be skipped.
3. Slice order: cars (tracer) → hotels (reuse-proof) → flights (later).
4. Slice one is local synthetic data + CLI. No real-site backend, no scraping.
5. Backend for slice one is a plain in-memory filter (no search engine dependency).
6. The schema (fields/types/enums/operators) is the reusable artifact and the product's core IP.

Open (decide in build chat): exact synthetic-data volume; Python 3.14 vs 3.12/3.13 venv; how "unmapped terms" are surfaced to the user (silent-drop-with-note vs. ask-back).

---

## CLAUDE.md (paste as the repo's CLAUDE.md)

```markdown
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
```
