# Operating pydocs-mcp

You are answering a question about a Python repository that has been indexed by
pydocs-mcp. The server exposes six task-shaped tools. Pick the tool that matches
the SHAPE of the question, retrieve, then read the cited file. Do not read files
blindly — retrieve first, then open exactly what the answer points to.

## Which tool answers which question shape

- `get_overview` — orient in an unfamiliar package or subsystem. Use it FIRST
  when you do not yet know the module layout, entry points, or vocabulary. It
  returns the package's shape (top modules, key classes) so your later queries
  use the repo's own terms instead of guesses.
- `search_codebase` — the workhorse for "where / which code does X?" Give it a
  behavior or capability in natural language ("where are embeddings written to
  disk", "which function fuses BM25 and dense results"). It returns ranked chunks
  with file + symbol locations. Reach for this whenever you do not already know
  the exact symbol name.
- `get_symbol` — you already know ONE fully-qualified symbol
  (`pkg.module.ClassName` or `pkg.module.func`) and want its signature,
  docstring, or source. Use when the question names a specific thing, not when
  you are still hunting for it.
- `get_context` — you have TWO OR MORE known symbols and want how they relate:
  shared call sites, common collaborators, the surface that ties them together.
  Use for "how do A and B interact" once you know both names.
- `get_references` — the reference graph. Answers callers ("what calls X"),
  callees ("what X calls"), inheritance ("what subclasses X"), and impact
  ("what breaks if X changes"). Use for blast-radius and dependency questions
  once you have the target symbol.
- `get_why` — rationale and decision questions ("why is the FTS rebuild
  deferred", "why does the cache key include a pipeline hash"). Returns the
  design notes and commentary behind a choice, not the mechanics of it.

## Decompose a repository question into 1-3 retrieval queries

Before reading any file, break the question into at most three retrieval steps:

1. If you do not know the repo's vocabulary, run `get_overview` on the package
   the question is about to learn its terms.
2. Turn the core ask into ONE `search_codebase` query phrased in behavior terms.
   If the question has two distinct behaviors, that is two queries, not one
   sprawling query.
3. If a query returns a promising symbol, escalate to the precise tool:
   `get_symbol` for its definition, `get_references` for its callers/impact,
   `get_context` to relate it to a second symbol, `get_why` for its rationale.

Prefer several sharp queries over one broad one. Two focused searches beat a
single vague sentence that mixes concerns.

## When to STOP searching and read the cited file

Stop retrieving the moment a result names the file and symbol that must contain
the answer. Retrieval locates; the file confirms. Once a chunk cites
`path/to/file.py` and the relevant symbol, open THAT file and read the actual
code — do not run more searches hoping for a cleaner phrasing. More queries
after you have the location add cost and noise, not certainty. If three queries
have not surfaced a location, widen with `get_overview` rather than repeating
near-identical searches.
