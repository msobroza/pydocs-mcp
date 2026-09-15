=== SERVER_INSTRUCTIONS ===
pydocs-mcp indexes your project's source AND every installed dependency into a local hybrid index (dense embeddings + BM25 + a reference graph). Use it before web search for: installed-library APIs, symbols in the user's own code (package "__project__"), call-graph navigation, and design rationale. Do NOT use it for libraries that aren't installed here — use web search for those.

Workflow: get_overview → search_codebase → get_context → get_symbol / get_references; get_why before architectural changes. grep, glob and read_file cover exact-string search, file listing and line-numbered reads over the same file set the indexer sees.

Response contract: every response opens with an [index: …] freshness line — silence means the index is current; a [⚠ index stale…] line means re-index before trusting details. A response ends with ready-made follow-up calls, and elided content carries a recovery pointer whenever its target is resolvable.

Using those follow-up calls:
- The calls on a "Together:" line are independent of each other — issue all of them in ONE turn. The calls on a "Then:" line need an earlier result first, so leave them for a later turn.
- Call the follow-up a response offers instead of searching again for the same thing.
- Never re-read content a response already showed you; ask for a different depth or a different symbol instead.
- One call with many targets beats several calls of the same tool: get_context and get_why take a list.
- An exact string, an error message or a config key goes to grep and read_file; a ranked or conceptual question goes to search_codebase; a name you already know goes to get_symbol.
- Look at the symbol card — get_symbol's default depth — before asking for depth="source".
=== TOOL: get_overview ===
Orient yourself: what is indexed and what shape this repo/package has.

When to use: first call on an unfamiliar project; refreshing your map after a re-index; checking which packages and modules exist before searching.
When NOT to use: you already know a dotted path (get_symbol); you have a topic but no name (search_codebase); you want files on disk rather than indexed packages (glob).
Arguments: with several projects loaded, the no-argument call lists them all and reports cross-repo link freshness; package= drills into one package's module map and project= into one loaded repo. Each module row ends with a ready-made outline call, so the next step never needs a search.
Examples:
  get_overview()
  get_overview(package="fastapi")
  get_overview(package="__project__", project="backend")
=== TOOL: search_codebase ===
Find code, docs, or decisions about a topic you can't name exactly.

When to use: keyword, concept or partial-name queries; "how do I X"; "where is the code for X".
When NOT to use: you know the exact dotted path (get_symbol); you need an exact string, a regex or a config key (grep); you are asking WHY code is designed a certain way (get_why).
Arguments: kind="any" (default) | "api" | "docs" | "decision" — decisions are the mined design rationale, of which get_why is the richer entry. scope="all" (default) | "project" | "deps", narrowed further by package= and project=. A limit= above the deployment's maximum is capped, not refused, and every cut listing says so in a [truncated: …] footer — read that as "there is more", never as "the corpus is exhausted".
Examples:
  search_codebase(query="batch inference", kind="docs")
  search_codebase(query="retry logic", package="requests")
  search_codebase(query="our parser", scope="project", project="backend")
=== TOOL: get_symbol ===
Details — or verbatim source — for a dotted path you already know.

