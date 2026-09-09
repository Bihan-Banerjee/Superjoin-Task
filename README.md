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

Every number below is measured from the committed run over the six starter documents, not
estimated. `backend/scripts/evaluate.py` and `backend/scripts/benchmark.py` reproduce all of
them, and the snapshot in `backend/seed/` lets a reviewer see the same results with no API
key at all.

| | |
| --- | --- |
| Documents / pages | 6 / 511 |
| Facts kept | 3,600 of 4,311 proposed (**83.5% grounded**) |
| Attribution precision on a 40-fact sample | **87.5%** (35/40): see below |
| Evidence re-verified against source pages | **3,600 of 3,600** |
| Quotes located for highlighting | 95.8% |
| Relations | 583, of which **427 cross-document** (73%) |
| Decided by rule vs by model | 473 / 110 |
| Measures discovered | 1,407, 100 seen in more than one document |
| Local pipeline cost | **138 ms/page** across 511 pages |
| Model calls | 587, 77% served from cache |

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
.venv/Scripts/python -m pytest                                  # 244 tests, ~22s
```

## Video Demo

> **To add:** link here. The walkthrough covers upload with live progress, a fact opened to
> its highlighted evidence on the page, the four cases, the registry growing across
> documents, and the evaluation figures.

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

The model returns a verbatim quote. The pipeline then checks it independently: locates it in
the page's raw text (tolerating ligatures, soft hyphens and line-break hyphenation), confirms
the value appears **in the source page** within that span, and resolves the span to rectangles
on the rendered page.

That check reads the page and never the model's own quote. Searching the quote too would let a
fabricated figure corroborate itself: write any number into the quote and it passes. That was a
real defect here, caught by the end-to-end tests.

Anything that fails is written to a rejection table with a typed reason: not logged. That
table is the measurement of how much the extractor got wrong, and it is where the required
failure case comes from.

### Rules decide first; the model handles the residue

A deterministic engine classifies pairs by period, unit scale, currency, scope, segment and
basis. It escalates only when it cannot decide: an unresolved period, an implausibly large
gap, low extraction confidence, or a non-numeric fact where agreement is a question about
language rather than arithmetic.

Rules are free, so far more pairs can be compared than a model budget allows; reproducible, so
the same corpus always yields the same relationships; and explainable in a way that matters: *"these differ because one is stated in crore and the other in millions"* names a checkable
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
never the same number: confidently wrong output, which is much worse. Merges across unit
classes are blocked outright, so "revenue" can never absorb "revenue growth".

### Reading order is reconstructed

PDFs store text in content-stream order, not reading order. A two-column central bank review
interleaves its columns line by line; a chart slide separates every number from its label.
Page 9 of the Delhivery deck extracts as:

```
59% 63% 62% 24% 16% 19% ... 7,054 7,224 8,142 FY22 FY23 FY24 Express Parcel PTL ...
```

Every number there is real and none is attributable. Columns are recovered from an occupancy
histogram whose threshold is relative to the page's own density (a gutter is not empty: a
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

Claude (Anthropic) was used throughout as a pair programmer: for design discussion,
implementation, and reviewing the reconciliation logic against real pages from the corpus.
Every architectural decision above was made deliberately and is defended in
[`docs/TECHNICAL.md`](docs/TECHNICAL.md).

At runtime the system uses a language model for four things only: profiling a document's
conventions, extracting candidate facts, linking ambiguous measure names, and adjudicating
fact pairs the rules cannot settle. Embeddings run locally on CPU.

## The four required cases

The **Cases** page derives all four from whatever is currently in the layer, ranked by what
makes a good example, with the selection reasons shown. Ingest a different corpus and it
answers from that corpus: a fixed list would prove the documents contained the examples, not
that the system found them.

These are the examples it currently returns. Run `python scripts/evaluate.py cases` to
reproduce them.

**1. Corroboration across documents, expressed differently.** The annual report states
revenue from services for FY24 as `81,415` on a page declaring amounts in Indian Rupees in
million. The earnings deck states the same measure as `8,142` in crore. Both normalise to
â‚¹81.42 billion, so the pair corroborates on the `unit_scale` dimension: decided by rule,
with no model call. This is the case the whole design exists for: the two figures share no
digits, no unit and no wording, and are the same fact.

**2. A genuine contradiction.** Two figures for revenue over the nine months ended
31 December 2021, `48,105.30` and `46,230.56` million, on the same entity, period and
basis, a 3.9% gap and no dimension that explains it. Flagged with a severity score rather
than asserted flatly, because the extractor may still have missed a distinction the page
made.

**3. An apparent contradiction explained by context.** Figures that differ because they
cover different periods (FY24 against the year ended 31 March 2021), reported as
`reconciled_by_context` on the `period` dimension. The layer also produces `supersedes`
where one basis replaces another (a restatement against the figure it restates), which
says not only that the difference is explained but which figure is now current.

**4. An extraction or reasoning failure.** Of 4,311 candidate facts, 711 were refused.
The reasons are counted, not described: 308 whose measure could not be resolved, 215 whose
quote could not be located on the page, 60 where the value was absent from the cited span,
54 ambiguous short quotes, and the rest smaller. Every one is a row in `rejections` with the
candidate that produced it, so the failure mode is inspectable rather than anecdotal.

> **Screenshots to add:** `docs/screenshots/`: Documents, Facts with the evidence overlay,
> Relations, Cases, Registry, Evaluation.

## Precision

Grounding rate says the evidence is real. It says nothing about whether a fact was attached
to the *right* measure, period or scope, and only reading the page answers that. So 40 facts
were drawn at random (`scripts/evaluate.py sample`, seed 7) and checked one by one against
their source pages. The worksheet, with every verdict, is in
[`docs/precision-sample.md`](docs/precision-sample.md).

**35 of 40 correct: 87.5%.** The five failures are worth stating individually, because their
shape is the point:

| # | Verdict | What went wrong |
| --- | --- | --- |
| 10 | `wrong-measure` | `66` read as payable days; the column was receivable days |
| 20 | `wrong-scope` | A directorship date attributed to the wrong director |
| 21 | `wrong-scope` | Options exercised by one individual reported as the plan total |
| 36 | `wrong-measure` | Electricity in joules given the document's default currency |
| 40 | `wrong-measure` | "129 Service Centres" was "129 Freight Service Centres" |

Every one is a **misattribution, not an invention**. In all five the number is real, on the
page, and correctly transcribed; what is wrong is the label attached to it. That is the
failure mode this design chooses: verification is against the source text, so a fabricated
figure cannot survive, while a figure attached to a neighbouring row's heading can. Four of
the five come from dense tabular pages where the label sits in a different column from the
value: the same structural problem the layout stage exists to attack, and does not fully
solve.

Fact 36 was the useful one: it exposed a real defect rather than a judgement call, and led to
the currency-inheritance fix described in `docs/TECHNICAL.md`.

**How the sample was checked.** The verdicts were produced by a separate language model
reading each fact against its quoted evidence, then spot-checked by hand. That is weaker than
a full manual review and the number should be read with that in mind: an independent reader
is a reasonable cross-check on attribution, but it is not the same as a person with the PDF
open. The worksheet is committed so the judgements can be disputed rather than taken on
trust.

## Limitations and Next Steps

**Stacked bar charts are genuinely ambiguous in the text layer.** Segment percentages within a
bar map to legend entries by colour, and there is no colour in the text. With a vision model
configured the page image is attached and the model may attach a value to a series where a
segment's colour matches a legend swatch, recording what it matched so the attribution can be
checked. That is the one claim in the system no later check can verify against the page, so
the licence is narrow: where colours are close, where the legend outnumbers the segments, or
where the rendering is too small, the extractor still reports the value as unattributed rather
than guessing. That is the honest behaviour,
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

**Performance.** Parsing the 511-page starter corpus went from 263s to 38s: table detection
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
document: including the self-validating quote described above, and a page-declared scale that
was silently dropped whenever the extractor also reported a currency, which would have made
every figure in the Delhivery annual report a million times too small.

**Credentials.** No key is committed. `.gitignore` excludes all `.env` files except the
example. An evaluation key is supplied separately in the submission form so the pipeline can be
run fresh; the committed snapshot means it is not needed to see results.

**Documentation.** [`docs/TECHNICAL.md`](docs/TECHNICAL.md) covers the stack, data model, every
pipeline stage, prompt design, performance work and test strategy.
