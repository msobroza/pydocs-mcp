# Late-interaction evaluation reports

Generated per-run reports for the late-interaction (ColBERT/MaxSim) retrieval
conditions land in this directory.

For current numbers and analysis, see `benchmarks/README.md` §Results (the
"Method comparison" table carries the LI rows). The LI conditions themselves —
overlays, pipelines, and the required `[late-interaction]` extra — are
documented as conditions 9–10 in `benchmarks/EXPERIMENTS.md`.

**Caveat:** hybrid-LI results recorded before 2026-07-10 are BM25-only (the
overlays' ingestion pipeline key was silently ignored, so fast-plaid stayed
empty and the late-interaction branch scored nothing) and must not be cited
as late-interaction evidence. See the LI warning block in
`benchmarks/EXPERIMENTS.md` for the full explanation.
