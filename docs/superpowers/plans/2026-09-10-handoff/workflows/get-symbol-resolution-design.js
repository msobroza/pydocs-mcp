export const meta = {
  name: 'get-symbol-resolution-design',
  description: 'Understand + design (judge panel, adversarial critics) contract-compatible get_symbol target resolution: src-root strip, unique bare name, closest-name hints',
  phases: [
    { title: 'Understand', detail: 'five parallel readers: contract, resolution path, src-root discovery, symbol lookup data, tests/infra' },
    { title: 'Design', detail: 'three independent designs from different angles' },
    { title: 'Judge', detail: 'score, pick winner, synthesize with grafts' },
    { title: 'Critique', detail: 'contract-compliance and correctness critics try to break it' },
    { title: 'Finalize', detail: 'apply verified critiques, write + commit the design doc' },
  ],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const WT = S + '/symbol-resolve'
const HANDOFF = '/Users/msobroza/pydocs-handoffs/2026-09-10/HANDOFF-get-symbol-resolution.md'
const GROUND = `Repo pydocs-mcp. Read code ONLY from the worktree ${WT} (branch feat/get-symbol-resolution at origin/main 5461d8e; no venv yet). READ-ONLY phase: do not edit, build, commit or push anything; do not touch the main checkout ${R} or other worktrees. Read ${WT}/CLAUDE.md first — especially "MCP API surface vs YAML configuration" (nine tools FROZEN by docs/tool-contracts.md; no new tool, no new parameter), Null-object, uow_factory and default-values rules.
PROBLEM (owner-assigned 2026-09-10): the ask-your-docs agent asked about example_needle (a src/ layout project: src/needle/...) called get_symbol once per turn with a failing target, got isError, then retried with the right name. CLI repro against the same index:
  - symbol needle.scoring.strategies.MaxSimScorer → works (node_id needle.scoring.strategies.MaxSimScorer)
  - symbol src.needle.scoring.strategies.MaxSimScorer → "Error: no module matching 'src.needle.scoring.strategies.MaxSimScorer' found under 'src'"   (application/lookup_service.py:398)
  - symbol MaxSimScorer → "Error: package 'MaxSimScorer' not indexed"   (application/lookup_service.py:430)
GOAL, in order of preference: (1) when a dotted target fails and starts with a source-root segment the project's discovery strips (e.g. src.), retry once without it; (2) when a bare undotted name does not match a package, look it up as a unique symbol name in __project__ and resolve when exactly one match exists; (3) otherwise put the closest indexed qualified names in the error text or meta.suggestion. Resolution logic in the application layer (not server.py); a structured JSON log event when a fallback resolves; regression tests for all three shapes + an ambiguous bare name that still errors and lists candidates; docs/tool-contracts.md changes only if the contract text requires it (ADR process — owner-gated); CHANGELOG [Unreleased] entry.
LIVE REPRO (optional, free — get_symbol never calls the embedding API): the PyPI 0.6.1 CLI is ~/venvs/ayd-openrouter/bin/pydocs-mcp and the indexed bundle is ~/pydocs-openrouter/index (config ~/pydocs-openrouter/config.yaml). The config's embedder needs the key only to construct: run \`export OPENROUTER_API_KEY="$(grep -E '^OPENROUTER_API_KEY=' ${R}/.env | cut -d= -f2-)"\` and NEVER print, echo or log it. Command shape: \`pydocs-mcp --config ~/pydocs-openrouter/config.yaml symbol <target> --workspace ~/pydocs-openrouter/index\` (also \`context\`, \`refs\`). Do NOT run search/why (they embed = paid). The 0.6.1 code may differ slightly from origin/main — cite origin/main lines from ${WT}.
Cite evidence as path:line from ${WT}. Report facts, not guesses; mark anything unverified.`

const FACTS = {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    facts: { type: 'array', items: { type: 'object', properties: { claim: { type: 'string' }, evidence: { type: 'string' } }, required: ['claim', 'evidence'] } },
    open_questions: { type: 'array', items: { type: 'string' } },
  },
  required: ['summary', 'facts', 'open_questions'],
}

