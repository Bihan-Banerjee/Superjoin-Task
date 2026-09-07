# Fact Knowledge Layer

Reads PDFs, extracts facts that are grounded in verifiable evidence, and works out where
those facts agree, disagree, or only appear to disagree because they were measured over
different periods, scopes, units or data vintages.

The problem is not extraction. It is that the same number is written many ways. Delhivery's
FY24 annual report states revenue as **81,419.7** under a heading reading *"All amounts in
Indian Rupees in million"*. Their Q4 FY24 earnings deck states the same figure as **8,142**,
in crore. A system comparing text reports a ten-fold contradiction. This one reports
corroboration, names the reason, and shows both sentences highlighted on their source pages.

Nothing in the pipeline is specific to the starter documents: no hard-coded facts, filenames,
measures or schemas. Page routing uses structural signals only, prompts describe *kinds* of
things to look for, and the measure vocabulary is a database table that grows as documents
introduce new measurements.

> **Status: in progress.** The pipeline, API, interface and test suite are complete and
> passing. The corpus run, the committed evaluation snapshot, and the measured results below
> are pending a model API key. Sections marked _(pending run)_ will be filled from the real
> run rather than estimated.

## Setup and Run Instructions

Requires Python 3.10+ and Node 18+.

### 1. Backend

```bash
cd backend
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1
# Windows Git Bash
source .venv/Scripts/activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

The first run downloads the embedding model (about 130 MB) to your user cache. It is a local
ONNX model, needs no credentials, and is only downloaded once.

### 2. Configuration

```bash
cp ../.env.example .env
```

Nothing is required to browse a restored snapshot. To process new PDFs, set one provider key
in `backend/.env`:

```ini
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-key-here
```

`LLM_PROVIDER` also accepts `openrouter` (any OpenAI-compatible model), `ollama` (fully
local, no credentials) and `replay` (recorded responses, no network). Concurrency and request
rate default to values inside the Gemini free tier; raising them is allowed and warned about
rather than blocked.

### 3. Run

```bash
# terminal 1
cd backend && .venv/Scripts/python -m uvicorn app.main:app --port 8000

# terminal 2
cd frontend && npm install && npm run dev
```

Open http://localhost:5173 and drop a PDF onto the Documents page.

### Reviewing without a key

```bash
cd backend && .venv/Scripts/python scripts/snapshot.py load
```

This restores the committed snapshot of a real run over the six starter documents. The
interface then shows actual facts, evidence and relationships with no credentials and no
processing. `backend/seed/export.json` holds the same content as readable, diffable data.

To re-run the pipeline itself offline, set `LLM_PROVIDER=replay`: every model response from
that run is committed under `backend/seed/replay/`, keyed by request. A request with no
recording fails loudly rather than being invented, so a replay cannot quietly diverge from
the run it reproduces.

### Command line

```bash
cd backend
.venv/Scripts/python scripts/ingest.py ../samples --recursive   # ingest a corpus
.venv/Scripts/python scripts/evaluate.py metrics                # what the run produced
.venv/Scripts/python scripts/evaluate.py audit                  # re-verify every fact
.venv/Scripts/python scripts/evaluate.py sample --size 40       # manual precision worksheet
.venv/Scripts/python scripts/snapshot.py export                 # write the snapshot
.venv/Scripts/python -m pytest                                  # 127 tests, ~50s
```

## Video Demo

_(pending run)_

## Approach

### A fact is a normalised claim, not a sentence

```
Fact = (subject, measure, qualifiers, period) -> value[unit, scale, currency]
       + provenance(document, page, character span, bounding boxes, verbatim quote)
       + basis(actual / estimate / projection / revised / restated / pro forma)
