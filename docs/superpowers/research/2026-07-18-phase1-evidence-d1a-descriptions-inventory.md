# d1a SUMMARY

All nine MCP tool descriptions come from one dict — `TOOL_DOCS: dict[str, str]` in python/pydocs_mcp/application/tool_docs.py:37-137 — passed explicitly as `mcp.tool(name=..., description=TOOL_DOCS[name], ...)` inside server.py's `_register` helper (server.py:642-650); handler docstrings are deliberately NOT used, and the return annotation is stamped post-def (`fn.__annotations__["return"] = Annotated[CallToolResult, ENVELOPE_MODELS[name]]`, server.py:641) to advertise the {text, items, meta} outputSchema. Parameter-level descriptions DO NOT EXIST on the MCP surface: neither the handler signatures (server.py) nor the pydantic input models (application/mcp_inputs.py) carry a single `Field(description=...)`, and an empirical FastMCP registration + `list_tools()` dump confirms zero `description` keys in any advertised inputSchema property (enums and grep's dash wire-names -i/-n/-A/-B/-C do reach it via Literal aliases + validation_alias). Parameter guidance lives only in TOOL_DOCS prose and in CLI-only argparse `help=` strings (__main__.py:265-467). SERVER_INSTRUCTIONS (tool_docs.py:139-150, 937 chars) is consumed twice: as FastMCP `instructions=` (server.py:551) and as the CLI top-level argparse `description` (__main__.py:66); both TOOL_DOCS and SERVER_INSTRUCTIONS are imported function-locally at call time (server.py:527, 622; __main__.py:60) precisely so the benchmarks-side overlay can re-bind the module attributes — a mechanism already implemented and tested (benchmarks/src/pydocs_eval/optimize/_overlay_server.py; tests/test_tool_docs_overlay_seam.py). The ask-your-docs agent's prompts are versioned Jinja templates under python/pydocs_mcp/ask_your_docs/prompts/ (shared/system_v1.j2 3110 chars, rewrite_v1.j2 216, vision_extraction_v1 584, reinspect_description_v1 533, reinspect_budget_message_v1 192, inline/system_suffix_v1 401) loaded through a per-architecture fallback namespace, and the planned `prompts=` seam HAS shipped: `AskPrompts` + `build_agent(..., prompts=)` + `reformulate(rewrite_template=)` (agent.py:144-157, 187, 264-289), consumed by benchmarks/src/pydocs_eval/optimize/ask_binding.py. Crucially, a "seed source document" already exists in embryo: benchmarks/src/pydocs_eval/optimize/artifacts/_delimited.py defines the shared `=== SECTION ===` grammar (closed header set SERVER_INSTRUCTIONS | SYSTEM_PROMPT | REWRITE_PROMPT | TOOL: <name>), `ToolDocsArtifact.render()` serializes the live TOOL_DOCS+SERVER_INSTRUCTIONS into it, `AskPromptArtifact` does the same for the agent prompts (with committed seed copies ask_prompt_seed.md 3363 chars and usage_skill_seed.md 4373 chars, byte-pinned by regeneration tests), and `validate()` re-runs the §D13 lint constants (REQUIRED_MARKERS, CHARS_PER_TOKEN=4, PER_TOOL_TOKEN_BUDGET=500, TOTAL_TOKEN_BUDGET=3600) that are importable from the product module. TOOL_DOCS does NOT include parameter descriptions (there are none), and today's delimited grammar has no sections for param text, CLI help, or the two retrieval tree-reasoning prompts (retrieval/prompts/*.j2, 1225/2078 chars). Other model-facing strings split into plausibly-optimizable prose (freshness header/stale warning and truncation footer wording in envelope.py:35-71, pointer render templates "→ get_symbol(...)" in formatting.py:104-181, empty-result messages "No matches found."/"No symbols found." in multi_project_search.py:69-70, YAML-anchored ServiceUnavailableError texts in null_services.py:45-69 and file_tools.py:443-448) versus fixed grammar that must not be optimized (the [[next:...]] token regex, the delimited seed grammar, the frozen {text, items, meta} envelope, cat-n line-number rendering).

# MEASUREMENTS

- **TOOL_DOCS total size** = 9307 chars ≈ 2326 tokens (budget 3600)  (Python import of repo source (sys.path→worktree python/), len() per entry; CHARS_PER_TOKEN=4 from tool_docs.py:21)
- **Per-tool description sizes (chars)** = get_overview 1003, search_codebase 982, get_symbol 1011, get_context 938, get_references 1136, get_why 891, grep 1360, glob 960, read_file 1026  (same script (scratchpad/measure_docs.py))
- **SERVER_INSTRUCTIONS size** = 937 chars  (len(SERVER_INSTRUCTIONS) on imported repo source)
- **Shared fragments** = _WORKFLOW 123 chars, _CONTRACT 293 chars  (len() on imported repo source)
- **Param descriptions in advertised MCP inputSchema** = 0 across all nine tools (77 total advertised params: 2+6+3+2+4+3+13+4+4)  (Registered _register_tools on FastMCP with stub router under /opt/homebrew python3.11 (mcp installed), dumped list_tools() and checked every property for a 'description' key (scratchpad/dump_schema.py))
- **Tool description wire parity** = advertised description == TOOL_DOCS[name] for all 9; outputSchema properties == [text, items, meta] for all 9; grep advertises dash wire-names -i/-n/-A/-B/-C  (same list_tools() dump)
- **ask-your-docs prompt template sizes** = system_v1 3110, rewrite_v1 216, vision_extraction_v1 584, reinspect_description_v1 533, reinspect_budget_message_v1 192, inline/system_suffix_v1 401 chars  (read_text() length per .j2 (scratchpad/measure_prompts.py))
- **retrieval tree-reasoning prompt sizes** = tree_reasoning_pydocs_v1 2078, tree_reasoning_pageindex_v1 1225 chars  (read_text() length)
- **Harness seed sizes** = ask_prompt_seed.md 3363 chars, usage_skill_seed.md 4373 chars  (read_text() length; ask seed byte-pinned to live render by benchmarks/tests/optimize/test_ask_prompt_artifact.py:41-43)
- **Repo venv** = absent in this worktree; global pydocs-mcp at /opt/homebrew/bin (python3.11, mcp+pydantic 2.12.5 available)  (ls .venv/bin/python; which pydocs-mcp; interpreter shebang probe)

# UNVERIFIED

- The ~4500-char aggregate for CLI-only argparse help strings (row 21) is an eyeball estimate from reading __main__.py:54-524, not a script measurement — individual help= strings were not summed programmatically.
- Runtime behavior of the FastMCP `instructions=` field on the wire (whether specific MCP clients actually surface SERVER_INSTRUCTIONS to the model) was not exercised against a live client; only the registration call site (server.py:551) is verified.
- The list_tools() schema dump ran against mcp/pydantic versions from the homebrew python3.11 env (pydantic 2.12.5), not the repo's pinned uv.lock versions; the no-param-descriptions result follows from the source (zero Field(description=) anywhere) and should be version-independent, but the exact pinned-version schema bytes were not reproduced.
- ~/.pydocs-mcp bundles and ~/pydocs-index were not inspected; no indexed queries were run (static inventory only).
- Whether the two retrieval tree_reasoning .j2 prompts are reachable in the shipped default pipeline (llm_tree_reasoning is opt-in per CLAUDE.md) was taken from CLAUDE.md, not re-verified against pipelines/*.yaml in this session.

# EVIDENCE

# R-D1a — Inventory of ALL optimizable text and its attachment mechanisms

All paths relative to `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/phase-2-instrumentation-spec-498def/` unless absolute. Empirical measurements ran the repo's own source via `PYTHONPATH`-style `sys.path` insertion under `/opt/homebrew/opt/python@3.11/bin/python3.11` (the interpreter behind the globally installed `/opt/homebrew/bin/pydocs-mcp`; it has `mcp` 1.x + pydantic 2.12.5). **No repo `.venv` exists in this worktree** (checked: `.venv/bin/python` absent).

## Q1 — How each of the nine tools gets its description; where param descriptions live

**Mechanism: explicit `description=` kwarg, single-source dict, function-local import.**

- `TOOL_DOCS: dict[str, str]` — python/pydocs_mcp/application/tool_docs.py:37 (dict literal spans :37-137). Keys are exactly the nine tool names; values are complete multi-section description strings (f-strings interpolating two shared fragments).
- Shared fragments: `_WORKFLOW` tool_docs.py:25-28 (123 chars, "Workflow: get_overview → search_codebase → …") and `_CONTRACT` tool_docs.py:29-35 (293 chars, the "[index: …] freshness line / recovery pointer" response-contract paragraph). Interpolated into every one of the nine docs AND into SERVER_INSTRUCTIONS (tool_docs.py:145,148).
- Registration: `_register_tools(mcp, tools)` server.py:599-739 defines a closure `_register(fn, name)` (server.py:625-650) that does `mcp.tool(name=name, description=TOOL_DOCS[name], annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True))(fn)` — server.py:642-650. The docstring of `_register` states: "``description=`` is passed explicitly rather than relying on ``fn.__doc__`` so the tool text is the ``TOOL_DOCS`` single source regardless of how FastMCP resolves docstrings across versions (§D13)" (server.py:630-632). The nine handler functions themselves have NO docstrings.
- Post-def annotation stamping (output side): `fn.__annotations__["return"] = Annotated[CallToolResult, ENVELOPE_MODELS[name]]` — server.py:641. Stamped dynamically because `from __future__ import annotations` stringifies source annotations and FastMCP's eval_str cannot see the function-local names (server.py:634-639 docstring).
- `TOOL_DOCS` is imported **function-locally inside `_register_tools`** (server.py:622) and `SERVER_INSTRUCTIONS` **function-locally inside `run`** (server.py:527) — this is the overlay-injection seam. The three filesystem tools are registered by `_register_filesystem_tools(register, tools)` (server.py:742-813) through the same `_register` closure (server.py:788, 799, 812).
- Overlay-seam guard test: tests/test_tool_docs_overlay_seam.py — AST-inspects that the imports stay function-local ("a module-level ``from ... import TOOL_DOCS`` would freeze the pre-overlay binding at import time", test file lines 7-11).

**Parameter descriptions: none exist on the MCP surface.**

- application/mcp_inputs.py (591 lines, read in full): every `Field(...)` carries only constraints — `min_length`/`max_length` (e.g. `SearchInput.query` :233, `ContextInput.targets` :404), `ge` (:249, :304, :435, :520-525, :581-583), `default_factory` (:249, :304, :435), `validation_alias` (:518-522). **Zero `description=` kwargs in the file.**
- server.py handler signatures likewise: only `Annotated[..., Field(validation_alias="-i"|-n|-A|-B|-C, ge=0)]` on grep (server.py:761-765); no description metadata anywhere.
- **Empirical confirmation** (scratchpad script `dump_schema.py`): built a `FastMCP("probe")`, called `_register_tools(mcp, StubTools())`, ran `mcp.list_tools()`. Result per tool: `params with description: NONE` for all nine. Counts: get_overview 2 params, search_codebase 6, get_symbol 3, get_context 2, get_references 4, get_why 3, grep 13, glob 4, read_file 4. Also verified `t.description == TOOL_DOCS[t.name]` → True for all nine, and `outputSchema` properties == ['text','items','meta'] for all nine. grep's inputSchema advertises the literal wire names `-i`, `-n`, `-A`, `-B`, `-C` (via `validation_alias`; see the load-bearing rationale at mcp_inputs.py:497-507: `alias` would break FastMCP's kwarg binding, `validation_alias` advertises dash names + dumps by Python field name).
- Enum values DO reach the inputSchema — not as descriptions but as `enum:` lists — because the handler params are typed with the shared Literal aliases (`KindLiteral`/`ScopeLiteral`/`DepthLiteral`/`DirectionLiteral`/`OutputModeLiteral`, mcp_inputs.py:44-48) imported module-level in server.py:37-43 ("FastMCP evals the handlers' stringified signatures against THIS module's globals" — server.py:32-36 comment). Confirmed in the dump (e.g. output_mode enum content/files_with_matches/count).
- Envelope/output models also carry no field descriptions: application/tool_response.py `MetaModel`/`*Item` classes (:40-115) have plain typed fields, no `Field(description=...)` (grep over the file showed none).
- The ONLY parameter-level prose anywhere: (a) inside TOOL_DOCS body text (e.g. grep's `output_mode:` line tool_docs.py:108, corpus semantics :107; get_references direction semantics :84); (b) CLI-only argparse `help=` strings (Q3 below). Neither reaches the MCP inputSchema.

## Q2 — SERVER_INSTRUCTIONS

- Defined: tool_docs.py:139-150. **937 chars** (measured). Content: what pydocs-mcp indexes (project + installed deps, hybrid index), when to use before web search, "Nine task-shaped tools:" + `_WORKFLOW`, grep/glob/read_file one-liner, + `_CONTRACT`, closing "Do NOT use for libraries that aren't installed here (use web search)."
- Consumed (2 sites):
  1. server.py:551 `mcp = FastMCP("pydocs-mcp", instructions=SERVER_INSTRUCTIONS)` — MCP session-level orientation; imported function-locally at server.py:527.
  2. __main__.py:66 `argparse.ArgumentParser(prog="pydocs-mcp", description=SERVER_INSTRUCTIONS)` — CLI top-level help; imported function-locally at __main__.py:60 with the comment (:57-59): "Function-local on purpose (R2): the benchmarks-side description overlay re-binds these module attributes before the parser is built."

## Q3 — CLI help sourcing and CLI-only strings

- `_task_parser(canonical, aliases)` __main__.py:254-263: `help=TOOL_DOCS[canonical].splitlines()[0]` (first line only) and `description=TOOL_DOCS[canonical]` (full text) with `RawDescriptionHelpFormatter` ("TOOL_DOCS is pre-formatted prose … don't re-wrap it", :260-262). Canonical subcommand names == MCP tool names; historical short verbs (`search`, `overview`, `symbol`, `context`, `refs`, `why`) are argparse aliases (:265, 302, 306, 311, 315, 325). Enum `choices=` come from `typing.get_args(<Literal>)` (:271, 284, 308, 319, 363, 426) — same single source as the inputSchema.
- **CLI-only strings that duplicate/extend tool descriptions** (all in `_build_parser`, __main__.py:54-524):
  - search_codebase param help: `query` :268, `--kind` :273 (129 chars, restates kind semantics), `-p/--package` :280, `--scope` :286 (167 chars incl. agent guidance 'Use "project" when the user asks about THEIR code'), `--limit` :295-298.
  - grep param help: pattern :349, --path :353, --glob :358, --output-mode :365-366, -i :373, -n :378, --no-line-numbers :386, -A/-B/-C :394/402/410, --head-limit :417, --multiline :422, --scope :428-429.
  - glob: pattern :434, --path :438, --head-limit :445. read_file: file_path :452-453, --offset :459, --limit :465.
  - get_why `--target` help :335-340 (~250 chars — §D11 path-vs-qname classification prose, mirrored from `_classify_target`).
  - Deprecated `lookup` subcommand: help/description :471-478, 8-line examples epilog :479-489, `target` help :496, `--show` help :511-520 (~600 chars mapping the eight show modes to question shapes). Runtime deprecation warning printed to stderr: __main__.py:1065-1069.
  - `link` subcommand description :222-228; serve/index/watch flag helps :163-213; shared query-flag helps :85-124 (`--cache-dir`, `--workspace`, `--db`, `--project`, `-v`).
- All CLI subcommand output text is the SAME rendered body as MCP (shared `ToolRouter` + envelope, `surface="cli"` selects CLI pointer syntax — __main__.py:880-903, server.py:496-510).

## Q4 — ask-your-docs system prompts and the prompts= seam

- Template layout (python/pydocs_mcp/ask_your_docs/prompts/__init__.py:10-22 docstring): `shared/` fallback pool + per-architecture dirs; `prompts/<arch>/*.j2` overrides `prompts/shared/*.j2` by filename; versioning rule "never edit a shipped `_vN` in place — ship `_vN+1`" (:20-22). Loader: `ArchitecturePrompts.resolve_source/render` :43-58 via the single repo-wide Jinja env `render_prompt_from` (retrieval/prompts/_loader.py:20-45; StrictUndefined, autoescape off).
- Files + measured sizes:
  - `shared/system_v1.j2` — **3110 chars, 50 lines**. Sections: role ("You answer ONLY from the results of your tools — never from memory"), a nine-tool signature list (all nine tools with param lists and usage guidance — an independent re-statement of TOOL_DOCS semantics), and 6 numbered Rules (infer project, rewrite follow-ups, one clarifying question, cite + fenced code, always end with an Example snippet, respect the "[pinned scope: …]" note).
  - `shared/rewrite_v1.j2` — 216 chars ({{ history }} / {{ question }} reformulation).
  - `shared/vision_extraction_v1.j2` — 584 chars (ERROR:/SYMBOL:/PATH:/TEXT:/VISUAL: fact extraction).
  - `shared/reinspect_description_v1.j2` — 533 chars (becomes the reinspect_images TOOL DESCRIPTION — consumed at ask_your_docs/reinspect.py:40).
  - `shared/reinspect_budget_message_v1.j2` — 192 chars (budget-exhausted tool reply — reinspect.py:67).
  - `inline/system_suffix_v1.j2` — 401 chars (image-handling section appended by the inline architecture).
- Import-time constants: `SYSTEM_PROMPT` / `REINSPECT_DESCRIPTION` / `BUDGET_MESSAGE` rendered once at prompts/__init__.py:91-93.
- **The prompts= seam SHIPPED** (was "planned for product 0.5.2"): `AskPrompts` frozen dataclass agent.py:144-157 (`system_prompt: str | None`, `rewrite_prompt: str | None`; docstring explains the rewrite override is a `str.format` template with `{history}`/`{question}`). `build_agent(..., prompts: AskPrompts | None = None)` agent.py:176-188 (param :187; docstring :202-204: "the evaluation-harness seam … the app and CLI never pass it, so product behavior is byte-identical by default"). `_assemble_prompt` agent.py:160-173 is "The ONE prompt-assembly site": candidate-or-shipped system + `"\nIndexed projects and packages:\n" + render_catalog(catalog)` (:173) — catalog stays OUTSIDE the override. `reformulate(..., rewrite_template: str | None)` agent.py:264-289 consumes the rewrite override (:285-287). Harness consumption: benchmarks/src/pydocs_eval/optimize/ask_binding.py:32 (imports AskPrompts), :76, :111-121 (lazy build_agent + AskPrompts construction).
- Other agent-composed model-visible strings: scope pin note `"[pinned scope: …] "` agent.py:101-110; history image placeholder `"[attached images: …]"` agent.py:338; `_history_line` "[image]" markers agent.py:246-261.

## Q5 — Inventory table of every optimizable string

Char counts measured by script (scratchpad/measure_docs.py, measure_prompts.py); token estimates = chars // 4 (the product's own CHARS_PER_TOKEN rule, tool_docs.py:21).

| # | String | Location | Chars | Duplicated where |
|---|--------|----------|-------|------------------|
| 1 | TOOL_DOCS["get_overview"] | tool_docs.py:38-48 | 1003 | first line → CLI help (__main__.py:258); full → CLI description (:259) |
| 2 | TOOL_DOCS["search_codebase"] | tool_docs.py:49-60 | 982 | same CLI mirroring; semantics re-stated in system_v1.j2 + usage_skill_seed.md + CLI --kind/--scope help |
| 3 | TOOL_DOCS["get_symbol"] | tool_docs.py:61-71 | 1011 | CLI mirroring; system_v1.j2 |
| 4 | TOOL_DOCS["get_context"] | tool_docs.py:72-81 | 938 | CLI mirroring; system_v1.j2 |
| 5 | TOOL_DOCS["get_references"] | tool_docs.py:82-92 | 1136 | CLI mirroring; system_v1.j2; hedging requirement referenced by docs/tool-contracts.md:391,429 |
| 6 | TOOL_DOCS["get_why"] | tool_docs.py:93-102 | 891 | CLI mirroring; CLI --target help (:335-340) |
| 7 | TOOL_DOCS["grep"] | tool_docs.py:103-114 | 1360 | CLI mirroring; per-flag argparse help :349-429 |
| 8 | TOOL_DOCS["glob"] | tool_docs.py:115-125 | 960 | CLI mirroring; :434-445 |
| 9 | TOOL_DOCS["read_file"] | tool_docs.py:126-136 | 1026 | CLI mirroring; :452-465 |
| — | TOOL_DOCS total | | **9307** (≈2326 tok of 3600 budget) | |
| 10 | `_WORKFLOW` fragment | tool_docs.py:25-28 | 123 | interpolated into all 9 docs + SERVER_INSTRUCTIONS (shared substring, not a drift risk) |
| 11 | `_CONTRACT` fragment | tool_docs.py:29-35 | 293 | ditto |
| 12 | SERVER_INSTRUCTIONS | tool_docs.py:139-150 | 937 | CLI top-level description __main__.py:66; delimited-doc section in ToolDocsArtifact |
| 13 | ask system prompt (system_v1.j2) | ask_your_docs/prompts/shared/system_v1.j2 | 3110 | ask_prompt_seed.md §SYSTEM_PROMPT (byte-pinned); re-states all nine tool signatures |
| 14 | rewrite prompt (rewrite_v1.j2) | prompts/shared/rewrite_v1.j2 | 216 | ask_prompt_seed.md §REWRITE_PROMPT (with literal {history}/{question}) |
| 15 | vision extraction prompt | prompts/shared/vision_extraction_v1.j2 | 584 | — |
| 16 | reinspect tool description | prompts/shared/reinspect_description_v1.j2 | 533 | — (a TOOL DESCRIPTION outside TOOL_DOCS — the agent-side reinspect_images tool, reinspect.py:40) |
| 17 | reinspect budget message | prompts/shared/reinspect_budget_message_v1.j2 | 192 | — |
| 18 | inline image-handling suffix | prompts/inline/system_suffix_v1.j2 | 401 | — |
| 19 | tree-reasoning prompt (pydocs) | retrieval/prompts/tree_reasoning_pydocs_v1.j2 | 2078 | — (LLM-retrieval-step prompt, YAML-selected) |
| 20 | tree-reasoning prompt (pageindex) | retrieval/prompts/tree_reasoning_pageindex_v1.j2 | 1225 | — |
| 21 | CLI-only param help strings (~40 strings) | __main__.py:68-124, 163-236, 265-521 | ~4500 total (unmeasured individually) | overlap TOOL_DOCS prose in different words |
| 22 | usage_skill seed (harness) | benchmarks/src/pydocs_eval/optimize/artifacts/usage_skill_seed.md | 4373 | validated to name all TOOL_DOCS keys (usage_skill.py:30-33); budget 1500 tok (:40) |
| 23 | ask_prompt seed (harness) | benchmarks/.../artifacts/ask_prompt_seed.md | 3363 | = live render of #13+#14; byte-parity test benchmarks/tests/optimize/test_ask_prompt_artifact.py:41-43 |
| 24 | scope-pin note template | agent.py:101-110 | ~60 | referenced by system_v1.j2 Rule 6 |
| 25 | freshness header / stale warning | application/envelope.py:35-46 | ~120 | format promised by `_CONTRACT` (tool_docs.py:30-31) |
| 26 | truncation footer | envelope.py:49-71 | ~60 | promised by `_CONTRACT` |
| 27 | pointer render templates (→ …) | formatting.py:104-124 (_SHOW_TO_TOOL), :139-156 (_POINTER_RENDERERS), :179-181 (fallback) | ~800 | promised by `_CONTRACT` ("ready-made follow-up call") |
| 28 | empty-result messages | multi_project_search.py:69-70 ("No matches found." / "No symbols found.") | 17/17 | exported as EMPTY_SEARCH_MESSAGES; ToolRouter appends zero-hit overview pointer |
| 29 | not-found error + search pointer | multi_project_search.py:475-478 | ~90 | — |
| 30 | ServiceUnavailable messages | null_services.py:45 (`reference graph not configured…`), :53 (tree), :64 (decisions) | 90/230/210 | — |
| 31 | file-tools errors | file_tools.py:107-109 (bad regex), :320, :322-325 (binary), :334-336 (offset), :443-448 (read-only bundle), :465-469 (boundary) | ~100-260 each | — |
| 32 | cross-repo links status line | tool_router.py:289 | ~30 | CLI `link` output strings __main__.py:1299-1355 |
| 33 | in-description examples | inside each TOOL_DOCS entry ("Examples:" blocks, e.g. tool_docs.py:44-47) | counted in #1-9 | CLI epilog examples for `lookup` only (__main__.py:479-489) |

**Non-duplication finding:** docs/tool-contracts.md does NOT quote TOOL_DOCS text verbatim. It declares descriptions explicitly mutable: "What is deliberately NOT frozen: Tool **descriptions** (the `TOOL_DOCS` text …) remain mutable. They are the substrate that the text-space description optimizer rewrites; the server reads them via function-local imports at registration time … Names frozen, descriptions optimizable — that split is the point of this freeze." (docs/tool-contracts.md:32-40). It references description CONTENT requirements at :225, :391, :429 (e.g. get_references hedging) — behavioral constraints, not copies.

## Q6 — "Parsing current state into a seed source document"

Largely ALREADY BUILT on the benchmarks side:
- Grammar: benchmarks/src/pydocs_eval/optimize/artifacts/_delimited.py — `render_delimited`/`parse_delimited`/`find_header_collisions`; closed header regex `^=== (SERVER_INSTRUCTIONS|SYSTEM_PROMPT|REWRITE_PROMPT|TOOL: [a-z_]+) ===$` (:31-33); escaping-free by construction (header-like content lines are rejected as collisions, :77-95); round-trip idempotent after one normalization pass (:11-18).
- `ToolDocsArtifact` (artifacts/tool_docs.py): `render()` serializes live `SERVER_INSTRUCTIONS` + each `TOOL_DOCS[name]` under `TOOL: <name>` headers in dict order (:58-65); `with_content` (:67-69); `validate()` (:71-87) re-runs §D13 rules against the IMPORTABLE product constants (`REQUIRED_MARKERS`, `CHARS_PER_TOKEN`, `PER_TOOL_TOKEN_BUDGET`, `TOTAL_TOKEN_BUDGET` — tool_docs.py:14-23, imported at artifact :22-29) — structure, order, markers, budgets. `landing_note()` (:89-95) points at the product file + lint test.
- Injection: `_overlay_server.py` `serve_with_overlay` validates then re-binds `td.SERVER_INSTRUCTIONS` and `td.TOOL_DOCS[name]` before `pydocs_mcp.server.run` (:99-116, :67-96); fail-closed via `OverlayValidationError` (:63-64, :106-110). Zero product hook needed because of the function-local imports (module docstring :1-30 cites server.py line numbers).
- `AskPromptArtifact` (artifacts/ask_prompt.py): two-section doc SYSTEM_PROMPT + REWRITE_PROMPT (:41-43); seed = live product prompts, rewrite rendered with LITERAL `{history}`/`{question}` (:57-68); budgets 1200/300 tok (:49-51); validate requires all nine TOOL_DOCS names in the system section (:155-160); feeds `AskPrompts.system_prompt` / `reformulate(rewrite_template=…)` (:118-124).
- **TOOL_DOCS shape answer:** yes, `dict[str, str]` keyed by tool name; values are the full description strings (multi-section prose incl. Examples). Param descriptions are NOT included and are not separate either — they do not exist on the MCP surface at all. A Phase-1 seed document that wants param descriptions must CREATE them (new sections in the grammar + a new attachment mechanism, e.g. `Field(description=...)` on mcp_inputs models or Annotated descriptions on handler signatures — both currently absent by design).
- Not covered by today's seed grammar: parameter descriptions, CLI-only argparse help, retrieval tree-reasoning prompts (versioned .j2, selected via YAML), vision/reinspect prompts, and the result-rendering strings of Q7.

## Q7 — Other model-facing strings in results

**Plausibly optimizable prose** (wording free; format promised loosely by `_CONTRACT`):
- Freshness header `[index: {sha7} · {n}d old · {k} packages]` + `[⚠ index stale: indexed {sha}, working tree at {sha} — run \`pydocs-mcp index .\`]` — envelope.py:35-46.
- Truncation footer `[truncated: {n} section{s} — recovery pointers inline]` + `- {description} {recovery}` lines — envelope.py:49-71; per-entry descriptions like "package doc for {pkg} truncated at {N} chars" formatting.py:414.
- Resolved pointer text: `→ get_references(target="…", direction="callers")` / `→ pydocs-mcp refs … --direction callers` etc. — formatting.py:104-124, :139-156, :179-181 (mcp+cli renderer pairs per action/show-mode).
- Empty-result bodies "No matches found." / "No symbols found." — multi_project_search.py:69-70 (single source, exported EMPTY_SEARCH_MESSAGES :71 so ToolRouter appends `[[next:overview:]]` zero-hit recovery).
- Reference-view empties: "No {caller|callee}s found." / "No inheritance edges found for `target`." / "Nothing transitively calls `target`." — formatting.py:494, :499, :706 (docstring-pinned renderings).
- Error texts reaching the model as JSON-RPC errors (typed MCPToolError subclasses raise through FastMCP — server.py:12-16, application/mcp_errors.py): null_services.py:45-69 (three YAML-anchored messages), multi_project_search.py:475-478 (NotFoundError embedding a literal `[[next:search:…]]` token — comment :465-468 notes str(exc) carries it on both surfaces), file_tools.py:107-109/320/322-325/334-336/443-448/465-469, mcp_inputs validator messages (:258-260, :267, :311-312, :390, :417-418, :459, :483, :539).
- Overview card copy incl. `cross-repo links: {status}` (tool_router.py:289), stat line `[{n} packages · {m} modules · …]` (formatting.py:950).
- Agent-side: scope-pin note (agent.py:101-110), image placeholders (:338), reinspect budget message (template #17).

**Fixed rendering / grammar — NOT optimizable text:**
- The `[[next:action:target(:show)]]` token grammar itself (formatting.py:92-100 regex; ':' and ']' are forbidden in targets because they corrupt it — mcp_inputs.py:57-62, :410-418).
- The envelope structure {text, items[], meta} and item field names — frozen by docs/tool-contracts.md (§2) and pinned by ENVELOPE_MODELS (tool_response.py).
- `cat -n`-style line rendering `{ln:>6}\t{line}` (file_tools.py:338-339) — contract §3.9.
- The delimited seed grammar (_delimited.py) — the optimizer's container, not its content.
- §D13 lint pins that CONSTRAIN any rewrite: five REQUIRED_MARKERS in every doc, "ONE call" in get_context/get_why, `project="` example in every doc, forbidden legacy tokens `lookup(` / `show="` (tests/application/test_tool_docs_lint.py:37-63), per-tool 500 tok / total 3600 tok budgets (:47-54) — mirrored verbatim in ToolDocsArtifact.validate().

## Environment notes
- Worktree HEAD f4a8f2e == main; git status clean (session snapshot).
- `~/.pydocs-mcp/` and `~/pydocs-index` were NOT inspected (not needed for this static inventory; no queries executed against indexes).
