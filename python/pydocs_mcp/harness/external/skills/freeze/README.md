# Frozen skill assets for the external CLI harness

**This directory is empty on purpose.** It is the sanctioned home for future
harness-LOCAL frozen assets, and it carries the rule that decides what may
land here.

## The rule

A text asset consumed by an optimizable task that is **not itself an
optimization target** is a *frozen experimental control*: it must not drift
between the runs that are being compared, so it is digested and pinned. That
is the doctrine `python/pydocs_mcp/harness/core/prompt_freeze.py` states for
prompt templates, and the third status
(`ACTIVE` / `INACTIVE` / frozen-control) that
`python/pydocs_mcp/harness/core/prompt_surfaces.py` enumerates.

Two consequences for this directory:

1. **Optimizable sections do not live here.** The `BACKBONE`,
   `TASK_HEAD: <task>` and `HARNESS_TASK_HEAD: external.<task>` sections this
   harness delivers are slices of ONE cross-harness document that is trained as
   a whole. They live in `python/pydocs_mcp/harness/core/skills/` and are
   loaded through `harness/core/skill_artifact_loader.py`. Copying a slice here
   would create a second spelling of a trained artifact — the exact failure the
   single-loader firewall exists to prevent.
2. **A local frozen asset needs a manifest — and there is none yet.** The
   shipped digest mechanism (`frozen_prompt_digests`) covers `*.j2` templates
   only; no `.md` freezing mechanism exists today. So the manifest arrives
   **with the first asset**, not before it: landing one means either extending
   `frozen_prompt_digests` to this directory's file type or minting a sibling
   digest + golden manifest under `tests/fixtures/goldens/`, in the same commit
   as the asset. Until then there is deliberately nothing to pin, and no empty
   manifest file pretending otherwise.
