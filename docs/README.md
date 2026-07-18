# docs/ — internal engineering record

This directory is the project's engineering record, split into two tiers.

## Normative (binding on current code)

- `tool-contracts.md` — the frozen contract for the nine task-shaped MCP
  tools: names, parameter schemas, response envelopes.
- `adr/` — architecture decision records. Each ADR states a decision, its
  context, and its consequences; ADRs are amended by newer ADRs, not edited.
- `description-authoring.md` — the authoring guide for tool and server
  description text (the optimizable description surface).

## Historical / internal (context, not contract)

- `superpowers/plans/`, `superpowers/specs/`, `superpowers/research/` —
  implementation plans, feature specs, and research evidence written for the
  implementer at the time the work was done. They are kept verbatim: code
  comments and tests cite these files by name, so the filenames are frozen.
  Read them for background — where they disagree with the code or with the
  normative tier above, the code and the normative tier win.

Reference documents that used to live at the repo root (`IDEAS.md`,
`PAGEINDEX_DIVS.md`) also live here; code comments cite them by filename.
