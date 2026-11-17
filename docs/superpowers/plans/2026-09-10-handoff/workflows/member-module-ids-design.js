export const meta = {
  name: 'member-module-ids-design',
  description: 'Design the root-cause fix (owner-approved OD-2): AstMemberExtractor member module ids must follow the chunker package-root rule; upgrade/reindex path, consumers, compat — readers → 2 designs → judge → critics → committed spec',
  phases: [
    { title: 'Understand', detail: 'extractor vs chunker id rules · consumers · upgrade/reindex mechanics · real-index measurements' },
    { title: 'Design', detail: 'minimal vs upgrade-first' },
    { title: 'Judge' },
    { title: 'Critique', detail: 'correctness/edge cases · upgrade/compat/contract' },
    { title: 'Finalize', detail: 'verified critiques applied, spec committed' },
  ],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const WT = S + '/member-ids'
const HANDOFF = '/Users/msobroza/pydocs-handoffs/2026-09-10/HANDOFF-member-module-ids.md'
const GSPEC = S + '/symbol-resolve/docs/superpowers/specs/2026-09-10-get-symbol-target-resolution-design.md'
const GROUND = `Repo pydocs-mcp. Read code ONLY from worktree ${WT} (branch fix/member-module-ids at origin/main 5461d8e; NO venv — do not build or install). READ-ONLY phase except the Finalize agent. Never touch the main checkout ${R} or other worktrees. Read ${WT}/CLAUDE.md first (hexagonal layers, uow_factory, single-source defaults, two-level cache: package content_hash skip + chunk content_hash with pipeline_hash, fallback contract Rust/Python, no PR jargon in READMEs).
OWNER-APPROVED TASK (OD-2, 2026-09-10): fix the ROOT CAUSE found by the get_symbol resolution design (${GSPEC}, "OD-2" section; read it): AstMemberExtractor builds member module ids with os.path.relpath(filepath, root) (python/pydocs_mcp/extraction/strategies/members/ast_extractor.py:166-169), skipping the chunker's package-root rule (python/pydocs_mcp/extraction/strategies/chunkers/ast_python.py:483-538 — ids rooted at the parent of the topmost __init__.py directory). In the example_needle bundle module_members has 128 modules starting with 'src.' (e.g. src.needle.scoring.strategies) while chunks has none (apart from 5 '#'-suffixed egg-info rows); search advertises those names (application/multi_project_search.py:231-233 qname, application/formatting.py:307-308 [[next:lookup:…]] pointer), so agents copy 'src.'-prefixed targets that then fail in get_symbol. The separate get_symbol PR (branch feat/get-symbol-resolution, in progress) adds a Rule-1 fallback that resolves such names; THIS fix removes the bad ids at the source. Separate PR; merge order must not matter.
Evidence tools (free, read-only): \`sqlite3 -readonly\` on ~/pydocs-openrouter/index/*.db and on other bundles under ~/.pydocs-mcp/*.db (8 GB of real indexes — query, never modify). Cite path:line from ${WT}. Facts only; mark anything unverified.`

const FACTS = { type: 'object', properties: {
  summary: { type: 'string' },
  facts: { type: 'array', items: { type: 'object', properties: { claim: { type: 'string' }, evidence: { type: 'string' } }, required: ['claim', 'evidence'] } },
  open_questions: { type: 'array', items: { type: 'string' } } }, required: ['summary', 'facts', 'open_questions'] }

const READERS = [
  ['id_rules', `READER — ID RULES. Compare exactly how each extractor derives a module id: AstMemberExtractor (ast_extractor.py), InspectMemberExtractor (inspect mode, dependencies), the AST chunker (ast_python.py:483-538 package-root rule), the markdown/notebook/text/multilang chunkers, and any shared helper (module_name_from_path or similar). Which layouts differ today: src/ layout, maturin python/ layout (this repo: python/pydocs_mcp), flat layout, namespace packages without __init__.py, scripts/ and tests/ dirs, top-level modules, nested non-package dirs, notebooks, dependency packages from site-packages (inspect vs static --no-inspect). Is there ONE function both should call (DRY single source) and where should it live (extraction/ layer)? Does the Rust/Python fallback contract (_fast/_native) produce module names anywhere (src/lib.rs parse functions)?`],
  ['consumers', `READER — CONSUMERS OF MEMBER MODULE IDS. Find every read of module_members.module (and member qualified names): search (multi_project_search.py qname, formatting.py next:lookup pointer), get_symbol/lookup_service member lookups, module_inspector, get_overview listings, reference graph (node_references — are member ids used as node ids/targets? CALLS/IMPORTS/INHERITS edges keyed by module paths?), graph_expand / centrality steps, decision GOVERNS edges, ask-your-docs graph page, benchmarks (pydocs_eval systems, oracles, gold labels that may embed module ids!). For each: does it currently rely on the src.-prefixed shape, and would changing member ids to the chunker's shape break or fix it? Measure on the example_needle bundle: join module_members.module against chunks/document_trees module ids — how many member rows have no matching chunk/tree module today, and how many would match after stripping to the chunker rule.`],
  ['upgrade', `READER — UPGRADE / REINDEX MECHANICS. After the fix, what happens to an EXISTING index on the next \`pydocs-mcp index .\`? Trace: package-level skip (packages.content_hash = xxh3 of (path, mtime) — does an unchanged project skip member re-extraction entirely?), chunk-level content_hash + pipeline_hash (does pipeline_hash include extractor versions / member extraction? ingestion.yaml bytes?), index_metadata (project/embedder identity, schema versions), \`--force\` (IndexingService.clear_all), the watcher, serve-time checks (EmbedderMismatchError-style guards), multirepo bundles. How were previous id-shape or extraction changes migrated (git log / CHANGELOG / ADRs: e.g. multilang scope folded into ingestion_pipeline_hash "re-embeds by design")? Options: (1) fold an extractor-version constant into the package content_hash or pipeline_hash so the next index re-extracts automatically (cost: re-embed?), (2) a cheap SQL migration rewriting module_members ids in place at open time, (3) document --force. For each give exact code points, cost (does re-extraction force re-EMBEDDING chunks? chunk hashes are content-based so unchanged chunks should skip embedding — verify), and risk.`],
  ['measure', `READER — REAL-INDEX MEASUREMENTS & TEST INFRA. Across the bundles in ~/.pydocs-mcp/*.db and ~/pydocs-openrouter/index/*.db (read-only): for each, the project root layout if inferable, count module_members modules under __project__ whose id is not a chunk/tree module id, group by the extra leading segment (src., python., lib., other), and a few examples. Check dependency packages too (are member ids for deps consistent with chunk ids?). Then the test infra: existing tests for ast_extractor / member extraction / indexing of src-layout projects (tests/…), fixtures that build tmp projects, tests or golden files that ASSERT the current src.-prefixed member ids (they would need updating), benchmark gold data embedding module ids (grep benchmarks/ for "src." module strings).`],
]

phase('Understand')
const understood = await parallel(READERS.map(([k, p]) => () =>
  agent(`${GROUND}\n\n${p}`, { label: `read:${k}`, phase: 'Understand', schema: FACTS, model: 'opus', effort: 'high' }).then(x => (x ? { key: k, ...x } : null))))
const ctx = understood.filter(Boolean)
const CTX = ctx.map(c => `### ${c.key}\n${c.summary}\nFACTS:\n${c.facts.map(f => `- ${f.claim} [${f.evidence}]`).join('\n')}\nOPEN:\n${c.open_questions.map(q => `- ${q}`).join('\n')}`).join('\n\n')
log(`readers: ${ctx.map(c => c.key).join(', ')}`)

const SPEC_SECTIONS = `Write a complete design (markdown): 1) root cause with path:line and measured impact; 2) the single-source module-id rule and exactly which extractors call it (signatures, placement, Rust/Python parity if relevant); 3) consumer impact table (each consumer: before/after, breaks/fixes); 4) upgrade path for existing indexes (chosen mechanism, exact code points, what a user sees, cost incl. whether chunks re-embed), and interplay with the separate get_symbol Rule-1 PR (merge order independent); 5) any config (YAML only, single-source defaults) — prefer none; 6) acceptance criteria (numbered); 7) test list mapped to ACs incl. src/, python/ (maturin), flat, namespace-package layouts and an upgrade test on an index built with the old ids; 8) docs/CHANGELOG (user-facing note: re-index needed or automatic); 9) risks + owner decisions (empty if none).`
phase('Design')
const designs = await parallel([
  ['minimal', 'MINIMAL angle: smallest diff that makes member ids equal the chunker ids for every layout, plus the cheapest correct upgrade path.'],
  ['upgrade_first', 'UPGRADE-FIRST angle: existing users must get correct ids automatically on their next index/serve without a surprise full re-embed; zero silent mixed-id states; multi-project bundles; benchmark caches.'],
].map(([k, a]) => () => agent(`${GROUND}\n\nREADER FINDINGS:\n${CTX}\n\nDESIGN — ${a}\n${SPEC_SECTIONS}\nVerify facts you rely on.`, { label: `design:${k}`, phase: 'Design', model: 'opus', effort: 'high' }).then(t => (t ? { key: k, text: t } : null))))
const ds = designs.filter(Boolean)

phase('Judge')
const synth = await agent(`${GROUND}\n\nREADER FINDINGS:\n${CTX}\n\nJudge these ${ds.length} designs (score 1-5: correctness across layouts, upgrade safety, simplicity/diff size, testability, consumer compatibility), name the winner, then write ONE synthesized design (same sections) grafting the best of the other. Verify contested facts.\n\n${ds.map((d, i) => `===== DESIGN ${i + 1} (${d.key}) =====\n${d.text}`).join('\n\n')}`, { label: 'judge', phase: 'Judge', model: 'opus', effort: 'high' })

phase('Critique')
const CRIT = { type: 'object', properties: { verdict: { type: 'string' }, findings: { type: 'array', items: { type: 'object', properties: {
  severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, claim: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' } }, required: ['severity', 'claim', 'evidence', 'fix'] } } }, required: ['verdict', 'findings'] }
const crit = await parallel([
  ['correctness', 'CORRECTNESS & EDGE CASES: find a layout or file where the new id is wrong or collides (two files → same id; namespace packages; src/ with a top-level module beside packages; tests/ inside src/; python/ maturin; __main__.py; conftest; notebooks; .pyi stubs; symlinks; Windows separators), a consumer that silently breaks (reference graph edges, graph node ids, benchmark gold labels, module_inspector), or a Rust/Python parity gap. Test claims on real bundles (read-only).'],
  ['upgrade_compat', 'UPGRADE, COMPAT & CONTRACT: try to produce a mixed-id index (some packages re-extracted, others skipped), a surprise full re-embed, a stale watcher/serve process, a multi-project bundle mismatch, broken benchmark index caches, or a contract violation (docs/tool-contracts.md: does any contract text define module ids / get_overview listing shape?). Check the CHANGELOG/user note is honest.'],
].map(([k, p]) => () => agent(`${GROUND}\n\nREADER FINDINGS:\n${CTX}\n\nDESIGN UNDER REVIEW:\n${synth}\n\nCRITIC — ${p} Report only verified findings.`, { label: `critic:${k}`, phase: 'Critique', schema: CRIT, model: 'opus', effort: 'high' }).then(x => (x ? { key: k, ...x } : null))))
const CR = crit.filter(Boolean).map(c => `### ${c.key} — ${c.verdict}\n${c.findings.map(f => `- [${f.severity}] ${f.claim}\n  evidence: ${f.evidence}\n  fix: ${f.fix}`).join('\n')}`).join('\n\n')

phase('Finalize')
const final = await agent(`${GROUND.replace('READ-ONLY phase except the Finalize agent.', 'FINALIZE: you may write ONE file and commit it (no push).')}

DESIGN:\n${synth}\n\nCRITIQUES:\n${CR}

Re-verify each critique; apply confirmed ones; reject (one-line reason) any you cannot reproduce. Write the final spec to ${WT}/docs/superpowers/specs/2026-09-10-member-module-ids-design.md (sections 1-9 + "Critique resolution" table + top "Owner decisions" list, EMPTY if none). Commit only that file on fix/member-module-ids with the existing identity, NO trailers ("docs(spec): member module ids follow the package-root rule"), never push. Create/append ${HANDOFF} (decisions, SHA, open owner decisions, next step = implement test-first). Return the SHA, owner decisions, upgrade-path verdict, and the full final spec text.`, { label: 'finalize', phase: 'Finalize', model: 'opus', effort: 'high' })
return { readers: ctx.map(c => c.key), designs: ds.map(d => d.key), final }
