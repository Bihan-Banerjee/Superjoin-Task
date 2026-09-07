# Technical documentation

Living record of the stack, architecture, data model and pipeline. Updated as each
milestone lands rather than written at the end.

## Stack

| Layer | Choice | Why |
| --- | --- | --- |
| PDF parsing | PyMuPDF | Word-level bounding boxes, `search_for` for evidence highlighting, and table detection. The bounding boxes are what let a fact point at a rectangle on a page rather than just a page number. |
| API | FastAPI + Uvicorn | Async request handling, automatic OpenAPI, and native support for the streaming progress endpoint. |
| Storage | SQLite (WAL) + FTS5 | The whole knowledge layer is one portable file. No server to run, and it can be committed as an evaluation snapshot. FTS5 provides lexical retrieval without a second system. |
| ORM | SQLAlchemy 2.0 | Typed models, cascade behaviour, and raw SQL when the query is better expressed that way. |
| Embeddings | fastembed (ONNX, `bge-small-en-v1.5`) | Runs locally on CPU. Candidate generation happens for every pair of facts, so it has to be free and offline. |
| Language model | Gemini, OpenRouter or Ollama behind one interface | Extraction and adjudication only. Provider is configuration, not architecture. |
| Frontend | Vite + React + TypeScript | Fast dev loop, no framework-level opinions to fight. |
| PDF rendering | pdf.js | Renders the source page in the browser so evidence can be highlighted in place. |

## Repository layout

```
backend/app/core/       units, periods, text normalisation, hashing
backend/app/db/         SQLAlchemy models and engine setup
backend/app/llm/        provider abstraction, caching, structured output
backend/app/pipeline/   parse -> classify -> extract -> ground -> normalise -> link
backend/app/api/        HTTP surface
frontend/src/           review workbench UI
samples/                the assignment's starter corpora, committed for reproduction
```

## Core primitives

### Units (`app/core/units.py`)

Every quantity reduces to `(magnitude in a class base unit, unit class, currency)`.
Comparison only happens inside one class and one currency.

Currency conversion is deliberately not performed. Exchange rates have their own vintage
problem, and applying one would invent a number that appears in no document. Two facts in
different currencies are reported as differing on the `currency` dimension instead.

Indian scales (lakh, crore) sit alongside international ones because this corpus mixes
them freely — often on the same page.

Tolerance is not a flat percentage. `rounding_tolerance` widens the band to match the
significant figures actually present, so "8,142 Cr" and "81,419.7 million" corroborate
rather than registering a difference.

### Periods (`app/core/periods.py`)

Every period resolves to a dated interval plus a kind. Two facts share a period only when
their intervals are identical; otherwise the relationship between the intervals
(containment, partial overlap, disjoint) is what the reconciler reports.

Fiscal conventions are per document. Under the Indian convention `FY24`, `FY 2023-24`,
`2023-24`, `2024/25` and `the year ended March 31, 2024` all resolve correctly, and
`Q4 FY24` is recognised as contained by `FY24` rather than equal to it.

### Text (`app/core/text.py`)

Normalises ligatures, soft hyphens, line-break hyphenation, non-breaking spaces and curly
punctuation while carrying an index map back to the original offsets. Matching happens in
normalised space; results are translated back so a fact can still point at exact
characters in the source page.

## Data model

See `backend/app/db/models.py`. Notes on the shape:

- Facts store both the surface form (what the document said) and the normalised form (what
  it means). Dropping either one breaks something — the surface is what makes evidence
  verifiable, the normal form is what makes comparison possible.
- `measures`, `entities` and `qualifier_keys` are registries. New kinds of facts create
  rows, not migrations.
- `rejections` is a table, not a log. A discarded candidate fact is the most useful signal
  the pipeline can give about its own reliability.
- `llm_calls` records every model call so cost and cache-hit rates are measured rather
  than asserted.

## Pipeline

_Documented as each stage lands._

## API

_Documented as each endpoint lands._