const READERS = [
  { key: 'contract', prompt: `READER — CONTRACT & ADRs. Read docs/tool-contracts.md fully (esp. §1 freeze, §2 envelope incl. errors/isError, §2.1, §2.3 meta.suggestion — it names exactly three suggestion-emitting tools, §3 dotted-target grammar, §3.3 get_symbol, §3.4/§3.5 which share the grammar, §5, §6 migration row 5 "bare project-qualified names now resolve"), ADR 0007 (suggestions) and any ADR describing target resolution or the amendment process (docs/adr/). Answer precisely: (a) is it contract-compatible WITHOUT a text change for get_symbol to resolve previously-erroring targets (src.-prefixed dotted, unique bare name)? cite the grammar text. (b) Is the error TEXT frozen anywhere (exact strings, error envelope)? (c) Can get_symbol carry meta.suggestion without amending §2.3? If not, what exact minimal amendment + ADR steps would be needed, and is it owner-gated (look for ratification language / ADR 0007 process)? (d) Does the grammar/§3.3 already promise anything about bare names, ambiguity, or which of get_context/get_references must behave the same (consistency obligation)? (e) the doc-conformance tests that parse tool-contracts.md (find them under tests/) and what they would force if the text changes.` },
  { key: 'resolution', prompt: `READER — RESOLUTION PATH. Trace get_symbol end to end on origin/main: server.py get_symbol (~:735) → application/tool_router.py get_symbol (~:152, _resolve_source ~:113) → application/lookup_service.py (target parsing, parsed.package, the __project__ bare project-qualified path from migration row 5, the raise sites :398 and :430, _resolve_context_target ~:666) → symbol_source.py; plus the CLI \`symbol\` path in __main__.py and how NotFoundError becomes an MCP isError result / CLI "Error:" line. Explain EXACTLY why 'src.needle.scoring.strategies.MaxSimScorer' produces "found under 'src'" (which branch treats 'src' as a package? why does needle.* resolve — how is __project__ matched?) and why 'MaxSimScorer' produces "package ... not indexed". Map every place a fallback could hook in (function names + lines), which other tools share this resolver (get_context, get_references, lookup alias) and whether a fix at the shared resolver changes them too. Note multi-project (project= selector, multirepo.py / multi_project_search.py) handling of targets. Give the call signatures and return types involved.` },
  { key: 'srcroot', prompt: `READER — SOURCE-ROOT DISCOVERY. Find how the indexer turns a project file path (e.g. <root>/src/needle/scoring/strategies.py) into a module/qualified name (needle.scoring.strategies) — which code strips 'src' (search extraction/strategies/discovery, extraction/, application/project_indexer or indexing_service, deps.py, db.py: names like module_name, _module_from_path, source_root, src_layout, package_dir, strip). Answer: (a) exactly which leading segments are stripped (only 'src'? 'lib'? 'python'? pyproject [tool.setuptools] package-dir / maturin python-source?), is it config-driven (YAML discovery.* / pyproject) or hardcoded; (b) is the stripped segment RECORDED anywhere at index time (index_metadata table, packages row, chunk metadata, document_trees) so the query side can know which prefixes this project strips without re-deriving from the filesystem; (c) if not recorded, can the query side derive it cheaply and deterministically (e.g. from stored file paths vs module names in module_members/document_trees rows) — show a concrete query; (d) this repo itself uses a maturin python/ layout (python/pydocs_mcp) — would 'python.pydocs_mcp.x' be the analogous failing target? Verify with a real index row shape if possible (sqlite3 on ~/pydocs-openrouter/index/*.db is allowed read-only: \`sqlite3 -readonly\`).` },
  { key: 'lookupdata', prompt: `READER — SYMBOL LOOKUP DATA & CANDIDATES. For preference (2) and (3): which persisted data can answer "which __project__ symbols are named X" and "which indexed qualified names are closest to target T"? Examine db.py schema (module_members, document_trees, chunks, node_references, packages, index_metadata) and the repository Protocols in storage/protocols.py + Sqlite repositories (list/filter capabilities, filter tree vocabulary in filters.py) — can it be done through uow.module_members / uow.trees with the existing FilterAdapter, or does it need a new repository method? What node kinds count as a "symbol" (class, function, method, module? md headings?) and what do other tools treat as addressable targets? Measure on the real bundle (sqlite3 -readonly ~/pydocs-openrouter/index/*.db): how many rows are named MaxSimScorer; how many bare names in __project__ are non-unique (ambiguity rate); sizes (rows) to judge the cost of a closest-name scan; is there an existing fuzzy/closest helper (difflib, rapidfuzz, _closest, did_you_mean) anywhere in python/. Also: how do dependency packages (non-__project__) qualify — should a bare-name fallback ever search deps? Consider 'scope'/'project' semantics.` },
  { key: 'infra', prompt: `READER — TESTS, LOGGING & GATES INFRA. Find: existing tests for lookup_service / get_symbol / tool_router / CLI symbol (file names, fixtures, fakes: tests/_fakes.py make_fake_uow_factory, any in-memory index builders or tmp src-layout projects already used in tests); how tests assert NotFoundError / isError; the structured-log conventions (application/suggestions.py log_suggestion_fired, other json.dumps({"event": ...}) sites) and how tests capture them (caplog?); module line budgets (tests/*line_budget*), complexipy snapshot rules, vulture; the CHANGELOG.md current top (is there an [Unreleased] on origin/main?); doc/README conformance tests that might pin get_symbol behaviour or error strings (grep tests for 'not indexed', 'no module matching'); YAML/AppConfig: does any existing setting govern lookup/resolution (so a new toggle would be a YAML field per CLAUDE.md, with single-source default)? Also list the exact CI gate commands from CLAUDE.md "Tests & Lint" and ci.yml's python job install line.` },
]

