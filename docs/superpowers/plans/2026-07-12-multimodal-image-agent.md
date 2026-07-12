# multimodal-image-agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement spec `docs/superpowers/specs/2026-07-11-multimodal-image-agent-spec.md` — multimodal auto-detection ladder, image attachments, three agent architectures behind a new `agent_registry`, `AskYourDocsConfig` on AppConfig, graceful text-only degradation. 25 ACs.

**Architecture:** The spec's §3 design is adopted verbatim (its inline code sketches are near-complete); this plan records the scout-verified deltas and the commit mapping. All anchors re-verified against 9720d37 (post #184–#187) by a 5-agent scout (63 items: 49 confirmed, 14 benign drifts, 1 GONE — local runnability, remedied below).

**Scout-verified facts that shape implementation:**

- **Locked APIs all support the design** (verified against the exact uv-cache wheels): langgraph 1.2.8 (`from langgraph.graph import END, START, MessagesState, StateGraph` verbatim-valid; compiled graphs are legal `add_node` targets; `get_graph()`/`ainvoke` shared by `create_react_agent` output), langchain-core 1.4.9 (`RemoveMessage` deletes by id — spec's WHY comment exact; unknown-id raises, fine since the id comes from state), langchain-openai 1.1.9 (image_url data-URI blocks pass through), streamlit 1.59.1 (`chat_input(accept_file="multiple", file_type=[...])` returns `ChatInputValue` with `.text`/`.files`). CAVEAT: `create_react_agent` is deprecated in langgraph-prebuilt 1.1.0 (moved to `langchain.agents.create_agent`) — the repo already uses it; keep it (migration out of scope), note in a WHY comment.
- **Streamlit floor bump is metadata-only** (locked 1.59.1 ≫ 1.43); relock still required for the gate. Extra now at `pyproject.toml:118-124` and includes `streamlit-agraph>=0.0.45`.
- **`ComponentRegistry` count arithmetic holds** (4 instances today; agent_registry is the fifth). #186's `ProviderRegistry` (extraction/strategies/embedders/registry.py) is a bespoke function-registry sibling — cite it in `architectures/__init__.py`'s docstring as the *other* registry family so the DRY framing stays honest.
- **AppConfig slots:** new field after `late_interaction` (app_config.py:161-168), before `model_config` (:184-188). `load()` def at :296, `.exists()` guard at :311 (raises FileNotFoundError — PYDOCS_CONFIG must be wrapped in `Path`). `default_config.yaml` is 255 lines; the new documented `ask_your_docs:` block appends at the end (after the commented `late_interaction:` block).
- **#187 interactions:** `cli.py` now has `_build_parser` (tests/ask_your_docs/test_cli_parser.py pins core-only parser build + lazy-import contract — MUST stay green). The doc-conformance harness auto-validates any documented `ask_your_docs.*` dotted path / yaml block via AppConfig introspection — config lands in commit 1, docs in commit 5, so ordering is safe. `examples/ask_your_docs_agent/configs/*.yaml` are OUTSIDE the harness corpus (safe for a commented block).
- **Local runnability (the GONE item):** no env can import langgraph/streamlit — FIRST STEP installs the extra's deps into the worktree venv (`~/.local/bin/uv pip install --python .venv/bin/python "langgraph>=0.2" "langchain-mcp-adapters>=0.3" "langchain-openai>=0.2" "streamlit>=1.43" "streamlit-agraph>=0.0.45"`). Note these tests skip in CI (extra never installed there) — local runs are the only executions; record in the PR body.

**Resolved §7 open questions** (adopt-recommendations rule): Q1 endpoint-probe fields — conservative positive-only heuristic over commonly-seen fields (`capabilities`/`modality`/`modalities`/`architecture`/`tags` containing `vision|image|multimodal`), absence falls through, dated WHY comment; ships off by default. Q2 process-level cache only. Q3/Q4/Q6 out of scope. **Q5 extra_multimodal_prefixes: NOT added** — the app configures one model at a time, so `detection.override` fully covers the local-custom-name case; a prefix-list knob would duplicate it (YAGNI); rationale recorded here for review.

**Static prefix tables (§3.9 rung 2, finalized):** positive prefixes: `gpt-4o, gpt-4.1, gpt-4-turbo, gpt-5, chatgpt-4o, o3, o4, gemini, claude, gemma-3, llava, llama-3.2, llama-4, qwen2-vl, qwen2.5-vl, qwen3-vl, qwen2.5-omni, pixtral, internvl, minicpm-v, phi-3-vision, phi-3.5-vision, phi-4-multimodal, molmo, idefics, smolvlm`; negative: `gpt-3.5, gpt-4-0, davinci, text-, qwen2.5-coder, qwen2.5-math, deepseek, mistral-, mixtral, llama-3.1, llama-3-, llama-2, phi-3, phi-2, starcoder, codellama, gemma-2, gemma-7b, gemma-2b`. Longest-prefix across BOTH tables wins (so `phi-3-vision` beats `phi-3`; `llama-3.2` positive is deliberate — the 3.2 line's 11B/90B are vision; 1B/3B text-only models mis-detect positive, corrected by `override: false`; dated WHY comment). Case-insensitive; name is lowercased and matched after stripping an optional org/path prefix up to the last `/` (HF-style `org/model` ids).

---

## Commit map (spec §6 landing order; each independently green)

### Commit 1 — config sub-model (core-tested: AC23, AC24)
- NEW `python/pydocs_mcp/retrieval/config/ask_your_docs_models.py`: §3.5's sketch verbatim (`MultimodalDetectionConfig`, `MultimodalConfig`, `ImagesConfig`, `AskYourDocsConfig`), `extra="forbid"` on all four (sub-model convention), full type hints + docstrings.
- `app_config.py`: `ask_your_docs: AskYourDocsConfig = Field(default_factory=AskYourDocsConfig)` after `late_interaction`, with a WHY comment (first agent-side consumer; litmus-test rationale).
- `defaults/default_config.yaml`: documented `ask_your_docs:` block (§3.5 YAML verbatim) appended at file end.
- Tests `tests/retrieval/test_ask_your_docs_config.py` (RED first): AC23 (defaults + overlay + `PYDOCS_ASK_YOUR_DOCS__ARCHITECTURE` env precedence), AC24 (YAML↔Field parity via `AppConfig.load()` == `AskYourDocsConfig()`).
- Gates check: mypy MUST pass on the new module (not excluded); the doc-conformance harness's `_valid_dotted_paths` picks the sub-model up automatically.

### Commit 2 — attachments + detection ladder (AC10–AC15, AC17)
- NEW `ask_your_docs/attachments.py`: §3.2 `ImageAttachment` + `_MAX_*` constants + `validate_attachment(...)` helper (raises ValueError naming offending value + limit); re-export `weave_attachments` here later (commit 3 moves it — NO, spec keeps weave in agent.py with attachments.py re-exporting; simplest compliant: attachments.py imports nothing heavy, agent.py keeps weave_attachments; skip the re-export unless moving it — spec §3.1 says agent.weave_attachments re-exported FOR back-compat, i.e. weave moves INTO attachments.py and agent.py re-exports. Do that: move verbatim + `from .attachments import weave_attachments` in agent.py).
- NEW `ask_your_docs/multimodal.py`: `ModelCapabilities`, `DetectionSource`, prefix tables (above), `detect_capabilities(model, base_url, cfg, *, http_get=None, probe_llm=None)` — injectable seams (named fakes: `FakeModelsEndpoint`, `FakeVisionLlm`) so tests need no network; bounded retry (3 attempts, 2s/4s backoff, 5s timeout) mirroring `_with_retry_async`; 1×1 PNG base64 module constant.
- Tests (RED first): `test_multimodal_detection.py` AC10–AC15 (AC15 via an explicit `functools`-style per-pair cache or the documented get_agent-cache contract — implement a small `_detection_cache: dict[tuple[str, str|None], ModelCapabilities]` in multimodal.py so the unit test can pin call-once semantics without Streamlit); `test_image_attachment.py` AC17.

### Commit 3 — registry + text_react extraction (AC1–AC4, AC16 anchor)
- NEW `ask_your_docs/architectures/{__init__,base,text_react}.py` per §3.2/§3.3/§3.4.0 (AgentBuildContext, AgentArchitecture ABC with `requires_multimodal` ClassVar + `from_dict`; registry = fifth ComponentRegistry; side-effect imports).
- NEW extra-local exception `AgentArchitectureError` (in `architectures/base.py`).
- `agent.py`: `build_agent` gains `architecture`/`capabilities`/`config` plumbing and delegates graph construction to the registry (§3.4.4 validation verbatim: unknown-name ValueError listing `names()`; requires_multimodal check with the YAML-anchored message). Default path (`text_react`, no config) byte-identical (AC3).
- Tests (RED first): `test_agent_registry.py` AC1, AC2 (duplicate registration — use `ComponentRegistry` behavior on a scratch instance to avoid polluting the real one), AC9; `test_architectures.py` AC3 (FakeLlm message-shape regression anchor), AC4 (get_graph + mermaid for every registered name — mermaid render via `draw_mermaid()`, not PNG, to avoid network/pyppeteer).
- AC16: `test_attachment.py` untouched and green (weave moved, import path preserved).

### Commit 4 — inline + vision_subagent + auto + message flow (AC5–AC8, AC18–AC22)
- NEW `architectures/inline.py`, `vision_subagent.py`, `auto.py` — §3.4.1/§3.4.2/§3.4.3 code verbatim (vision node: RemoveMessage WHY comment; `_VISION_EXTRACTION_PROMPT`; `_IMAGE_ANALYSIS_PROMPT_SECTION`).
- `agent.py` `ask()`: keyword-only `images: tuple[ImageAttachment, ...] = ()` (§3.6 content construction + history placeholder + trim unchanged); `reformulate()` hardening via `_history_line(m)` (flatten content blocks to text + `[image]` markers).
- `attachments.py`: `text_only_policy(images, capabilities, cfg) -> Rejection | DescribeNote | None` pure helper implementing §3.8 (reject message text verbatim incl. override YAML path; describe prefix with names) — UI consumes it in commit 5; unit-testable headless.
- Tests (RED first): `test_architectures.py` AC5–AC8 (FakeVisionLlm one-call assertion, passthrough, auto routing via graph-node names); `test_image_attachment.py` AC18–AC20; `test_text_only_fallback.py` AC21–AC22 (against the pure helper + ask() call-site assertion).

### Commit 5 — UI + floor bump + docs (AC25 + rollout)
- `app.py`: `st.chat_input(accept_file="multiple", file_type=["png","jpg","jpeg","webp","gif"])` + ChatInputValue handling; validation → inline error chips; image chips distinct from symbol chips; capability badge (`vision: yes (static)` style) in the sidebar; detection cached in `get_agent`; degradation wiring (reject preserves the question; describe prefixes); `AppConfig.load(Path(PYDOCS_CONFIG))` consumption.
- `pyproject.toml`: `streamlit>=1.36` → `>=1.43` with `# WHY: chat_input(accept_file=...)` comment; `~/.local/bin/uv lock` (metadata-only — locked 1.59.1 already satisfies).
- Docs: `examples/ask_your_docs_agent/README.md` — new YAML keys + `PYDOCS_ASK_YOUR_DOCS__*` env form + per-architecture agent-graph note (jargon/vendor rules apply); commented `ask_your_docs:` block in `examples/ask_your_docs_agent/configs/*.yaml`.
- Tests: `test_app_image_attachment.py` AC25 (AppTest pattern).

### Task 6 — adversarial AC review (ultracode) → Task 7 — gates, push, PR
- Review workflow: refuters per AC group (config AC23-24; detection AC10-15; registry/architectures AC1-9; message-flow AC16-22; UI/rollout AC25 + §2 non-goals: no MCP change, no LlmClient Protocol change, no new heavy deps in default install, no image indexing/persistence) + cross-check.
- Gates: full CLAUDE.md set. NOTE the extra deps installed locally perturb the venv — run the coverage gate BEFORE judging vulture/mypy oddities; `uv lock --check` after the floor bump; extra tests run locally only (CI skips them — stated in the PR body).
- Re-fetch origin before push (concurrent-PR rule); push; PR; NO merge without explicit go.

## Self-review notes
- AC map: 1-9 commits 3-4; 10-15+17 commit 2; 16 commit 3; 18-22 commit 4; 23-24 commit 1; 25 commit 5. All 25 covered.
- Non-goals honored by construction (no server.py/protocols.py edits; images never touch storage/).
- Deviation from spec §3.1: `weave_attachments` moves INTO attachments.py with agent.py re-export (the spec's own back-compat note); test_attachment.py imports stay green either way.
- Q5 skipped with YAGNI rationale (override covers the single-model case) — flag to reviewers.

---

## Implementation addendum

- **§3.7 chips reconciliation:** with the chosen `accept_file` design (§4.7), files arrive atomically with the question, so pre-send "removable chips" are the chat_input widget's own native file list (each entry has its ✕). The app renders the last turn's images as 🖼 markdown pills (distinct from the symbol-name buttons) driven by `session_state["image_chips"]` — the AppTest seam AC25 asserts.
- **Commit granularity:** spec stages 3+4 landed as one commit (registry + all four architectures) — the AC1/AC9 tests presume the full name set, and the PR is the revert unit.
- **Deprecation caveat carried:** `create_react_agent` warns (moved upstream to `langchain.agents`) in the locked langgraph-prebuilt 1.1.0 — pre-existing usage, migration out of scope, WHY comment at the import site.
- **`ask()` reject-path note:** the UI preserves the unsent question as visible text (chat_input cannot be programmatically pre-filled — the spec's "preserved in the input box" is approximated with an explicit "not sent" info line).
- **Amendment A1 (user-directed, folded in-flight):** the `reinspect_images` agent-local tool + session image store + `images.session_retention` config — spec updated in-file (Amendment A1, AC26-AC28); tests in `tests/ask_your_docs/test_reinspect_tool.py` + store/config tests.
