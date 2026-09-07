# Evaluation snapshot

Written by `python scripts/snapshot.py export` after a real run, and committed so the
project can be evaluated without credentials.

| File | What it is |
| --- | --- |
| `knowledge.sqlite` | The database as it stood after the run. Restore it with `python scripts/snapshot.py load` and the interface shows the real knowledge layer immediately. |
| `export.json` | The same content as readable, diffable data. Useful for reviewing extraction quality without running anything, and for seeing what changed between two runs. |
| `replay/` | Every model response the run made, content-addressed by request. With `LLM_PROVIDER=replay` the pipeline re-runs offline against these, so the extraction path itself can be exercised rather than taken on trust. A request with no recording fails loudly rather than being invented. |

The PDFs themselves are not in here — they are in `samples/` at the repository root. Copy
them into `backend/data/uploads/` (or re-run the ingest) if you want the evidence viewer to
render source pages after restoring a snapshot taken on another machine.
