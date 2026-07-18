# Authoring the description source

Every LLM-visible description string the MCP server serves — the nine per-tool
descriptions, the server-level instructions, and the session-start context preamble —
lives in **one delimited text document**: the packaged
`python/pydocs_mcp/defaults/descriptions.md`, replaceable at startup by an
override document. This guide is for anyone editing that document or authoring
an override: the grammar, the section conventions, the validation rules that
reject a bad document, and the flags that select which document a server run
serves.

Rationale lives in `docs/adr/0005-description-source-format.md` (why one
delimited document) and `docs/adr/0006-description-injection-and-precedence.md`
(how it is loaded, overridden, and fingerprinted). The tool *names*, parameter
schemas, and response envelope are frozen by `docs/tool-contracts.md`; the
description *prose* documented here is deliberately mutable.

## 1. The grammar

A description document is a sequence of sections. Each section is one header
line followed by free-prose content running to the next header or end of file:

```
=== SERVER_INSTRUCTIONS ===
<prose>
=== TOOL: get_overview ===
<prose>
…
=== SESSION_START_PREAMBLE ===
<prose>
```

Rules, all enforced by
`python/pydocs_mcp/application/description_source.py`:

- **Header form.** Exactly `=== <KEY> ===` on its own line — three equals
  signs, one space each side of the key, nothing else. Anything that does not
  match the header pattern is content.
- **Closed header set.** The set of legal keys is enumerated in one regex
  (`_HEADER_RE`). A header-like line using any other key does not parse as
  content — the parser promotes it to a section of its own, and validation
  rejects it as a **header collision**. There is no escaping mechanism: a
  section body can never smuggle a header, because the smuggled line stops
  being body text the moment it looks like a header. Bad documents fail
  loudly; nothing is silently split or swallowed.