phase('Understand')
const understood = await parallel(READERS.map(r => () =>
  agent(`${GROUND}\n\n${r.prompt}`, { label: `read:${r.key}`, phase: 'Understand', schema: FACTS, model: 'opus', effort: 'high' })
    .then(x => (x ? { key: r.key, ...x } : null))))
const ctx = understood.filter(Boolean)
log(`readers returned: ${ctx.map(c => c.key).join(', ')} (${ctx.length}/${READERS.length})`)
const CTX = ctx.map(c => `### ${c.key}\n${c.summary}\nFACTS:\n${c.facts.map(f => `- ${f.claim} [${f.evidence}]`).join('\n')}\nOPEN:\n${c.open_questions.map(q => `- ${q}`).join('\n')}`).join('\n\n')

const ANGLES = [
  { key: 'minimal', text: 'MINIMAL-CHANGE angle: the smallest diff that fixes the three observed shapes, reusing existing resolver branches and repository methods; no new YAML unless required.' },
  { key: 'contract', text: 'CONTRACT-FIRST angle: start from what docs/tool-contracts.md already permits; prefer behaviour that needs zero contract text change (e.g. candidates in the error text rather than meta.suggestion) and keep get_symbol/get_context/get_references consistent under the shared grammar; spell out any amendment that would be unavoidable.' },
  { key: 'robust', text: 'ROBUSTNESS-FIRST angle: correctness under ambiguity, multi-project workspaces, dependency packages shadowing project names, non-Python/markdown nodes, performance of candidate search on large indexes, deterministic ordering, and never resolving to the WRONG symbol silently.' },
]
const DESIGN_SPEC = `Write a complete implementation design (markdown) covering: 1) root cause per failing shape with path:line; 2) the resolution algorithm for (1)/(2)/(3), exact hook point(s) and function signatures (functions 4-20 lines), where the src-root knowledge comes from, when the fallback fires and when it must NOT (e.g. a real package named src; ambiguity; deps); 3) error text and/or meta.suggestion wording (exact strings) with a contract analysis citing tool-contracts.md sections — state explicitly whether any contract text must change; 4) the structured JSON log event (name + fields); 5) effects on get_context/get_references and the CLI; 6) config: is any YAML toggle needed (justify; single-source default); 7) numbered acceptance criteria; 8) test list mapped to ACs (file names, fakes, including: src.-prefixed resolves; unique bare name resolves; unresolvable target lists closest candidates; ambiguous bare name errors and lists candidates; default/regression cases unchanged byte-for-byte); 9) docs/CHANGELOG changes; 10) risks and open owner decisions.`

phase('Design')
const designs = await parallel(ANGLES.map(a => () =>
  agent(`${GROUND}\n\nREADER FINDINGS (origin/main):\n${CTX}\n\nDESIGN TASK — ${a.text}\n${DESIGN_SPEC}\nVerify any claim you rely on in the code; you may run the free CLI repro.`, { label: `design:${a.key}`, phase: 'Design', model: 'opus', effort: 'high' })
    .then(t => (t ? { key: a.key, text: t } : null))))
const ds = designs.filter(Boolean)
log(`designs produced: ${ds.map(d => d.key).join(', ')}`)