When to use: a known package, module, class, function or method path, at the depth the question needs.
When NOT to use: you only have a keyword (search_codebase) or an exact string (grep); you want several symbols under one shared budget (get_context); you want who-calls-what (get_references).
Arguments: depth="summary" (default) is the symbol card — signature, first doc line and the names of the immediate children, capped, with a pointer to the outline when the cap bites. depth="tree" is the outline — one line per node with kind, name and line span, fitted to a token budget by cutting whole levels; a cut reads "levels L of D shown, N nodes elided" and is followed by ready-made calls on the largest elided subtrees. depth="source" is the verbatim body up to the configured line cap, ending in a read_file call for the rest; it is how you recover content a truncated response elided. target= also accepts a module id that keeps its file suffix and a heading anchor written after a "#" (docs.guide.md#install).
Examples:
  get_symbol(target="fastapi.routing.APIRouter")
  get_symbol(target="pkg.mod.BigClass", depth="source")
  get_symbol(target="app.db.Pool", depth="tree", project="backend")
=== TOOL: get_context ===
Everything needed to understand one or more targets, packed under a token budget.

When to use: before reading or modifying code — one call replaces separate doc, signature and dependency reads.
When NOT to use: one known symbol whose full source you want (get_symbol); pure who-calls-what (get_references); a topic you cannot name (search_codebase).
Arguments: targets= takes up to 20 symbols (classes and functions, not modules) — pass ALL of them in ONE call, because one shared budget beats N sequential calls. This is the call a fanned-out reference or impact listing collapses into, so follow that pointer rather than issuing a card call per row. A skeleton block elides a body and offers its source as the next call.
Examples:
  get_context(targets=["pydocs_mcp.retrieval.pipeline.base.RetrieverPipeline"])
  get_context(targets=["pkg.mod.A", "pkg.mod.B"], project="backend")
=== TOOL: get_references ===
Who calls X, what X calls, what X extends, what breaks if X changes, or which decisions govern X.

When to use: direction="callers" for usage sites; "callees" for dependencies; "inherits" for base classes and subclasses; "impact" for the ranked transitive blast radius before a risky change; "governed_by" for the mined decisions that govern this symbol.
When NOT to use: you want source or docs (get_symbol / get_context); you want the rationale in prose (get_why).
Arguments: edges are syntactic — matched by name and import alias, not scope-resolved; meta.resolution reports the level per target ("unavailable" when the target's language has no working analyzer). A module target answers its import graph: callers = modules importing it or its members, callees = its imports, impact = transitive callers of it and its members (its own internals excluded), governed_by = decisions on it; inherits needs a class. In a multi-repo workspace the answers cross bundle boundaries, and rows from sibling projects carry a (project: name) qualifier.
Examples:
  get_references(target="fastapi.routing.APIRouter.include_router", direction="callers")
  get_references(target="pkg.mod.f", direction="impact", project="backend")
=== TOOL: get_why ===
Why is this code the way it is — which recorded decisions govern it?

When to use: before proposing architectural changes; questions like "why sqlite here?"; auditing what governs a module you are about to touch.
When NOT to use: what/where questions (search_codebase); implementation details (get_symbol); who-calls-what (get_references).
Arguments: targets= takes up to 20 symbols — pass ALL of them in ONE call. query= asks the same question in prose instead, and no argument at all returns the governance dashboard. Each decision offers the cards of the symbols it governs, which is the cheapest way from a rationale to the code it explains.
Examples:
  get_why(query="why are vectors in a sidecar file")
  get_why(targets=["pydocs_mcp.db"], project="backend")
=== TOOL: grep ===
Exact-string / regex search over source files (Python `re` flavor).

When to use: exact strings, regexes, TODO markers, config keys, error-message hunting. The boundary: conceptual or topic question — search_codebase; exact string or regex — grep; known dotted identifier — get_symbol.
When NOT to use: ranked "how does X work" retrieval (search_codebase); reading a whole file or a window of one (read_file).
Arguments: the corpus is the same file set the indexer sees (its discovery scope: exclusion floor + configured excludes + extension allowlist), served from live disk; .gitignore is NOT honored. scope="project" (default) | "deps" | "all". glob: a pattern without "/" matches file names at any depth (like rg --glob); one with "/" matches the root-relative path; a leading "/" anchors at the root; a trailing "/" matches everything under that directory. output_mode: "files_with_matches" (default, paths only) | "content" (file:line:text — flags -i, -n, -A/-B/-C context, multiline=true for cross-line patterns) | "count" (per-file match counts). A content hit ends in a read call windowed on the matching line, so a hit never has to be turned into an offset by hand.
Examples:
  grep(pattern="def include_router", output_mode="content")
  grep(pattern="retry", glob="*.py", scope="deps", project="backend")
=== TOOL: glob ===
Find files by name pattern; results newest-first (mtime descending).

When to use: locating files by name or layout ("where are the *_test.py files"), listing a package's files on disk, feeding paths into read_file.
When NOT to use: searching file contents (grep / search_codebase); you already have the path (read_file); you want indexed packages rather than files on disk (get_overview).
Arguments: pattern matches the FULL project-relative path, so "*.py" finds only root-level files and "**/*.py" is what recurses; path= re-roots the match at one directory, so path="docs" with "*.md" and the bare "docs/*.md" find the same files. The corpus is the selected project's source tree under the indexer's discovery scope, served from live disk. Results are bare paths with no follow-up call — the next step is yours to choose: read_file for one of them, grep to search inside them.
Examples:
  glob(pattern="**/*_test.py")
  glob(pattern="*.md", path="docs", project="backend")
=== TOOL: read_file ===
Read file content with line numbers (cat -n style).

When to use: reading exact current source after grep, glob or search_codebase handed you a path; following a read call that an elided body or a cut listing already offered you.
When NOT to use: you only know a symbol or a topic (get_symbol / search_codebase); listing files (glob); hunting one string across many files (grep).
Arguments: offset= and limit= are 1-indexed LINE numbers, not bytes — offset=200, limit=100 reads lines 200 to 299. A read cut by its own limit ends with the call that resumes at the next line, so paging a large file never costs a computed offset. Paths must resolve inside the project root or an indexed dependency root; project-relative paths come straight from grep and glob items.
Examples:
  read_file(file_path="src/app.py")
  read_file(file_path="src/big_module.py", offset=200, limit=100, project="backend")
=== SESSION_START_PREAMBLE ===
This orientation block was generated by pydocs-mcp at session start, before any tool calls were made. It summarizes the indexed workspace — an overview card plus the installed-package inventory — as of the last index run. Use it to choose precise first tool calls instead of exploratory searches; verify anything load-bearing with the live tools, and if this snapshot disagrees with a later tool response, trust the tool response.