```

Comparison happens on the normalised tuple and never on text. Both forms are stored: dropping
the surface form would make the evidence unverifiable, dropping the normal form would make
comparison impossible.

### Evidence is verified, not trusted

The model returns a verbatim quote. The pipeline then checks it independently — locates it in
the page's raw text (tolerating ligatures, soft hyphens and line-break hyphenation), confirms
the value appears **in the source page** within that span, and resolves the span to rectangles
on the rendered page.

That check reads the page and never the model's own quote. Searching the quote too would let a
fabricated figure corroborate itself: write any number into the quote and it passes. That was a
real defect here, caught by the end-to-end tests.

Anything that fails is written to a rejection table with a typed reason — not logged. That
table is the measurement of how much the extractor got wrong, and it is where the required
failure case comes from.

### Rules decide first; the model handles the residue

A deterministic engine classifies pairs by period, unit scale, currency, scope, segment and
basis. It escalates only when it cannot decide — an unresolved period, an implausibly large
gap, low extraction confidence, or a non-numeric fact where agreement is a question about
language rather than arithmetic.

Rules are free, so far more pairs can be compared than a model budget allows; reproducible, so
the same corpus always yields the same relationships; and explainable in a way that matters —
*"these differ because one is stated in crore and the other in millions"* names a checkable
reason. Every relation records whether a rule or the model decided it, and which rule.

Agreement tolerance is derived from the significant figures each value was written at, not a
flat percentage. A flat 1% band would call 6.5% and 6.6% GDP growth the same figure while
still needing to accept 8,142 Cr against 81,419.7 million.

### The schema is data

Measures, entities and qualifier dimensions live in tables and grow as documents introduce
them. Resolution escalates cheapest-first: exact alias, then a local embedding, then a batched
model call for the uncertain band only.

When uncertain, the registry **creates rather than merges**. Two rows for one measure loses
some links. One row for two measures manufactures contradictions between numbers that were
never the same number — confidently wrong output, which is much worse. Merges across unit
classes are blocked outright, so "revenue" can never absorb "revenue growth".

### Reading order is reconstructed

PDFs store text in content-stream order, not reading order. A two-column central bank review
interleaves its columns line by line; a chart slide separates every number from its label.
Page 9 of the Delhivery deck extracts as:

```
59% 63% 62% 24% 16% 19% ... 7,054 7,224 8,142 FY22 FY23 FY24 Express Parcel PTL ...
```

Every number there is real and none is attributable. Columns are recovered from an occupancy
histogram whose threshold is relative to the page's own density (a gutter is not empty — a
centred heading crosses it), and full-width lines are detected by continuity so a running head
is not cut into fragments. Chart slides are split into vertical panels and grouped by shared
horizontal position, which is the relationship a bar chart is drawn with, giving:

```
[x 238-328] 8,142 (y=139) | 10% | 7% | 19% | 62% | FY24 (y=452)
```

### Trade-offs

| Decision | Rejected alternative | Why |
| --- | --- | --- |
| Relations as a SQL table | Graph database | The hard part is deciding whether two facts relate, not storing the edge. Every query is a filter or a two-hop join. |
| Exact vector scan over a NumPy matrix | FAISS / sqlite-vec | At these corpus sizes an exact scan is fast, with no build step, no tuning and no index to go stale. |
| Never convert currencies | Apply a rate | A rate has its own date and source. Converting would put a number in the layer that appears in no document. |
| Server-rendered page images | pdf.js | Highlight rectangles land in the same coordinate space by construction, and a 100 MB filing is never shipped to the browser to show one page. |
| SQLite | Postgres | One portable file that can be committed as an evaluation snapshot. |

### AI tools used

Claude (Anthropic) was used throughout as a pair programmer — for design discussion,
implementation, and reviewing the reconciliation logic against real pages from the corpus.
Every architectural decision above was made deliberately and is defended in
[`docs/TECHNICAL.md`](docs/TECHNICAL.md).

At runtime the system uses a language model for four things only: profiling a document's
conventions, extracting candidate facts, linking ambiguous measure names, and adjudicating
fact pairs the rules cannot settle. Embeddings run locally on CPU.

## The four required cases

The **Cases** page derives all four from whatever is currently in the layer, ranked by what
makes a good example, with the selection reasons shown. Ingest a different corpus and it
answers from that corpus — a fixed list would prove the documents contained the examples, not
that the system found them.

_(pending run — screenshots and specific examples to follow)_

## Limitations and Next Steps

**Stacked bar charts are genuinely ambiguous.** Segment percentages within a bar map to legend
entries by colour, and there is no colour in the text layer. Where the mapping is unclear the
extractor reports the value as unattributed rather than guessing. That is the honest behaviour,
and it does lose real facts.

**Precision is not yet measured against a gold set.** Grounding pass rate says the evidence
checks out; it does not say the fact was attached to the right measure or period. A fact can be
perfectly grounded and still mis-attributed. `scripts/evaluate.py sample` produces the worksheet
for checking this systematically.

**Adjudication is not adversarial.** One model call decides each escalated pair. Asking twice
with the facts in both orders and flagging disagreement would control for position bias.

**No OCR.** Scanned PDFs are detected but not handled.

**Entity resolution is shallow.** Aliases, embeddings and legal-suffix stripping. It does not
know that a subsidiary belongs to a parent, which matters for consolidated versus standalone
comparisons.

**Table structure is not modelled.** Tables are serialised to delimited rows for the model to
read. Nested headers and multi-row spans are handled as best it can, and financial statements
are where the density is.

Next, in order: measure attribution precision on a hand-checked sample; adversarial
adjudication; structured table extraction.

## Additional Notes

**Performance.** Parsing the 511-page starter corpus went from 263s to 38s — table detection
costs ~450ms/page and is now gated behind cheap structural signals, unused font metadata
extraction was removed, and pages are parsed across processes. Non-content pages never reach a
model. Model responses are content-addressed on disk, so re-ingesting is nearly free and
deterministic.

**Incremental ingest.** A new document is paired only against the existing corpus, never
rebuilt from scratch, so adding a document costs work proportional to that document.

**Testing.** 127 tests in about 50 seconds. The end-to-end tests run the real parsing,
grounding, normalisation, registry and rule engine against a stub model, over PDFs generated
inside the test so the text a quote must match sits beside the assertion about it. They found
four correctness bugs and a SQLite writer deadlock before a single token was spent on a real
document — including the self-validating quote described above, and a page-declared scale that
was silently dropped whenever the extractor also reported a currency, which would have made
every figure in the Delhivery annual report a million times too small.

**Credentials.** No key is committed. `.gitignore` excludes all `.env` files except the
example. An evaluation key is supplied separately in the submission form so the pipeline can be
run fresh; the committed snapshot means it is not needed to see results.

**Documentation.** [`docs/TECHNICAL.md`](docs/TECHNICAL.md) covers the stack, data model, every
pipeline stage, prompt design, performance work and test strategy.