phase('Judge')
const synthesized = await agent(`${GROUND}\n\nREADER FINDINGS:\n${CTX}\n\nYou are the judge. Below are ${ds.length} independent designs. Score each 1-5 on: contract compliance (no new tool/param; minimal or no contract text change), correctness (never silently resolves to a wrong symbol; ambiguity handled), simplicity (smallest diff, CLAUDE.md code shape), testability, and consistency across the tools that share the dotted-target grammar. Print the score table, name the winner, then write ONE synthesized final-candidate design (same 10 sections as the inputs) built on the winner and grafting the best ideas from the others. Verify contested facts in the code before choosing.\n\n${ds.map((d, i) => `===== DESIGN ${i + 1} (${d.key}) =====\n${d.text}`).join('\n\n')}`, { label: 'judge:synthesize', phase: 'Judge', model: 'opus', effort: 'high' })

const CRIT = {
  type: 'object',
  properties: {
    findings: { type: 'array', items: { type: 'object', properties: {
      severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, claim: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' } }, required: ['severity', 'claim', 'evidence', 'fix'] } },
    verdict: { type: 'string' },
  },
  required: ['findings', 'verdict'],
}
phase('Critique')
const critiques = await parallel([
  ['contract', `CRITIC — CONTRACT & CONVENTIONS. Try to break the design against docs/tool-contracts.md (freeze, §2.3 suggestion-tool list, grammar, error envelope, conformance tests), the ADR amendment process, and CLAUDE.md rules (no new MCP param; YAML-only tunables with single-source defaults; application-layer placement; uow_factory; Null-object; function/file size; structured JSON logs; greppable names). Any contract text change the design missed or glossed = blocker. Only report findings you verified in the code/docs.`],
  ['correctness', `CRITIC — CORRECTNESS & EDGE CASES. Try to make the design resolve the WRONG symbol or regress a working call: a real package/module named 'src' or the stripped segment; a project module actually named like a dependency package; a bare name that equals an indexed package name (must keep package semantics); ambiguous names (class vs function vs method vs md heading with the same name); multi-project workspaces and the project= selector; case sensitivity; targets with trailing members (Class.method); very large indexes (cost of the candidate scan, deterministic ordering, result caps); get_context/get_references parity; CLI vs MCP differences; the log event firing exactly when a fallback resolves. Use the real bundle read-only (sqlite3 -readonly) or the free CLI to test claims. Only report verified findings.`],
].map(([k, p]) => () => agent(`${GROUND}\n\nREADER FINDINGS:\n${CTX}\n\nDESIGN UNDER REVIEW:\n${synthesized}\n\n${p}`, { label: `critic:${k}`, phase: 'Critique', schema: CRIT, model: 'opus', effort: 'high' }).then(x => (x ? { key: k, ...x } : null))))
const cr = critiques.filter(Boolean)
const CR = cr.map(c => `### critic:${c.key} — ${c.verdict}\n${c.findings.map(f => `- [${f.severity}] ${f.claim}\n  evidence: ${f.evidence}\n  fix: ${f.fix}`).join('\n')}`).join('\n\n')

phase('Finalize')
const final = await agent(`${GROUND.replace('READ-ONLY phase: do not edit, build, commit or push anything;', 'FINALIZE phase: you may write ONE file and commit it (no push);')}

DESIGN:\n${synthesized}\n\nCRITIQUES:\n${CR}

TASK: re-verify each critique finding yourself; apply every confirmed one; reject (with a one-line reason) any you cannot reproduce. Then write the final design to ${WT}/docs/superpowers/specs/2026-09-10-get-symbol-target-resolution-design.md (internal planning artifact: sections 1-10, a "Critique resolution" table, and a top "Owner decisions" list — EMPTY if nothing needs the owner). Be explicit on: does docs/tool-contracts.md text need to change (yes/no + exact diff if yes); exact error/suggestion strings; exact log event; AC list; test list. Commit only that file on feat/get-symbol-resolution with the existing git identity, NO trailers (\`git -C ${WT} add <file> && git -C ${WT} commit -m "docs(spec): get_symbol target resolution design"\`), never push. Append a dated entry to ${HANDOFF} (create it if missing: what was decided, the commit SHA, open owner decisions, exact next step = implement per the spec test-first). Return: the commit SHA, the contract verdict, the owner-decision list, and the full final design text.`, { label: 'finalize:design', phase: 'Finalize', model: 'opus', effort: 'high' })

return { readers: ctx.map(c => c.key), designs: ds.map(d => d.key), critiques: cr.map(c => ({ key: c.key, verdict: c.verdict, n: c.findings.length })), final }