- **Order-preserving, lossless.** Parsing yields `{key: content}` in document
  order; rendering writes sections back in that order. The pair is lossless
  after one normalization pass (see [hash semantics](#5-hash-semantics)).
- **A `.md` file that is not markdown.** `=== … ===` headers render as plain
  paragraphs in markdown viewers. The extension is kept for editor
  ergonomics only.

## 2. Required sections

The product document must contain **exactly eleven sections** — no more, no
fewer, in any order (canonical order below is the convention the packaged
document follows):

| Section | Content |
|---|---|
| `SERVER_INSTRUCTIONS` | Server-level orientation FastMCP advertises to clients at connect time. |
| `TOOL: get_overview` | Tool description (MCP wire) + CLI help (first line). |
| `TOOL: search_codebase` | 〃 |
| `TOOL: get_symbol` | 〃 |
| `TOOL: get_context` | 〃 |
| `TOOL: get_references` | 〃 |
| `TOOL: get_why` | 〃 |
| `TOOL: grep` | 〃 |
| `TOOL: glob` | 〃 |
| `TOOL: read_file` | 〃 |
| `SESSION_START_PREAMBLE` | Framing prose for the session-start context pack (`serve.session_start_context`). |

The nine `TOOL:` keys derive from the frozen tool names of
`docs/tool-contracts.md` §1 — so a tool rename can never silently orphan a
section; it surfaces as an unknown-header failure instead. `SESSION_START_PREAMBLE`
is **always required**, even though the session-start-context feature is off by default: the
section set stays fixed so validation is unconditional and every override
document is complete.

A missing section, an extra section, or a `TOOL:` key naming an unknown tool
is a hard validation error.

## 3. Section conventions and required markers

Section bodies are free prose, with one structural requirement: **every
`TOOL:` section must contain all five required markers** —

- `When to use`
- `When NOT to use`
- `Workflow`
- `Response contract`
- `Examples`

These are **colon-free substring markers**: the validator checks that each
string appears somewhere in the section body, exactly as written above —
without a trailing colon. You are free to style them (`When to use: …`,
`When to use — …`, a heading); what you may not do is reword them
(`When should I use this` does not match) or drop one. The marker constants
are importable as `REQUIRED_MARKERS` from
`pydocs_mcp.application.tool_docs`.

Conventions the packaged seed follows (recommended, not enforced):

- First line of a `TOOL:` section is a one-sentence summary — it doubles as
  the CLI subcommand help text, so make it stand alone.
- `Examples` shows 2–3 runnable calls in tool-call syntax, indented.
- The `Workflow` and `Response contract` lines are shared across sections
  verbatim — the document stores fully expanded text per section (no
  templating), so keeping them identical is the author's job.

`SERVER_INSTRUCTIONS` and `SESSION_START_PREAMBLE` have no marker requirements.

## 4. Token budgets

Budgets are enforced on the **nine `TOOL:` sections only** (server
instructions and the session-start preamble are outside the budget lint):

| Budget | Value | Constant |
|---|---|---|
| Per `TOOL:` section | 500 tokens | `PER_TOOL_TOKEN_BUDGET` |
| All nine `TOOL:` sections combined | 3,600 tokens | `TOTAL_TOKEN_BUDGET` |

Tokens are **estimated**, not model-tokenized: `len(section) // 4` characters
per token (`CHARS_PER_TOKEN = 4`). A section over its budget — or a combined
surface over the total — is a hard validation error naming the offending
section, its estimated token count, and the budget.

All four constants live in
`pydocs_mcp.application.description_source` and are re-exported from
`pydocs_mcp.application.tool_docs`.

## 5. Hash semantics

Every server run logs a fingerprint of the description surface it actually
serves, at startup, next to the "MCP ready" line:

```
descriptions artifact <hash12> source=packaged|<path>
```

`<hash12>` is the first 12 hex chars of `current_artifact_hash()`: the
SHA-256 of the **normalized** rendered surface, prefixed with a renderer
version stamp (`renderer:v1`, the `RENDERER_VERSION` constant).

Two rules make the hash meaningful:

- **One-normalization-pass rule.** Rendering appends one trailing newline per
  section; parsing trims exactly that one back off. So render → parse is not
  byte-stable on the *first* pass when a section's content already ended in a
  newline — but it is idempotent from then on:
  `normalize(normalize(text)) == normalize(text)`. Every fingerprint consumer
  hashes the normalized surface, never raw input bytes. Practical
  consequence: two documents that differ only by a trailing newline inside a
  section hash identically — they serve identical surfaces.
- **Renderer version stamp.** `RENDERER_VERSION` is folded into the hash and
  bumped on any change to how the document is rendered or normalized, so
  equal source bytes under a different renderer never collide with an old
  fingerprint.

The hash is computed on demand from the live module attributes — whatever is
actually bound at the time — so it stays truthful no matter which path bound
the surface (packaged load, an override document, or a test harness rebinding
the attributes directly). It changes if and only if the normalized surface or
the renderer version changes; it does **not** change with the origin (the
same document served via flag, env var, or YAML hashes identically).

## 6. Overrides: flags and precedence

By default the server serves the packaged document. To serve a different one,
name it through any of three channels — highest priority first:

| Priority | Channel | Form |
|---|---|---|
| 1 | CLI flag | `pydocs-mcp serve . --descriptions PATH` |
| 2 | Environment variable | `PYDOCS_SERVE__DESCRIPTIONS_PATH=PATH` |
| 3 | User YAML | `serve.descriptions_path: PATH` in your `pydocs-mcp.yaml` |
| 4 | *(none named)* | packaged `defaults/descriptions.md` |

Notes:

- The env var outranking the YAML key is standard pydantic-settings source
  order; error messages name which channel supplied the winning path so a
  failure is diagnosable without knowing that ordering.
- An empty-string YAML value counts as unset, not as an explicit empty
  source. A **set-but-empty** `PYDOCS_SERVE__DESCRIPTIONS_PATH` is a hard
  error (`EmptyDescriptionsEnvError`): because the env layer outranks the
  YAML key, an empty env value would otherwise silently suppress a
  YAML-configured override into the packaged fallback — unset the variable
  or give it a path.
- The flag outranks the env var on the failure path too: a serve invocation
  carrying `--descriptions` never dies on a set-but-invalid env value — the
  flag-named source is the one that must load.
- The flag also applies to `pydocs-mcp serve . --watch`.
- **CLI-help parity:** an exported `PYDOCS_SERVE__DESCRIPTIONS_PATH` is
  applied before the argument parser is built, so `pydocs-mcp <tool> --help`
  renders the same description bundle the MCP server would serve.

## 7. Strictness

**An explicitly named source that is missing or invalid is a hard startup
error — never a silent fallback.** If any of the three channels names a path,
that document must load and validate, or the server refuses to start. The
packaged default is used only when *no* override was named at all. This is
what makes an A/B run trustworthy: a candidate document can never silently
degrade to the shipped defaults mid-experiment.

Validation runs before anything is bound (a bad document can never
half-apply) and raises a typed error carrying the offending values:

| Error | Meaning |
|---|---|
| `HeaderCollisionError` | A section header outside the allowed set — an unknown key, a renamed tool, or a header-like line smuggled into a body. |
| `MissingSectionError` | One or more of the eleven required sections is absent. |
| `MissingMarkerError` | A `TOOL:` section lacks one or more of the five required markers. |
| `TokenBudgetExceededError` | A `TOOL:` section (or the nine-section total) exceeds its budget. |
| `StrayContentError` | Non-blank content before the first section header (e.g. a git-conflict marker block) — it would otherwise be silently dropped. |
| `DuplicateSectionError` | The same section header appears more than once — last-copy-wins would silently discard the earlier body. |

All six subclass `DescriptionSourceError` (importable from
`pydocs_mcp.application.description_source`), which is also a `ValueError`.
The same validation guards the packaged document at import time — a corrupted
shipped file breaks every entry point loudly rather than serving a partial
surface.

## 8. Adding a tool section (future surface change)

The nine-tool surface is frozen; adding a tool is a contract-versioning event
for `docs/tool-contracts.md` (see its §1 freeze statement), not a routine
edit. When such an
event lands, the description document grows with it:

1. Add the new name to `FROZEN_TOOL_NAMES` in
   `application/description_source.py` (in contract order). The canonical
   header set derives from that tuple, so the new `TOOL: <name>` section
   becomes *required* everywhere at once. The header regex already accepts
   any `TOOL: [a-z_]+` key — no grammar change for a new tool.
2. Add the `=== TOOL: <name> ===` section to the packaged
   `defaults/descriptions.md`, carrying all five required markers, within the
   per-tool budget.
3. Decide whether `TOTAL_TOKEN_BUDGET` grows — the shipped 3,600 total was
   sized for nine sections at ≤500 tokens each with headroom; a tenth section
   consumes that headroom.
4. Update the section table in this document and the contract inventory.
5. Every existing override document is now invalid until it gains the new
   section — deliberate: an override authored against the old surface must
   not silently serve a tool with no description.

Adding a new **section kind** (not a tool) is rarer and touches the shared
grammar: widen the single `_HEADER_RE` alternation and extend each
consumer's allowed-header set (the product's canonical set here, and any
other delimited artifacts that delegate to the same grammar module). A key
present in the regex but absent from a consumer's allowed set parses but is
rejected for that consumer — that firewall is how the product document keeps
out section kinds that belong to other artifacts.

## 9. Related configuration

Adjacent knobs that consume sections of this document or shape the same
LLM-facing surface (all in YAML — the canonical reference is
`python/pydocs_mcp/defaults/default_config.yaml`):

| Key | Default | Effect |
|---|---|---|
| `serve.descriptions_path` | `null` | Override document path (see [precedence](#6-overrides-flags-and-precedence)). |
| `serve.session_start_context.enabled` | `false` | Inject the session-start context pack (marker line + `SESSION_START_PREAMBLE` + overview card + version inventory) into the ask-your-docs agent prompt. `pydocs-mcp session-start-context` prints the pack regardless of the flag. |
| `serve.session_start_context.budget_tokens` | `2000` | Hard cap on the pack, in real (tiktoken) tokens; the overview card is trimmed before the version inventory, and truncation is noted in the pack. |
| `output.suggestions.grep_zero_hit` | `true` | On a zero-hit `grep`, append a fixed `[suggestion: …]` line redirecting to `search_codebase`. |
| `output.suggestions.grep_truncated` | `true` | On a truncated `grep`, append a fixed narrowing hint (`path=` / `glob=` / `head_limit=`). |
| `output.suggestions.search_zero_hit` | `true` | On zero-hit `search_codebase` / `get_why`, append the `get_overview` pointer. |

The suggestion texts themselves are deterministic machinery — fixed
constants in `application/suggestions.py` with the `[suggestion:` prefix,
**not** part of the optimizable description document. A transcript line
starting with that prefix is always server-initiated, never model-earned
routing (`docs/adr/0007-deterministic-routing-suggestions.md`); likewise the
session-start marker line is fixed machinery
(`docs/adr/0008-turn0-context-injection.md`).
