# Ask-your-docs LLM Connection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the ask-your-docs chat agent talk to one OpenAI-format endpoint whose bearer comes from an internal token service (renewed on a `401` and retried once) or from an environment variable, discover the endpoint's models, and route images to the main model or to a second model on the same endpoint — all from one `ask_your_docs.llm` YAML block, overridable per launch and per session, with byte-identical behavior when the block is absent.

**Architecture:** One frozen `LlmConnection` value object is resolved once per session by a pure precedence fold (YAML < environment < CLI < dialog). One `BearerSource` per auth identity (Null Object for no-auth) feeds a single client factory that installs the bearer as a callable `api_key` and, for a token service, an `httpx.Auth` that renews on a `401` and re-sends the same request once. Every LLM-bound call — agent, reformulation, image calls, both capability probes, the model listing, the dialog's test — goes through that factory; every auth failure crosses one redaction boundary. The sidebar's four inputs collapse into one status line and a `st.dialog`.

**Tech Stack:** Python 3.11, pydantic v2 (`AppConfig` sub-models), `openai` 1.109.1 (locked), `langchain-openai` 1.1.9 (locked), `httpx` 0.28.1 (`httpx.Auth`, `httpx.MockTransport`), Streamlit 1.59.1 (`st.dialog`, `AppTest`), pytest.

**Spec:** `docs/superpowers/specs/2026-09-05-ask-your-docs-llm-connection-design.md` (rules R1–R10 and H1–H4 in §3; module map §4.1; acceptance criteria AC-1…AC-45 in §8). Every task below names the ACs it closes.

## Global Constraints

- **Locked toolkit first.** Step 0 of every session on this plan is `~/.local/bin/uv sync --frozen --all-extras` in the worktree (the worktree `.venv` did not hold the locked set at review time). Every test runs on `openai` 1.109.1 / `langchain-openai` 1.1.9 / `httpx` 0.28.1 / `streamlit` 1.59.1 / `langchain-core` 1.4.9 / `langgraph` 1.2.8.
- **No MCP change.** No tool, parameter or envelope field is added (spec §7.1); `tests/fixtures/goldens/mcp_registration_surface.json` is not touched.
- **Byte identity without the block** (spec §7.2, R2): with no `ask_your_docs.llm` block the factory issues exactly `ChatOpenAI(model=..., base_url=...)`; the prompt, the eval delivery-map digest `5072aa2e926f9c508233ce84b21edd937a3efe9706a00408f3ff84a998115e6d` and `Trajectory.cost_usd == 0.0` are unchanged. The one sanctioned no-block difference: the endpoint probe and the model listing carry `Authorization: Bearer $OPENAI_API_KEY` when that variable is set.
- **Secrets never in YAML, argv, logs, the UI or a cache key** (G8, H4). External keys come only from the environment variable named by `auth.api_key_env`; internal tokens only from `auth.token_url`. No `--api-key` flag; no key field in the dialog. `last_four` is shown inline in the UI (D4) and never logged.
- **The bearer follows the effective endpoint** (D3, H1): an origin change is a visible warning (`bearer_origin_changed` log + status-line note), never a withheld bearer. Plain-http non-loopback endpoints are a visible warning (`bearer_over_cleartext` log + `⚠ http`), never an error (H2).
- **Config keys are exactly** `base_url`, `model`, `auth` (exactly one of `token_url` / `api_key_env`), `token_field`, `renew_on_status`, `vision` (D2). `renew_on_status ⊆ {401, 403, 407}`. `auth.token_url` with `base_url: null` is rejected (E14).
- **Repository rules** (CLAUDE.md): `StrEnum` vocabularies with UPPER_SNAKE members; plain-English names; files under 500 lines (`ask_your_docs_models.py` under 200); functions of 4–20 lines; at most two indentation levels; error messages carrying the offending value and the expected shape; structured JSON logs (`log.info(json.dumps({...}))`); timeouts and bounded retries on every network call; the Null Object pattern (`NoBearer`); vendor-neutral docs ("an OpenAI-format endpoint"); the README jargon audit.
- **Lazy-import contract.** `import pydocs_mcp.harness.ask_your_docs.cli` leaves `streamlit`, `langgraph` and `httpx` out of `sys.modules` (AC-24); `langchain_openai` and `openai` are imported function-locally in the new modules; `httpx` (transitive via the required `openai` dep) may be imported at module level in `bearer_tokens.py` only.
- **Streamlit floor.** `st.dialog` needs 1.34+; the pending UI plan raises `pyproject.toml:143` to `streamlit>=1.57`. This plan makes no `pyproject.toml` dependency change; if it lands first, the floor stays `>=1.43` (already above 1.34).
- **Full gate before every push** (CLAUDE.md §Tests & Lint): `ruff check python/ tests/ benchmarks/`, `ruff format --check python/ tests/ benchmarks/`, `mypy python/pydocs_mcp`, `complexipy python/pydocs_mcp --max-complexity-allowed 15`, `vulture python/pydocs_mcp --min-confidence 80`, `pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90`. Restore `complexipy-snapshot.json` from HEAD before staging (a local run rewrites it).
- **Git authorship:** no `Co-Authored-By` trailers, no `--author`, no signing, no `git config` edits. Commit only; never push or tag without the owner's explicit word.

---

## File Structure

New modules under `python/pydocs_mcp/harness/ask_your_docs/` (line budgets are AC-29 gates):

| File | Budget | Responsibility |
|---|---|---|
| `bearer_tokens.py` (new) | < 500 | `BearerSource` Protocol + `BearerStatus`; `NoBearer`, `EnvironmentKeyBearer`, `TokenServiceBearer`; `RenewOnStatusAuth`, `StripAuthorizationAuth` (`httpx.Auth`); `redact_bearer`, `display_url`, `display_host`, `translate_auth_errors`, `last_four_of`; the token-fetch constants; `TokenServiceError`, `BearerUnavailableError`, `BearerRejectedError`. |
| `llm_connection.py` (new) | < 500 | `ConnectionOverride`, `LlmConnection` (+ `origin_changed`, `cleartext_bearer`), `resolve_llm_connection`, `connection_identity`, the bearer registry (`bearer_for_connection`, `clear_bearer_registry`), the client factory (`connection_auth_kwargs`, `build_chat_model`), `resolve_vision_capabilities`, `test_connection`. |
| `model_listing.py` (new) | < 500 | `ModelListing`, `fetch_models_payload`, `fetch_model_ids`, `cached_model_listing`, `clear_model_listing_cache`, the listing constants. |
| `connection_dialog.py` (new, Streamlit-only) | < 500 | `ConnectionActions`, `auth_cell`, `vision_cell`, `render_connection_status_line`, `open_connection_dialog`, the widget / session-state keys. |
| `reformulation.py` (new) | < 500 | `reformulate` + `_history_line`, moved verbatim from `agent.py`. |

Modified files:

| File | Change |
|---|---|
| `python/pydocs_mcp/retrieval/config/ask_your_docs_models.py` | `AuthMode`, `VisionRule` StrEnums; `LlmAuthConfig`, `VisionModelConfig`, `LlmConnectionConfig`; `AskYourDocsConfig.llm`; `preferred_architecture` default → `inline`; the `_DEFAULT_*` constants. Stays under 200 lines. |
| `python/pydocs_mcp/defaults/default_config.yaml` | `preferred_architecture: inline`; `llm: null` + commented example. |
| `python/pydocs_mcp/harness/ask_your_docs/multimodal.py` | `CapabilitySource` StrEnum (+ `CONFIGURED`); rung seams take `(connection, bearer)`; bearer failures propagate and are never cached; default seams go through the factory / the listing. |
| `python/pydocs_mcp/harness/ask_your_docs/architectures/base.py` | `ImageModelRoute`; `AgentBuildContext.vision_llm` / `vision_capabilities` / `bearer` with Null-Object defaults; `effective_tools(ctx, route)`. |
| `python/pydocs_mcp/harness/ask_your_docs/architectures/auto.py` | The routing table of spec §4.7. |
| `python/pydocs_mcp/harness/ask_your_docs/architectures/vision_subagent.py`, `reinspect.py`, `attachments.py` | `describe_images` seam; both image call sites use the image model inside `translate_auth_errors`. |
| `python/pydocs_mcp/harness/ask_your_docs/agent.py` | `build_agent(connection=, bearer=, vision_capabilities=)`; `build_chat_model`; `resolve_vision_capabilities`; E19; `_build_architecture` route gates; `reformulate` moved out. |
| `python/pydocs_mcp/harness/ask_your_docs/__init__.py` | `reformulate` lazy re-export points at `reformulation`. |
| `python/pydocs_mcp/harness/ask_your_docs/app.py` | Status line + dialog opener; caches keyed on the auth identity; bearer preflight; send-loop error boundary. |
| `python/pydocs_mcp/harness/ask_your_docs/cli.py` | Help text only (`_DEFAULT_MODEL` from the config module). |
| `python/pydocs_mcp/harness/ask_your_docs/binding.py` | `connection_block_for_binding`; `connection=` passed to `build_agent`. |
| `pyproject.toml` | `[tool.vulture] ignore_names` gains the Protocol parameter names `rejected`, `reason`. |
| `examples/harness/ask_your_docs_agent/configs/serve_cpu_openvino.yaml`, `README.md`, `CHANGELOG.md` | The block, the paragraph, the entry (spec §5.3–§5.5). `configs/index_gpu.yaml` is unchanged byte for byte. |

New tests under `tests/harness/ask_your_docs/`: `_connection_fakes.py`, `test_bearer_tokens.py`, `test_llm_connection.py`, `test_chat_model_factory.py`, `test_sdk_pins.py`, `test_model_listing.py`, `test_agent_connection.py`, `test_app_connection_dialog.py`, `test_module_line_budgets.py`. Modified tests: `tests/test_config_ask_your_docs.py`, `test_multimodal_detection.py`, `test_architectures.py`, `test_reinspect_tool.py`, `test_image_attachment.py`, `test_prompt_seam.py`, `test_binding.py`, `test_cli_parser.py`, `test_app_attachment.py`, `test_app_image_attachment.py`.

### Coexistence with the pending UI plan

`docs/superpowers/plans/2026-09-04-ask-your-docs-branch-scope-ui.md` is unmerged. This plan is written against worktree HEAD `bf21e8a`. Whichever lands second rebases on these shared lines (spec §7.4): `AskYourDocsConfig`'s field list and the YAML block (both additive); `build_agent` keeps returning a 2-tuple and the UI plan's `build_agent_with_scope_capabilities` gains the same `connection=` / `bearer=` / `vision_capabilities=` keywords; the UI plan's `page_scope_capabilities(workspace, model, base_url, config)` becomes `page_scope_capabilities(workspace, connection, config)`; `get_agent` keeps the UI plan's parameter order and appends `identity, _connection, _bearer`; the `reformulation.py` move happens once (this plan's Task 9 performs it; the UI plan's Task 4 moves `scope_prefix`).

### Deviations from the spec (recorded, each with its reason)

- **D-1** `AuthMode` and `VisionRule` live in `retrieval/config/ask_your_docs_models.py`, not `llm_connection.py`: `bearer_tokens.py` needs `AuthMode` and must not import `llm_connection.py` (which imports `bearer_tokens.py`), and the UI plan's precedent puts harness enums in the mypy-checked config module. `llm_connection.py` re-exports both.
- **D-2** The bearer registry (`bearer_for_connection`, `clear_bearer_registry`) and `connection_identity` live in `llm_connection.py` (the module that owns the connection), not `bearer_tokens.py` — same import-cycle reason.
- **D-3** The model listing builds an `openai.AsyncOpenAI` from the factory's one auth decision (`connection_auth_kwargs`) instead of a `ChatOpenAI` whose `model` would have to be a placeholder when no model is chosen yet; the observable contract (same base URL, same bearer, same renewing `Auth`, `GET /models`) is unchanged.
- **D-4** A renewal that fails inside the `httpx.Auth` flow does not raise out of `send` (the SDK would retry the whole request and multiply the token fetches); the flow returns the rejected response, and `TokenServiceBearer.last_error` rides into the `BearerRejectedError` message (`; renew failed: …`). The renew rate limit counts attempts, not successes.
- **D-5** `test_connection` lives in `llm_connection.py` (pure async, testable without Streamlit); `connection_dialog.py` only renders.
- **D-6** The page's AppTest seams are session-state objects `connection_bearer`, `connection_list_models` and `connection_transport` (a `BearerSource`, a listing seam callable, an `httpx` transport) rather than a seeded `ModelListing`, so the eviction and Test-connection criteria (AC-43, AC-45) are observable through real code paths.
- **D-7** Factory-path tests live in `test_chat_model_factory.py` (they need `langchain_openai`); `test_bearer_tokens.py` stays core-deps-only.

---
## Task 1: Config models, vocabularies and shipped defaults

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config/ask_your_docs_models.py` (whole file replaced below)
- Modify: `python/pydocs_mcp/defaults/default_config.yaml:345-362`
- Test: `tests/test_config_ask_your_docs.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `AuthMode(StrEnum)` {`NONE`, `ENV_KEY`, `TOKEN_SERVICE`}; `VisionRule(StrEnum)` {`DETECT`, `MULTIMODAL`, `TEXT_ONLY`, `SEPARATE_MODEL`}; `LlmAuthConfig(token_url, api_key_env)`; `VisionModelConfig(model)`; `LlmConnectionConfig(base_url, model, auth, token_field, renew_on_status, vision)`; `AskYourDocsConfig.llm: LlmConnectionConfig | None`; constants `_DEFAULT_MODEL = "gpt-4o-mini"`, `_DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"`, `_DEFAULT_RENEW_ON_STATUS = (401,)`, `_RENEWABLE_STATUSES = frozenset({401, 403, 407})`, `_DEFAULT_PREFERRED_ARCHITECTURE = "inline"`. Closes AC-21, AC-22 (config half).

- [ ] **Step 1: Write the failing tests**

Edit `tests/test_config_ask_your_docs.py`. Change the two `preferred_architecture` assertions (lines 18 and 45) from `"vision_subagent"` to `"inline"`, then append:

```python
# ── ask_your_docs.llm (LLM-connection design §5.1 — AC-21, AC-22) ──


def test_llm_block_absent_by_default() -> None:
    """AC-21: no block ⇒ today's behavior; the dated default flip is pinned."""
    cfg = AppConfig.load().ask_your_docs
    assert cfg.llm is None
    assert cfg.multimodal.preferred_architecture == "inline"


def test_llm_block_parses_token_service_and_vision_model(tmp_path) -> None:
    from pydocs_mcp.retrieval.config.ask_your_docs_models import VisionModelConfig

    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "ask_your_docs:\n"
        "  llm:\n"
        "    base_url: http://llm.internal/v1\n"
        "    auth:\n"
        "      token_url: http://localhost:8899/access-token\n"
        "    token_field: access_token\n"
        "    renew_on_status: [401, 403]\n"
        "    vision:\n"
        "      model: vision-b\n",
        encoding="utf-8",
    )
    llm = AppConfig.load(explicit_path=overlay).ask_your_docs.llm
    assert llm is not None
    assert llm.base_url == "http://llm.internal/v1"
    assert llm.model is None
    assert llm.auth is not None and llm.auth.token_url == "http://localhost:8899/access-token"
    assert llm.auth.api_key_env is None
    assert llm.token_field == "access_token"
    assert llm.renew_on_status == (401, 403)
    assert llm.vision == VisionModelConfig(model="vision-b")


def test_llm_vision_true_false_and_api_key_env(tmp_path) -> None:
    from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

    assert LlmConnectionConfig(vision=True).vision is True
    assert LlmConnectionConfig(vision=False).vision is False
    external = LlmConnectionConfig.model_validate({"auth": {"api_key_env": "LLM_KEY"}})
    assert external.base_url is None  # D2: an external key on the vendor default endpoint
    assert external.auth is not None and external.auth.api_key_env == "LLM_KEY"
    assert external.renew_on_status == (401,)


def test_llm_auth_needs_exactly_one_source() -> None:
    """E8: both or neither of token_url / api_key_env is rejected, naming what was given."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmAuthConfig

    with pytest.raises(ValidationError, match="expected exactly one of token_url / api_key_env"):
        LlmAuthConfig(token_url="http://localhost:8899/access-token", api_key_env="LLM_KEY")
    with pytest.raises(ValidationError, match="got neither"):
        LlmAuthConfig()


def test_llm_token_url_needs_base_url() -> None:
    """E14: a token service authenticates one internal endpoint — the block must name it."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

    with pytest.raises(ValidationError, match="token_url needs base_url; got null"):
        LlmConnectionConfig.model_validate({"auth": {"token_url": "http://localhost:8899/t"}})


def test_llm_token_url_rejects_credentials_in_userinfo_or_query() -> None:
    """E16: credentials never ride the URL; the message shows the stripped form only."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmAuthConfig

    with pytest.raises(ValidationError, match="must not carry credentials") as excinfo:
        LlmAuthConfig(token_url="http://user:s3cr3tpw@host:8899/t?k=v4lue")
    assert "s3cr3tpw" not in str(excinfo.value) and "v4lue" not in str(excinfo.value)


def test_llm_renew_on_status_must_be_renewable() -> None:
    """E17: only 401 / 403 / 407 may renew; 200 and 503 are rejected with the allowed set."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

    for bad in (200, 503):
        with pytest.raises(ValidationError, match=rf"got {bad}, expected a subset of \[401, 403, 407\]"):
            LlmConnectionConfig(renew_on_status=(bad,))
    assert LlmConnectionConfig(renew_on_status=(401, 407)).renew_on_status == (401, 407)


def test_llm_rejects_unknown_keys_and_empty_vision_model() -> None:
    """E7 + the vision.model shape."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import (
        LlmAuthConfig,
        LlmConnectionConfig,
    )

    with pytest.raises(ValidationError):
        LlmConnectionConfig.model_validate({"profiles": []})
    with pytest.raises(ValidationError):
        LlmAuthConfig.model_validate({"api_key_env": "K", "allow_cleartext": True})
    with pytest.raises(ValidationError):
        LlmConnectionConfig.model_validate({"vision": {"model": ""}})


def test_llm_vision_env_overlay(monkeypatch) -> None:
    """AC-22: PYDOCS_ASK_YOUR_DOCS__LLM__VISION=true reaches the block through the env layer."""
    monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__LLM__VISION", "true")
    llm = AppConfig.load().ask_your_docs.llm
    assert llm is not None and llm.vision is True


def test_default_yaml_ships_llm_null_and_the_flipped_default() -> None:
    """AC-21 YAML half: the shipped block carries `llm: null` and `preferred_architecture: inline`."""
    from pathlib import Path as _P

    import yaml

    root = _P(__file__).resolve().parents[1]
    shipped = yaml.safe_load(
        (root / "python/pydocs_mcp/defaults/default_config.yaml").read_text(encoding="utf-8")
    )
    block = shipped["ask_your_docs"]
    assert "llm" in block and block["llm"] is None
    assert block["multimodal"]["preferred_architecture"] == "inline"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_config_ask_your_docs.py -q`
Expected: FAIL — `AssertionError` on `preferred_architecture == "inline"`, `AttributeError: 'AskYourDocsConfig' object has no attribute 'llm'`, `ImportError` for `LlmConnectionConfig`.

- [ ] **Step 3: Replace `ask_your_docs_models.py`**

```python
"""Ask-your-docs agent config sub-models.

Spec 2026-07-11-multimodal-image-agent §3.5 (architecture, multimodal, images)
and 2026-09-05-ask-your-docs-llm-connection-design §5.1 (the ``llm`` block).

The first agent-side consumer of AppConfig — sanctioned because agent
architecture choice and multimodal-detection strategy are "A/B-testable
against a benchmark" behaviors (CLAUDE.md §MCP API surface vs YAML
configuration litmus test). Light pydantic only: importing this from the
``[harness-ask-your-docs]`` extra pulls no heavy deps.

Defaults are duplicated in ``defaults/default_config.yaml`` intentionally —
the YAML is the user-visible knob (CLAUDE.md §Default values).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Single sources (CLAUDE.md §Default values): the harness modules import these
# instead of repeating the literals; the YAML duplicates them on purpose.
_DEFAULT_MODEL = "gpt-4o-mini"  # the no-block default (formerly app.py / cli.py literals)
_DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"
_DEFAULT_RENEW_ON_STATUS: tuple[int, ...] = (401,)
# WHY only these: 200 would re-send a successful, non-idempotent completion;
# 408/409/429/5xx are retried by the SDK itself, and listing them here would
# multiply the two bounds instead of composing them (design E17).
_RENEWABLE_STATUSES = frozenset({401, 403, 407})
# 2026-09-05: was vision_subagent. A multimodal main model answers and sees in
# one prompt; set vision_subagent back for a separate describe hop (design R6).
_DEFAULT_PREFERRED_ARCHITECTURE = "inline"


class AuthMode(StrEnum):
    """Where the chat model's bearer comes from (design §4.2)."""

    NONE = "none"  # no Authorization header at all
    ENV_KEY = "env_key"  # bearer = os.environ[api_key_env]
    TOKEN_SERVICE = "token_service"  # bearer fetched from token_url, renewable


class VisionRule(StrEnum):
    """How the ``vision`` key resolves (design §4.2, §4.7)."""

    DETECT = "detect"  # vision: null  -> run the detection ladder as today
    MULTIMODAL = "multimodal"  # vision: true  -> the main model sees, no probe
    TEXT_ONLY = "text_only"  # vision: false -> the main model never sees
    SEPARATE_MODEL = "separate_model"  # vision: {model: ...}


class MultimodalDetectionConfig(BaseModel):
    """The capability-detection ladder's per-rung toggles (spec §3.9).

    ``override`` always wins; the probes are opt-in because rung 3 adds a
    network call at agent build and rung 4 spends a real (tiny) LLM call.
    """

    model_config = ConfigDict(extra="forbid")

    override: bool | None = Field(default=None)
    static_table: bool = Field(default=True)
    endpoint_probe: bool = Field(default=False)
    image_probe: bool = Field(default=False)


class MultimodalConfig(BaseModel):
    """Image-handling policy for the ask-your-docs agent."""

    model_config = ConfigDict(extra="forbid")

    # What "auto" builds on a vision-capable model (the single source is the
    # module constant above — see its dated comment).
    preferred_architecture: str = Field(default=_DEFAULT_PREFERRED_ARCHITECTURE)
    detection: MultimodalDetectionConfig = Field(default_factory=MultimodalDetectionConfig)
    # Text-only models + attached images: "reject" fails loudly with the fix
    # in hand (user-requested content must not silently degrade — the raising
    # side of the Null Object asymmetry); "describe" proceeds text-only with
    # an explicit cannot-see note.
    text_only_fallback: Literal["reject", "describe"] = Field(default="reject")


class ImagesConfig(BaseModel):
    """Per-turn image attachment limits + the session reinspect store size."""

    model_config = ConfigDict(extra="forbid")

    max_per_turn: int = Field(default=3, ge=1, le=10)
    max_bytes: int = Field(default=5_000_000, ge=1)
    # How many recently-attached images the session keeps (bytes live OUTSIDE
    # conversation history) so the reinspect_images tool can re-read earlier
    # attachments against a NEW question without re-paying vision tokens
    # per turn. 0 disables retention (the tool then finds no stored images).
    session_retention: int = Field(default=12, ge=0, le=50)
    # Necessity gating: each reinspect call is a full vision-model call, so a
    # per-turn budget stops a looping agent from burning them; repeated
    # same-args calls are memoized (free) and don't count. 0 disables the
    # tool's vision path entirely.
    max_reinspect_per_turn: int = Field(default=2, ge=0, le=10)


def _reject_credentials_in_url(token_url: str) -> None:
    """Design E16: credentials never ride the URL (display_url would strip them anyway)."""
    parts = urlsplit(token_url)
    if parts.username or parts.password or parts.query:
        raise ValueError(
            "ask_your_docs.llm.auth.token_url must not carry credentials in userinfo "
            f"or query; got {parts.scheme}://{parts.hostname or ''}{parts.path}"
        )


class LlmAuthConfig(BaseModel):
    """Where the bearer comes from — exactly one of ``token_url`` / ``api_key_env`` (R2)."""

    model_config = ConfigDict(extra="forbid")

    token_url: str | None = Field(default=None)
    api_key_env: str | None = Field(default=None)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> LlmAuthConfig:
        given = [name for name in ("token_url", "api_key_env") if getattr(self, name)]
        if len(given) != 1:
            raise ValueError(
                f"ask_your_docs.llm.auth: got {given or 'neither'}, "
                "expected exactly one of token_url / api_key_env"
            )
        if self.token_url is not None:
            _reject_credentials_in_url(self.token_url)
        return self


class VisionModelConfig(BaseModel):
    """``vision: {model: <id>}`` — a second model on the same endpoint sees the images."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)


class LlmConnectionConfig(BaseModel):
    """The ``ask_your_docs.llm`` block (design §5.1); ``None`` on the parent = today."""

    model_config = ConfigDict(extra="forbid")

    base_url: str | None = Field(default=None)  # None = the SDK's vendor default
    model: str | None = Field(default=None)  # None = pick in the dialog
    auth: LlmAuthConfig | None = Field(default=None)  # None = no bearer
    token_field: str | None = Field(default=None)  # None = the whole body is the token
    renew_on_status: tuple[int, ...] = Field(default=_DEFAULT_RENEW_ON_STATUS)
    vision: bool | VisionModelConfig | None = Field(default=None)  # None = detect

    @field_validator("renew_on_status")
    @classmethod
    def _only_renewable_statuses(cls, statuses: tuple[int, ...]) -> tuple[int, ...]:
        for status in statuses:
            if status not in _RENEWABLE_STATUSES:
                raise ValueError(
                    f"ask_your_docs.llm.renew_on_status: got {status}, "
                    f"expected a subset of {sorted(_RENEWABLE_STATUSES)}"
                )
        return statuses

    @model_validator(mode="after")
    def _token_service_names_its_endpoint(self) -> LlmConnectionConfig:
        # Design E14: a token service authenticates one internal endpoint, so the
        # block must name it; api_key_env with base_url: null is the
        # vendor-default case (D2) and stays valid.
        if self.auth is not None and self.auth.token_url and not self.base_url:
            raise ValueError("ask_your_docs.llm.auth.token_url needs base_url; got null")
        return self


class AskYourDocsConfig(BaseModel):
    """Top-level ``ask_your_docs:`` block — architecture, multimodal policy, LLM connection."""

    model_config = ConfigDict(extra="forbid")

    # One of agent_registry.names(); "text_react" pins pre-image behavior
    # exactly, "auto" routes by the detected capability.
    architecture: str = Field(default="auto")
    multimodal: MultimodalConfig = Field(default_factory=MultimodalConfig)
    images: ImagesConfig = Field(default_factory=ImagesConfig)
    # The chat model's endpoint, bearer and vision rule; None = today's
    # behavior (vendor default endpoint, OPENAI_API_KEY read by the SDK).
    llm: LlmConnectionConfig | None = Field(default=None)


__all__ = (
    "AskYourDocsConfig",
    "AuthMode",
    "ImagesConfig",
    "LlmAuthConfig",
    "LlmConnectionConfig",
    "MultimodalConfig",
    "MultimodalDetectionConfig",
    "VisionModelConfig",
    "VisionRule",
)
```

- [ ] **Step 4: Update the shipped YAML**

In `python/pydocs_mcp/defaults/default_config.yaml`, replace the `preferred_architecture` line and append the `llm` keys after the `images:` block so the `ask_your_docs:` block reads:

```yaml
ask_your_docs:
  architecture: auto              # one of the agent_registry names; "text_react"
                                  # preserves pre-image behavior exactly
  multimodal:
    preferred_architecture: inline   # what "auto" picks on a vision model
                                     # (2026-09-05: was vision_subagent; set it
                                     # back for a separate describe hop on the
                                     # main model)
    detection:
      override: null              # true | false | null (= run the ladder)
      static_table: true          # rung 2: model-name prefix table
      endpoint_probe: false       # rung 3: GET /v1/models metadata (opt-in)
      image_probe: false          # rung 4: one-shot 1x1-px capability probe (opt-in)
    text_only_fallback: reject    # reject | describe
  images:
    max_per_turn: 3
    max_bytes: 5000000            # per image
    session_retention: 12         # recent images kept reinspectable by the
                                  # reinspect_images agent tool (0 disables)
    max_reinspect_per_turn: 2     # vision-call budget per turn for that tool
                                  # (repeats are memoized and free; 0 disables)
  # The chat model's endpoint. Omit the whole block (or leave it null) for
  # the vendor default endpoint with OPENAI_API_KEY, exactly as before.
  # Precedence: this block < OPENAI_BASE_URL / LLM_MODEL < --base-url /
  # --model < the Connection dialog (session only). Secrets never go here:
  # keys come from the environment variable named by auth.api_key_env,
  # tokens from auth.token_url.
  llm: null
  # llm:
  #   base_url: https://llm.internal/v1  # OpenAI-format endpoint; null = vendor default
  #                                      # (required with auth.token_url)
  #   model: null                        # preselected model id; null = pick in the dialog
  #   auth:                              # exactly one of token_url / api_key_env; omit
  #                                      # `auth` for no bearer
  #     token_url: http://localhost:8899/access-token   # no credentials in the URL
  #     api_key_env: null                # e.g. OPENAI_API_KEY
  #   token_field: null                  # null = the whole response body is the token;
  #                                      # a name reads that field of a JSON body
  #   renew_on_status: [401]             # renew the bearer and retry the request once;
  #                                      # only 401 / 403 / 407 are accepted here
  #   vision: null                       # true | false | null (detect) | {model: <id>}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_config_ask_your_docs.py -q`
Expected: PASS (all rows, including the pre-existing ones).

- [ ] **Step 6: Run the config-adjacent suites**

Run: `pytest tests/test_config_ask_your_docs.py tests/retrieval -q -k "config or default" && ruff check python/pydocs_mcp/retrieval/config tests/test_config_ask_your_docs.py && ruff format --check python/pydocs_mcp/retrieval/config tests/test_config_ask_your_docs.py && mypy python/pydocs_mcp/retrieval/config/ask_your_docs_models.py`
Expected: PASS; `wc -l python/pydocs_mcp/retrieval/config/ask_your_docs_models.py` prints under 200.

- [ ] **Step 7: Commit**

```bash
git add python/pydocs_mcp/retrieval/config/ask_your_docs_models.py python/pydocs_mcp/defaults/default_config.yaml tests/test_config_ask_your_docs.py
git commit -m "config: ask_your_docs.llm block (endpoint, auth, vision) + preferred_architecture default inline"
```

---
## Task 2: Bearer sources, the renewing `httpx.Auth` and the redaction boundary

**Files:**
- Create: `python/pydocs_mcp/harness/ask_your_docs/bearer_tokens.py`
- Create: `tests/harness/ask_your_docs/_connection_fakes.py`
- Create: `tests/harness/ask_your_docs/test_bearer_tokens.py`
- Modify: `pyproject.toml` (`[tool.vulture] ignore_names`)

**Interfaces:**
- Consumes: `AuthMode` (Task 1); `PydocsMCPError` (`pydocs_mcp/exceptions.py`).
- Produces: `BearerSource` Protocol (`current() -> str`, `peek() -> str`, `renew(rejected=None, *, reason="manual") -> str`, `describe() -> BearerStatus`); `BearerStatus(auth_mode, renewed_at, last_four)`; `NoBearer`, `EnvironmentKeyBearer(var_name, *, required)`, `TokenServiceBearer(token_url, *, token_field=None, transport=None, now=time.monotonic, sleep=time.sleep)` with `.last_error`; `RenewOnStatusAuth(bearer, statuses)`, `StripAuthorizationAuth()`; `last_four_of(token)`, `display_url(url)`, `display_host(base_url)`, `redact_bearer(text, bearer)`, `translate_auth_errors(bearer)` (a context manager); `TokenServiceError`, `BearerUnavailableError`, `BearerRejectedError(status, host, last_four, detail=None)`; constants `_TOKEN_FETCH_TIMEOUT_SECONDS = 5.0`, `_TOKEN_FETCH_ATTEMPTS = 3`, `_TOKEN_FETCH_BACKOFF_SECONDS = (2.0, 4.0)`, `_MIN_RENEW_INTERVAL_SECONDS = 5.0`. Test fakes `FakeTokenService`, `RecordingTransport`, `RotatingBearer`, `FakeBearer`, `FakeClock`, `FakeModelsEndpoint`, `FakeProbeLlm`, `chat_completion_body`. Closes AC-4, AC-9 (bearer half), AC-10, AC-11, AC-32, AC-33 (bearer half), AC-36, AC-37, AC-41.

- [ ] **Step 1: Write the named fakes**

Create `tests/harness/ask_your_docs/_connection_fakes.py`:

```python
"""Named fakes for the LLM-connection tests (clean-code rule: no ad-hoc mocks).

Every fake is an ``httpx.MockTransport`` handler or a ``BearerSource`` — no
network leaves a test. ``FakeModelsEndpoint`` / ``FakeProbeLlm`` are the
capability ladder's injectable seams (moved here from
test_multimodal_detection.py when the seams widened to ``(connection, bearer)``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerStatus,
    TokenServiceError,
    last_four_of,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import AuthMode


def chat_completion_body(text: str) -> dict:
    """The minimal chat-completion JSON langchain-openai parses into an AIMessage."""
    return {
        "id": "cmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": "fake",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class FakeTokenService:
    """The token endpoint as an ``httpx.MockTransport`` handler; records every call.

    ``fail_first`` answers 503 that many times and then recovers; ``fail_from``
    answers 503 from that call number on (1-based); ``body_shape`` is
    ``"text"`` (the body IS the token), ``"json"`` (``{json_key: token}``) or
    ``"html"`` (a non-JSON body, to provoke E2).
    """

    def __init__(
        self,
        tokens: list[str],
        *,
        fail_first: int = 0,
        fail_from: int | None = None,
        body_shape: str = "text",
        json_key: str = "access_token",
    ) -> None:
        self.tokens = list(tokens)
        self.fail_first = fail_first
        self.fail_from = fail_from
        self.body_shape = body_shape
        self.json_key = json_key
        self.calls = 0
        self.served = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls <= self.fail_first or (self.fail_from and self.calls >= self.fail_from):
            return httpx.Response(503, text="token service down")
        token = self.tokens[min(self.served, len(self.tokens) - 1)]
        self.served += 1
        if self.body_shape == "json":
            return httpx.Response(200, json={self.json_key: token})
        if self.body_shape == "html":
            return httpx.Response(200, text="<html>nope</html>", headers={"content-type": "text/html"})
        return httpx.Response(200, text=token)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


class RecordingTransport:
    """The chat endpoint (and ``/models``) as an ``httpx.MockTransport`` handler.

    Answers the scripted statuses in order (200 after the script runs out; an
    ``Exception`` item is raised instead of answered), records every request,
    and — with ``echo_bearer_in_401`` — puts the presented bearer into the 401
    body, the way gateways do (AC-35).
    """

    def __init__(
        self,
        script: list[int | Exception] | None = None,
        *,
        echo_bearer_in_401: bool = False,
        model_ids: tuple[str, ...] = ("model-a", "model-b"),
        reply: str = "OK",
    ) -> None:
        self.script = list(script or [])
        self.echo_bearer_in_401 = echo_bearer_in_401
        self.model_ids = model_ids
        self.reply = reply
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status = self.script.pop(0) if self.script else 200
        if isinstance(status, Exception):
            raise status
        if status == 200 and request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": i} for i in self.model_ids]})
        if status == 200:
            return httpx.Response(200, json=chat_completion_body(self.reply))
        presented = request.headers.get("Authorization", "")
        message = f"rejected {presented}" if self.echo_bearer_in_401 else "rejected"
        return httpx.Response(status, json={"error": {"message": message, "type": "auth"}})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def authorizations(self) -> list[str | None]:
        return [r.headers.get("Authorization") for r in self.requests]

    def retry_counts(self) -> list[str | None]:
        return [r.headers.get("x-stainless-retry-count") for r in self.requests]


class RotatingBearer:
    """A ``BearerSource`` whose ``current()`` returns k1, k2, k3… (proves per-attempt re-evaluation)."""

    def __init__(self) -> None:
        self.count = 0
        self.last = ""

    def current(self) -> str:
        self.count += 1
        self.last = f"k{self.count}"
        return self.last

    def peek(self) -> str:
        return self.last

    def renew(self, rejected: str | None = None, *, reason: str = "manual") -> str:
        return self.current()

    def describe(self) -> BearerStatus:
        return BearerStatus(AuthMode.ENV_KEY, None, last_four_of(self.last))


class FakeBearer:
    """A fixed token-service bearer for page tests: fixed renewal time, renew counter, optional failure."""

    def __init__(
        self,
        value: str = "tok-fixed-abcd",
        *,
        renewed_at: datetime | None = None,
        fail: bool = False,
    ) -> None:
        self.value = value
        self.renewed_at = renewed_at
        self.fail = fail
        self.renewals = 0
        self.last_error: str | None = None

    def current(self) -> str:
        if self.fail:
            raise TokenServiceError(
                "token service http://localhost:8899/access-token unreachable after 3 attempts "
                "(last: ConnectError)"
            )
        return self.value

    def peek(self) -> str:
        return self.value

    def renew(self, rejected: str | None = None, *, reason: str = "manual") -> str:
        self.renewals += 1
        return self.current()

    def describe(self) -> BearerStatus:
        return BearerStatus(AuthMode.TOKEN_SERVICE, self.renewed_at, last_four_of(self.value))


class FakeClock:
    """An injectable ``now`` for the renew interval and the listing TTL — tests never sleep."""

    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeModelsEndpoint:
    """The rung-3 / listing seam ``(connection, bearer) -> list[dict]``; records the bearer it saw."""

    def __init__(
        self,
        entry: dict | None = None,
        error: Exception | None = None,
        *,
        ids: tuple[str, ...] = (),
    ) -> None:
        self.entry = entry
        self.error = error
        self.ids = ids
        self.calls = 0
        self.seen_bearer: str | None = None
        self.seen_base_url: str | None = None

    async def __call__(self, connection: Any, bearer: Any) -> list[dict]:
        self.calls += 1
        self.seen_bearer = bearer.current()
        self.seen_base_url = connection.base_url
        if self.error is not None:
            raise self.error
        if self.entry is not None:
            return [self.entry]
        return [{"id": i} for i in self.ids]


class FakeProbeLlm:
    """The rung-4 seam ``(connection, bearer, model, timeout) -> str``; simulates the tiny-image call."""

    def __init__(self, outcome: str = "ok") -> None:
        self.outcome = outcome  # "ok" | "image_error" | "server_error"
        self.calls = 0
        self.seen_bearer: str | None = None

    async def __call__(self, connection: Any, bearer: Any, model: str, timeout: float) -> str:
        self.calls += 1
        self.seen_bearer = bearer.current()
        if self.outcome == "image_error":
            raise ValueError("400: image content not supported by this model")
        if self.outcome == "server_error":
            raise TimeoutError("upstream timeout")
        return "OK"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/harness/ask_your_docs/test_bearer_tokens.py`:

```python
"""Bearer sources, the renewing httpx.Auth and the redaction boundary
(LLM-connection design §4.4 — AC-4, AC-9, AC-10, AC-11, AC-32, AC-33, AC-36,
AC-37, AC-41). Core deps only: httpx ships with the required openai dep."""

from __future__ import annotations

import asyncio
import logging
import threading

import httpx
import pytest

from pydocs_mcp.harness.ask_your_docs import bearer_tokens as bt
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerRejectedError,
    BearerUnavailableError,
    EnvironmentKeyBearer,
    NoBearer,
    RenewOnStatusAuth,
    StripAuthorizationAuth,
    TokenServiceBearer,
    TokenServiceError,
    display_host,
    display_url,
    last_four_of,
    redact_bearer,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import AuthMode

from ._connection_fakes import FakeClock, FakeTokenService, RecordingTransport

_URL = "http://localhost:8899/access-token"


def _bearer(service: FakeTokenService, **kw) -> TokenServiceBearer:
    return TokenServiceBearer(_URL, transport=service.transport, sleep=lambda _s: None, **kw)


def test_token_service_fetches_once_and_caches() -> None:
    """AC-4: two current() calls, one HTTP GET; the stripped text body is the token."""
    service = FakeTokenService(["  tok-one-abcd\n"])
    bearer = _bearer(service)
    assert bearer.current() == "tok-one-abcd"
    assert bearer.current() == "tok-one-abcd"
    assert service.calls == 1
    status = bearer.describe()
    assert status.auth_mode is AuthMode.TOKEN_SERVICE
    assert status.last_four == "abcd" and status.renewed_at is not None
    assert bearer.peek() == "tok-one-abcd"


def test_token_field_reads_the_json_body() -> None:
    """AC-4: token_field names the JSON field that holds the token."""
    service = FakeTokenService(["tok-json-wxyz"], body_shape="json", json_key="access_token")
    bearer = _bearer(service, token_field="access_token")
    assert bearer.current() == "tok-json-wxyz"


def test_token_service_down_after_three_attempts() -> None:
    """AC-10: 3 attempts with the 2s/4s backoff, then TokenServiceError naming the URL and 3."""
    service = FakeTokenService(["never"], fail_first=3)
    sleeps: list[float] = []
    bearer = TokenServiceBearer(_URL, transport=service.transport, sleep=sleeps.append)
    with pytest.raises(TokenServiceError) as excinfo:
        bearer.current()
    assert "token service http://localhost:8899/access-token unreachable after 3 attempts" in str(
        excinfo.value
    )
    assert "status 503" in str(excinfo.value)
    assert service.calls == 3
    assert sleeps == [2.0, 4.0]
    assert bearer.last_error is not None and "unreachable" in bearer.last_error


def test_empty_token_body_is_an_error() -> None:
    """AC-41 / E3: an empty or whitespace-only body (text or JSON field) never becomes a bearer."""
    for tokens, field in (([""], None), (["   \n"], None), ([""], "access_token")):
        shape = "json" if field else "text"
        service = FakeTokenService(tokens, body_shape=shape)
        bearer = _bearer(service, token_field=field)
        with pytest.raises(TokenServiceError, match="expected a non-empty token body, got empty"):
            bearer.current()
        assert service.calls == 1  # a body-shape failure is not retried


def test_unparsable_body_names_the_shape_never_the_bytes() -> None:
    """AC-37 / E2: a JSON body under another key, or a non-JSON body, is described by shape only."""
    other_key = FakeTokenService(["tok-secret-9999"], body_shape="json", json_key="token")
    with pytest.raises(TokenServiceError) as excinfo:
        _bearer(other_key, token_field="access_token").current()
    assert "expected JSON body with field 'access_token', got keys=['token']" in str(excinfo.value)
    assert "tok-secret-9999" not in str(excinfo.value)
    html = FakeTokenService(["tok-secret-9999"], body_shape="html")
    with pytest.raises(TokenServiceError, match=r"got non-JSON body \(text/html, \d+ bytes\)"):
        _bearer(html, token_field="access_token").current()


def test_renew_is_compare_and_swap() -> None:
    """AC-32: a renew for a token that is no longer the cache returns the cache without fetching."""
    service = FakeTokenService(["t1", "t2", "t3"])
    bearer = _bearer(service)
    assert bearer.current() == "t1"
    assert bearer.renew("t1", reason="rejected_status") == "t2"
    assert service.calls == 2
    assert bearer.renew("t1", reason="rejected_status") == "t2"  # another flow already renewed
    assert service.calls == 2


def test_renew_is_rate_limited_between_renewals() -> None:
    """AC-33: renewals inside _MIN_RENEW_INTERVAL_SECONDS return the cache; the first renew
    after the initial fetch is never rate-limited."""
    clock = FakeClock()
    service = FakeTokenService(["t1", "t2", "t3"])
    bearer = _bearer(service, now=clock)
    assert bearer.current() == "t1"
    assert bearer.renew("t1", reason="rejected_status") == "t2"  # first renew: fetches
    assert bearer.renew("t2", reason="rejected_status") == "t2"  # within the interval: cache
    assert service.calls == 2
    clock.advance(bt._MIN_RENEW_INTERVAL_SECONDS + 1)
    assert bearer.renew("t2", reason="rejected_status") == "t3"
    assert service.calls == 3


def test_concurrent_first_requests_cost_one_fetch() -> None:
    """AC-32: the double-checked lock makes N concurrent first requests one HTTP GET."""
    service = FakeTokenService(["t1"])
    bearer = _bearer(service)
    seen: list[str] = []
    threads = [threading.Thread(target=lambda: seen.append(bearer.current())) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == ["t1"] * 6
    assert service.calls == 1


def test_environment_key_bearer_reads_every_call(monkeypatch) -> None:
    """AC-11: rotating the variable rotates the value; strict raises, lenient yields ''."""
    monkeypatch.setenv("LLM_KEY", "key-one-1111")
    strict = EnvironmentKeyBearer("LLM_KEY", required=True)
    assert strict.current() == "key-one-1111"
    monkeypatch.setenv("LLM_KEY", "key-two-2222")
    assert strict.current() == "key-two-2222"
    assert strict.describe() == bt.BearerStatus(AuthMode.ENV_KEY, None, "2222")
    monkeypatch.delenv("LLM_KEY")
    with pytest.raises(BearerUnavailableError, match="environment variable LLM_KEY is unset"):
        strict.current()
    lenient = EnvironmentKeyBearer("LLM_KEY", required=False)
    assert lenient.current() == "" and lenient.peek() == ""
    assert lenient.describe().last_four == ""


def test_no_bearer_is_the_null_object() -> None:
    bearer = NoBearer()
    assert bearer.current() == "" and bearer.renew() == "" and bearer.peek() == ""
    assert bearer.describe() == bt.BearerStatus(AuthMode.NONE, None, "")


def test_display_url_and_host_strip_credentials_and_query() -> None:
    """AC-36 / H4."""
    assert display_url("http://user:s3cr3tpw@host:8899/t?k=v4lue") == "http://host:8899/t"
    assert display_url("https://llm.internal/v1") == "https://llm.internal/v1"
    assert display_host("http://gpu-box:8000/v1") == "gpu-box:8000"
    assert display_host("https://llm.internal/v1") == "llm.internal"
    assert display_host(None) == "vendor default"
    service = FakeTokenService(["never"], fail_first=3)
    bearer = TokenServiceBearer(
        "http://user:s3cr3tpw@localhost:8899/t?k=v4lue",
        transport=service.transport,
        sleep=lambda _s: None,
    )
    with pytest.raises(TokenServiceError) as excinfo:
        bearer.current()
    assert "s3cr3tpw" not in str(excinfo.value) and "v4lue" not in str(excinfo.value)


def test_last_four_and_redaction() -> None:
    """D4 + H4: last_four is the last four characters; redact_bearer masks the value and any
    'Bearer <x>' pattern."""
    assert last_four_of("tok-one-abcd") == "abcd" and last_four_of("") == ""
    service = FakeTokenService(["tok-one-abcd"])
    bearer = _bearer(service)
    bearer.current()
    text = "Error code: 401 - {'error': 'rejected Bearer tok-one-abcd'} (tok-one-abcd)"
    redacted = redact_bearer(text, bearer)
    assert "tok-one-abcd" not in redacted
    assert "…abcd" in redacted
    assert redact_bearer("Authorization: Bearer some-other-xyz1", NoBearer()) == (
        "Authorization: Bearer …"
    )


def _flow_client(bearer, statuses, recorder: RecordingTransport, first_token: str) -> httpx.Client:
    client = httpx.Client(
        auth=RenewOnStatusAuth(bearer, statuses),
        transport=recorder.transport,
        headers={"Authorization": f"Bearer {first_token}"},
    )
    return client


def test_renew_on_status_auth_renews_and_resends_exactly_once() -> None:
    """R4 at the httpx level: 401 → renew → the SAME request once more with the new bearer;
    a second 401 is returned, not retried again."""
    service = FakeTokenService(["t1", "t2"])
    bearer = _bearer(service)
    assert bearer.current() == "t1"
    recorder = RecordingTransport([401, 200])
    with _flow_client(bearer, (401,), recorder, "t1") as client:
        response = client.post("http://llm.test/v1/chat/completions", json={"q": 1})
    assert response.status_code == 200
    assert recorder.authorizations() == ["Bearer t1", "Bearer t2"]
    assert service.calls == 2
    again = RecordingTransport([401, 401])
    with _flow_client(bearer, (401,), again, "t2") as client:
        response = client.post("http://llm.test/v1/chat/completions", json={"q": 1})
    assert response.status_code == 401 and len(again.requests) == 2
    other = RecordingTransport([403])
    with _flow_client(bearer, (401,), other, "t2") as client:
        assert client.get("http://llm.test/v1/models").status_code == 403
    assert len(other.requests) == 1  # 403 is not in renew_on_status → no renew


def test_renew_on_status_auth_async_flow_matches_sync() -> None:
    service = FakeTokenService(["t1", "t2"])
    bearer = _bearer(service)
    bearer.current()
    recorder = RecordingTransport([401, 200])

    async def go() -> int:
        async with httpx.AsyncClient(
            auth=RenewOnStatusAuth(bearer, (401,)),
            transport=recorder.transport,
            headers={"Authorization": "Bearer t1"},
        ) as client:
            return (await client.post("http://llm.test/v1/chat/completions", json={})).status_code

    assert asyncio.run(go()) == 200
    assert recorder.authorizations() == ["Bearer t1", "Bearer t2"]


def test_failed_renewal_inside_the_flow_returns_the_rejected_response() -> None:
    """Plan deviation D-4: a token service that is down at renew time does not raise out of
    send (the SDK would retry the whole request); the 401 surfaces and last_error is kept."""
    service = FakeTokenService(["t1"], fail_from=2)
    bearer = _bearer(service)
    assert bearer.current() == "t1"
    recorder = RecordingTransport([401, 200])
    with _flow_client(bearer, (401,), recorder, "t1") as client:
        response = client.post("http://llm.test/v1/chat/completions", json={})
    assert response.status_code == 401
    assert len(recorder.requests) == 1
    assert service.calls == 4  # the initial fetch + one bounded renew burst of 3
    assert bearer.last_error is not None and "unreachable after 3 attempts" in bearer.last_error


def test_strip_authorization_auth_removes_the_header() -> None:
    recorder = RecordingTransport([200])
    with httpx.Client(
        auth=StripAuthorizationAuth(),
        transport=recorder.transport,
        headers={"Authorization": "Bearer placeholder"},
    ) as client:
        assert client.get("http://llm.test/v1/models").status_code == 200
    assert recorder.authorizations() == [None]


def test_bearer_rejected_error_carries_last_four_and_detail() -> None:
    error = BearerRejectedError(status=401, host="llm.internal", last_four="abcd")
    assert str(error) == (
        "endpoint llm.internal rejected bearer …abcd (status 401); "
        "renew the token or check ask_your_docs.llm.auth"
    )
    detailed = BearerRejectedError(status=401, host="h", last_four="abcd", detail="down")
    assert str(detailed).endswith("; renew failed: down")


def test_token_never_appears_in_logs(caplog) -> None:
    """AC-9 (bearer half): bearer_fetched / bearer_renewed records carry neither the token
    nor last_four."""
    caplog.set_level(logging.DEBUG)
    service = FakeTokenService(["tok-one-abcd", "tok-two-efgh"])
    bearer = _bearer(service)
    bearer.current()
    bearer.renew("tok-one-abcd", reason="rejected_status")
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "bearer_fetched" in text and "bearer_renewed" in text
    assert "tok-one" not in text and "tok-two" not in text
    assert "abcd" not in text and "efgh" not in text
    assert '"token_url_host": "localhost"' in text
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_bearer_tokens.py -q`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'pydocs_mcp.harness.ask_your_docs.bearer_tokens'`.

- [ ] **Step 4: Write `bearer_tokens.py`**

```python
"""Bearer sources for the ask-your-docs chat model (LLM-connection design §4.4).

One ``BearerSource`` per auth identity holds the value that becomes
``Authorization: Bearer <value>``: nothing (``NoBearer``), an environment
variable (``EnvironmentKeyBearer``) or a token fetched from an internal token
service and renewed on demand (``TokenServiceBearer``). ``RenewOnStatusAuth``
is the ``httpx.Auth`` flow that renews on a ``401`` and re-sends the same
request once (the SDK itself never retries a 401); ``StripAuthorizationAuth``
removes the header for the no-auth case. ``redact_bearer``,
``translate_auth_errors`` and ``display_url`` are the redaction boundary every
auth failure crosses before a person, a tool result or a trace sees it (H4).

Light by contract: ``httpx`` (transitive via the required ``openai`` dep) at
module level; ``openai`` itself is imported function-locally.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

import httpx

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.retrieval.config.ask_your_docs_models import AuthMode

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")

# WHY these values: the envelope the capability probes use (multimodal.py
# _PROBE_*), defined again here on purpose — a later probe tuning must never
# silently change token fetching.
_TOKEN_FETCH_TIMEOUT_SECONDS = 5.0
_TOKEN_FETCH_ATTEMPTS = 3
_TOKEN_FETCH_BACKOFF_SECONDS = (2.0, 4.0)
# H3: a persistently rejecting endpoint must not turn every SDK attempt into a
# token fetch — a renew younger than this (measured between renew attempts,
# never against the first fetch) returns the cache.
_MIN_RENEW_INTERVAL_SECONDS = 5.0
_BEARER_PREFIX = "Bearer "
_BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+")


class TokenServiceError(PydocsMCPError, RuntimeError):
    """The token service could not produce a token (design E1, E2, E3)."""


class BearerUnavailableError(PydocsMCPError, RuntimeError):
    """A configured environment key is unset (design E5)."""


class BearerRejectedError(PydocsMCPError, RuntimeError):
    """The endpoint rejected the bearer after the one renewal (design E4).

    Built WITHOUT the response body, which gateways fill with the presented
    credential (H4); ``detail`` carries a failed renewal's cause.
    """

    def __init__(self, *, status: int, host: str, last_four: str, detail: str | None = None) -> None:
        self.status = status
        self.host = host
        self.last_four = last_four
        message = (
            f"endpoint {host} rejected bearer …{last_four} (status {status}); "
            "renew the token or check ask_your_docs.llm.auth"
        )
        if detail:
            message += f"; renew failed: {detail}"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class BearerStatus:
    """What the UI may show — never the value itself (D4)."""

    auth_mode: AuthMode
    renewed_at: datetime | None
    last_four: str  # "" only when there is no bearer


@runtime_checkable
class BearerSource(Protocol):
    def current(self) -> str: ...  # the cached value; fetches lazily on the first call
    def peek(self) -> str: ...  # the cached value without fetching (redaction, the UI)
    def renew(self, rejected: str | None = None, *, reason: str = "manual") -> str: ...
    def describe(self) -> BearerStatus: ...


def last_four_of(token: str) -> str:
    """The last four characters (D4); ``""`` for an empty bearer."""
    return token[-4:]


def display_url(url: str) -> str:
    """``scheme://host[:port]/path`` — userinfo and query stripped (H4)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    prefix = f"{parts.scheme}://" if parts.scheme else ""
    return f"{prefix}{host}{parts.path}"


def display_host(base_url: str | None) -> str:
    """``host[:port]`` for the status line, or ``vendor default``."""
    if not base_url:
        return "vendor default"
    parts = urlsplit(base_url)
    host = parts.hostname or base_url
    return f"{host}:{parts.port}" if parts.port is not None else host


class NoBearer:
    """Null Object: no ``Authorization`` header at all (``AuthMode.NONE``)."""

    def current(self) -> str:
        return ""

    def peek(self) -> str:
        return ""

    def renew(self, rejected: str | None = None, *, reason: str = "manual") -> str:
        return ""

    def describe(self) -> BearerStatus:
        return BearerStatus(AuthMode.NONE, None, "")


class EnvironmentKeyBearer:
    """The bearer is an environment variable, re-read on every call.

    ``required=False`` is the lenient no-block form (an unset variable means
    no header, never an error — the SDK's own rule); ``required=True`` is the
    strict form for an explicit ``auth.api_key_env`` (design E5).
    """

    def __init__(self, var_name: str, *, required: bool) -> None:
        self.var_name = var_name
        self.required = required

    def current(self) -> str:
        value = os.environ.get(self.var_name, "")
        if not value and self.required:
            raise BearerUnavailableError(
                f"environment variable {self.var_name} is unset; "
                "ask_your_docs.llm.auth.api_key_env names it"
            )
        return value

    def peek(self) -> str:
        return os.environ.get(self.var_name, "")

    def renew(self, rejected: str | None = None, *, reason: str = "manual") -> str:
        return self.current()

    def describe(self) -> BearerStatus:
        return BearerStatus(AuthMode.ENV_KEY, None, last_four_of(self.peek()))


class TokenServiceBearer:
    """A token fetched from ``token_url`` on first use, cached, renewed on demand (R4)."""

    def __init__(
        self,
        token_url: str,
        *,
        token_field: str | None = None,
        transport: httpx.BaseTransport | None = None,  # test seam
        now: Callable[[], float] = time.monotonic,  # test seam (the renew interval)
        sleep: Callable[[float], None] = time.sleep,  # test seam (the fetch backoff)
    ) -> None:
        self.token_url = token_url
        self.token_field = token_field
        self.last_error: str | None = None
        self._transport = transport
        self._now = now
        self._sleep = sleep
        self._lock = threading.Lock()
        self._token = ""
        self._renewed_at: datetime | None = None
        self._renew_attempted_at: float | None = None

    def current(self) -> str:
        if self._token:
            return self._token
        with self._lock:  # a second concurrent first request waits, then reads the cache
            if not self._token:
                self._replace(self._fetch_recording(), reason="first_request")
            return self._token

    def peek(self) -> str:
        return self._token

    def renew(self, rejected: str | None = None, *, reason: str = "manual") -> str:
        with self._lock:
            if rejected is not None and self._token != rejected:
                return self._cache_hit()  # another flow renewed first (H3)
            if self._within_renew_interval():
                return self._cache_hit()  # a persistently rejecting endpoint (H3)
            self._renew_attempted_at = self._now()
            self._replace(self._fetch_recording(), reason=reason)
            return self._token

    def describe(self) -> BearerStatus:
        return BearerStatus(AuthMode.TOKEN_SERVICE, self._renewed_at, last_four_of(self._token))

    def _within_renew_interval(self) -> bool:
        if self._renew_attempted_at is None or not self._token:
            return False
        return self._now() - self._renew_attempted_at < _MIN_RENEW_INTERVAL_SECONDS

    def _cache_hit(self) -> str:
        self._log("bearer_renewed", reason="cache_hit")
        return self._token

    def _replace(self, token: str, *, reason: str) -> None:
        self._token = token
        self._renewed_at = datetime.now().astimezone()
        self._log("bearer_fetched" if reason == "first_request" else "bearer_renewed", reason=reason)

    def _log(self, event: str, *, reason: str) -> None:
        # Never the token, never last_four, never the full URL (H4).
        log.info(
            json.dumps(
                {
                    "event": event,
                    "auth_mode": AuthMode.TOKEN_SERVICE.value,
                    "token_url_host": urlsplit(self.token_url).hostname,
                    "attempts": _TOKEN_FETCH_ATTEMPTS,
                    "renewed_at": self._renewed_at.isoformat() if self._renewed_at else None,
                    "reason": reason,
                }
            )
        )

    def _fetch_recording(self) -> str:
        try:
            token = self._fetch()
        except TokenServiceError as exc:
            self.last_error = str(exc)
            raise
        self.last_error = None
        return token

    def _fetch(self) -> str:
        last = "no attempt"
        for attempt in range(_TOKEN_FETCH_ATTEMPTS):
            try:
                with httpx.Client(
                    timeout=_TOKEN_FETCH_TIMEOUT_SECONDS, transport=self._transport
                ) as client:
                    response = client.get(self.token_url)
                response.raise_for_status()
                return self._parse_body(response)
            except httpx.HTTPStatusError as exc:
                last = f"status {exc.response.status_code}"
            except httpx.HTTPError as exc:
                last = exc.__class__.__name__
            if attempt < _TOKEN_FETCH_ATTEMPTS - 1:
                self._sleep(_TOKEN_FETCH_BACKOFF_SECONDS[min(attempt, 1)])
        raise TokenServiceError(
            f"token service {display_url(self.token_url)} unreachable after "
            f"{_TOKEN_FETCH_ATTEMPTS} attempts (last: {last})"
        )

    def _parse_body(self, response: httpx.Response) -> str:
        where = f"token service {display_url(self.token_url)}"
        content_type = response.headers.get("content-type", "unknown")
        if self.token_field is None:
            token = response.text.strip()  # the owner's requests.get(url).text
        else:
            token = _json_field(response, self.token_field, where, content_type)
        if not token:
            raise TokenServiceError(
                f"{where}: expected a non-empty token body, got empty (content-type {content_type})"
            )
        return token


def _json_field(response: httpx.Response, field: str, where: str, content_type: str) -> str:
    """The token under ``field`` — failures name the body's SHAPE, never its bytes (E2)."""
    try:
        payload = response.json()
    except ValueError:
        raise TokenServiceError(
            f"{where}: expected JSON body with field {field!r}, got non-JSON body "
            f"({content_type}, {len(response.content)} bytes)"
        ) from None
    if not isinstance(payload, dict) or field not in payload:
        keys = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
        raise TokenServiceError(f"{where}: expected JSON body with field {field!r}, got keys={keys}")
    return str(payload[field]).strip()


def _bearer_in(request: httpx.Request) -> str:
    return request.headers.get("Authorization", "").removeprefix(_BEARER_PREFIX)


class RenewOnStatusAuth(httpx.Auth):
    """Renew the bearer on a status in ``statuses`` and re-send the SAME request once (R4).

    The header on the first pass comes from the SDK's callable ``api_key``;
    this flow only rewrites it after a renewal. ``sent`` is parsed from the
    request itself so the compare-and-swap sees the token the endpoint
    actually rejected. A renewal that fails (token service down) is not
    raised out of ``send`` — the SDK would retry the whole request — the
    rejected response is returned and the cause rides ``bearer.last_error``.
    """

    def __init__(self, bearer: BearerSource, statuses: tuple[int, ...]) -> None:
        self.bearer = bearer
        self.statuses = statuses

    def sync_auth_flow(self, request: httpx.Request):
        response = yield request
        if response.status_code not in self.statuses:
            return
        try:
            renewed = self.bearer.renew(_bearer_in(request), reason="rejected_status")
        except TokenServiceError:
            return
        request.headers["Authorization"] = _BEARER_PREFIX + renewed
        yield request

    async def async_auth_flow(self, request: httpx.Request):
        response = yield request
        if response.status_code not in self.statuses:
            return
        try:  # the bounded fetch never blocks the event loop (CLAUDE.md §Async Patterns)
            renewed = await asyncio.to_thread(
                self.bearer.renew, _bearer_in(request), reason="rejected_status"
            )
        except TokenServiceError:
            return
        request.headers["Authorization"] = _BEARER_PREFIX + renewed
        yield request


class StripAuthorizationAuth(httpx.Auth):
    """No ``Authorization`` header on the wire (``AuthMode.NONE``)."""

    def auth_flow(self, request: httpx.Request):
        request.headers.pop("Authorization", None)
        yield request


def redact_bearer(text: str, bearer: BearerSource) -> str:
    """Mask the bearer's cached value and any ``Bearer <x>`` pattern in ``text`` (H4)."""
    token = bearer.peek()
    mask = f"…{last_four_of(token)}" if token else "…"
    if token:
        text = text.replace(token, mask)
    return _BEARER_PATTERN.sub(f"Bearer {mask}", text)


@contextmanager
def translate_auth_errors(bearer: BearerSource) -> Iterator[None]:
    """Turn the SDK's 401/403 errors into ``BearerRejectedError`` without the body (E4, H4).

    Wraps every consumer of a factory-built model, for ALL auth modes: with
    an environment key or no auth there is no renewing flow, so a 401 is a
    raw SDK error that would otherwise carry the response body.
    """
    import openai  # heavy; function-local by contract

    try:
        yield
    except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
        host = exc.response.url.host if exc.response is not None else "endpoint"
        raise BearerRejectedError(
            status=exc.status_code,
            host=host,
            last_four=last_four_of(bearer.peek()),
            detail=getattr(bearer, "last_error", None),
        ) from None


__all__ = (
    "BearerRejectedError",
    "BearerSource",
    "BearerStatus",
    "BearerUnavailableError",
    "EnvironmentKeyBearer",
    "NoBearer",
    "RenewOnStatusAuth",
    "StripAuthorizationAuth",
    "TokenServiceBearer",
    "TokenServiceError",
    "display_host",
    "display_url",
    "last_four_of",
    "redact_bearer",
    "translate_auth_errors",
)
```

- [ ] **Step 5: Whitelist the Protocol parameter names for vulture**

In `pyproject.toml` `[tool.vulture] ignore_names`, add two lines after `"node_qname"`:

```toml
    "rejected",               # bearer_tokens.py BearerSource.renew Protocol param (NoBearer ignores it)
    "reason",                 # bearer_tokens.py BearerSource.renew Protocol param (NoBearer ignores it)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/harness/ask_your_docs/test_bearer_tokens.py -q`
Expected: PASS (17 tests).

- [ ] **Step 7: Lint, budget, commit**

Run: `ruff check python/pydocs_mcp/harness/ask_your_docs/bearer_tokens.py tests/harness/ask_your_docs/ && ruff format --check python/pydocs_mcp/harness/ask_your_docs/bearer_tokens.py tests/harness/ask_your_docs/ && vulture python/pydocs_mcp --min-confidence 80 && complexipy python/pydocs_mcp/harness/ask_your_docs/bearer_tokens.py --max-complexity-allowed 15 && wc -l python/pydocs_mcp/harness/ask_your_docs/bearer_tokens.py`
Expected: clean; the line count is under 500. Then:

```bash
git checkout -- complexipy-snapshot.json
git add python/pydocs_mcp/harness/ask_your_docs/bearer_tokens.py tests/harness/ask_your_docs/_connection_fakes.py tests/harness/ask_your_docs/test_bearer_tokens.py pyproject.toml
git commit -m "harness(ask-your-docs): bearer sources, renew-on-401 httpx.Auth, redaction boundary"
```

---
## Task 3: The `LlmConnection` value object, precedence resolution, identity and the bearer registry

**Files:**
- Create: `python/pydocs_mcp/harness/ask_your_docs/llm_connection.py` (the pure half; Task 5 appends the factory, Task 7 appends `resolve_vision_capabilities`)
- Test: `tests/harness/ask_your_docs/test_llm_connection.py`

**Interfaces:**
- Consumes: `AuthMode`, `VisionRule`, `LlmConnectionConfig`, `VisionModelConfig`, `_DEFAULT_MODEL`, `_DEFAULT_API_KEY_ENV`, `_DEFAULT_RENEW_ON_STATUS` (Task 1); `NoBearer`, `EnvironmentKeyBearer`, `TokenServiceBearer`, `display_url`, `display_host` (Task 2).
- Produces: `ConnectionOverride(base_url=None, model=None)`; `LlmConnection` (fields `base_url, model, auth_mode, token_url, api_key_env, token_field, renew_on_status, vision_rule, vision_model, config_path, block_present, configured_base_url`; properties `origin_changed`, `cleartext_bearer`); `resolve_llm_connection(yaml_block, environment, launch, dialog, *, config_path) -> LlmConnection`; `connection_identity(connection) -> tuple[AuthMode, str, bool]`; `bearer_for_connection(connection) -> BearerSource`; `clear_bearer_registry()`; constants `_LOOPBACK_HOSTS`, `_TIER_NAMES`. Closes AC-1, AC-2, AC-20, AC-31 (resolution half), AC-39 (resolution half), AC-40 (registry half, with Task 5's factory test).

- [ ] **Step 1: Write the failing tests**

Create `tests/harness/ask_your_docs/test_llm_connection.py`:

```python
"""LlmConnection resolution, identity and the bearer registry (LLM-connection
design §4.2–§4.3 — AC-1, AC-2, AC-20, AC-28, AC-31, AC-39, AC-40). Core deps only."""

from __future__ import annotations

import json
import logging

import pytest

from pydocs_mcp.harness.ask_your_docs import bearer_tokens as bt
from pydocs_mcp.harness.ask_your_docs import llm_connection as lc
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    EnvironmentKeyBearer,
    NoBearer,
    TokenServiceBearer,
)
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    LlmConnection,
    bearer_for_connection,
    clear_bearer_registry,
    connection_identity,
    resolve_llm_connection,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import (
    AuthMode,
    LlmConnectionConfig,
    VisionRule,
)

_YAML_URL = "https://llm.internal/v1"
_TOKEN_URL = "http://localhost:8899/access-token"
_NONE = ConnectionOverride()


def _block(**overrides) -> LlmConnectionConfig:
    data = {"base_url": _YAML_URL, "auth": {"token_url": _TOKEN_URL}}
    data.update(overrides)
    return LlmConnectionConfig.model_validate(data)


def _resolve(block, env=None, launch=_NONE, dialog=_NONE) -> LlmConnection:
    return resolve_llm_connection(block, env or {}, launch, dialog, config_path="cfg.yaml")


@pytest.fixture(autouse=True)
def _fresh_registry():
    clear_bearer_registry()
    yield
    clear_bearer_registry()


@pytest.mark.parametrize(
    ("env", "launch", "dialog", "expected", "tier"),
    [
        ({}, _NONE, _NONE, _YAML_URL, "yaml"),
        ({"OPENAI_BASE_URL": "http://localhost:8000/v1"}, _NONE, _NONE, "http://localhost:8000/v1", "environment"),
        (
            {"OPENAI_BASE_URL": "http://localhost:8000/v1"},
            ConnectionOverride(base_url="http://gpu-box:8000/v1"),
            _NONE,
            "http://gpu-box:8000/v1",
            "cli",
        ),
        (
            {"OPENAI_BASE_URL": "http://localhost:8000/v1"},
            ConnectionOverride(base_url="http://gpu-box:8000/v1"),
            ConnectionOverride(base_url="http://other/v1"),
            "http://other/v1",
            "dialog",
        ),
        ({"OPENAI_BASE_URL": ""}, _NONE, _NONE, _YAML_URL, "yaml"),  # empty = unset
    ],
)
def test_base_url_precedence_fold(caplog, env, launch, dialog, expected, tier) -> None:
    """AC-1: YAML < environment < CLI < dialog, field by field; the log names the tier."""
    caplog.set_level(logging.INFO)
    connection = _resolve(_block(), env, launch, dialog)
    assert connection.base_url == expected
    resolved = [json.loads(r.getMessage()) for r in caplog.records if "connection_resolved" in r.getMessage()]
    assert resolved and resolved[-1]["base_url_tier"] == tier


def test_model_precedence_and_the_two_bottoms() -> None:
    """AC-1: model follows the same fold; the bottom is _DEFAULT_MODEL without a block and
    None with one (D2: pick in the dialog)."""
    assert _resolve(None).model == "gpt-4o-mini"
    assert _resolve(_block()).model is None
    assert _resolve(_block(model="yaml-m")).model == "yaml-m"
    assert _resolve(_block(model="yaml-m"), {"LLM_MODEL": "env-m"}).model == "env-m"
    assert _resolve(_block(), {"LLM_MODEL": "env-m"}, ConnectionOverride(model="cli-m")).model == "cli-m"
    assert (
        _resolve(_block(), {}, ConnectionOverride(model="cli-m"), ConnectionOverride(model="dlg-m")).model
        == "dlg-m"
    )
    assert _resolve(_block(), {"LLM_MODEL": ""}).model is None


def test_no_block_is_todays_shape() -> None:
    """AC-2: no block ⇒ lenient OPENAI_API_KEY bearer, DETECT, (401,), nothing to compare against."""
    connection = _resolve(None, {"OPENAI_BASE_URL": "http://gpu-box:8000/v1"})
    assert connection.block_present is False
    assert connection.auth_mode is AuthMode.ENV_KEY
    assert connection.api_key_env == "OPENAI_API_KEY" and connection.token_url is None
    assert connection.vision_rule is VisionRule.DETECT and connection.vision_model is None
    assert connection.renew_on_status == (401,)
    assert connection.configured_base_url is None
    assert connection.origin_changed is False and connection.cleartext_bearer is False
    bearer = bearer_for_connection(connection)
    assert isinstance(bearer, EnvironmentKeyBearer) and bearer.required is False
    assert connection.config_path == "cfg.yaml"


def test_block_without_auth_is_none_mode() -> None:
    """AC-2: block present, auth absent ⇒ NONE and the Null Object bearer."""
    connection = _resolve(LlmConnectionConfig(base_url=_YAML_URL))
    assert connection.auth_mode is AuthMode.NONE
    assert connection.token_url is None and connection.api_key_env is None
    assert isinstance(bearer_for_connection(connection), NoBearer)
    assert connection.configured_base_url == _YAML_URL


def test_auth_fields_follow_the_block() -> None:
    token = _resolve(_block(token_field="access_token", renew_on_status=[401, 403]))
    assert token.auth_mode is AuthMode.TOKEN_SERVICE
    assert token.token_url == _TOKEN_URL and token.token_field == "access_token"
    assert token.renew_on_status == (401, 403)
    key = _resolve(LlmConnectionConfig.model_validate({"auth": {"api_key_env": "LLM_KEY"}}))
    assert key.auth_mode is AuthMode.ENV_KEY and key.api_key_env == "LLM_KEY"
    assert key.base_url is None and key.configured_base_url is None
    bearer = bearer_for_connection(key)
    assert isinstance(bearer, EnvironmentKeyBearer) and bearer.required is True


def test_vision_rules(caplog) -> None:
    """AC-20 + the four VisionRule values."""
    caplog.set_level(logging.WARNING)
    assert _resolve(_block()).vision_rule is VisionRule.DETECT
    assert _resolve(_block(vision=True)).vision_rule is VisionRule.MULTIMODAL
    assert _resolve(_block(vision=False)).vision_rule is VisionRule.TEXT_ONLY
    separate = _resolve(_block(model="main-a", vision={"model": "vision-b"}))
    assert separate.vision_rule is VisionRule.SEPARATE_MODEL and separate.vision_model == "vision-b"
    same = _resolve(_block(model="main-a", vision={"model": "main-a"}))
    assert same.vision_rule is VisionRule.MULTIMODAL and same.vision_model is None
    warnings = [r.getMessage() for r in caplog.records if "vision_model_equals_main" in r.getMessage()]
    assert len(warnings) == 1 and "'main-a' equals the main model" in warnings[0]


def test_origin_change_is_flagged_and_logged_not_withheld(caplog) -> None:
    """AC-31 (resolution half): another origin at any tier ⇒ origin_changed, one warning, the
    same auth mode (the bearer still follows the endpoint); another path on the same origin
    ⇒ nothing."""
    caplog.set_level(logging.WARNING)
    moved = _resolve(_block(), {"OPENAI_BASE_URL": "https://gpu-box:8443/v1"})
    assert moved.origin_changed is True and moved.auth_mode is AuthMode.TOKEN_SERVICE
    records = [json.loads(r.getMessage()) for r in caplog.records if "bearer_origin_changed" in r.getMessage()]
    assert len(records) == 1
    assert records[0]["configured_origin"] == "https://llm.internal/v1"
    assert records[0]["resolved_origin"] == "https://gpu-box:8443/v1"
    caplog.clear()
    same_origin = _resolve(_block(), {}, ConnectionOverride(base_url="https://llm.internal/other"))
    assert same_origin.origin_changed is False
    assert not [r for r in caplog.records if "bearer_origin_changed" in r.getMessage()]
    vendor_default_key = _resolve(
        LlmConnectionConfig.model_validate({"auth": {"api_key_env": "LLM_KEY"}}),
        {"OPENAI_BASE_URL": "https://gpu-box:8443/v1"},
    )
    assert vendor_default_key.origin_changed is False  # no YAML base_url to compare against


def test_cleartext_bearer_is_flagged_and_logged_never_an_error(caplog) -> None:
    """AC-39 (resolution half): plain http to a non-loopback host with a bearer ⇒ a warning;
    loopback and the no-block path ⇒ nothing."""
    caplog.set_level(logging.WARNING)
    plain = _resolve(_block(base_url="http://llm.internal/v1"))
    assert plain.cleartext_bearer is True
    assert len([r for r in caplog.records if "bearer_over_cleartext" in r.getMessage()]) == 1
    caplog.clear()
    for loopback in ("http://localhost:8000/v1", "http://127.0.0.1:8000/v1", "http://[::1]:8000/v1"):
        assert _resolve(_block(base_url=loopback)).cleartext_bearer is False
    assert _resolve(LlmConnectionConfig(base_url="http://llm.internal/v1")).cleartext_bearer is False
    assert _resolve(None, {"OPENAI_BASE_URL": "http://gpu-box:8000/v1"}).cleartext_bearer is False
    assert not [r for r in caplog.records if "bearer_over_cleartext" in r.getMessage()]


def test_connection_identity_carries_no_secret() -> None:
    """AC-26 (identity half): (auth_mode, token_url or api_key_env or "", block_present)."""
    assert connection_identity(_resolve(_block())) == (AuthMode.TOKEN_SERVICE, _TOKEN_URL, True)
    assert connection_identity(_resolve(None)) == (AuthMode.ENV_KEY, "OPENAI_API_KEY", False)
    explicit = _resolve(LlmConnectionConfig.model_validate({"auth": {"api_key_env": "OPENAI_API_KEY"}}))
    assert connection_identity(explicit) == (AuthMode.ENV_KEY, "OPENAI_API_KEY", True)
    assert connection_identity(_resolve(LlmConnectionConfig(base_url=_YAML_URL))) == (AuthMode.NONE, "", True)


def test_bearer_registry_shares_one_source_per_identity() -> None:
    """AC-40 (registry half): the same identity ⇒ the same TokenServiceBearer object; a
    different token_url ⇒ another; clear_bearer_registry() resets."""
    first = bearer_for_connection(_resolve(_block()))
    assert isinstance(first, TokenServiceBearer)
    assert bearer_for_connection(_resolve(_block(), {"OPENAI_BASE_URL": "http://other/v1"})) is first
    other = bearer_for_connection(_resolve(_block(auth={"token_url": "http://localhost:9000/t"})))
    assert other is not first
    clear_bearer_registry()
    assert bearer_for_connection(_resolve(_block())) is not first


def test_network_constants_are_finite_and_bounded() -> None:
    """AC-28: every network-bound helper has an explicit timeout and a bounded attempt count."""
    assert 0 < bt._TOKEN_FETCH_TIMEOUT_SECONDS < 60
    assert 0 < bt._MIN_RENEW_INTERVAL_SECONDS < 60
    assert 1 <= bt._TOKEN_FETCH_ATTEMPTS <= 3
    assert len(bt._TOKEN_FETCH_BACKOFF_SECONDS) == 2
    assert 0 < lc._TEST_CONNECTION_TIMEOUT_SECONDS < 120
    from pydocs_mcp.harness.ask_your_docs import model_listing as ml

    assert 0 < ml._LISTING_TIMEOUT_SECONDS < 120 and ml._LISTING_MAX_RETRIES <= 3
    assert 0 < ml._MODEL_LISTING_TTL_SECONDS < 3600
```

(`test_network_constants_are_finite_and_bounded` goes green only after Tasks 5 and 6 add `_TEST_CONNECTION_TIMEOUT_SECONDS` and `model_listing.py`; mark it `@pytest.mark.xfail(strict=True, reason="Tasks 5-6")` until then and remove the marker in Task 6.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_llm_connection.py -q`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'pydocs_mcp.harness.ask_your_docs.llm_connection'`.

- [ ] **Step 3: Write `llm_connection.py` (the pure half)**

```python
"""The chat model's connection: one value object, resolved once per session.

LLM-connection design §4.2 (the value object), §4.3 (precedence), §4.4 (the
bearer registry), §4.5 (the client factory) and §4.7 (capability resolution).
The connection is resolved by a pure fold over four tiers — YAML <
environment (``OPENAI_BASE_URL`` / ``LLM_MODEL``) < CLI (``--base-url`` /
``--model``) < the Connection dialog — for ``base_url`` and ``model`` only;
``auth``, ``token_field``, ``renew_on_status`` and ``vision`` come from the
YAML block (and its ``PYDOCS_ASK_YOUR_DOCS__LLM__*`` env overlay) alone.

Light by contract: ``langchain_openai`` and ``openai`` are imported
function-locally inside the factory.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerSource,
    EnvironmentKeyBearer,
    NoBearer,
    TokenServiceBearer,
    display_host,
    display_url,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import (
    _DEFAULT_API_KEY_ENV,
    _DEFAULT_MODEL,
    _DEFAULT_RENEW_ON_STATUS,
    AuthMode,
    LlmConnectionConfig,
    VisionRule,
)

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_TIER_NAMES = ("yaml", "environment", "cli", "dialog")


@dataclass(frozen=True, slots=True)
class ConnectionOverride:
    """One shape for the CLI tier and the dialog tier; ``None`` = tier unset."""

    base_url: str | None = None
    model: str | None = None


@dataclass(frozen=True, slots=True)
class LlmConnection:
    """Endpoint, model, auth mode and vision rule for one session (design §4.2)."""

    base_url: str | None  # None = the SDK's vendor default, as today
    model: str | None  # None = not chosen yet (block present, no tier set it)
    auth_mode: AuthMode
    token_url: str | None  # TOKEN_SERVICE only
    api_key_env: str | None  # ENV_KEY only
    token_field: str | None  # None = the whole body, stripped, is the token
    renew_on_status: tuple[int, ...]
    vision_rule: VisionRule
    vision_model: str | None  # SEPARATE_MODEL only
    config_path: str | None  # the pydocs YAML the block came from
    block_present: bool  # False = no ask_your_docs.llm block (byte identity)
    configured_base_url: str | None  # the YAML base_url, kept for the origin check (H1)

    @property
    def origin_changed(self) -> bool:
        """H1: a YAML base_url exists and the effective one has another origin."""
        if self.configured_base_url is None or self.base_url is None:
            return False
        return _origin(self.configured_base_url) != _origin(self.base_url)

    @property
    def cleartext_bearer(self) -> bool:
        """H2: a bearer this design introduces travels over plain http to a non-loopback host."""
        if not self.block_present or self.auth_mode is AuthMode.NONE or self.base_url is None:
            return False
        parts = urlsplit(self.base_url)
        return parts.scheme == "http" and (parts.hostname or "") not in _LOOPBACK_HOSTS


def _origin(url: str) -> tuple[str, str, int | None]:
    # scheme / hostname / port — never netloc, which carries userinfo (H4).
    parts = urlsplit(url)
    return (parts.scheme, parts.hostname or "", parts.port)


def resolve_llm_connection(
    yaml_block: LlmConnectionConfig | None,
    environment: Mapping[str, str],
    launch: ConnectionOverride,
    dialog: ConnectionOverride,
    *,
    config_path: str | None,
) -> LlmConnection:
    """The pure precedence fold of design §4.3 — no I/O, no Streamlit."""
    block_present = yaml_block is not None
    base_url, base_tier = _fold_tiers(
        _yaml_field(yaml_block, "base_url"),
        environment.get("OPENAI_BASE_URL"),
        launch.base_url,
        dialog.base_url,
    )
    model, model_tier = _fold_tiers(
        _yaml_field(yaml_block, "model"), environment.get("LLM_MODEL"), launch.model, dialog.model
    )
    if model is None and not block_present:
        model, model_tier = _DEFAULT_MODEL, "default"  # today's page default (byte identity)
    auth_mode, token_url, api_key_env = _auth_fields(yaml_block)
    vision_rule, vision_model = _vision_fields(yaml_block, model)
    connection = LlmConnection(
        base_url=base_url,
        model=model,
        auth_mode=auth_mode,
        token_url=token_url,
        api_key_env=api_key_env,
        token_field=_yaml_field(yaml_block, "token_field"),
        renew_on_status=tuple(yaml_block.renew_on_status) if yaml_block else _DEFAULT_RENEW_ON_STATUS,
        vision_rule=vision_rule,
        vision_model=vision_model,
        config_path=config_path,
        block_present=block_present,
        configured_base_url=_yaml_field(yaml_block, "base_url"),
    )
    _log_resolution(connection, base_tier, model_tier)
    return connection


def _yaml_field(block: LlmConnectionConfig | None, field: str) -> str | None:
    return getattr(block, field) if block is not None else None


def _fold_tiers(*values: str | None) -> tuple[str | None, str]:
    """Lowest tier first; an empty string means "unset at that tier" (today's `or None`)."""
    chosen, tier = None, "none"
    for name, value in zip(_TIER_NAMES, values, strict=True):
        if value:
            chosen, tier = value, name
    return chosen, tier


def _auth_fields(block: LlmConnectionConfig | None) -> tuple[AuthMode, str | None, str | None]:
    if block is None:
        return AuthMode.ENV_KEY, None, _DEFAULT_API_KEY_ENV  # the lenient no-block bearer
    if block.auth is None:
        return AuthMode.NONE, None, None
    if block.auth.token_url:
        return AuthMode.TOKEN_SERVICE, block.auth.token_url, None
    return AuthMode.ENV_KEY, None, block.auth.api_key_env


def _vision_fields(block: LlmConnectionConfig | None, model: str | None) -> tuple[VisionRule, str | None]:
    vision = block.vision if block is not None else None
    if vision is None:
        return VisionRule.DETECT, None
    if vision is True:
        return VisionRule.MULTIMODAL, None
    if vision is False:
        return VisionRule.TEXT_ONLY, None
    if vision.model == model:  # design E9: the same model is not a separate model
        log.warning(
            json.dumps(
                {
                    "event": "vision_model_equals_main",
                    "model": model,
                    "message": (
                        f"ask_your_docs.llm.vision.model {model!r} equals the main model; "
                        "treating as vision: true"
                    ),
                }
            )
        )
        return VisionRule.MULTIMODAL, None
    return VisionRule.SEPARATE_MODEL, vision.model


def _log_resolution(connection: LlmConnection, base_tier: str, model_tier: str) -> None:
    log.info(
        json.dumps(
            {
                "event": "connection_resolved",
                "base_url_tier": base_tier,
                "model_tier": model_tier,
                "endpoint": display_host(connection.base_url),
                "auth_mode": connection.auth_mode.value,
                "vision_rule": connection.vision_rule.value,
            }
        )
    )
    if connection.origin_changed:  # H1: visible, never withheld
        log.warning(
            json.dumps(
                {
                    "event": "bearer_origin_changed",
                    "configured_origin": display_url(connection.configured_base_url or ""),
                    "resolved_origin": display_url(connection.base_url or ""),
                }
            )
        )
    if connection.cleartext_bearer:  # H2: visible, never an error
        log.warning(
            json.dumps(
                {"event": "bearer_over_cleartext", "endpoint": display_url(connection.base_url or "")}
            )
        )


def connection_identity(connection: LlmConnection) -> tuple[AuthMode, str, bool]:
    """Where the bearer comes from, without carrying it — the cache and registry key (§1.4)."""
    return (
        connection.auth_mode,
        connection.token_url or connection.api_key_env or "",
        connection.block_present,
    )


# One bearer per identity per process: the app and the eval binding get the
# same object for the same identity, so a 1300-record campaign fetches one
# token, not one per sample (design §4.4).
_bearer_registry: dict[tuple[AuthMode, str, bool], BearerSource] = {}
_registry_lock = threading.Lock()


def bearer_for_connection(connection: LlmConnection) -> BearerSource:
    """The registry's bearer for the connection's identity (``NoBearer`` for ``NONE``)."""
    if connection.auth_mode is AuthMode.NONE:
        return NoBearer()
    identity = connection_identity(connection)
    with _registry_lock:
        bearer = _bearer_registry.get(identity)
        if bearer is None:
            bearer = _bearer_registry[identity] = _new_bearer(connection)
    return bearer


def _new_bearer(connection: LlmConnection) -> BearerSource:
    if connection.auth_mode is AuthMode.TOKEN_SERVICE:
        return TokenServiceBearer(connection.token_url or "", token_field=connection.token_field)
    # Strict for an explicit auth.api_key_env; lenient (no header, no error) on the no-block path.
    return EnvironmentKeyBearer(
        connection.api_key_env or _DEFAULT_API_KEY_ENV, required=connection.block_present
    )


def clear_bearer_registry() -> None:
    """Test seam: forget every bearer (the sibling of ``clear_detection_cache``)."""
    with _registry_lock:
        _bearer_registry.clear()


__all__ = (
    "AuthMode",
    "ConnectionOverride",
    "LlmConnection",
    "VisionRule",
    "bearer_for_connection",
    "clear_bearer_registry",
    "connection_identity",
    "resolve_llm_connection",
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/harness/ask_your_docs/test_llm_connection.py -q`
Expected: PASS except the `xfail`-marked constants test (reported as `x`).

- [ ] **Step 5: Lint and commit**

Run: `ruff check python/pydocs_mcp/harness/ask_your_docs/llm_connection.py tests/harness/ask_your_docs/test_llm_connection.py && ruff format --check python/pydocs_mcp/harness/ask_your_docs/llm_connection.py tests/harness/ask_your_docs/test_llm_connection.py && vulture python/pydocs_mcp --min-confidence 80`
Expected: clean.

```bash
git checkout -- complexipy-snapshot.json
git add python/pydocs_mcp/harness/ask_your_docs/llm_connection.py tests/harness/ask_your_docs/test_llm_connection.py
git commit -m "harness(ask-your-docs): LlmConnection value object, precedence fold, identity and bearer registry"
```

---
## Task 4: The capability ladder takes the connection and its bearer

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/multimodal.py` (whole file replaced below; the two prefix tables are unchanged)
- Modify: `tests/harness/ask_your_docs/test_multimodal_detection.py`

**Interfaces:**
- Consumes: `LlmConnection`, `ConnectionOverride`, `resolve_llm_connection`, `bearer_for_connection` (Task 3, imported function-locally); `TokenServiceError`, `BearerUnavailableError`, `BearerRejectedError` (Task 2); `FakeModelsEndpoint`, `FakeProbeLlm`, `FakeBearer` (Task 2 fakes).
- Produces: `CapabilitySource(StrEnum)` {`OVERRIDE`, `STATIC`, `ENDPOINT`, `PROBE`, `DEFAULT`, `CONFIGURED`} (+ the alias `DetectionSource = CapabilitySource`); seams `ListModels = Callable[[LlmConnection, BearerSource], Awaitable[list[dict]]]` and `ProbeLlm = Callable[[LlmConnection, BearerSource, str, float], Awaitable[str]]`; `detect_capabilities(model, base_url, cfg, *, connection=None, bearer=None, list_models=None, probe_llm=None)`; `_BEARER_ERRORS`; the production seams `_default_list_models` / `_default_probe_llm` (bodies land in Task 6 — until then they raise `NotImplementedError` and only the injected fakes run). Closes AC-15 (seam half), AC-34 (ladder half).

- [ ] **Step 1: Update the tests**

Replace `tests/harness/ask_your_docs/test_multimodal_detection.py` with:

```python
"""Capability-detection ladder (spec 2026-07-11-multimodal-image-agent §3.9;
LLM-connection design §4.7 — the rungs take (connection, bearer), AC-15, AC-34).

Pure-async, Streamlit-free; HTTP and LLM rungs are injectable named fakes
from _connection_fakes. No heavy imports — runs in the core venv.
"""

from __future__ import annotations

import asyncio

import pytest

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NoBearer, TokenServiceError
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    clear_bearer_registry,
    resolve_llm_connection,
)
from pydocs_mcp.harness.ask_your_docs.multimodal import (
    CapabilitySource,
    ModelCapabilities,
    _detection_cache,
    clear_detection_cache,
    detect_capabilities,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import (
    LlmConnectionConfig,
    MultimodalDetectionConfig,
)

from ._connection_fakes import FakeBearer, FakeModelsEndpoint, FakeProbeLlm

_BASE_URL = "http://localhost:8000/v1"


@pytest.fixture(autouse=True)
def _fresh_caches():
    clear_detection_cache()
    clear_bearer_registry()
    yield
    clear_detection_cache()
    clear_bearer_registry()


def _detect(model: str, cfg: MultimodalDetectionConfig, **kw) -> ModelCapabilities:
    clear_detection_cache()
    return asyncio.run(detect_capabilities(model, _BASE_URL, cfg, **kw))


def _token_connection(model: str = "my-vlm"):
    block = LlmConnectionConfig.model_validate(
        {"base_url": _BASE_URL, "model": model, "auth": {"token_url": "http://localhost:8899/t"}}
    )
    return resolve_llm_connection(block, {}, ConnectionOverride(), ConnectionOverride(), config_path=None)


def test_override_short_circuits_ladder() -> None:
    """AC10: override wins with no table lookup and no HTTP."""
    endpoint = FakeModelsEndpoint()
    probe = FakeProbeLlm()
    cfg = MultimodalDetectionConfig(override=True, endpoint_probe=True, image_probe=True)
    caps = _detect("gpt-3.5-turbo", cfg, list_models=endpoint, probe_llm=probe)
    assert caps == ModelCapabilities(multimodal=True, source="override")
    assert endpoint.calls == 0 and probe.calls == 0
    cfg_off = MultimodalDetectionConfig(override=False)
    assert _detect("gpt-4o", cfg_off).multimodal is False


def test_static_table_longest_prefix_wins() -> None:
    """AC11: longest-prefix semantics across the positive AND negative tables
    (mirrors model_budget.py's context_window_tokens)."""
    cfg = MultimodalDetectionConfig()
    # phi-3-vision matches negative 'phi-3' AND positive 'phi-3-vision' — the
    # longer positive prefix must win.
    assert _detect("phi-3-vision-128k", cfg) == ModelCapabilities(True, "static")
    assert _detect("phi-3-mini", cfg) == ModelCapabilities(False, "static")
    assert _detect("gpt-4o-mini", cfg) == ModelCapabilities(True, "static")
    assert _detect("gpt-3.5-turbo", cfg) == ModelCapabilities(False, "static")
    # HF-style org prefix is stripped before matching.
    assert _detect("Qwen/qwen2.5-vl-7b-instruct", cfg) == ModelCapabilities(True, "static")


def test_unknown_model_conservative_default() -> None:
    """AC12: probes off + unknown name → (False, 'default')."""
    caps = _detect("my-custom-vlm-v2", MultimodalDetectionConfig())
    assert caps == ModelCapabilities(multimodal=False, source="default")


def test_endpoint_probe_positive_absent_and_error(monkeypatch) -> None:
    """AC13: a vision hint decides positive; absence and errors fall through
    (never decide text-only); errors are retried ≤3 times."""
    from pydocs_mcp.harness.ask_your_docs import multimodal as mm

    monkeypatch.setattr(mm, "_PROBE_BACKOFF_SECONDS", (0.0, 0.0))
    cfg = MultimodalDetectionConfig(static_table=False, endpoint_probe=True)
    hit = FakeModelsEndpoint(entry={"id": "my-vlm", "capabilities": {"vision": True}})
    assert _detect("my-vlm", cfg, list_models=hit) == ModelCapabilities(True, "endpoint")
    bare = FakeModelsEndpoint(entry={"id": "my-vlm"})
    assert _detect("my-vlm", cfg, list_models=bare) == ModelCapabilities(False, "default")
    down = FakeModelsEndpoint(error=ConnectionError("refused"))
    assert _detect("my-vlm", cfg, list_models=down) == ModelCapabilities(False, "default")
    assert down.calls == 3  # the full bounded-retry envelope ran


def test_image_probe_outcomes() -> None:
    """AC14: 200→(True,'probe'); image-content 4xx→(False,'probe');
    5xx/timeout→fall through to (False,'default')."""
    cfg = MultimodalDetectionConfig(static_table=False, image_probe=True)
    assert _detect("my-vlm", cfg, probe_llm=FakeProbeLlm("ok")) == ModelCapabilities(True, "probe")
    assert _detect("my-vlm", cfg, probe_llm=FakeProbeLlm("image_error")) == ModelCapabilities(
        False, "probe"
    )
    assert _detect("my-vlm", cfg, probe_llm=FakeProbeLlm("server_error")) == ModelCapabilities(
        False, "default"
    )


def test_detection_cached_per_model_base_url_pair() -> None:
    """AC15: repeated same-cfg calls for one (model, base_url) hit the cache —
    the probe fires exactly once. (The cfg fingerprint is part of the key —
    see test_different_cfg_reruns_the_ladder.)"""
    cfg = MultimodalDetectionConfig(static_table=False, image_probe=True)
    probe = FakeProbeLlm("ok")

    async def twice() -> tuple[ModelCapabilities, ModelCapabilities]:
        a = await detect_capabilities("my-vlm", "http://x/v1", cfg, probe_llm=probe)
        b = await detect_capabilities("my-vlm", "http://x/v1", cfg, probe_llm=probe)
        return a, b

    a, b = asyncio.run(twice())
    assert a == b == ModelCapabilities(True, "probe")
    assert probe.calls == 1


def test_different_cfg_reruns_the_ladder() -> None:
    """Regression for the cfg-fingerprinted cache key: flipping
    detection.override for an already-detected (model, base_url) pair must
    take effect without a process restart."""
    probe = FakeProbeLlm("ok")
    cfg_probe = MultimodalDetectionConfig(static_table=False, image_probe=True)

    async def flip() -> tuple[ModelCapabilities, ModelCapabilities]:
        first = await detect_capabilities("my-vlm", "http://x/v1", cfg_probe, probe_llm=probe)
        flipped = await detect_capabilities(
            "my-vlm", "http://x/v1", MultimodalDetectionConfig(override=False), probe_llm=probe
        )
        return first, flipped

    first, flipped = asyncio.run(flip())
    assert first == ModelCapabilities(True, "probe")
    assert flipped == ModelCapabilities(False, "override")  # not the stale probe verdict


# ── LLM-connection design §4.7: the rungs carry the connection's bearer ──


def test_capability_source_is_a_str_enum_with_configured() -> None:
    assert CapabilitySource.STATIC == "static" and CapabilitySource.CONFIGURED == "configured"
    assert ModelCapabilities(True, "static") == ModelCapabilities(True, CapabilitySource.STATIC)
    assert {s.value for s in CapabilitySource} == {
        "override", "static", "endpoint", "probe", "default", "configured"
    }


def test_rungs_receive_the_connections_bearer() -> None:
    """AC-15 (seam half): both rungs are handed the connection and its bearer (D5)."""
    connection = _token_connection()
    bearer = FakeBearer("tok-fixed-abcd")
    endpoint = FakeModelsEndpoint(entry={"id": "my-vlm"})
    probe = FakeProbeLlm("ok")
    cfg = MultimodalDetectionConfig(static_table=False, endpoint_probe=True, image_probe=True)
    caps = asyncio.run(
        detect_capabilities(
            "my-vlm", _BASE_URL, cfg, connection=connection, bearer=bearer,
            list_models=endpoint, probe_llm=probe,
        )
    )
    assert caps == ModelCapabilities(True, "probe")
    assert endpoint.seen_bearer == "tok-fixed-abcd" and endpoint.seen_base_url == _BASE_URL
    assert probe.seen_bearer == "tok-fixed-abcd"


def test_todays_callers_get_the_no_block_connection(monkeypatch) -> None:
    """Callers that pass only (model, base_url) — the ladder's pre-existing shape — get the
    lenient no-block connection and bearer built for them."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    endpoint = FakeModelsEndpoint(entry={"id": "my-vlm"})
    cfg = MultimodalDetectionConfig(static_table=False, endpoint_probe=True)
    _detect("my-vlm", cfg, list_models=endpoint)
    assert endpoint.seen_base_url == _BASE_URL
    assert endpoint.seen_bearer == ""  # OPENAI_API_KEY unset in the test env → no header


def test_bearer_failures_propagate_and_are_never_cached(monkeypatch) -> None:
    """AC-34 (ladder half, H3): a token service that is down raises out of the ladder at once —
    one call, not the 3-attempt envelope — and no verdict is cached."""
    from pydocs_mcp.harness.ask_your_docs import multimodal as mm

    monkeypatch.setattr(mm, "_PROBE_BACKOFF_SECONDS", (0.0, 0.0))
    connection = _token_connection()
    endpoint = FakeModelsEndpoint(entry={"id": "my-vlm"})
    cfg = MultimodalDetectionConfig(static_table=False, endpoint_probe=True)
    with pytest.raises(TokenServiceError):
        asyncio.run(
            detect_capabilities(
                "my-vlm", _BASE_URL, cfg, connection=connection, bearer=FakeBearer(fail=True),
                list_models=endpoint,
            )
        )
    assert endpoint.calls == 1
    assert _detection_cache == {}
    probe = FakeProbeLlm("ok")
    cfg_probe = MultimodalDetectionConfig(static_table=False, image_probe=True)
    with pytest.raises(TokenServiceError):
        asyncio.run(
            detect_capabilities(
                "my-vlm", _BASE_URL, cfg_probe, connection=connection,
                bearer=FakeBearer(fail=True), probe_llm=probe,
            )
        )
    assert _detection_cache == {}
    # After the service recovers the next call runs the ladder for real.
    caps = asyncio.run(
        detect_capabilities(
            "my-vlm", _BASE_URL, cfg_probe, connection=connection, bearer=NoBearer(),
            probe_llm=probe,
        )
    )
    assert caps == ModelCapabilities(True, "probe")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_multimodal_detection.py -q`
Expected: FAIL — `ImportError: cannot import name 'CapabilitySource'`, then `TypeError: detect_capabilities() got an unexpected keyword argument 'list_models'`.

- [ ] **Step 3: Replace `multimodal.py`**

Keep the two prefix tuples `_MULTIMODAL_MODEL_PREFIXES` and `_TEXT_ONLY_MODEL_PREFIXES` exactly as they are at `multimodal.py:35-91` (with their WHY comments); everything else becomes:

```python
"""Multimodal capability detection for the ask-your-docs agent (spec §3.9).

The ladder: explicit override → static prefix table → optional endpoint
metadata probe → optional one-shot tiny-image probe → conservative text-only
default. Pure-async and Streamlit-free; the two network rungs take injectable
callables so tests use named fakes and production wires thin defaults lazily
(no heavy import at module level — the lazy-import contract holds). Both
production rungs go through the LLM connection's client factory, so they
carry the same bearer as the agent (LLM-connection design §4.6–§4.7, D5).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerRejectedError,
    BearerUnavailableError,
    TokenServiceError,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import MultimodalDetectionConfig

if TYPE_CHECKING:
    from pydocs_mcp.harness.ask_your_docs.bearer_tokens import BearerSource
    from pydocs_mcp.harness.ask_your_docs.llm_connection import LlmConnection

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")


class CapabilitySource(StrEnum):
    """Which ladder rung (or the YAML ``vision`` key) decided — surfaced in the UI badge."""

    OVERRIDE = "override"
    STATIC = "static"
    ENDPOINT = "endpoint"
    PROBE = "probe"
    DEFAULT = "default"
    CONFIGURED = "configured"  # ask_your_docs.llm.vision: true | false | {model}


# The pre-StrEnum name; kept so existing import sites resolve (the values are unchanged).
DetectionSource = CapabilitySource

# Injectable rung seams. list_models returns the /v1/models entries for the
# connection (raising on transport errors); probe_llm runs the tiny-image
# completion on the connection's endpoint and returns the reply text.
ListModels = Callable[["LlmConnection", "BearerSource"], Awaitable[list[dict]]]
ProbeLlm = Callable[["LlmConnection", "BearerSource", str, float], Awaitable[str]]

# Bearer failures are never retried by the ladder and never cached as a
# verdict (design H3): they are already bounded internally, and a token
# service that is down at build time must fail loudly, not land the
# deployment on text-only for the process lifetime.
_BEARER_ERRORS = (TokenServiceError, BearerUnavailableError, BearerRejectedError)

# … _MULTIMODAL_MODEL_PREFIXES and _TEXT_ONLY_MODEL_PREFIXES exactly as today (lines 35-91) …

# One-shot probe payload: a 1x1 transparent PNG (67 bytes decoded).
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

_PROBE_TIMEOUT_SECONDS = 5.0
_PROBE_ATTEMPTS = 3
_PROBE_BACKOFF_SECONDS = (2.0, 4.0)  # mirrors llm_clients/openai._with_retry_async

# Process-level cache per (model, base_url) — detection of a fixed pair does
# not change between questions (spec §3.7; persisted cache deferred, §7 Q2).
_detection_cache: dict[tuple, ModelCapabilities] = {}


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    multimodal: bool
    source: CapabilitySource  # which rung decided — surfaced in the UI badge (a str compares equal)


def clear_detection_cache() -> None:
    """Test seam: reset the per-process detection cache."""
    _detection_cache.clear()


def _static_lookup(model: str) -> bool | None:
    """Longest-prefix match across both tables; None = unknown (fall through)."""
    name = model.lower().rsplit("/", 1)[-1]  # strip HF-style org prefix
    best_len, best_verdict = 0, None
    for table, verdict in (
        (_MULTIMODAL_MODEL_PREFIXES, True),
        (_TEXT_ONLY_MODEL_PREFIXES, False),
    ):
        for prefix in table:
            if name.startswith(prefix) and len(prefix) > best_len:
                best_len, best_verdict = len(prefix), verdict
    return best_verdict


async def _with_rung_retry(fn: Callable[[], Awaitable[object]]) -> object:
    """Bounded retry for the endpoint rung (3 attempts, 2s/4s backoff).

    Bearer failures re-raise at once: they are bounded inside the bearer
    already, and retrying them here would cost 3 × 3 token fetches (H3). The
    image probe (rung 4) deliberately does NOT use this: it is one-shot by
    design — an image-rejection 400 is deterministic, and a transient
    failure falls through to the conservative default anyway.
    """
    for attempt in range(_PROBE_ATTEMPTS):
        try:
            return await fn()
        except _BEARER_ERRORS:
            raise
        except Exception:
            if attempt == _PROBE_ATTEMPTS - 1:
                raise
            # Module-level constant so tests can zero the backoff.
            await asyncio.sleep(_PROBE_BACKOFF_SECONDS[min(attempt, 1)])
    raise AssertionError("unreachable")


def _entry_hints_vision(entry: dict) -> bool:
    """Positive-only heuristic over commonly-seen /v1/models metadata fields.

    WHY (2026-07-12): there is no modality-field standard across
    OpenAI-compatible servers — absence of a hint proves nothing, so this
    rung only ever decides POSITIVE; unknown shapes fall through (§7 Q1).
    """
    for field in ("capabilities", "modality", "modalities", "architecture", "tags"):
        value = entry.get(field)
        if value is None:
            continue
        text = str(value).lower()
        if "vision" in text or "image" in text or "multimodal" in text:
            return True
    return False


async def _default_list_models(connection: LlmConnection, bearer: BearerSource) -> list[dict]:
    """Production rung-3 seam: GET {base_url}/models with the connection's bearer."""
    # WHY function-local: model_listing imports llm_connection, which imports this module.
    from pydocs_mcp.harness.ask_your_docs.model_listing import fetch_models_payload

    return await fetch_models_payload(connection, bearer)


async def _default_probe_llm(
    connection: LlmConnection, bearer: BearerSource, model: str, timeout: float
) -> str:
    """Production rung-4 seam: one tiny-image chat completion through the client factory."""
    from langchain_core.messages import HumanMessage  # heavy; lazy by contract

    from pydocs_mcp.harness.ask_your_docs.bearer_tokens import translate_auth_errors
    from pydocs_mcp.harness.ask_your_docs.llm_connection import build_chat_model

    llm = build_chat_model(connection, bearer, model=model, timeout_seconds=timeout, max_retries=0)
    with translate_auth_errors(bearer):
        reply = await llm.ainvoke(
            [
                HumanMessage(
                    content=[
                        {"type": "text", "text": "Reply with the single word OK."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{_TINY_PNG_B64}"},
                        },
                    ]
                )
            ]
        )
    return str(reply.content)


def _looks_like_image_rejection(exc: Exception) -> bool:
    text = str(exc).lower()
    if not any(marker in text for marker in ("image", "vision", "multimodal", "content type")):
        return False
    return "400" in text or "invalid" in text or "not supported" in text or "unsupported" in text


async def detect_capabilities(
    model: str,
    base_url: str | None,
    cfg: MultimodalDetectionConfig,
    *,
    connection: LlmConnection | None = None,
    bearer: BearerSource | None = None,
    list_models: ListModels | None = None,
    probe_llm: ProbeLlm | None = None,
) -> ModelCapabilities:
    """Run the detection ladder (spec §3.9), cached per (model, base_url, cfg).

    The cfg fingerprint is part of the key so the advertised escape hatch
    (flipping ``detection.override`` in YAML) takes effect without a process
    restart — a (model, base_url)-only key would pin the stale verdict.
    ``connection`` / ``bearer`` default to the no-block connection for
    ``(model, base_url)``, so pre-existing callers keep their shape. A bearer
    failure propagates before the cache is written (H3).
    """
    key = (model, base_url, cfg.override, cfg.static_table, cfg.endpoint_probe, cfg.image_probe)
    if key in _detection_cache:
        return _detection_cache[key]
    connection, bearer = _connection_and_bearer(model, base_url, connection, bearer)
    caps = await _run_ladder(
        model, cfg, connection, bearer, list_models=list_models, probe_llm=probe_llm
    )
    _detection_cache[key] = caps
    log.info("multimodal detection: model=%s -> %s (%s)", model, caps.multimodal, caps.source)
    return caps


def _connection_and_bearer(
    model: str,
    base_url: str | None,
    connection: LlmConnection | None,
    bearer: BearerSource | None,
) -> tuple[LlmConnection, BearerSource]:
    """Today's callers pass (model, base_url) only: build the no-block connection for them."""
    # WHY function-local: llm_connection imports this module (resolve_vision_capabilities).
    from pydocs_mcp.harness.ask_your_docs.llm_connection import (
        ConnectionOverride,
        bearer_for_connection,
        resolve_llm_connection,
    )

    if connection is None:
        connection = resolve_llm_connection(
            None, {}, ConnectionOverride(base_url, model), ConnectionOverride(), config_path=None
        )
    if bearer is None:
        bearer = bearer_for_connection(connection)
    return connection, bearer


async def _endpoint_rung(
    model: str,
    connection: LlmConnection,
    bearer: BearerSource,
    list_models: ListModels | None,
) -> ModelCapabilities | None:
    """Rung 3 — positive-only signal; network trouble/absence falls through."""
    lister = list_models or _default_list_models
    try:
        payload = await _with_rung_retry(lambda: lister(connection, bearer))
    except _BEARER_ERRORS:
        raise
    except Exception:
        return None  # network trouble → fall through, never decide
    entries = payload if isinstance(payload, list) else []
    entry = next((e for e in entries if isinstance(e, dict) and e.get("id") == model), None)
    if entry is not None and _entry_hints_vision(entry):
        return ModelCapabilities(multimodal=True, source=CapabilitySource.ENDPOINT)
    return None


async def _image_probe_rung(
    model: str,
    connection: LlmConnection,
    bearer: BearerSource,
    probe_llm: ProbeLlm | None,
) -> ModelCapabilities | None:
    """Rung 4 — ground truth, opt-in (costs one real call). Only an
    image-rejection error decides text-only; 5xx/timeout falls through."""
    prober = probe_llm or _default_probe_llm
    try:
        await prober(connection, bearer, model, _PROBE_TIMEOUT_SECONDS)
        return ModelCapabilities(multimodal=True, source=CapabilitySource.PROBE)
    except _BEARER_ERRORS:
        raise
    except Exception as exc:
        if _looks_like_image_rejection(exc):
            return ModelCapabilities(multimodal=False, source=CapabilitySource.PROBE)
        return None


async def _run_ladder(
    model: str,
    cfg: MultimodalDetectionConfig,
    connection: LlmConnection,
    bearer: BearerSource,
    *,
    list_models: ListModels | None,
    probe_llm: ProbeLlm | None,
) -> ModelCapabilities:
    if cfg.override is not None:  # rung 1
        return ModelCapabilities(multimodal=cfg.override, source=CapabilitySource.OVERRIDE)
    if cfg.static_table:  # rung 2
        verdict = _static_lookup(model)
        if verdict is not None:
            return ModelCapabilities(multimodal=verdict, source=CapabilitySource.STATIC)
    if cfg.endpoint_probe and connection.base_url:
        caps = await _endpoint_rung(model, connection, bearer, list_models)
        if caps is not None:
            return caps
    if cfg.image_probe:
        caps = await _image_probe_rung(model, connection, bearer, probe_llm)
        if caps is not None:
            return caps
    return ModelCapabilities(multimodal=False, source=CapabilitySource.DEFAULT)  # rung 5


__all__ = (
    "CapabilitySource",
    "DetectionSource",
    "ModelCapabilities",
    "clear_detection_cache",
    "detect_capabilities",
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/harness/ask_your_docs/test_multimodal_detection.py tests/harness/ask_your_docs/test_architectures.py tests/harness/ask_your_docs/test_image_attachment.py -q`
Expected: PASS (the architecture and attachment suites still construct `ModelCapabilities(..., source="override")` with plain strings).

- [ ] **Step 5: Lint and commit**

Run: `ruff check python/pydocs_mcp/harness/ask_your_docs/multimodal.py tests/harness/ask_your_docs/test_multimodal_detection.py && ruff format --check python/pydocs_mcp/harness/ask_your_docs/multimodal.py tests/harness/ask_your_docs/test_multimodal_detection.py && complexipy python/pydocs_mcp/harness/ask_your_docs/multimodal.py --max-complexity-allowed 15`
Expected: clean.

```bash
git checkout -- complexipy-snapshot.json
git add python/pydocs_mcp/harness/ask_your_docs/multimodal.py tests/harness/ask_your_docs/test_multimodal_detection.py
git commit -m "harness(ask-your-docs): capability ladder takes the connection and its bearer; CapabilitySource StrEnum"
```

---
## Task 5: The client factory, the auth-error boundary on the SDK path, and the SDK pins

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/llm_connection.py` (append the factory half)
- Create: `tests/harness/ask_your_docs/test_chat_model_factory.py`
- Create: `tests/harness/ask_your_docs/test_sdk_pins.py`

**Interfaces:**
- Consumes: `RenewOnStatusAuth`, `StripAuthorizationAuth`, `translate_auth_errors`, `redact_bearer` (Task 2); `LlmConnection`, `bearer_for_connection` (Task 3); `RecordingTransport`, `FakeTokenService`, `RotatingBearer` (Task 2 fakes).
- Produces: `connection_auth_kwargs(connection, bearer, *, tolerate_missing_key=False) -> tuple[Any, httpx.Auth | None]`; `sync_httpx_client(auth, transport)`, `async_httpx_client(auth, transport)`, `httpx_clients(auth, transport) -> dict`; `build_chat_model(connection, bearer, *, model=None, timeout_seconds=None, max_retries=None, tolerate_missing_key=False, transport=None) -> ChatOpenAI`; `run_connection_test(connection, bearer, *, transport=None) -> str` (the spec's `test_connection`, renamed so pytest never collects it); constants `_NO_AUTH_PLACEHOLDER = "no-auth"`, `_TEST_CONNECTION_TIMEOUT_SECONDS = 15.0`, `_TEST_CONNECTION_PROMPT`, `_TEST_REPLY_MAX_CHARS = 40`. Closes AC-3, AC-5, AC-6, AC-7, AC-8, AC-9 (SDK half), AC-19, AC-31 (wire half), AC-33 (transport half), AC-35 (translation half), AC-38, AC-40 (registry half), AC-43 (the `run_connection_test` half).

- [ ] **Step 1: Write the failing factory tests**

Create `tests/harness/ask_your_docs/test_chat_model_factory.py`:

```python
"""build_chat_model + translate_auth_errors on the locked SDK (LLM-connection
design §4.4–§4.5 — AC-3, AC-5–AC-9, AC-19, AC-31, AC-33, AC-35, AC-40, AC-43)."""

from __future__ import annotations

import asyncio
import logging

import pytest

pytest.importorskip("langchain_openai")

import httpx
import langchain_openai
import openai._base_client as sdk_base

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerRejectedError,
    EnvironmentKeyBearer,
    NoBearer,
    TokenServiceBearer,
    translate_auth_errors,
)
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    bearer_for_connection,
    build_chat_model,
    clear_bearer_registry,
    resolve_llm_connection,
    run_connection_test,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

from ._connection_fakes import FakeTokenService, RecordingTransport, RotatingBearer

_URL = "http://llm.test/v1"
_TOKEN_URL = "http://localhost:8899/access-token"


@pytest.fixture(autouse=True)
def _fast_sdk_and_fresh_registry(monkeypatch):
    # The SDK sleeps 0.5 s+ between its own retries; the constants are module globals.
    monkeypatch.setattr(sdk_base, "INITIAL_RETRY_DELAY", 0.0)
    monkeypatch.setattr(sdk_base, "MAX_RETRY_DELAY", 0.0)
    clear_bearer_registry()
    yield
    clear_bearer_registry()


def _connection(block: dict | None, env: dict | None = None):
    cfg = LlmConnectionConfig.model_validate(block) if block is not None else None
    return resolve_llm_connection(cfg, env or {}, ConnectionOverride(), ConnectionOverride(), config_path=None)


def _token_service_setup(tokens: list[str]):
    service = FakeTokenService(tokens)
    connection = _connection({"base_url": _URL, "model": "m", "auth": {"token_url": _TOKEN_URL}})
    bearer = TokenServiceBearer(_TOKEN_URL, transport=service.transport, sleep=lambda _s: None)
    return service, connection, bearer


def _ask(llm, bearer, text: str = "hi") -> str:
    async def go() -> str:
        with translate_auth_errors(bearer):
            reply = await llm.ainvoke(text)
        return str(reply.content)

    return asyncio.run(go())


def test_no_block_is_exactly_todays_call(monkeypatch) -> None:
    """AC-19 / R2 byte identity: no block ⇒ ChatOpenAI(model=, base_url=) and nothing else;
    the probe's timeout / max_retries are added only when the caller passes them."""
    seen: list[dict] = []

    class _SpyChatOpenAI:
        def __init__(self, **kwargs) -> None:
            seen.append(kwargs)

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", _SpyChatOpenAI)
    connection = _connection(None, {"OPENAI_BASE_URL": _URL, "LLM_MODEL": "m"})
    build_chat_model(connection, bearer_for_connection(connection))
    assert seen == [{"model": "m", "base_url": _URL}]
    build_chat_model(connection, NoBearer(), model="probe-m", timeout_seconds=5.0, max_retries=0)
    assert seen[1] == {"model": "probe-m", "base_url": _URL, "timeout": 5.0, "max_retries": 0}


def test_none_mode_strips_the_header_on_the_wire() -> None:
    """AC-3: block without auth ⇒ the placeholder key never reaches the wire."""
    recorder = RecordingTransport([200])
    connection = _connection({"base_url": _URL, "model": "m"})
    llm = build_chat_model(connection, NoBearer(), transport=recorder.transport)
    assert _ask(llm, NoBearer()) == "OK"
    assert recorder.authorizations() == [None]


def test_env_key_callable_is_reevaluated_per_attempt(monkeypatch) -> None:
    """AC-5: with max_retries=2 and 500, 500, 200 the three attempts carry k1, k2, k3."""
    recorder = RecordingTransport([500, 500, 200])
    connection = _connection({"base_url": _URL, "model": "m", "auth": {"api_key_env": "LLM_KEY"}})
    bearer = RotatingBearer()
    llm = build_chat_model(connection, bearer, max_retries=2, transport=recorder.transport)
    assert _ask(llm, bearer) == "OK"
    assert recorder.authorizations() == ["Bearer k1", "Bearer k2", "Bearer k3"]
    assert recorder.retry_counts() == ["0", "1", "2"]


def test_env_key_strict_form_reads_the_variable(monkeypatch) -> None:
    monkeypatch.setenv("LLM_KEY", "key-one-1111")
    recorder = RecordingTransport([200])
    connection = _connection({"base_url": _URL, "model": "m", "auth": {"api_key_env": "LLM_KEY"}})
    bearer = bearer_for_connection(connection)
    assert isinstance(bearer, EnvironmentKeyBearer) and bearer.required
    llm = build_chat_model(connection, bearer, transport=recorder.transport)
    assert _ask(llm, bearer) == "OK"
    assert recorder.authorizations() == ["Bearer key-one-1111"]


def test_token_service_renews_on_401_and_retries_once() -> None:
    """AC-6: 401 then 200 ⇒ the SAME request once more with the renewed bearer; the SDK saw
    one attempt (retry count 0 on both), the token service saw initial + renew."""
    service, connection, bearer = _token_service_setup(["tok-one-abcd", "tok-two-efgh"])
    recorder = RecordingTransport([401, 200])
    llm = build_chat_model(connection, bearer, transport=recorder.transport)
    assert _ask(llm, bearer) == "OK"
    assert recorder.authorizations() == ["Bearer tok-one-abcd", "Bearer tok-two-efgh"]
    assert recorder.retry_counts() == ["0", "0"]
    assert service.calls == 2


def test_second_401_surfaces_bearer_rejected_without_the_body() -> None:
    """AC-7 + AC-35: 401, 401 ⇒ exactly two requests and a BearerRejectedError that carries the
    last four characters and the host, never the token nor the echoing body."""
    service, connection, bearer = _token_service_setup(["tok-one-abcd", "tok-two-efgh"])
    recorder = RecordingTransport([401, 401], echo_bearer_in_401=True)
    llm = build_chat_model(connection, bearer, transport=recorder.transport)
    with pytest.raises(BearerRejectedError) as excinfo:
        _ask(llm, bearer)
    message = str(excinfo.value)
    assert len(recorder.requests) == 2
    assert "…efgh" in message and "llm.test" in message and "(status 401)" in message
    assert "tok-two-efgh" not in message and "tok-one-abcd" not in message
    assert "rejected Bearer" not in message and excinfo.value.__cause__ is None


def test_sdk_retries_reuse_the_renewed_token() -> None:
    """AC-8: after a renewal, a 429-then-200 pair sends the renewed bearer twice with no
    further token-service call."""
    service, connection, bearer = _token_service_setup(["tok-one-abcd", "tok-two-efgh"])
    first = RecordingTransport([401, 200])
    assert _ask(build_chat_model(connection, bearer, transport=first.transport), bearer) == "OK"
    second = RecordingTransport([429, 200])
    llm = build_chat_model(connection, bearer, max_retries=1, transport=second.transport)
    assert _ask(llm, bearer) == "OK"
    assert second.authorizations() == ["Bearer tok-two-efgh", "Bearer tok-two-efgh"]
    assert service.calls == 2


def test_persistent_401s_across_two_invokes_fetch_one_renewal() -> None:
    """AC-33 (transport half): 401 ×4 over two invokes ⇒ initial fetch + ONE renewal; the
    second renewal is inside _MIN_RENEW_INTERVAL_SECONDS and returns the cache."""
    service, connection, bearer = _token_service_setup(["t1", "t2", "t3"])
    for _ in range(2):
        recorder = RecordingTransport([401, 401])
        with pytest.raises(BearerRejectedError):
            _ask(build_chat_model(connection, bearer, transport=recorder.transport), bearer)
        assert len(recorder.requests) == 2
    assert service.calls == 2


def test_origin_change_still_sends_the_bearer() -> None:
    """AC-31 (wire half, D3/H1): an override on another origin receives the bearer."""
    service = FakeTokenService(["tok-one-abcd"])
    connection = _connection(
        {"base_url": "https://llm.internal/v1", "model": "m", "auth": {"token_url": _TOKEN_URL}},
        {"OPENAI_BASE_URL": _URL},
    )
    assert connection.origin_changed is True
    bearer = TokenServiceBearer(_TOKEN_URL, transport=service.transport, sleep=lambda _s: None)
    recorder = RecordingTransport([200])
    assert _ask(build_chat_model(connection, bearer, transport=recorder.transport), bearer) == "OK"
    assert recorder.authorizations() == ["Bearer tok-one-abcd"]
    assert str(recorder.requests[0].url).startswith(_URL)


def test_translate_auth_errors_covers_every_mode(monkeypatch) -> None:
    """AC-35: env-key and no-auth connections have no renewing flow, so a 401 is a raw SDK
    error — the boundary still yields a redacted BearerRejectedError."""
    monkeypatch.setenv("LLM_KEY", "key-one-1111")
    cases = [
        ({"base_url": _URL, "model": "m", "auth": {"api_key_env": "LLM_KEY"}}, "…1111"),
        ({"base_url": _URL, "model": "m"}, "…"),
    ]
    for block, mask in cases:
        recorder = RecordingTransport([401], echo_bearer_in_401=True)
        connection = _connection(block)
        bearer = bearer_for_connection(connection)
        with pytest.raises(BearerRejectedError) as excinfo:
            _ask(build_chat_model(connection, bearer, transport=recorder.transport), bearer)
        assert mask in str(excinfo.value) and "key-one-1111" not in str(excinfo.value)
        assert len(recorder.requests) == 1


def test_no_token_in_sdk_or_httpx_logs(caplog) -> None:
    """AC-9 (SDK half): the openai / httpx loggers at DEBUG never carry the token."""
    caplog.set_level(logging.DEBUG, logger="openai")
    caplog.set_level(logging.DEBUG, logger="httpx")
    caplog.set_level(logging.DEBUG, logger="pydocs-mcp.harness.ask-your-docs")
    service, connection, bearer = _token_service_setup(["tok-one-abcd", "tok-two-efgh"])
    recorder = RecordingTransport([401, 200])
    _ask(build_chat_model(connection, bearer, transport=recorder.transport), bearer)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "tok-one-abcd" not in text and "tok-two-efgh" not in text


def test_registry_shares_one_bearer_across_builds() -> None:
    """AC-40 (registry half): two builds for one identity share one bearer and one fetch."""
    service = FakeTokenService(["tok-one-abcd"])
    connection = _connection({"base_url": _URL, "model": "m", "auth": {"token_url": _TOKEN_URL}})
    shared = bearer_for_connection(connection)
    assert isinstance(shared, TokenServiceBearer)
    shared._transport = service.transport  # the registry built it; point it at the fake service
    shared._sleep = lambda _s: None
    for _ in range(2):
        recorder = RecordingTransport([200])
        llm = build_chat_model(connection, bearer_for_connection(connection), transport=recorder.transport)
        assert _ask(llm, shared) == "OK"
        assert recorder.authorizations() == ["Bearer tok-one-abcd"]
    assert service.calls == 1


def test_run_connection_test_passes_and_fails_redacted() -> None:
    """AC-43 (helper half, E11): a caption string on success and on failure; never a raise."""
    service, connection, bearer = _token_service_setup(["tok-one-abcd", "tok-two-efgh"])
    good = RecordingTransport([200], reply="OK")
    assert asyncio.run(run_connection_test(connection, bearer, transport=good.transport)) == "test passed: OK"
    bad = RecordingTransport([401, 401], echo_bearer_in_401=True)
    result = asyncio.run(run_connection_test(connection, bearer, transport=bad.transport))
    assert result.startswith("test failed: BearerRejectedError:")
    assert "tok-" not in result and "rejected Bearer" not in result
    down = RecordingTransport([httpx.ConnectError("refused")])
    result = asyncio.run(run_connection_test(connection, bearer, transport=down.transport))
    assert result.startswith("test failed: APIConnectionError:")
```

- [ ] **Step 2: Write the failing SDK-pin tests**

Create `tests/harness/ask_your_docs/test_sdk_pins.py`:

```python
"""AC-38: the three SDK behaviors the renew-on-401 design leans on, pinned on the installed
(locked) openai so a future bump goes red instead of silently disabling renewal.

(a) a callable api_key is re-evaluated before every attempt (sync AND async clients — the
async client awaits its provider); (b) a placeholder key + StripAuthorizationAuth leaves no
header on the wire; (c) an httpx client-level auth is honored by the SDK's send.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

openai = pytest.importorskip("openai")
import openai._base_client as sdk_base

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import StripAuthorizationAuth

from ._connection_fakes import RecordingTransport, RotatingBearer

_URL = "http://llm.test/v1"


@pytest.fixture(autouse=True)
def _fast_sdk_retries(monkeypatch):
    monkeypatch.setattr(sdk_base, "INITIAL_RETRY_DELAY", 0.0)
    monkeypatch.setattr(sdk_base, "MAX_RETRY_DELAY", 0.0)


def test_sync_client_reevaluates_a_callable_api_key_per_attempt() -> None:
    recorder = RecordingTransport([500, 200])
    bearer = RotatingBearer()
    client = openai.OpenAI(
        api_key=bearer.current,
        base_url=_URL,
        max_retries=1,
        http_client=openai.DefaultHttpxClient(transport=recorder.transport),
    )
    ids = [m.id for m in client.models.list().data]
    assert ids == ["model-a", "model-b"]
    assert recorder.authorizations() == ["Bearer k1", "Bearer k2"]


def test_async_client_awaits_its_api_key_provider() -> None:
    recorder = RecordingTransport([500, 200])
    bearer = RotatingBearer()

    async def provider() -> str:
        return bearer.current()

    async def go() -> list[str]:
        client = openai.AsyncOpenAI(
            api_key=provider,
            base_url=_URL,
            max_retries=1,
            http_client=openai.DefaultAsyncHttpxClient(transport=recorder.transport),
        )
        return [m.id for m in (await client.models.list()).data]

    assert asyncio.run(go()) == ["model-a", "model-b"]
    assert recorder.authorizations() == ["Bearer k1", "Bearer k2"]


def test_placeholder_key_with_strip_auth_sends_no_header() -> None:
    recorder = RecordingTransport([200])
    client = openai.OpenAI(
        api_key="no-auth",
        base_url=_URL,
        http_client=openai.DefaultHttpxClient(auth=StripAuthorizationAuth(), transport=recorder.transport),
    )
    client.models.list()
    assert recorder.authorizations() == [None]


def test_client_level_httpx_auth_is_honored_by_send() -> None:
    class _CountingAuth(httpx.Auth):
        def __init__(self) -> None:
            self.flows = 0

        def auth_flow(self, request: httpx.Request):
            self.flows += 1
            yield request

    recorder = RecordingTransport([200])
    counting = _CountingAuth()
    client = openai.OpenAI(
        api_key="k",
        base_url=_URL,
        http_client=openai.DefaultHttpxClient(auth=counting, transport=recorder.transport),
    )
    client.models.list()
    assert counting.flows == 1
    assert client.custom_auth is None  # the SDK passes no auth of its own; the client-level one applies
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_chat_model_factory.py tests/harness/ask_your_docs/test_sdk_pins.py -q`
Expected: the factory module fails at import (`ImportError: cannot import name 'build_chat_model'`); the SDK pins PASS already (they pin the toolkit, not our code) — keep them.

- [ ] **Step 4: Append the factory half to `llm_connection.py`**

Add these imports at the top (keep the existing ones): `from typing import Any`, and from `bearer_tokens` also `RenewOnStatusAuth`, `StripAuthorizationAuth`, `redact_bearer`, `translate_auth_errors`. Then append after `clear_bearer_registry`:

```python
# WHY a placeholder: an empty api_key is SDK-version-fragile (a newer release
# rejects it at construction); the header is stripped on the wire instead.
_NO_AUTH_PLACEHOLDER = "no-auth"
_TEST_CONNECTION_TIMEOUT_SECONDS = 15.0
_TEST_CONNECTION_PROMPT = "Reply with the single word OK."
_TEST_REPLY_MAX_CHARS = 40


def connection_auth_kwargs(
    connection: LlmConnection, bearer: BearerSource, *, tolerate_missing_key: bool = False
) -> tuple[Any, Any]:
    """The ONE auth decision (design §4.5): ``(api_key, httpx auth)``.

    ``api_key`` ``None`` = rule 1 (no block: the SDK reads OPENAI_API_KEY
    itself); a sync callable = rules 2 and 4 (re-read before every attempt);
    the placeholder = rule 3 (the header is stripped on the wire).
    ``tolerate_missing_key`` is the rule-1 carve-out for the listing and rung
    3, which must work with the variable unset, as today's bare GET does.
    """
    if not connection.block_present:
        if not tolerate_missing_key:
            return None, None
        if bearer.current():
            return bearer.current, None
        return _NO_AUTH_PLACEHOLDER, StripAuthorizationAuth()
    if connection.auth_mode is AuthMode.ENV_KEY:
        return bearer.current, None
    if connection.auth_mode is AuthMode.NONE:
        return _NO_AUTH_PLACEHOLDER, StripAuthorizationAuth()
    return bearer.current, RenewOnStatusAuth(bearer, connection.renew_on_status)


def sync_httpx_client(auth: Any, transport: Any) -> Any:
    """The SDK's own sync client (its timeout and limits), carrying ``auth`` and a test transport."""
    from openai import DefaultHttpxClient  # heavy; lazy by contract

    extra = {"transport": transport} if transport is not None else {}
    return DefaultHttpxClient(auth=auth, **extra)


def async_httpx_client(auth: Any, transport: Any) -> Any:
    from openai import DefaultAsyncHttpxClient  # heavy; lazy by contract

    extra = {"transport": transport} if transport is not None else {}
    return DefaultAsyncHttpxClient(auth=auth, **extra)


def httpx_clients(auth: Any, transport: Any) -> dict[str, Any]:
    """Both clients: ``ainvoke`` uses the async pair, ``invoke`` the sync pair (design §4.5 rule 4)."""
    return {
        "http_client": sync_httpx_client(auth, transport),
        "http_async_client": async_httpx_client(auth, transport),
    }


def build_chat_model(
    connection: LlmConnection,
    bearer: BearerSource,
    *,
    model: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    tolerate_missing_key: bool = False,
    transport: Any = None,
) -> Any:
    """The one ``ChatOpenAI`` construction site (design §4.5).

    With no ``ask_your_docs.llm`` block this is exactly today's call —
    ``ChatOpenAI(model=..., base_url=...)`` plus the caller's own ``timeout``
    / ``max_retries`` — pinned by a kwargs spy (AC-19). ``transport`` is a
    test seam: when given, both httpx clients are built and carry it.
    """
    from langchain_openai import ChatOpenAI  # heavy; lazy by contract

    kwargs: dict[str, Any] = {"model": model or connection.model, "base_url": connection.base_url}
    if timeout_seconds is not None:
        kwargs["timeout"] = timeout_seconds
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    api_key, auth = connection_auth_kwargs(
        connection, bearer, tolerate_missing_key=tolerate_missing_key
    )
    if api_key is not None:
        kwargs["api_key"] = api_key
    if auth is not None or transport is not None:
        kwargs.update(httpx_clients(auth, transport))
    return ChatOpenAI(**kwargs)


async def run_connection_test(
    connection: LlmConnection, bearer: BearerSource, *, transport: Any = None
) -> str:
    """One round-trip on a candidate connection (design §4.9 item 5, E11) — always a caption."""
    llm = build_chat_model(
        connection,
        bearer,
        timeout_seconds=_TEST_CONNECTION_TIMEOUT_SECONDS,
        max_retries=0,
        transport=transport,
    )
    try:
        with translate_auth_errors(bearer):
            reply = await llm.ainvoke(_TEST_CONNECTION_PROMPT)
    except Exception as exc:  # noqa: BLE001 — every failure becomes the caption text, redacted (H4)
        return f"test failed: {exc.__class__.__name__}: {redact_bearer(str(exc), bearer)}"
    return f"test passed: {str(reply.content).strip()[:_TEST_REPLY_MAX_CHARS]}"
```

and extend `__all__` with `"build_chat_model"`, `"connection_auth_kwargs"`, `"httpx_clients"`, `"run_connection_test"`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/harness/ask_your_docs/test_chat_model_factory.py tests/harness/ask_your_docs/test_sdk_pins.py tests/harness/ask_your_docs/test_llm_connection.py -q`
Expected: PASS (the constants test in `test_llm_connection.py` stays `xfail` until Task 6).

- [ ] **Step 6: Lint and commit**

Run: `ruff check python/pydocs_mcp/harness/ask_your_docs/llm_connection.py tests/harness/ask_your_docs/ && ruff format --check python/pydocs_mcp/harness/ask_your_docs/llm_connection.py tests/harness/ask_your_docs/ && complexipy python/pydocs_mcp/harness/ask_your_docs/llm_connection.py --max-complexity-allowed 15 && vulture python/pydocs_mcp --min-confidence 80`
Expected: clean.

```bash
git checkout -- complexipy-snapshot.json
git add python/pydocs_mcp/harness/ask_your_docs/llm_connection.py tests/harness/ask_your_docs/test_chat_model_factory.py tests/harness/ask_your_docs/test_sdk_pins.py
git commit -m "harness(ask-your-docs): one client factory — callable api_key, renewing Auth, no-auth strip, connection test; SDK pins"
```

---
## Task 6: Model discovery and the ladder's production seams

**Files:**
- Create: `python/pydocs_mcp/harness/ask_your_docs/model_listing.py`
- Create: `tests/harness/ask_your_docs/test_model_listing.py`
- Modify: `tests/harness/ask_your_docs/test_llm_connection.py` (drop the `xfail` marker on the constants test)

**Interfaces:**
- Consumes: `connection_auth_kwargs`, `async_httpx_client`, `connection_identity`, `build_chat_model` (Task 5); `translate_auth_errors`, `redact_bearer`, `display_host`, the three bearer errors (Task 2); the ladder's `_default_list_models` / `_default_probe_llm` (Task 4) now resolve.
- Produces: `ModelListing(model_ids, error, fetched_at)`; `fetch_models_payload(connection, bearer, *, list_models=None, transport=None) -> list[dict]`; `fetch_model_ids(connection, bearer, *, list_models=None, transport=None, now=time.monotonic) -> ModelListing`; `cached_model_listing(connection, bearer, *, now=time.monotonic, list_models=None, transport=None) -> ModelListing` (async); `clear_model_listing_cache(connection=None)`; constants `_LISTING_TIMEOUT_SECONDS = 10.0`, `_LISTING_MAX_RETRIES = 1`, `_MODEL_LISTING_TTL_SECONDS = 60.0`. Closes AC-12, AC-13, AC-14, AC-15 (wiring half), AC-16, AC-28.

- [ ] **Step 1: Write the failing tests**

Create `tests/harness/ask_your_docs/test_model_listing.py`:

```python
"""Model discovery (LLM-connection design §4.6 — AC-12, AC-13, AC-14) and the ladder's
production seams (AC-15 wiring half, AC-16)."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import httpx
import pytest

pytest.importorskip("langchain_openai")

from pydocs_mcp.harness.ask_your_docs import llm_connection, model_listing, multimodal
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NoBearer, TokenServiceBearer, TokenServiceError
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    bearer_for_connection,
    clear_bearer_registry,
    resolve_llm_connection,
)
from pydocs_mcp.harness.ask_your_docs.model_listing import (
    ModelListing,
    cached_model_listing,
    clear_model_listing_cache,
    fetch_model_ids,
    fetch_models_payload,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

from ._connection_fakes import FakeBearer, FakeClock, FakeModelsEndpoint, FakeTokenService, RecordingTransport

_URL = "http://llm.test/v1"
_TOKEN_URL = "http://localhost:8899/access-token"


@pytest.fixture(autouse=True)
def _fresh():
    clear_bearer_registry()
    clear_model_listing_cache()
    yield
    clear_bearer_registry()
    clear_model_listing_cache()


def _connection(block: dict | None, env: dict | None = None):
    cfg = LlmConnectionConfig.model_validate(block) if block is not None else None
    return resolve_llm_connection(cfg, env or {}, ConnectionOverride(), ConnectionOverride(), config_path=None)


def _token_bearer(service: FakeTokenService) -> TokenServiceBearer:
    return TokenServiceBearer(_TOKEN_URL, transport=service.transport, sleep=lambda _s: None)


def test_listing_sends_the_bearer_and_sorts_ids() -> None:
    """AC-12: GET /models with the connection's bearer; sorted, deduplicated ids."""
    service = FakeTokenService(["tok-one-abcd"])
    recorder = RecordingTransport([200], model_ids=("zeta", "alpha", "alpha"))
    connection = _connection({"base_url": _URL, "auth": {"token_url": _TOKEN_URL}})
    listing = asyncio.run(fetch_model_ids(connection, _token_bearer(service), transport=recorder.transport))
    assert listing == ModelListing(("alpha", "zeta"), None, listing.fetched_at)
    assert recorder.requests[0].url.path.endswith("/models")
    assert recorder.authorizations() == ["Bearer tok-one-abcd"]


def test_listing_without_auth_and_on_the_no_block_path(monkeypatch) -> None:
    """AC-12: NoBearer ⇒ no header; the no-block path works with OPENAI_API_KEY unset (no header,
    no construction error) and carries it when set; base_url=None uses the SDK default path."""
    recorder = RecordingTransport([200])
    none = _connection({"base_url": _URL})
    asyncio.run(fetch_model_ids(none, NoBearer(), transport=recorder.transport))
    assert recorder.authorizations() == [None]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    unset = RecordingTransport([200])
    no_block = _connection(None, {"OPENAI_BASE_URL": _URL})
    listing = asyncio.run(fetch_model_ids(no_block, bearer_for_connection(no_block), transport=unset.transport))
    assert listing.model_ids == ("model-a", "model-b") and unset.authorizations() == [None]
    monkeypatch.setenv("OPENAI_API_KEY", "env-key-9999")
    with_key = RecordingTransport([200])
    asyncio.run(fetch_model_ids(no_block, bearer_for_connection(no_block), transport=with_key.transport))
    assert with_key.authorizations() == ["Bearer env-key-9999"]
    vendor_default = RecordingTransport([200])
    asyncio.run(fetch_model_ids(_connection(None), bearer_for_connection(_connection(None)), transport=vendor_default.transport))
    assert vendor_default.requests[0].url.path == "/v1/models"


def test_listing_failures_are_non_fatal(caplog) -> None:
    """AC-13 / E6: 5xx, a connection error and an id-less payload become ModelListing(error=…)
    with one model_listing_failed log; bearer failures propagate instead."""
    caplog.set_level(logging.WARNING)
    connection = _connection({"base_url": _URL})
    down = RecordingTransport([500, 500])  # the listing client retries once
    listing = asyncio.run(fetch_model_ids(connection, NoBearer(), transport=down.transport))
    assert listing.model_ids == () and "InternalServerError" in (listing.error or "")
    refused = RecordingTransport([httpx.ConnectError("refused")])
    listing = asyncio.run(fetch_model_ids(connection, NoBearer(), transport=refused.transport))
    assert listing.error is not None and listing.error.startswith("APIConnectionError")
    no_ids = FakeModelsEndpoint(entry={"name": "x"})
    listing = asyncio.run(fetch_model_ids(connection, NoBearer(), list_models=no_ids))
    assert listing.error == "unexpected /models payload: expected {'data': [{'id': ...}]}, got keys=['name']"
    assert len([r for r in caplog.records if "model_listing_failed" in r.getMessage()]) == 2
    with pytest.raises(TokenServiceError):
        asyncio.run(fetch_model_ids(connection, FakeBearer(fail=True), list_models=FakeModelsEndpoint(ids=("a",))))


def test_listing_cache_ttl_and_eviction() -> None:
    """AC-14: one fetch inside the TTL, a second past it, clear_model_listing_cache evicts."""
    clock = FakeClock()
    connection = _connection({"base_url": _URL})
    endpoint = FakeModelsEndpoint(ids=("model-a",))

    def listing() -> ModelListing:
        return asyncio.run(cached_model_listing(connection, NoBearer(), now=clock, list_models=endpoint))

    assert listing().model_ids == ("model-a",)
    assert listing().model_ids == ("model-a",)
    assert endpoint.calls == 1
    clock.advance(model_listing._MODEL_LISTING_TTL_SECONDS + 1)
    listing()
    assert endpoint.calls == 2
    clear_model_listing_cache(connection)
    listing()
    assert endpoint.calls == 3
    other = _connection({"base_url": "http://other/v1"})
    asyncio.run(cached_model_listing(other, NoBearer(), now=clock, list_models=endpoint))
    assert endpoint.calls == 4  # keyed on (base_url, identity)


def test_rung_three_default_seam_goes_through_the_listing(monkeypatch) -> None:
    """AC-15 (wiring half): _default_list_models is fetch_models_payload(connection, bearer)."""
    seen: list[tuple] = []

    async def _spy(connection, bearer, **kwargs):
        seen.append((connection, bearer))
        return [{"id": "my-vlm", "capabilities": {"vision": True}}]

    monkeypatch.setattr(model_listing, "fetch_models_payload", _spy)
    connection = _connection({"base_url": _URL, "model": "my-vlm", "auth": {"token_url": _TOKEN_URL}})
    bearer = FakeBearer("tok-fixed-abcd")
    payload = asyncio.run(multimodal._default_list_models(connection, bearer))
    assert payload[0]["id"] == "my-vlm" and seen == [(connection, bearer)]


def test_rung_four_default_seam_builds_through_the_factory(monkeypatch) -> None:
    """AC-16: the probe model is built by build_chat_model with max_retries=0 and the probe timeout."""
    seen: list[dict] = []

    class _SpyLlm:
        async def ainvoke(self, messages):
            return SimpleNamespace(content="OK")

    def _spy_build(connection, bearer, **kwargs):
        seen.append(kwargs)
        return _SpyLlm()

    monkeypatch.setattr(llm_connection, "build_chat_model", _spy_build)
    connection = _connection({"base_url": _URL, "model": "my-vlm", "auth": {"token_url": _TOKEN_URL}})
    reply = asyncio.run(multimodal._default_probe_llm(connection, FakeBearer(), "my-vlm", 5.0))
    assert reply == "OK"
    assert seen == [{"model": "my-vlm", "timeout_seconds": 5.0, "max_retries": 0}]


def test_fetch_models_payload_keeps_the_extra_fields() -> None:
    """Rung 3 keeps reading the metadata fields it reads today (_entry_hints_vision)."""

    class _Endpoint(RecordingTransport):
        def __call__(self, request):
            self.requests.append(request)
            return httpx.Response(
                200, json={"data": [{"id": "my-vlm", "object": "model", "capabilities": {"vision": True}}]}
            )

    recorder = _Endpoint()
    connection = _connection({"base_url": _URL})
    payload = asyncio.run(fetch_models_payload(connection, NoBearer(), transport=recorder.transport))
    assert payload == [{"id": "my-vlm", "object": "model", "capabilities": {"vision": True}}]
```

Also delete the `@pytest.mark.xfail(...)` line above `test_network_constants_are_finite_and_bounded` in `tests/harness/ask_your_docs/test_llm_connection.py`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_model_listing.py -q`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'pydocs_mcp.harness.ask_your_docs.model_listing'`.

- [ ] **Step 3: Write `model_listing.py`**

```python
"""Model discovery for the Connection dialog and the endpoint probe (LLM-connection design §4.6).

``GET {base_url}/models`` through an ``openai.AsyncOpenAI`` built from the
client factory's one auth decision — the same bearer, timeout and renewing
``Auth`` as the chat model — cached briefly per ``(base_url, auth identity)``
and failing soft (a listing is a convenience, not a gate; D5, R5).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerRejectedError,
    BearerSource,
    BearerUnavailableError,
    TokenServiceError,
    display_host,
    redact_bearer,
    translate_auth_errors,
)
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    LlmConnection,
    async_httpx_client,
    connection_auth_kwargs,
    connection_identity,
)

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")

_LISTING_TIMEOUT_SECONDS = 10.0
_LISTING_MAX_RETRIES = 1
_MODEL_LISTING_TTL_SECONDS = 60.0
# Bearer failures are E1 / E4 / E5, never a listing failure (design §4.6).
_BEARER_ERRORS = (TokenServiceError, BearerUnavailableError, BearerRejectedError)

ListModels = Callable[[LlmConnection, BearerSource], Awaitable[list[dict]]]


@dataclass(frozen=True, slots=True)
class ModelListing:
    model_ids: tuple[str, ...]
    error: str | None  # non-fatal: shown in the dialog caption (E6)
    fetched_at: float  # the injected clock's value


def _async_key(api_key: Any) -> Any:
    """The async SDK client AWAITS its api_key provider (openai/_client.py:649-651)."""
    if not callable(api_key):
        return api_key

    async def provider() -> str:
        # The first lazy token fetch is blocking I/O — keep it off the event loop.
        return await asyncio.to_thread(api_key)

    return provider


async def fetch_models_payload(
    connection: LlmConnection,
    bearer: BearerSource,
    *,
    list_models: ListModels | None = None,
    transport: Any = None,
) -> list[dict]:
    """``GET {base_url}/models`` with the connection's bearer; entries as plain dicts."""
    if list_models is not None:
        return await list_models(connection, bearer)
    from openai import AsyncOpenAI  # heavy; lazy by contract

    api_key, auth = connection_auth_kwargs(connection, bearer, tolerate_missing_key=True)
    client = AsyncOpenAI(
        base_url=connection.base_url,
        api_key=_async_key(api_key),
        http_client=async_httpx_client(auth, transport),
        timeout=_LISTING_TIMEOUT_SECONDS,
        max_retries=_LISTING_MAX_RETRIES,
    )
    with translate_auth_errors(bearer):
        page = await client.models.list()
    # Keep the extra metadata fields: rung 3's _entry_hints_vision reads them.
    return [{"id": entry.id, **(entry.model_extra or {})} for entry in page.data]


async def fetch_model_ids(
    connection: LlmConnection,
    bearer: BearerSource,
    *,
    list_models: ListModels | None = None,
    transport: Any = None,
    now: Callable[[], float] = time.monotonic,
) -> ModelListing:
    """The listing record — sorted unique ids, or a non-fatal ``error`` (E6)."""
    try:
        payload = await fetch_models_payload(
            connection, bearer, list_models=list_models, transport=transport
        )
    except _BEARER_ERRORS:
        raise
    except Exception as exc:  # noqa: BLE001 — E6: the dialog falls back to a text field
        error = f"{exc.__class__.__name__}: {redact_bearer(str(exc), bearer)}"
        log.warning(
            json.dumps(
                {
                    "event": "model_listing_failed",
                    "endpoint": display_host(connection.base_url),
                    "error": error,
                }
            )
        )
        return ModelListing((), error, now())
    ids = sorted({str(e["id"]) for e in payload if isinstance(e, dict) and e.get("id")})
    if payload and not ids:
        first = payload[0]
        keys = sorted(first) if isinstance(first, dict) else type(first).__name__
        return ModelListing(
            (), f"unexpected /models payload: expected {{'data': [{{'id': ...}}]}}, got keys={keys}", now()
        )
    return ModelListing(tuple(ids), None, now())


# One process-level cache keyed on (base_url, auth identity); the dialog's
# refresh action and a Renew evict through clear_model_listing_cache.
_listing_cache: dict[tuple, ModelListing] = {}


def _cache_key(connection: LlmConnection) -> tuple:
    return (connection.base_url, connection_identity(connection))


async def cached_model_listing(
    connection: LlmConnection,
    bearer: BearerSource,
    *,
    now: Callable[[], float] = time.monotonic,
    list_models: ListModels | None = None,
    transport: Any = None,
) -> ModelListing:
    key = _cache_key(connection)
    cached = _listing_cache.get(key)
    if cached is not None and now() - cached.fetched_at < _MODEL_LISTING_TTL_SECONDS:
        return cached
    listing = await fetch_model_ids(
        connection, bearer, list_models=list_models, transport=transport, now=now
    )
    _listing_cache[key] = listing
    return listing


def clear_model_listing_cache(connection: LlmConnection | None = None) -> None:
    """Evict one connection's entry, or everything (the test seam, like ``clear_detection_cache``)."""
    if connection is None:
        _listing_cache.clear()
    else:
        _listing_cache.pop(_cache_key(connection), None)


__all__ = (
    "ModelListing",
    "cached_model_listing",
    "clear_model_listing_cache",
    "fetch_model_ids",
    "fetch_models_payload",
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/harness/ask_your_docs/test_model_listing.py tests/harness/ask_your_docs/test_llm_connection.py tests/harness/ask_your_docs/test_multimodal_detection.py -q`
Expected: PASS, including the previously `xfail` constants test.

- [ ] **Step 5: Lint and commit**

Run: `ruff check python/pydocs_mcp/harness/ask_your_docs/model_listing.py tests/harness/ask_your_docs/ && ruff format --check python/pydocs_mcp/harness/ask_your_docs/model_listing.py tests/harness/ask_your_docs/ && complexipy python/pydocs_mcp/harness/ask_your_docs/model_listing.py --max-complexity-allowed 15 && vulture python/pydocs_mcp --min-confidence 80`
Expected: clean.

```bash
git checkout -- complexipy-snapshot.json
git add python/pydocs_mcp/harness/ask_your_docs/model_listing.py tests/harness/ask_your_docs/test_model_listing.py tests/harness/ask_your_docs/test_llm_connection.py
git commit -m "harness(ask-your-docs): model discovery through the shared auth decision; ladder production seams"
```

---
## Task 7: Vision capabilities, the image-model route and the `describe_images` seam

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/llm_connection.py` (append `resolve_vision_capabilities`)
- Modify: `python/pydocs_mcp/harness/ask_your_docs/architectures/base.py`
- Modify: `python/pydocs_mcp/harness/ask_your_docs/attachments.py` (append `describe_images`)
- Modify: `python/pydocs_mcp/harness/ask_your_docs/architectures/vision_subagent.py`
- Modify: `python/pydocs_mcp/harness/ask_your_docs/reinspect.py`
- Test: `tests/harness/ask_your_docs/test_llm_connection.py`, `test_architectures.py`, `test_reinspect_tool.py`, `test_image_attachment.py`

**Interfaces:**
- Consumes: `VisionRule`, `LlmConnection` (Task 3); `CapabilitySource`, `ModelCapabilities`, `detect_capabilities` (Task 4); `NoBearer`, `translate_auth_errors`, `redact_bearer` (Task 2); `FakeLlm` / `FakeVisionLlm` (`_agent_fakes.py`), `FakeBearer`.
- Produces: `resolve_vision_capabilities(connection, bearer, detection) -> tuple[ModelCapabilities, ModelCapabilities]`; `ImageModelRoute(StrEnum)` {`MAIN`, `VISION`}; `AgentBuildContext.vision_llm` / `vision_capabilities` / `bearer` (Null-Object defaults); `AgentArchitecture.image_model_route: ClassVar[ImageModelRoute]`; `effective_tools(ctx, route=ImageModelRoute.MAIN)`; `describe_images(image_llm, question, image_blocks, *, render=None) -> str`; `build_reinspect_tool(llm, *, max_per_turn, bearer=None)`. Closes AC-17 (helper half), AC-23.

- [ ] **Step 1: Write the failing tests**

Append to `tests/harness/ask_your_docs/test_llm_connection.py`:

```python
# ── §4.7 resolve_vision_capabilities: the single call site (AC-17 helper half) ──


def _capabilities_spy(monkeypatch, verdict):
    calls: list[dict] = []

    async def _fake_detect(model, base_url, cfg, **kwargs):
        calls.append({"model": model, "base_url": base_url, **kwargs})
        return verdict

    monkeypatch.setattr(lc, "detect_capabilities", _fake_detect)
    return calls


def test_resolve_vision_capabilities_detects_only_under_detect(monkeypatch) -> None:
    import asyncio

    from pydocs_mcp.harness.ask_your_docs.multimodal import CapabilitySource, ModelCapabilities
    from pydocs_mcp.retrieval.config.ask_your_docs_models import MultimodalDetectionConfig

    detected = ModelCapabilities(True, CapabilitySource.STATIC)
    calls = _capabilities_spy(monkeypatch, detected)
    detection = MultimodalDetectionConfig()
    bearer = NoBearer()
    sees = ModelCapabilities(True, CapabilitySource.CONFIGURED)
    blind = ModelCapabilities(False, CapabilitySource.CONFIGURED)

    detect = _resolve(_block(model="main-a"))
    assert asyncio.run(lc.resolve_vision_capabilities(detect, bearer, detection)) == (detected, detected)
    assert calls == [{"model": "main-a", "base_url": _YAML_URL, "connection": detect, "bearer": bearer}]

    assert asyncio.run(lc.resolve_vision_capabilities(_resolve(_block(model="m", vision=True)), bearer, detection)) == (sees, sees)
    assert asyncio.run(lc.resolve_vision_capabilities(_resolve(_block(model="m", vision=False)), bearer, detection)) == (blind, blind)
    separate = _resolve(_block(model="main-a", vision={"model": "vision-b"}))
    assert asyncio.run(lc.resolve_vision_capabilities(separate, bearer, detection)) == (blind, sees)
    assert len(calls) == 1  # no ladder run for the three configured rules
```

Append to `tests/harness/ask_your_docs/test_architectures.py`:

```python
# ── LLM-connection design §4.8: the image-model route ──


def test_context_defaults_are_identity_and_the_null_object() -> None:
    from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NoBearer

    ctx = _ctx(FakeLlm())
    assert ctx.vision_llm is ctx.llm
    assert ctx.vision_capabilities == ctx.capabilities
    assert isinstance(ctx.bearer, NoBearer)


def test_vision_subagent_routes_images_to_the_vision_model() -> None:
    """A separate vision model: the vision node and the reinspect tool see it, the ReAct loop
    stays on the (text-only) main model."""
    main = FakeLlm(replies=["done"])
    vision = FakeVisionLlm(replies=["- SYMBOL: pkg.mod.f"])
    ctx = AgentBuildContext(
        llm=main,
        tools=(),
        prompt="SYSTEM-P",
        capabilities=_CAPS_TEXT,
        config=AskYourDocsConfig(),
        vision_llm=vision,
        vision_capabilities=_CAPS_VISION,
    )
    graph = agent_registry.get("vision_subagent")().build(ctx)
    content = [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]
    result = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content=content)]}))
    assert result["messages"][-1].content == "done"
    assert len(vision.vision_calls) == 1 and main.calls and not any(
        not isinstance(getattr(m, "content", ""), str) for msgs in main.calls for m in msgs
    )
    assert any("tools" in n for n in graph.get_graph(xray=True).nodes)  # reinspect bound to the vision model


def test_vision_node_lets_a_provider_failure_propagate() -> None:
    """The person attached the image on purpose: the node does not swallow the failure (the
    app's send-loop boundary renders it redacted)."""

    class _Failing(FakeVisionLlm):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError("upstream rejected Bearer tok-one-abcd")

    graph = _build("vision_subagent", _Failing())
    content = [
        {"type": "text", "text": "q"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]
    with pytest.raises(RuntimeError, match="upstream rejected"):
        asyncio.run(graph.ainvoke({"messages": [HumanMessage(content=content)]}))
```

Append to `tests/harness/ask_your_docs/test_reinspect_tool.py`:

```python
def test_provider_failure_becomes_a_redacted_tool_result() -> None:
    """AC-23 / H4: the failure text enters the model's context and the traces, so it is a
    redacted tool RESULT, never an exception."""
    from ._connection_fakes import FakeBearer

    class _Failing(FakeVisionLlm):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError("upstream said: Bearer tok-one-abcd rejected (tok-one-abcd)")

    tool = build_reinspect_tool(_Failing(), max_per_turn=5, bearer=FakeBearer("tok-one-abcd"))
    tokens = _pin_turn_state({"a.png": _att("a.png")})
    try:
        out = _run_tool(tool, names=["a.png"], question="q")
    finally:
        _reset_turn_state(tokens)
    assert out.startswith("Image re-inspection failed:")
    assert "tok-one-abcd" not in out and "…abcd" in out
```

Append to `tests/harness/ask_your_docs/test_image_attachment.py`:

```python
# ── describe_images (LLM-connection design §4.8, AC-23) ──


def test_describe_images_sends_one_multimodal_message_and_strips() -> None:
    pytest.importorskip("langgraph")
    from pydocs_mcp.harness.ask_your_docs.attachments import describe_images
    from pydocs_mcp.harness.ask_your_docs.prompts import render_shared

    from ._agent_fakes import FakeVisionLlm

    fake = FakeVisionLlm(replies=["  - ERROR: KeyError 'x'  \n"])
    blocks = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}]
    facts = asyncio.run(describe_images(fake, "why?", blocks))
    assert facts == "- ERROR: KeyError 'x'"
    (call,) = fake.vision_calls
    (message,) = call
    assert message.content[0] == {
        "type": "text",
        "text": render_shared("vision_extraction_v1", question="why?"),
    }
    assert message.content[1:] == blocks


def test_describe_images_flattens_content_parts_and_honors_render() -> None:
    pytest.importorskip("langgraph")
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from pydocs_mcp.harness.ask_your_docs.attachments import describe_images

    from ._agent_fakes import FakeVisionLlm

    class _Parts(FakeVisionLlm):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls.append(list(messages))
            parts = [{"type": "reasoning", "text": "hmm"}, {"type": "text", "text": "- PATH: a/b.py"}]
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=parts))])

    fake = _Parts()
    facts = asyncio.run(
        describe_images(fake, "q", [], render=lambda name, **kw: f"CUSTOM {name} {kw['question']}")
    )
    assert facts == "- PATH: a/b.py"
    assert fake.calls[0][0].content[0]["text"] == "CUSTOM vision_extraction_v1 q"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_llm_connection.py tests/harness/ask_your_docs/test_architectures.py tests/harness/ask_your_docs/test_reinspect_tool.py tests/harness/ask_your_docs/test_image_attachment.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'resolve_vision_capabilities'`, `TypeError: AgentBuildContext.__init__() got an unexpected keyword argument 'vision_llm'`, `ImportError: cannot import name 'describe_images'`, `TypeError: build_reinspect_tool() got an unexpected keyword argument 'bearer'`.

- [ ] **Step 3: Append `resolve_vision_capabilities` to `llm_connection.py`**

Add the imports `from pydocs_mcp.harness.ask_your_docs.multimodal import CapabilitySource, ModelCapabilities, detect_capabilities` and `from pydocs_mcp.retrieval.config.ask_your_docs_models import MultimodalDetectionConfig` (extend the existing import), then append:

```python
async def resolve_vision_capabilities(
    connection: LlmConnection, bearer: BearerSource, detection: MultimodalDetectionConfig
) -> tuple[ModelCapabilities, ModelCapabilities]:
    """The single call site of design §4.7: the ``(main, vision)`` verdicts.

    ``DETECT`` runs today's ladder with authenticated probes; the three
    configured rules never probe. Under ``SEPARATE_MODEL`` the main model is
    by construction not the image model, so its verdict is "blind" and the
    vision model's is "sees" — nothing consumes a main verdict there.
    """
    sees = ModelCapabilities(multimodal=True, source=CapabilitySource.CONFIGURED)
    blind = ModelCapabilities(multimodal=False, source=CapabilitySource.CONFIGURED)
    rule = connection.vision_rule
    if rule is VisionRule.DETECT:
        main = await detect_capabilities(
            connection.model or "", connection.base_url, detection, connection=connection, bearer=bearer
        )
        return main, main
    if rule is VisionRule.MULTIMODAL:
        return sees, sees
    if rule is VisionRule.TEXT_ONLY:
        return blind, blind
    return blind, sees
```

and add `"resolve_vision_capabilities"` to `__all__`.

- [ ] **Step 4: Rewrite `architectures/base.py`**

```python
"""AgentArchitecture ABC + AgentBuildContext (spec §3.2; LLM-connection design §4.8).

Light module: no langgraph/streamlit imports — those live inside the entry
modules' ``build`` methods, so importing the registry stays cheap and the
subpackage's lazy-import contract holds.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NoBearer
from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig


class AgentArchitectureError(ValueError):
    """A selected architecture cannot be built for the detected model
    capabilities — the message carries the fix (YAML-anchored pointer)."""


class ImageModelRoute(StrEnum):
    """Which model an architecture sends image blocks to (design §4.8)."""

    MAIN = "main"  # ctx.llm
    VISION = "vision"  # ctx.vision_llm (= ctx.llm unless a separate vision model is configured)


@dataclass(frozen=True, slots=True)
class AgentBuildContext:
    """Ambient dependencies for architecture builders — the agent-side mirror
    of retrieval's BuildContext (retrieval/serialization.py)."""

    llm: Any  # ChatOpenAI (typed Any: the extra is mypy-excluded)
    tools: Sequence[Any]  # MCP-adapter tools from MultiServerMCPClient
    prompt: str  # SYSTEM_PROMPT + catalog listing
    capabilities: ModelCapabilities
    config: AskYourDocsConfig
    vision_llm: Any = None  # the image model; None = llm (the identity default)
    vision_capabilities: ModelCapabilities | None = None  # None = capabilities
    bearer: Any = None  # BearerSource, for redaction in tool results; None = NoBearer()

    def __post_init__(self) -> None:
        # Null-Object defaults on a frozen, slotted dataclass: every existing
        # five-field construction site keeps working (design §4.8).
        if self.vision_llm is None:
            object.__setattr__(self, "vision_llm", self.llm)
        if self.vision_capabilities is None:
            object.__setattr__(self, "vision_capabilities", self.capabilities)
        if self.bearer is None:
            object.__setattr__(self, "bearer", NoBearer())


def effective_tools(ctx: AgentBuildContext, route: ImageModelRoute = ImageModelRoute.MAIN) -> tuple:
    """The MCP tools, plus ``reinspect_images`` when the EFFECTIVE image model can see.

    The reinspect tool re-reads stored image bytes, so it is gated on the
    capability of the model that will actually see them and bound to that
    same model: ``ctx.vision_llm`` on the VISION route, ``ctx.llm`` on MAIN.
    Without a separate vision model both resolve to the same objects, so
    text-only builds omit the tool exactly as before.
    """
    on_vision_route = route is ImageModelRoute.VISION
    image_caps = ctx.vision_capabilities if on_vision_route else ctx.capabilities
    if image_caps is None or not image_caps.multimodal:
        return tuple(ctx.tools)
    from pydocs_mcp.harness.ask_your_docs.reinspect import build_reinspect_tool

    image_llm = ctx.vision_llm if on_vision_route else ctx.llm
    return (
        *ctx.tools,
        build_reinspect_tool(
            image_llm, max_per_turn=ctx.config.images.max_reinspect_per_turn, bearer=ctx.bearer
        ),
    )


class AgentArchitecture(ABC):
    """One registrable agent architecture. Entries are stateless frozen
    dataclasses; ``build`` returns a compiled LangGraph graph exposing
    ``ainvoke({"messages": [...]})`` and ``get_graph()`` (introspection
    contract — the README's agent-graph.png regeneration must keep working)."""

    #: Build-time capability requirement, validated by build_agent BEFORE
    #: building (spec §3.4.4). ClassVar metadata is the minimal extension over
    #: the ComponentRegistry precedent (which carries only the class itself).
    requires_multimodal: ClassVar[bool] = False

    #: Which model this architecture sends image blocks to; the requirement
    #: above is checked against THAT model's capabilities (design §4.8).
    image_model_route: ClassVar[ImageModelRoute] = ImageModelRoute.MAIN

    #: The registry name — set by @register_architecture, which also binds
    #: the prompt namespace (prompts/<architecture_name>/ with shared/
    #: fallback). Never set this by hand; the decorator is the single wiring.
    architecture_name: ClassVar[str]

    @classmethod
    def prompts(cls):
        """This architecture's prompt namespace (convention: its registry
        name IS its prompt directory; shared/ serves everything else)."""
        from pydocs_mcp.harness.ask_your_docs.prompts import prompts_for

        return prompts_for(cls.architecture_name)

    @abstractmethod
    def build(self, ctx: AgentBuildContext) -> Any: ...

    # from_dict/to_dict follow the ComponentRegistry contract so
    # agent_registry.build({"type": name, ...}, ctx) works if a future spec
    # wants data-driven construction; for now entries carry no parameters.
    @classmethod
    def from_dict(cls, data: dict, context: object) -> AgentArchitecture:
        return cls()  # type: ignore[call-arg]


__all__ = ("AgentArchitecture", "AgentArchitectureError", "AgentBuildContext", "ImageModelRoute")
```

Also export `ImageModelRoute` from `architectures/__init__.py` (add it to the `from ...base import (...)` list and to `__all__`).

- [ ] **Step 5: Append `describe_images` to `attachments.py`**

Extend the imports (`from collections.abc import Callable`, `from typing import TYPE_CHECKING, Any`) and append before `__all__`:

```python
async def describe_images(
    image_llm: Any,
    question: str,
    image_blocks: list[dict],
    *,
    render: Callable[..., str] | None = None,
) -> str:
    """Run the vision-extraction prompt over image blocks on ``image_llm`` (design §4.8).

    ``image_llm`` is the EFFECTIVE image client for the calling architecture
    (``ctx.vision_llm`` on the VISION route, ``ctx.llm`` on MAIN). ``render``
    lets each call site keep its prompt namespace (the vision node's
    architecture namespace, a ``prompts=`` override) instead of hard-wiring
    the shared pool. Structured replies (content parts) flatten to their
    text parts, mirroring ``_history_line``'s defensive flattening.
    """
    from langchain_core.messages import HumanMessage  # function-local: the lazy-import contract

    from pydocs_mcp.harness.ask_your_docs.prompts import render_shared

    render = render or render_shared
    rendered = render("vision_extraction_v1", question=question)
    reply = await image_llm.ainvoke(
        [HumanMessage(content=[{"type": "text", "text": rendered}, *image_blocks])]
    )
    content = reply.content
    if isinstance(content, list):
        content = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content).strip()
```

and add `"describe_images"` to `__all__`.

- [ ] **Step 6: Route the two image call sites through the seam**

`architectures/vision_subagent.py` — replace the class body:

```python
@register_architecture("vision_subagent")
@dataclass(frozen=True, slots=True)
class VisionSubagentArchitecture(AgentArchitecture):
    requires_multimodal: ClassVar[bool] = True
    image_model_route: ClassVar[ImageModelRoute] = ImageModelRoute.VISION

    def build(self, ctx: AgentBuildContext) -> Any:
        from langchain_core.messages import HumanMessage, RemoveMessage
        from langgraph.graph import END, START, MessagesState, StateGraph
        from langgraph.prebuilt import create_react_agent

        from pydocs_mcp.harness.ask_your_docs.attachments import describe_images
        from pydocs_mcp.harness.ask_your_docs.bearer_tokens import translate_auth_errors

        react = create_react_agent(
            ctx.llm, effective_tools(ctx, self.image_model_route), prompt=ctx.prompt
        )

        async def vision_extract(state: MessagesState):
            last = state["messages"][-1]
            if isinstance(last.content, str):  # no image this turn
                return {}
            blocks = last.content
            question = next(b["text"] for b in blocks if b["type"] == "text")
            images = [b for b in blocks if b["type"] == "image_url"]
            # The person attached the image on purpose: a failure propagates to
            # the app's send-loop boundary, which renders it redacted (§4.8).
            with translate_auth_errors(ctx.bearer):
                facts = await describe_images(
                    ctx.vision_llm, question, images, render=self.prompts().render
                )
            # Replace the multimodal message with a TEXT-ONLY message: facts
            # woven in the weave_attachments style, so the downstream ReAct
            # agent never sees image blocks.
            woven = (
                f"[image analysis]\n{facts}\n[/image analysis]\n{question}" if facts else question
            )
            # WHY RemoveMessage: MessagesState's ``add_messages`` reducer
            # merges by message id — a returned list APPENDS/updates, it
            # never deletes by omission. Without the explicit removal the
            # multimodal message would stay in state and the ReAct node
            # would still see (and re-pay for) the image blocks.
            return {"messages": [RemoveMessage(id=last.id), HumanMessage(woven)]}

        graph = StateGraph(MessagesState)
        graph.add_node("vision_extract", vision_extract)
        graph.add_node("react_agent", react)
        graph.add_edge(START, "vision_extract")
        graph.add_edge("vision_extract", "react_agent")
        graph.add_edge("react_agent", END)
        return graph.compile()
```

(add `ImageModelRoute` to the `architectures.base` import at the top of the file).

`reinspect.py` — change the signature and the vision call:

```python
def build_reinspect_tool(llm: Any, *, max_per_turn: int, bearer: Any = None) -> Any:
    """Build the tool bound to ``llm`` (must be vision-capable — architectures
    only attach it when the detected capabilities say so). ``max_per_turn``
    comes from ``images.max_reinspect_per_turn`` at graph-build time;
    ``bearer`` is the connection's BearerSource for redacting a failure
    (``None`` = the Null Object)."""
    from langchain_core.tools import StructuredTool

    from pydocs_mcp.harness.ask_your_docs.agent import _active_image_store, _reinspect_state
    from pydocs_mcp.harness.ask_your_docs.attachments import describe_images
    from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
        NoBearer,
        redact_bearer,
        translate_auth_errors,
    )
    from pydocs_mcp.harness.ask_your_docs.prompts import BUDGET_MESSAGE, REINSPECT_DESCRIPTION

    bearer = bearer if bearer is not None else NoBearer()

    async def reinspect_images(names: list[str], question: str) -> str:
        # … the four guard branches (store empty / names empty / unknown names / memo / budget)
        #   stay exactly as today (reinspect.py:45-68) …
        state["calls"] += 1
        selected = [store[n] for n in names]
        try:
            with translate_auth_errors(bearer):
                facts = await describe_images(
                    llm, question, [att.as_content_block() for att in selected]
                )
        except Exception as exc:  # noqa: BLE001 — a tool RESULT, never a crash; redacted (H4)
            return f"Image re-inspection failed: {redact_bearer(str(exc), bearer)}"
        state["memo"][memo_key] = facts
        return facts

    return StructuredTool.from_function(
        coroutine=reinspect_images,
        name="reinspect_images",
        description=REINSPECT_DESCRIPTION,
    )
```

(the `HumanMessage` and `render_shared` imports at `reinspect.py:34, 41` are no longer used and go.)

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/harness/ask_your_docs/ -q`
Expected: PASS (the existing architecture, reinspect and attachment suites keep passing on the identity defaults).

- [ ] **Step 8: Lint and commit**

Run: `ruff check python/pydocs_mcp/harness/ask_your_docs tests/harness/ask_your_docs && ruff format --check python/pydocs_mcp/harness/ask_your_docs tests/harness/ask_your_docs && complexipy python/pydocs_mcp/harness/ask_your_docs --max-complexity-allowed 15 && vulture python/pydocs_mcp --min-confidence 80`
Expected: clean.

```bash
git checkout -- complexipy-snapshot.json
git add python/pydocs_mcp/harness/ask_your_docs/llm_connection.py python/pydocs_mcp/harness/ask_your_docs/architectures/base.py python/pydocs_mcp/harness/ask_your_docs/architectures/__init__.py python/pydocs_mcp/harness/ask_your_docs/architectures/vision_subagent.py python/pydocs_mcp/harness/ask_your_docs/attachments.py python/pydocs_mcp/harness/ask_your_docs/reinspect.py tests/harness/ask_your_docs/
git commit -m "harness(ask-your-docs): vision capabilities from the connection; image-model route; describe_images seam with redaction"
```

---
## Task 8: `auto` routing and the build-time image-capability gate

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/architectures/base.py` (append `require_image_capability`)
- Modify: `python/pydocs_mcp/harness/ask_your_docs/architectures/__init__.py` (export it)
- Modify: `python/pydocs_mcp/harness/ask_your_docs/architectures/auto.py`
- Modify: `python/pydocs_mcp/harness/ask_your_docs/agent.py:152-180` (`_build_architecture`)
- Test: `tests/harness/ask_your_docs/test_architectures.py`, create `tests/harness/ask_your_docs/test_agent_connection.py`

**Interfaces:**
- Consumes: `ImageModelRoute`, `AgentBuildContext` (Task 7).
- Produces: `require_image_capability(arch_cls, ctx, name, model) -> None` (raises `AgentArchitectureError`, design E13); `_build_architecture(name, *, llm, tools, prompt, capabilities, config, model, vision_llm=None, vision_capabilities=None, bearer=None)`; the `auto` routing table of design §4.7 with the `auto_routing` log (E12). Closes AC-18.

- [ ] **Step 1: Write the failing tests**

In `tests/harness/ask_your_docs/test_architectures.py`, replace `test_auto_routes_by_capability` with:

```python
def test_auto_routes_by_capability() -> None:
    """AC8 + design R6: text-only → text_react; vision → preferred_architecture, whose shipped
    default is now inline (vision_subagent on request)."""
    text_nodes = set(_build("auto", FakeLlm(), caps=_CAPS_TEXT).get_graph().nodes)
    assert "vision_extract" not in text_nodes  # the plain ReAct graph
    inline_nodes = set(_build("auto", FakeVisionLlm(), caps=_CAPS_VISION).get_graph().nodes)
    assert "vision_extract" not in inline_nodes  # default preferred: inline == plain ReAct shape
    cfg = AskYourDocsConfig.model_validate({"multimodal": {"preferred_architecture": "vision_subagent"}})
    vision_nodes = set(
        _build("auto", FakeVisionLlm(), caps=_CAPS_VISION, config=cfg).get_graph().nodes
    )
    assert "vision_extract" in vision_nodes


def test_auto_routes_a_separate_vision_model_to_vision_subagent(caplog) -> None:
    """AC-18: a separate vision model always builds vision_subagent; preferred inline is
    overridden with one auto_routing log (E12), preferred vision_subagent logs nothing."""
    import logging

    caplog.set_level(logging.INFO)

    def _separate(config: AskYourDocsConfig):
        ctx = AgentBuildContext(
            llm=FakeLlm(),
            tools=(),
            prompt="P",
            capabilities=_CAPS_TEXT,
            config=config,
            vision_llm=FakeVisionLlm(),
            vision_capabilities=_CAPS_VISION,
        )
        return set(agent_registry.get("auto")().build(ctx).get_graph().nodes)

    assert "vision_extract" in _separate(AskYourDocsConfig())  # preferred inline → re-routed
    routed = [r.getMessage() for r in caplog.records if "auto_routing" in r.getMessage()]
    assert len(routed) == 1 and '"built": "vision_subagent"' in routed[0]
    caplog.clear()
    cfg = AskYourDocsConfig.model_validate({"multimodal": {"preferred_architecture": "vision_subagent"}})
    assert "vision_extract" in _separate(cfg)
    assert not [r for r in caplog.records if "auto_routing" in r.getMessage()]
```

Create `tests/harness/ask_your_docs/test_agent_connection.py` (Task 9 appends to it):

```python
"""build_agent × the LLM connection (design §4.5, §4.7, §4.8 — AC-17, AC-18 gate rows, AC-34,
AC-42). Fake MCP client + fake graph builder, as in test_prompt_seam.py."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("langgraph")

from pydocs_mcp.harness.ask_your_docs import agent as agent_mod
from pydocs_mcp.harness.ask_your_docs.architectures import AgentArchitectureError
from pydocs_mcp.harness.ask_your_docs.multimodal import CapabilitySource, ModelCapabilities
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

from ._agent_fakes import FakeLlm, FakeVisionLlm

_SEES = ModelCapabilities(True, CapabilitySource.CONFIGURED)
_BLIND = ModelCapabilities(False, CapabilitySource.CONFIGURED)


def _build(name: str, **kw):
    return agent_mod._build_architecture(
        name, llm=FakeLlm(), tools=(), prompt="P", config=AskYourDocsConfig(), model="main-a", **kw
    )


def test_explicit_inline_under_a_separate_vision_model_is_rejected() -> None:
    """AC-18 / E13: inline routes images to the main model, which is blind under SEPARATE_MODEL."""
    with pytest.raises(AgentArchitectureError) as excinfo:
        _build("inline", capabilities=_BLIND, vision_llm=FakeVisionLlm(), vision_capabilities=_SEES)
    assert "ask_your_docs.llm.vision" in str(excinfo.value) and "'main-a'" in str(excinfo.value)


def test_vision_route_gate_checks_the_image_model() -> None:
    """AC-18 / E13: vision_subagent on a text-only separate vision model is rejected naming
    vision.model; on a seeing one it builds even though the main model is blind."""
    with pytest.raises(AgentArchitectureError, match="vision.model is text-only"):
        _build(
            "vision_subagent", capabilities=_BLIND, vision_llm=FakeVisionLlm(), vision_capabilities=_BLIND
        )
    graph = _build(
        "vision_subagent", capabilities=_BLIND, vision_llm=FakeVisionLlm(), vision_capabilities=_SEES
    )
    assert "vision_extract" in set(graph.get_graph().nodes)


def test_text_react_never_checks_capabilities() -> None:
    graph = _build("text_react", capabilities=_BLIND)
    assert "vision_extract" not in set(graph.get_graph().nodes)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_architectures.py tests/harness/ask_your_docs/test_agent_connection.py -q`
Expected: FAIL — the default-flip row (`vision_extract` present under the old routing), the separate-model rows, and `TypeError: _build_architecture() got an unexpected keyword argument 'vision_llm'`.

- [ ] **Step 3: Append `require_image_capability` to `architectures/base.py`**

```python
def require_image_capability(
    arch_cls: type[AgentArchitecture], ctx: AgentBuildContext, name: str, model: str
) -> None:
    """Design E13: a multimodal architecture needs a vision-capable IMAGE model — the model on
    its route — validated BEFORE building (spec §3.4.4)."""
    if not arch_cls.requires_multimodal:
        return
    on_vision_route = arch_cls.image_model_route is ImageModelRoute.VISION
    image_caps = ctx.vision_capabilities if on_vision_route else ctx.capabilities
    if image_caps is not None and image_caps.multimodal:
        return
    source = image_caps.source if image_caps is not None else "unknown"
    if on_vision_route and ctx.vision_llm is not ctx.llm:
        raise AgentArchitectureError(
            f"architecture {name!r} needs a vision-capable image model; the configured "
            f"ask_your_docs.llm.vision.model is text-only (source={source}); set "
            "ask_your_docs.llm.vision: true or name a vision-capable vision.model"
        )
    raise AgentArchitectureError(
        f"architecture {name!r} requires a multimodal model, but {model!r} was detected "
        f"text-only (source={source}). Set ask_your_docs.multimodal.detection.override: true "
        "in your YAML if the detection is wrong, set ask_your_docs.llm.vision: true, or "
        "select architecture: auto."
    )
```

Add `"require_image_capability"` to the module's `__all__` and to the `architectures/__init__.py` import list and `__all__`.

- [ ] **Step 4: Rewrite `architectures/auto.py`**

```python
"""``auto`` — conditional hybrid routed by detection (spec §3.4.3; design §4.7).

Build-time routing (per agent-cache entry), not per-message: a fixed model's
capability does not change between questions, so routing once at build keeps
the compiled graph static and ``get_graph()`` rendering meaningful.
Per-message image-vs-no-image branching already lives INSIDE each
architecture (the vision node passes through on str content; ``inline`` only
gets blocks when images exist).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, ClassVar

from pydocs_mcp.harness.ask_your_docs.architectures import agent_registry, register_architecture
from pydocs_mcp.harness.ask_your_docs.architectures.base import (
    AgentArchitecture,
    AgentBuildContext,
)

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")


@register_architecture("auto")
@dataclass(frozen=True, slots=True)
class AutoArchitecture(AgentArchitecture):
    # Validated at ROUTE time, not build time: auto itself builds on any model.
    requires_multimodal: ClassVar[bool] = False

    def build(self, ctx: AgentBuildContext) -> Any:
        chosen = ctx.config.multimodal.preferred_architecture
        if ctx.vision_llm is not ctx.llm:
            # A separate vision model: only vision_subagent can route image
            # blocks to it (design E12) — say so once when the preference differs.
            if chosen != "vision_subagent":
                log.info(
                    json.dumps(
                        {
                            "event": "auto_routing",
                            "preferred": chosen,
                            "built": "vision_subagent",
                            "reason": f"{chosen} cannot route images to a separate vision model",
                        }
                    )
                )
            return agent_registry.get("vision_subagent")().build(ctx)  # type: ignore[misc]
        if not ctx.capabilities.multimodal:
            return agent_registry.get("text_react")().build(ctx)  # type: ignore[misc]
        arch_cls = agent_registry.get(chosen)
        if arch_cls is None:
            raise ValueError(
                f"multimodal.preferred_architecture {chosen!r} is not a registered "
                f"architecture; known: {agent_registry.names()}"
            )
        return arch_cls().build(ctx)


__all__ = ("AutoArchitecture",)
```

- [ ] **Step 5: Widen `_build_architecture` in `agent.py`**

Replace `agent.py:152-180` with:

```python
def _build_architecture(
    name: str,
    *,
    llm,
    tools,
    prompt: str,
    capabilities: ModelCapabilities,
    config: AskYourDocsConfig,
    model: str,
    vision_llm=None,
    vision_capabilities: ModelCapabilities | None = None,
    bearer: BearerSource | None = None,
):
    """Validate + build the named architecture (spec §3.4.4; design §4.8).

    Split out of :func:`build_agent` so tests exercise validation and graph
    construction without an MCP server subprocess. ``vision_llm`` /
    ``vision_capabilities`` / ``bearer`` default to the context's Null Objects.
    """
    arch_cls = agent_registry.get(name)
    if arch_cls is None:
        raise ValueError(f"unknown architecture {name!r}; known: {agent_registry.names()}")
    ctx = AgentBuildContext(
        llm=llm,
        tools=tools,
        prompt=prompt,
        capabilities=capabilities,
        config=config,
        vision_llm=vision_llm,
        vision_capabilities=vision_capabilities,
        bearer=bearer,
    )
    require_image_capability(arch_cls, ctx, name, model)
    return arch_cls().build(ctx)
```

with `require_image_capability` added to the `architectures` import block at `agent.py:23-27` and `from pydocs_mcp.harness.ask_your_docs.bearer_tokens import BearerSource` added to the imports.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/harness/ask_your_docs/ -q`
Expected: PASS.

- [ ] **Step 7: Lint and commit**

Run: `ruff check python/pydocs_mcp/harness/ask_your_docs tests/harness/ask_your_docs && ruff format --check python/pydocs_mcp/harness/ask_your_docs tests/harness/ask_your_docs && complexipy python/pydocs_mcp/harness/ask_your_docs --max-complexity-allowed 15`
Expected: clean.

```bash
git checkout -- complexipy-snapshot.json
git add python/pydocs_mcp/harness/ask_your_docs/architectures/ python/pydocs_mcp/harness/ask_your_docs/agent.py tests/harness/ask_your_docs/test_architectures.py tests/harness/ask_your_docs/test_agent_connection.py
git commit -m "harness(ask-your-docs): auto routes a separate vision model to vision_subagent; image-capability gate per route"
```

---
## Task 9: `build_agent` on the connection; `reformulation.py`; the line budgets

**Files:**
- Create: `python/pydocs_mcp/harness/ask_your_docs/reformulation.py`
- Modify: `python/pydocs_mcp/harness/ask_your_docs/agent.py` (imports; `build_agent`; delete `_history_line` / `reformulate`)
- Modify: `python/pydocs_mcp/harness/ask_your_docs/__init__.py:21-28`
- Modify: `python/pydocs_mcp/harness/ask_your_docs/app.py:18` (import only — the page rewrite is Task 11)
- Test: `tests/harness/ask_your_docs/test_agent_connection.py` (append), `test_prompt_seam.py`, `test_image_attachment.py`; create `tests/harness/ask_your_docs/test_module_line_budgets.py`

**Interfaces:**
- Consumes: `LlmConnection`, `ConnectionOverride`, `resolve_llm_connection`, `bearer_for_connection`, `build_chat_model`, `resolve_vision_capabilities` (Tasks 3, 5, 7); `VisionRule`.
- Produces: `build_agent(workspace, model, base_url=None, pydocs_config=None, pydocs_cmd=None, catalog=None, *, architecture=None, config=None, capabilities=None, prompts=None, tool_names=None, skill_override=None, task_name=None, scope_pin=True, subprocess_env=None, mcp_tools=None, connection=None, bearer=None, vision_capabilities=None) -> (graph, llm)`; `reformulation.reformulate` / `reformulation._history_line`. Closes AC-17, AC-19 (signature half), AC-29, AC-34 (build half), AC-42.

- [ ] **Step 1: Write the failing tests**

Append to `tests/harness/ask_your_docs/test_agent_connection.py`:

```python
# ── build_agent × the connection (AC-17, AC-34 build half, AC-42) ──

import inspect

from pydocs_mcp.harness.ask_your_docs import llm_connection, multimodal
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NoBearer, TokenServiceError
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    clear_bearer_registry,
    resolve_llm_connection,
)
from pydocs_mcp.harness.ask_your_docs.multimodal import clear_detection_cache
from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

from ._connection_fakes import FakeBearer, FakeModelsEndpoint

_CATALOG = {"proj": ["pkg_a"]}


class _FakeMcpClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def get_tools(self):
        return []


@pytest.fixture
def harness(monkeypatch):
    """A network-free build_agent: fake MCP client, fake graph builder, spied chat models."""
    clear_bearer_registry()
    clear_detection_cache()
    built: list[dict] = []
    models: list[dict] = []

    def _capture_build(name, **kwargs):
        built.append({"name": name, **kwargs})
        return "GRAPH"

    def _spy_chat_model(connection, bearer, **kwargs):
        models.append({"connection": connection, "bearer": bearer, **kwargs})
        return FakeLlm()

    monkeypatch.setattr(agent_mod, "MultiServerMCPClient", _FakeMcpClient)
    monkeypatch.setattr(agent_mod, "_build_architecture", _capture_build)
    monkeypatch.setattr(agent_mod, "build_chat_model", _spy_chat_model)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    yield built, models
    clear_bearer_registry()
    clear_detection_cache()


def _connection(block: dict, **kw):
    cfg = LlmConnectionConfig.model_validate(block)
    return resolve_llm_connection(cfg, {}, ConnectionOverride(), ConnectionOverride(), config_path=None)


def test_build_agent_resolves_capabilities_once_unless_injected(harness, monkeypatch) -> None:
    """AC-17: with capabilities=None the single §4.7 call site runs once; an injected verdict
    skips it and doubles as the vision verdict."""
    built, _models = harness
    calls: list[tuple] = []

    async def _spy_resolve(connection, bearer, detection):
        calls.append((connection, bearer))
        return _SEES, _SEES

    monkeypatch.setattr(agent_mod, "resolve_vision_capabilities", _spy_resolve)
    connection = _connection({"base_url": "http://llm.test/v1", "model": "main-a", "vision": True})
    asyncio.run(agent_mod.build_agent("/tmp/ws", None, catalog=_CATALOG, connection=connection, bearer=NoBearer()))
    assert len(calls) == 1 and built[-1]["capabilities"] == _SEES and built[-1]["vision_capabilities"] == _SEES
    asyncio.run(
        agent_mod.build_agent(
            "/tmp/ws", None, catalog=_CATALOG, connection=connection, bearer=NoBearer(), capabilities=_BLIND
        )
    )
    assert len(calls) == 1
    assert built[-1]["capabilities"] == _BLIND and built[-1]["vision_capabilities"] == _BLIND


def test_separate_vision_model_builds_a_second_chat_model_on_the_same_bearer(harness) -> None:
    """AC-17: SEPARATE_MODEL ⇒ two constructions (main, vision) sharing the bearer object; the
    graph builder receives a distinct vision_llm."""
    built, models = harness
    connection = _connection(
        {"base_url": "http://llm.test/v1", "model": "main-a", "vision": {"model": "vision-b"}}
    )
    bearer = NoBearer()
    asyncio.run(agent_mod.build_agent("/tmp/ws", None, catalog=_CATALOG, connection=connection, bearer=bearer))
    assert [m.get("model") for m in models] == [None, "vision-b"]
    assert all(m["bearer"] is bearer and m["connection"] is connection for m in models)
    assert built[-1]["vision_llm"] is not built[-1]["llm"]
    assert built[-1]["capabilities"] == _BLIND and built[-1]["vision_capabilities"] == _SEES
    assert built[-1]["bearer"] is bearer


def test_no_model_chosen_fails_before_any_construction(harness, monkeypatch) -> None:
    """AC-42 / E19: connection.model is None ⇒ AgentArchitectureError before the serve
    subprocess or any chat model is built."""
    _built, models = harness
    served: list[tuple] = []
    monkeypatch.setattr(agent_mod, "serve_connection", lambda *a, **k: served.append(a) or {})
    connection = _connection({"base_url": "http://llm.test/v1"})
    with pytest.raises(AgentArchitectureError, match="no model chosen; set ask_your_docs.llm.model"):
        asyncio.run(agent_mod.build_agent("/tmp/ws", None, catalog=_CATALOG, connection=connection))
    assert served == [] and models == []


def test_token_service_down_at_build_time_raises_and_caches_nothing(harness, monkeypatch) -> None:
    """AC-34 (build half, H3): vision: null + endpoint_probe ⇒ the ladder's rung 3 asks the
    bearer, the bearer raises, nothing is cached, and the next build succeeds once it recovers."""
    built, _models = harness
    endpoint = FakeModelsEndpoint(entry={"id": "main-a", "capabilities": {"vision": True}})
    monkeypatch.setattr(multimodal, "_default_list_models", endpoint)
    cfg = AskYourDocsConfig.model_validate(
        {"multimodal": {"detection": {"static_table": False, "endpoint_probe": True}}}
    )
    connection = _connection({"base_url": "http://llm.test/v1", "model": "main-a",
                              "auth": {"token_url": "http://localhost:8899/t"}})
    with pytest.raises(TokenServiceError):
        asyncio.run(
            agent_mod.build_agent(
                "/tmp/ws", None, catalog=_CATALOG, config=cfg, connection=connection,
                bearer=FakeBearer(fail=True),
            )
        )
    assert endpoint.calls == 1 and multimodal._detection_cache == {} and built == []
    asyncio.run(
        agent_mod.build_agent(
            "/tmp/ws", None, catalog=_CATALOG, config=cfg, connection=connection,
            bearer=FakeBearer("tok-fixed-abcd"),
        )
    )
    assert built[-1]["capabilities"] == ModelCapabilities(True, CapabilitySource.ENDPOINT)


def test_build_agent_defaults_resolve_the_no_block_connection(harness) -> None:
    """Today's positional call shape still works: (workspace, model, base_url) ⇒ the no-block
    connection, the lenient bearer, rule-1 construction."""
    _built, models = harness
    asyncio.run(agent_mod.build_agent("/tmp/ws", "m", "http://x/v1", catalog=_CATALOG, capabilities=_BLIND))
    connection = models[-1]["connection"]
    assert connection.block_present is False and connection.model == "m"
    assert connection.base_url == "http://x/v1"


def test_build_agent_signature_keeps_the_two_tuple_and_keyword_only_seams() -> None:
    params = inspect.signature(agent_mod.build_agent).parameters
    for name in ("prompts", "connection", "bearer", "vision_capabilities"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY and params[name].default is None
```

Create `tests/harness/ask_your_docs/test_module_line_budgets.py`:

```python
"""AC-29: every harness module stays readable in one tool call (CLAUDE.md §Code shape), and
reformulation lives in its own module. Core deps only — a line count, no imports."""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_HARNESS = _ROOT / "python/pydocs_mcp/harness/ask_your_docs"
_BUDGETS = {
    _HARNESS / "agent.py": 500,
    _HARNESS / "app.py": 500,
    _HARNESS / "llm_connection.py": 500,
    _HARNESS / "bearer_tokens.py": 500,
    _HARNESS / "model_listing.py": 500,
    _HARNESS / "connection_dialog.py": 500,
    _HARNESS / "reformulation.py": 500,
    _ROOT / "python/pydocs_mcp/retrieval/config/ask_your_docs_models.py": 200,
}


@pytest.mark.parametrize(("path", "budget"), sorted(_BUDGETS.items()), ids=lambda p: getattr(p, "name", p))
def test_module_line_budget(path: Path, budget: int) -> None:
    if not path.exists():
        pytest.skip(f"{path.name} lands in a later task")
    assert len(path.read_text(encoding="utf-8").splitlines()) < budget


def test_reformulation_moved_out_of_agent() -> None:
    agent_source = (_HARNESS / "agent.py").read_text(encoding="utf-8")
    reformulation_source = (_HARNESS / "reformulation.py").read_text(encoding="utf-8")
    assert "def reformulate(" not in agent_source and "def _history_line(" not in agent_source
    assert "def reformulate(" in reformulation_source and "def _history_line(" in reformulation_source
```

Edit `tests/harness/ask_your_docs/test_prompt_seam.py`: change line 24 so `reformulate` is imported from `pydocs_mcp.harness.ask_your_docs.reformulation`, and give both `_capture_build` fakes (lines 80 and 140) the signature `def _capture_build(name, *, llm, tools, prompt, capabilities, config, model, **_extra):`.

Edit `tests/harness/ask_your_docs/test_image_attachment.py:139`: `from pydocs_mcp.harness.ask_your_docs.reformulation import _history_line`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_agent_connection.py tests/harness/ask_your_docs/test_module_line_budgets.py tests/harness/ask_your_docs/test_prompt_seam.py tests/harness/ask_your_docs/test_image_attachment.py -q`
Expected: FAIL — `ModuleNotFoundError: ... reformulation`, `TypeError: build_agent() got an unexpected keyword argument 'connection'`.

- [ ] **Step 3: Create `reformulation.py`**

```python
"""Follow-up reformulation — a distinct consumer of the chat model (design §4.1, R1).

Moved verbatim out of ``agent.py`` (the line-budget extraction). Text-only by
contract: it runs on the woven question BEFORE image blocks are attached
(multimodal spec §3.6 decision 1), and history carries only text +
placeholders — ``_history_line`` enforces that shape defensively.
"""

from __future__ import annotations

from typing import Any

from pydocs_mcp.harness.ask_your_docs.prompts import rewrite_prompt


def _history_line(m) -> str:
    """One REWRITE_PROMPT history line — never a Python-list repr.

    History is text-by-construction (§3.6), but harden anyway: content-block
    messages flatten to their text parts plus "[image]" markers, so a
    multimodal message can never mangle the rewrite prompt.
    """
    content = m.content
    if isinstance(content, str):
        return f"{m.type}: {content}"
    parts = [
        b.get("text", "") if b.get("type") == "text" else "[image]"
        for b in content
        if isinstance(b, dict)
    ]
    return f"{m.type}: {' '.join(p for p in parts if p)}"


async def reformulate(
    llm: Any,
    history: list,
    question: str,
    *,
    rewrite_template: str | None = None,
) -> str:
    """Condense the last question + conversation into a standalone question.

    ``rewrite_template`` is the evaluation-harness override (a ``str.format``
    template with ``{history}`` / ``{question}``); ``None`` — the app's and
    CLI's only shape — renders the shipped ``rewrite_v1`` template.
    """
    if not history:
        return question
    lines = "\n".join(_history_line(m) for m in history)
    if rewrite_template is not None:
        prompt_text = rewrite_template.format(history=lines, question=question)
    else:
        prompt_text = rewrite_prompt(history=lines, question=question)
    reply = await llm.ainvoke(prompt_text)
    return str(reply.content).strip() or question


__all__ = ("reformulate",)
```

- [ ] **Step 4: Rewire `agent.py`**

Imports (top of file): delete `from langchain_openai import ChatOpenAI` and `rewrite_prompt` from the `prompts` import; replace `from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities, detect_capabilities` with `from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities`; add:

```python
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import BearerSource
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    LlmConnection,
    bearer_for_connection,
    build_chat_model,
    resolve_llm_connection,
    resolve_vision_capabilities,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig, VisionRule
```

Delete `_history_line` and `reformulate` (`agent.py:373-416`). Replace `build_agent` (`agent.py:269-370`) with:

```python
async def build_agent(
    workspace: str,
    model: str | None,
    base_url: str | None = None,
    pydocs_config: str | None = None,
    pydocs_cmd: list[str] | None = None,
    catalog: dict[str, list[str]] | None = None,
    *,
    architecture: str | None = None,
    config: AskYourDocsConfig | None = None,
    capabilities: ModelCapabilities | None = None,
    prompts: AskPrompts | None = None,
    tool_names: tuple[str, ...] | None = None,
    skill_override: Path | None = None,
    task_name: str | None = None,
    scope_pin: bool = True,
    subprocess_env: dict[str, str] | None = None,
    mcp_tools: list | None = None,
    connection: LlmConnection | None = None,
    bearer: BearerSource | None = None,
    vision_capabilities: ModelCapabilities | None = None,
):
    """Start pydocs-mcp over the workspace; return ``(agent, llm)``.

    Pass ``catalog`` (from :func:`ask_your_docs.catalog.workspace_catalog`) to
    reuse a scan the caller already did — this keeps the prompt's project list
    identical to whatever the UI shows. When omitted it is scanned here.

    ``pydocs_cmd`` defaults to ``[sys.executable, "-m", "pydocs_mcp"]`` so the
    MCP server subprocess always runs under the SAME interpreter as this app —
    no reliance on ``pydocs-mcp`` being on the child's PATH.

    ``connection`` (LLM-connection design §4.5) is the resolved endpoint /
    model / auth / vision record; when omitted it is resolved from ``model``
    and ``base_url`` over ``config.llm`` with an EMPTY environment tier, so
    this function never reads the environment itself (the binding stays
    deterministic). ``bearer`` defaults to the per-identity registry;
    ``capabilities`` / ``vision_capabilities`` inject the verdicts (the app
    detects once and shares them with its status line). ``architecture``
    overrides ``config.architecture`` (default "auto"). ``prompts`` is the
    evaluation-harness seam (:class:`AskPrompts`) — the app and CLI never pass
    it, so product behavior is byte-identical by default.

    The run-contract keywords (§9 stage 2, HARNESS-PRIVATE — the cross-repo
    seam is the run contract, never this signature): ``tool_names`` narrows
    the bound tool set within what the server advertises (fail-loud;
    ``None`` — the default — binds everything, byte-identical to before);
    ``skill_override`` / ``task_name`` fold the skill artifact's backbone
    (+ the task section and this harness's head) at the single assembly
    site; ``scope_pin`` ``False`` omits the corpus-pin interceptor (the
    searched dimension's seam); ``subprocess_env`` extends the serve
    subprocess environment (the binding's trace channel); ``mcp_tools`` hands
    over already-session-bound tools and skips the spawn entirely (the
    binding's held-session path). All defaults together reproduce the
    pre-stage-2 build byte-for-byte — the experiment's control arm is provable.
    """
    cfg = config or AskYourDocsConfig()
    if connection is None:
        connection = resolve_llm_connection(
            cfg.llm, {}, ConnectionOverride(base_url, model), ConnectionOverride(), config_path=pydocs_config
        )
    if connection.model is None:  # design E19 — before any tool or LLM construction
        raise AgentArchitectureError(
            "no model chosen; set ask_your_docs.llm.model, LLM_MODEL, --model or pick one in "
            "the Connection dialog"
        )
    bearer = bearer if bearer is not None else bearer_for_connection(connection)

    if mcp_tools is not None:
        # The caller owns the session/spawn lifecycle (the binding holds ONE
        # session for a whole traced run — the per-tool-call session default
        # would re-spawn the server and trip the trajectory-id reuse guard).
        # The caller also owns interceptor wiring via load_mcp_tools.
        tools = mcp_tools
    else:
        serve = serve_connection(workspace, pydocs_config, pydocs_cmd, subprocess_env)
        client = MultiServerMCPClient(
            {"pydocs": serve},
            tool_interceptors=[_intercept] if scope_pin else [],
        )
        tools = await client.get_tools()
    if tool_names is not None:
        tools = _select_bound_tools(tools, tool_names)

    # Fold the full project/package catalog into the prompt so the model can
    # pick the right project= / package= filters itself. Built from the bundle
    # files directly: in workspace mode, get_overview(project="") describes only
    # the default project, so it can't produce this listing.
    if catalog is None:
        catalog = await asyncio.to_thread(workspace_catalog, workspace)

    llm = build_chat_model(connection, bearer)
    name = architecture or cfg.architecture
    # Per-architecture system prompt by the directory convention (an
    # architecture without prompts/<name>/system_v1.j2 gets shared/). Note:
    # `auto` composes with its own (shared) system prompt even when it
    # delegates the graph — a per-arch system override applies when that
    # architecture is selected directly.
    #
    # Session-start context pack (ADR 0008): appended at this single assembly
    # site ONLY when serve.session_start_context.enabled — the gate returns
    # None when off, keeping the prompt byte-identical (the ablation phase's
    # control arm).
    session_start_pack = await build_session_start_context_for_agent_prompt(
        workspace, pydocs_config
    )
    skill_block = _resolved_skill_block(skill_override, task_name)
    prompt = _assemble_prompt(name, catalog, prompts, session_start_pack, skill_block)
    caps, vision_caps = await _capabilities_for(
        connection, bearer, cfg, capabilities, vision_capabilities
    )
    vision_llm = llm
    if connection.vision_rule is VisionRule.SEPARATE_MODEL:  # same endpoint, same bearer (R6)
        vision_llm = build_chat_model(connection, bearer, model=connection.vision_model)
    graph = _build_architecture(
        name,
        llm=llm,
        tools=tools,
        prompt=prompt,
        capabilities=caps,
        config=cfg,
        model=connection.model,
        vision_llm=vision_llm,
        vision_capabilities=vision_caps,
        bearer=bearer,
    )
    return graph, llm


async def _capabilities_for(
    connection: LlmConnection,
    bearer: BearerSource,
    cfg: AskYourDocsConfig,
    capabilities: ModelCapabilities | None,
    vision_capabilities: ModelCapabilities | None,
) -> tuple[ModelCapabilities, ModelCapabilities]:
    """Injected verdicts win (tests, the app's cache); otherwise the single §4.7 call site."""
    if capabilities is not None:
        return capabilities, vision_capabilities or capabilities
    main, vision = await resolve_vision_capabilities(connection, bearer, cfg.multimodal.detection)
    return main, vision_capabilities or vision
```

Also update the module docstring's example at `agent.py:3` to `agent, llm = await build_agent("~/pydocs-index", model="gpt-4o-mini")` (unchanged text; keep it). In `__init__.py`, set `"reformulate": "reformulation"` in `_LAZY`. In `app.py:18`, replace the import with:

```python
from pydocs_mcp.harness.ask_your_docs.agent import ask, build_agent, weave_attachments
from pydocs_mcp.harness.ask_your_docs.reformulation import reformulate
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/harness/ask_your_docs/ tests/test_config_ask_your_docs.py -q`
Expected: PASS. `wc -l python/pydocs_mcp/harness/ask_your_docs/agent.py` prints under 500 (the budget test enforces it).

- [ ] **Step 6: Lint and commit**

Run: `ruff check python/pydocs_mcp/harness/ask_your_docs tests/harness/ask_your_docs && ruff format --check python/pydocs_mcp/harness/ask_your_docs tests/harness/ask_your_docs && complexipy python/pydocs_mcp/harness/ask_your_docs --max-complexity-allowed 15 && vulture python/pydocs_mcp --min-confidence 80`
Expected: clean.

```bash
git checkout -- complexipy-snapshot.json
git add python/pydocs_mcp/harness/ask_your_docs/reformulation.py python/pydocs_mcp/harness/ask_your_docs/agent.py python/pydocs_mcp/harness/ask_your_docs/__init__.py python/pydocs_mcp/harness/ask_your_docs/app.py tests/harness/ask_your_docs/
git commit -m "harness(ask-your-docs): build_agent takes the connection and bearer; reformulation extracted; line budgets pinned"
```

---
## Task 10: The eval binding picks the block up from the pydocs config

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/binding.py` (`connection_block_for_binding`; `_build_and_execute`)
- Test: `tests/harness/ask_your_docs/test_binding.py`

**Interfaces:**
- Consumes: `resolve_llm_connection`, `ConnectionOverride`, `bearer_for_connection` (Task 3); `AppConfig.load`.
- Produces: `connection_block_for_binding(settings: AskYourDocsRunnerSettings) -> LlmConnectionConfig | None`; `_build_and_execute` passes `connection=` to `build_agent`. `AskYourDocsRunnerSettings` is unchanged (R8). Closes AC-27, AC-40 (binding half).

- [ ] **Step 1: Write the failing tests**

Append to `tests/harness/ask_your_docs/test_binding.py`:

```python
# ── LLM-connection design §4.11 (AC-27, AC-40 binding half) ──


def _token_block_yaml(tmp_path: Path) -> str:
    cfg = tmp_path / "pydocs.yaml"
    cfg.write_text(
        "ask_your_docs:\n"
        "  llm:\n"
        "    base_url: http://llm.internal/v1\n"
        "    auth:\n"
        "      token_url: http://localhost:8899/access-token\n"
        "    vision: true\n",
        encoding="utf-8",
    )
    return str(cfg)


def test_connection_block_prefers_the_arm_then_the_file(tmp_path: Path, monkeypatch) -> None:
    """R8 / D8: an arm-level harness.llm wins; else the pydocs_config file; else none."""
    for var in list(os.environ):
        if var.startswith("PYDOCS_"):
            monkeypatch.delenv(var, raising=False)
    control = binding.AskYourDocsRunnerSettings.model_validate(_settings(tmp_path))
    assert binding.connection_block_for_binding(control) is None
    from_file = binding.AskYourDocsRunnerSettings.model_validate(
        {**_settings(tmp_path), "pydocs_config": _token_block_yaml(tmp_path)}
    )
    block = binding.connection_block_for_binding(from_file)
    assert block is not None and block.auth is not None
    assert block.auth.token_url == "http://localhost:8899/access-token" and block.vision is True
    from_arm = binding.AskYourDocsRunnerSettings.model_validate(
        {
            **_settings(tmp_path),
            "pydocs_config": _token_block_yaml(tmp_path),
            "harness": {"llm": {"base_url": "http://arm/v1", "auth": {"api_key_env": "ARM_KEY"}}},
        }
    )
    arm_block = binding.connection_block_for_binding(from_arm)
    assert arm_block is not None and arm_block.base_url == "http://arm/v1"


async def test_build_and_execute_passes_the_resolved_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-27: the control arm resolves a no-block connection (byte identity); a token-service
    file resolves TOKEN_SERVICE; both keep settings.model / settings.base_url; AC-40: two
    executions share one registry bearer."""
    pytest.importorskip("langgraph")
    import contextlib as _contextlib

    import pydocs_mcp.harness.ask_your_docs.agent as agent_module
    from pydocs_mcp.harness.ask_your_docs.llm_connection import bearer_for_connection, clear_bearer_registry
    from pydocs_mcp.retrieval.config.ask_your_docs_models import AuthMode

    seen: list = []

    class _Graph:
        async def ainvoke(self, _state, _config):
            from langchain_core.messages import AIMessage

            return {"messages": [AIMessage("answer")]}

    async def _fake_build_agent(*_args, **kwargs):
        seen.append(kwargs["connection"])
        return _Graph(), object()

    @_contextlib.asynccontextmanager
    async def _fake_session_tools(_settings, _trace_env):
        yield []

    monkeypatch.setattr(agent_module, "build_agent", _fake_build_agent)
    monkeypatch.setattr(binding, "_serve_session_tools", _fake_session_tools)
    clear_bearer_registry()

    async def _run(settings_dict: dict) -> None:
        settings = binding.AskYourDocsRunnerSettings.model_validate(settings_dict)
        await binding._build_and_execute(
            sample=conformant_sample(), settings=settings, overrides=binding.PromptOverrides(),
            skill_override=None, task_name=None, trace_env={},
        )

    await _run({**_settings(tmp_path), "base_url": "http://x/v1"})
    control = seen[-1]
    assert control.block_present is False and control.auth_mode is AuthMode.ENV_KEY
    assert control.model == "fake-model" and control.base_url == "http://x/v1"
    token_settings = {**_settings(tmp_path), "pydocs_config": _token_block_yaml(tmp_path)}
    await _run(token_settings)
    await _run(token_settings)
    first, second = seen[-2:]
    assert first.auth_mode is AuthMode.TOKEN_SERVICE and first.model == "fake-model"
    assert first.base_url == "http://llm.internal/v1" and first.config_path == token_settings["pydocs_config"]
    assert bearer_for_connection(first) is bearer_for_connection(second)
    clear_bearer_registry()
```

(`os` is already imported? If not, add `import os` to the test module's imports.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_binding.py -q`
Expected: FAIL — `AttributeError: module ... binding has no attribute 'connection_block_for_binding'`; `KeyError: 'connection'`.

- [ ] **Step 3: Edit `binding.py`**

Add the module-level imports:

```python
from pydocs_mcp.harness.ask_your_docs.llm_connection import ConnectionOverride, resolve_llm_connection
from pydocs_mcp.retrieval.config.app_config import AppConfig
from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig
```

Add, right before `_build_and_execute`:

```python
def connection_block_for_binding(settings: AskYourDocsRunnerSettings) -> LlmConnectionConfig | None:
    """The ``ask_your_docs.llm`` block this run uses (design §4.11, R8/D8).

    An arm may pin a block under ``harness: {llm: ...}``; otherwise the file
    named by ``pydocs_config`` is loaded through the same ``AppConfig`` loader
    the serve subprocess runs on it — nothing new is read. No file, no block
    ⇒ ``None`` ⇒ the control arm's byte-identical build.
    """
    if settings.harness.llm is not None:
        return settings.harness.llm
    if settings.pydocs_config is None:
        return None
    return AppConfig.load(explicit_path=Path(settings.pydocs_config)).ask_your_docs.llm
```

In `_build_and_execute`, before `async with _serve_session_tools(...)`, add:

```python
    # WHY an empty environment tier: the binding is settings-in, trajectory-out;
    # OPENAI_BASE_URL / LLM_MODEL must not leak into an experiment arm.
    connection = resolve_llm_connection(
        connection_block_for_binding(settings),
        {},
        ConnectionOverride(settings.base_url, settings.model),
        ConnectionOverride(),
        config_path=settings.pydocs_config,
    )
```

and pass `connection=connection,` to the `build_agent(...)` call (after `mcp_tools=tools`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/harness/ask_your_docs/test_binding.py tests/harness/core -q`
Expected: PASS, including `test_delivery_map_digest_is_stable_and_documents_the_channels` (the digest literal is untouched).

- [ ] **Step 5: Lint and commit**

Run: `ruff check python/pydocs_mcp/harness/ask_your_docs/binding.py tests/harness/ask_your_docs/test_binding.py && ruff format --check python/pydocs_mcp/harness/ask_your_docs/binding.py tests/harness/ask_your_docs/test_binding.py`
Expected: clean.

```bash
git add python/pydocs_mcp/harness/ask_your_docs/binding.py tests/harness/ask_your_docs/test_binding.py
git commit -m "harness(ask-your-docs): the eval binding resolves the LLM connection from the pydocs config"
```

---
## Task 11: The status line, the Connection dialog and the page

**Files:**
- Create: `python/pydocs_mcp/harness/ask_your_docs/connection_dialog.py`
- Modify: `python/pydocs_mcp/harness/ask_your_docs/app.py` (whole file replaced below)
- Modify: `python/pydocs_mcp/harness/ask_your_docs/cli.py:52-53` (help text)
- Modify: `tests/harness/ask_your_docs/_connection_fakes.py` (`FakeBearer.fail_message`)
- Test: create `tests/harness/ask_your_docs/test_app_connection_dialog.py`; modify `test_cli_parser.py`

**Interfaces:**
- Consumes: `LlmConnection`, `ConnectionOverride`, `resolve_llm_connection`, `connection_identity`, `bearer_for_connection`, `resolve_vision_capabilities`, `run_connection_test` (Tasks 3, 5, 7); `cached_model_listing`, `clear_model_listing_cache`, `ModelListing` (Task 6); `BearerStatus`, `display_host`, `redact_bearer`, `TokenServiceError` (Task 2); `build_agent(connection=, bearer=, capabilities=, vision_capabilities=)` (Task 9); `reformulate` (Task 9).
- Produces: `connection_dialog.py` — `ConnectionActions(resolve, list_models, refresh_models, test, renew)`, `auth_cell`, `vision_cell`, `render_connection_status_line`, `open_connection_dialog`, keys `KEY_OPEN="connection_open"`, `KEY_BASE_URL="connection_dialog_base_url"`, `KEY_RENEW="connection_renew"`, `KEY_MODEL="connection_dialog_model"`, `KEY_MODEL_TEXT="connection_dialog_model_text"`, `KEY_REFRESH="connection_refresh_models"`, `KEY_TEST="connection_test"`, `KEY_APPLY="connection_apply"`, `STATE_DIALOG_OPEN="connection_dialog_open"`, `STATE_OVERRIDE="connection_override"`, `STATE_TEST_RESULT="connection_test_result"`, `ORIGIN_NOTE`, `CLEARTEXT_NOTE`, `NOT_CHOSEN`, `TOKEN_UNAVAILABLE`; `app.py` — `resolve_connection`, `page_connection`, `page_bearer`, `preflight_bearer`, `get_capabilities(model, base_url, config, identity, _connection, _bearer)`, `get_agent(workspace, model, base_url, config, identity, _connection, _bearer)`, `dialog_actions`; AppTest seams `connection_bearer`, `connection_list_models`, `connection_transport`. Closes AC-24, AC-25, AC-26, AC-31 (status-line half), AC-35 (send-loop half), AC-39 (status-line half), AC-43, AC-44, AC-45.

- [ ] **Step 1: Extend `FakeBearer`**

In `tests/harness/ask_your_docs/_connection_fakes.py`, give `FakeBearer.__init__` a `fail_message: str | None = None` keyword stored on `self.fail_message`, and make `current()` raise `TokenServiceError(self.fail_message or "token service http://localhost:8899/access-token unreachable after 3 attempts (last: ConnectError)")` when `self.fail` is true.

- [ ] **Step 2: Write the failing tests**

Create `tests/harness/ask_your_docs/test_app_connection_dialog.py`:

```python
"""The status line and the Connection dialog via AppTest (LLM-connection design §4.9 —
AC-25, AC-26, AC-31, AC-35 send-loop half, AC-39 status-line half, AC-43, AC-44, AC-45).

Three session-state seams keep the network out: connection_bearer, connection_list_models,
connection_transport. No test sends a question that builds an agent. A test that clicks
Apply asserts session state right after that run and never chains another run on the same
AppTest (the st.rerun() inside a dialog leaves stale dialog widget state, design §4.9)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

import streamlit as st
from streamlit.testing.v1 import AppTest

import pydocs_mcp.harness.ask_your_docs.app as appmod
from pydocs_mcp.harness.ask_your_docs.connection_dialog import (
    CLEARTEXT_NOTE,
    KEY_APPLY,
    KEY_BASE_URL,
    KEY_MODEL,
    KEY_MODEL_TEXT,
    KEY_OPEN,
    KEY_REFRESH,
    KEY_RENEW,
    KEY_TEST,
    ORIGIN_NOTE,
    STATE_OVERRIDE,
    STATE_TEST_RESULT,
    TOKEN_UNAVAILABLE,
)
from pydocs_mcp.harness.ask_your_docs.llm_connection import ConnectionOverride, clear_bearer_registry
from pydocs_mcp.harness.ask_your_docs.model_listing import clear_model_listing_cache
from pydocs_mcp.harness.ask_your_docs.multimodal import clear_detection_cache

from ._connection_fakes import FakeBearer, FakeModelsEndpoint, RecordingTransport

_RENEWED_AT = datetime(2026, 9, 5, 12, 3)
_IDS = ("model-a", "model-b")


def _write_config(tmp_path: Path, *, base_url="https://llm.internal/v1", model=None, auth="token", vision="true") -> str:
    lines = ["ask_your_docs:", "  llm:", f"    base_url: {base_url}"]
    if model:
        lines.append(f"    model: {model}")
    if auth == "token":
        lines += ["    auth:", "      token_url: http://localhost:8899/access-token"]
    elif auth == "env":
        lines += ["    auth:", "      api_key_env: LLM_KEY"]
    lines.append(f"    vision: {vision}")
    path = tmp_path / "pydocs.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


@pytest.fixture(autouse=True)
def _page_env(tmp_path: Path, monkeypatch):
    (tmp_path / "ws").mkdir()
    monkeypatch.setenv("PYDOCS_WORKSPACE", str(tmp_path / "ws"))
    for var in ("PYDOCS_CONFIG", "OPENAI_BASE_URL", "LLM_MODEL", "OPENAI_API_KEY", "LLM_KEY"):
        monkeypatch.delenv(var, raising=False)
    clear_bearer_registry()
    clear_model_listing_cache()
    clear_detection_cache()
    st.cache_resource.clear()  # the identity-keyed page caches persist across AppTest runs
    yield
    clear_bearer_registry()
    clear_model_listing_cache()
    clear_detection_cache()
    st.cache_resource.clear()


def _app(**seeds) -> AppTest:
    at = AppTest.from_file(appmod.__file__, default_timeout=180)
    at.session_state["connection_list_models"] = seeds.pop("listing", FakeModelsEndpoint(ids=_IDS))
    for key, value in seeds.items():
        at.session_state[key] = value
    return at


def _status_line(at: AppTest) -> str:
    return next(c.value for c in at.caption if " · " in c.value and "vision:" in c.value)


def _open_dialog(at: AppTest) -> AppTest:
    at.run()
    assert not at.exception, at.exception
    at.button(key=KEY_OPEN).click().run()
    assert not at.exception, at.exception
    return at


def test_status_line_and_dialog_for_the_no_block_default() -> None:
    """AC-25 (a)(b): the no-block page renders host, model, auth and the vision verdict on one
    caption; the dialog carries Base URL, the seeded model ids and Apply."""
    at = _app()
    at.run()
    assert not at.exception, at.exception
    assert _status_line(at) == "vendor default · gpt-4o-mini · no auth · vision: yes (static)"
    _open_dialog(at)
    assert at.text_input(key=KEY_BASE_URL).value == ""
    assert list(at.selectbox(key=KEY_MODEL).options) == list(_IDS)
    assert at.selectbox(key=KEY_MODEL).value == "model-a"  # the default id is not listed ⇒ first id
    assert at.button(key=KEY_APPLY) is not None
    assert not [b for b in at.button if b.key == KEY_RENEW]  # no token service ⇒ no Renew (d)


def test_apply_writes_the_session_override_and_the_next_page_reads_it() -> None:
    """AC-25 (e) / AC-26: Apply stores a ConnectionOverride; a fresh page resolves it (session
    only, never persisted)."""
    at = _open_dialog(_app())
    at.text_input(key=KEY_BASE_URL).input("http://other/v1")
    at.selectbox(key=KEY_MODEL).select("model-b")
    at.button(key=KEY_APPLY).click().run()
    assert at.session_state[STATE_OVERRIDE] == ConnectionOverride(base_url="http://other/v1", model="model-b")
    fresh = _app(**{STATE_OVERRIDE: ConnectionOverride(base_url="http://other/v1", model="model-b")})
    fresh.run()
    assert _status_line(fresh).startswith("other · model-b · ")


def test_listing_failure_falls_back_to_a_text_field() -> None:
    """AC-25 (c) / E6."""
    at = _open_dialog(_app(listing=FakeModelsEndpoint(error=RuntimeError("boom"))))
    assert at.text_input(key=KEY_MODEL_TEXT).value == "gpt-4o-mini"
    assert any("listing failed: RuntimeError: boom" in c.value for c in at.caption)
    assert not [s for s in at.selectbox if s.key == KEY_MODEL]


def test_token_service_cells_inline_last_four_and_renewal_time(tmp_path, monkeypatch) -> None:
    """AC-44 (a)(d) / AC-25 (d)(f): token …abcd 12:03 inline on the status line; the dialog's
    auth row shows both; Renew is present; model: not chosen opens the selectbox on the first id."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path))
    at = _app(connection_bearer=FakeBearer("tok-fixed-abcd", renewed_at=_RENEWED_AT))
    at.run()
    assert not at.exception, at.exception
    assert _status_line(at) == "llm.internal · model: not chosen · token …abcd 12:03 · vision: yes (configured)"
    at.button(key=KEY_OPEN).click().run()
    assert any(c.value == "token …abcd · renewed 12:03" for c in at.caption)
    assert at.button(key=KEY_RENEW) is not None
    assert at.selectbox(key=KEY_MODEL).value == "model-a"


def test_token_unavailable_is_visible_and_builds_nothing(tmp_path, monkeypatch) -> None:
    """AC-44 (b) / E1 / H3: a token service that is down shows on the status line at render."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path, model="main-a"))
    at = _app(connection_bearer=FakeBearer(fail=True))
    at.run()
    assert not at.exception, at.exception
    assert TOKEN_UNAVAILABLE in _status_line(at)
    assert "vision: ?" in _status_line(at)  # no verdict is computed on an unavailable bearer


def test_environment_key_cells(tmp_path, monkeypatch) -> None:
    """AC-44 (c): $VAR missing / $VAR set for an explicit auth.api_key_env."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path, model="main-a", auth="env"))
    at = _app()
    at.run()
    assert "$LLM_KEY missing" in _status_line(at)
    monkeypatch.setenv("LLM_KEY", "key-one-1111")
    again = _app()
    again.run()
    assert "$LLM_KEY set" in _status_line(again)


def test_origin_change_and_cleartext_notes(tmp_path, monkeypatch) -> None:
    """AC-31 / AC-39 (status-line halves): an override on another plain-http origin ends the auth
    cell with the origin note, preceded by ⚠ http; the configured https origin shows neither."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path, model="main-a"))
    monkeypatch.setenv("OPENAI_BASE_URL", "http://gpu-box:8000/v1")
    at = _app(connection_bearer=FakeBearer("tok-fixed-abcd", renewed_at=_RENEWED_AT))
    at.run()
    line = _status_line(at)
    assert line.startswith("gpu-box:8000 · main-a · token …abcd 12:03 ")
    assert line.split(" · vision:")[0].endswith(ORIGIN_NOTE)
    assert f"{CLEARTEXT_NOTE} {ORIGIN_NOTE}" in line
    monkeypatch.delenv("OPENAI_BASE_URL")
    clean = _app(connection_bearer=FakeBearer("tok-fixed-abcd", renewed_at=_RENEWED_AT))
    clean.run()
    assert ORIGIN_NOTE not in _status_line(clean) and CLEARTEXT_NOTE not in _status_line(clean)


def test_test_connection_passes_fails_redacted_and_follows_the_endpoint(tmp_path, monkeypatch) -> None:
    """AC-43 (a)(b)(c) / E11."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path, model="main-a"))
    ok = RecordingTransport([200], reply="OK")
    at = _open_dialog(_app(connection_bearer=FakeBearer("tok-fixed-abcd"), connection_transport=ok.transport))
    at.button(key=KEY_TEST).click().run()
    assert at.session_state[STATE_TEST_RESULT] == "test passed: OK"
    assert ok.authorizations() == ["Bearer tok-fixed-abcd"]
    rejected = RecordingTransport([401], echo_bearer_in_401=True)
    at = _open_dialog(_app(connection_bearer=FakeBearer("tok-fixed-abcd"), connection_transport=rejected.transport))
    at.button(key=KEY_TEST).click().run()
    result = at.session_state[STATE_TEST_RESULT]
    assert result.startswith("test failed: BearerRejectedError:")
    assert "tok-fixed-abcd" not in result and "rejected Bearer" not in result
    elsewhere = RecordingTransport([200], reply="OK")
    at = _open_dialog(_app(connection_bearer=FakeBearer("tok-fixed-abcd"), connection_transport=elsewhere.transport))
    at.text_input(key=KEY_BASE_URL).input("https://gpu-box:8443/v1")
    at.button(key=KEY_TEST).click().run()
    assert elsewhere.authorizations() == ["Bearer tok-fixed-abcd"]  # the bearer follows the endpoint (D3)
    assert str(elsewhere.requests[0].url).startswith("https://gpu-box:8443/v1")
    assert at.session_state[STATE_TEST_RESULT].endswith(ORIGIN_NOTE)


def test_refresh_and_renew_evict_the_listing(tmp_path, monkeypatch) -> None:
    """AC-45: refresh re-lists; Renew renews the bearer once and evicts the listing entry."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path, model="main-a"))
    endpoint = FakeModelsEndpoint(ids=_IDS)
    bearer = FakeBearer("tok-fixed-abcd", renewed_at=_RENEWED_AT)
    at = _open_dialog(_app(listing=endpoint, connection_bearer=bearer))
    assert endpoint.calls == 1
    at.button(key=KEY_REFRESH).click().run()
    assert endpoint.calls == 2
    renewing = _open_dialog(_app(listing=endpoint, connection_bearer=bearer))
    assert endpoint.calls == 2  # inside the TTL: the cached listing served the dialog
    renewing.button(key=KEY_RENEW).click().run()
    assert bearer.renewals == 1
    assert endpoint.calls == 3  # the entry was evicted, the reopened dialog listed again


def test_send_loop_boundary_renders_a_redacted_error_and_keeps_the_question(tmp_path, monkeypatch) -> None:
    """AC-35 (send-loop half): an auth failure reaches the page as st.error through redact_bearer
    plus the kept question — here the E1 leg (the 401 leg is pinned in test_chat_model_factory)."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path, model="main-a"))
    bearer = FakeBearer("tok-fixed-abcd", fail=True, fail_message="token service down; last sent Bearer tok-fixed-abcd")
    at = _app(connection_bearer=bearer)
    at.run()
    at.chat_input[0].set_value("what does Pool.acquire return?").run()
    assert not at.exception, at.exception
    errors = [e.value for e in at.error]
    assert errors and "tok-fixed-abcd" not in errors[0] and "…abcd" in errors[0]
    assert any("Your question (not sent): what does Pool.acquire return?" in i.value for i in at.info)
```

Append to `tests/harness/ask_your_docs/test_cli_parser.py`:

```python
def test_parser_rejects_an_api_key_flag() -> None:
    """AC-24 / D2: secrets never enter argv."""
    from pydocs_mcp.harness.ask_your_docs.cli import _build_parser

    with pytest.raises(SystemExit):
        _build_parser().parse_args(["--api-key", "sk-nope"])


def test_module_import_leaves_httpx_out() -> None:
    """AC-24: the launcher never pulls the connection stack."""
    import subprocess

    code = (
        "import sys\n"
        "import pydocs_mcp.harness.ask_your_docs.cli\n"
        "assert 'httpx' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
```

(add `import pytest` to that module's imports.)

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_app_connection_dialog.py tests/harness/ask_your_docs/test_cli_parser.py -q`
Expected: FAIL — `ModuleNotFoundError: ... connection_dialog`; the CLI rows pass already (argparse rejects unknown flags; httpx is not imported) — keep them as pins.

- [ ] **Step 4: Write `connection_dialog.py`**

```python
"""The sidebar's connection status line and the Connection dialog (design §4.9, R7).

Streamlit-only rendering: the page injects every action (``ConnectionActions``),
so this module never touches the event loop, the bearer registry or the caches.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import BearerStatus, display_host
from pydocs_mcp.harness.ask_your_docs.llm_connection import ConnectionOverride, LlmConnection
from pydocs_mcp.harness.ask_your_docs.model_listing import ModelListing
from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities
from pydocs_mcp.retrieval.config.ask_your_docs_models import AuthMode

# Widget keys — AppTest addresses widgets by key.
KEY_OPEN = "connection_open"
KEY_BASE_URL = "connection_dialog_base_url"
KEY_RENEW = "connection_renew"
KEY_MODEL = "connection_dialog_model"
KEY_MODEL_TEXT = "connection_dialog_model_text"
KEY_REFRESH = "connection_refresh_models"
KEY_TEST = "connection_test"
KEY_APPLY = "connection_apply"
# Session-state keys — never persisted; a browser reload starts from precedence again.
STATE_DIALOG_OPEN = "connection_dialog_open"
STATE_OVERRIDE = "connection_override"
STATE_TEST_RESULT = "connection_test_result"

ORIGIN_NOTE = "⚠ endpoint differs from ask_your_docs.llm.base_url"
CLEARTEXT_NOTE = "⚠ http"
NOT_CHOSEN = "model: not chosen"
TOKEN_UNAVAILABLE = "token unavailable ⚠"


@dataclass(frozen=True, slots=True)
class ConnectionActions:
    """What the dialog can do — injected by the page (the event loop stays in app.py)."""

    resolve: Callable[[ConnectionOverride], LlmConnection]  # a candidate from the dialog's values
    list_models: Callable[[LlmConnection], ModelListing]
    refresh_models: Callable[[LlmConnection], ModelListing]  # evict, then list again
    test: Callable[[LlmConnection], str]  # the caption text (design E11)
    renew: Callable[[], str | None]  # None on success, else the error text


def auth_cell(
    connection: LlmConnection, status: BearerStatus, *, bearer_error: str | None = None
) -> str:
    """The status line's auth cell (design §4.9): mode, last four inline, renewal time, the notes."""
    if bearer_error is not None:
        cell = TOKEN_UNAVAILABLE
    elif connection.auth_mode is AuthMode.TOKEN_SERVICE:
        cell = f"token …{status.last_four} {_renewed(status)}"
    elif connection.auth_mode is AuthMode.ENV_KEY:
        cell = _env_key_cell(connection, status)
    else:
        cell = "no auth"
    if connection.cleartext_bearer:  # H2
        cell += f" {CLEARTEXT_NOTE}"
    if connection.origin_changed:  # H1 — always last: a cell that carries both ENDS with it
        cell += f" {ORIGIN_NOTE}"
    return cell


def _renewed(status: BearerStatus) -> str:
    return status.renewed_at.strftime("%H:%M") if status.renewed_at else "pending"


def _env_key_cell(connection: LlmConnection, status: BearerStatus) -> str:
    if not connection.block_present and not status.last_four:
        return "no auth"  # the lenient no-block form with the variable unset
    state = "set" if status.last_four else "missing"
    return f"${connection.api_key_env} {state}"


def vision_cell(capabilities: ModelCapabilities | None) -> str:
    """Today's badge text with its source; ``?`` while no verdict exists (bearer unavailable)."""
    if capabilities is None:
        return "vision: ?"
    return f"vision: {'yes' if capabilities.multimodal else 'no'} ({capabilities.source})"


def render_connection_status_line(
    connection: LlmConnection,
    status: BearerStatus,
    capabilities: ModelCapabilities | None,
    *,
    bearer_error: str | None = None,
) -> None:
    """One ``st.caption``: host · model · auth · vision (the E1 text rides ``help=``)."""
    cells = [
        display_host(connection.base_url),
        connection.model or NOT_CHOSEN,
        auth_cell(connection, status, bearer_error=bearer_error),
        vision_cell(capabilities),
    ]
    st.caption(" · ".join(cells), help=bearer_error)


@st.dialog("Connection", width="small")
def open_connection_dialog(
    connection: LlmConnection,
    status: BearerStatus,
    actions: ConnectionActions,
    *,
    vision_text: str,
    bearer_error: str | None = None,
) -> None:
    """The dialog body (design §4.9): Base URL, auth row (+ Renew), Model, status, Test, Apply."""
    base_url = st.text_input(
        "Base URL",
        value=connection.base_url or "",
        key=KEY_BASE_URL,
        help="session only; the bearer is sent to whatever endpoint you enter",
    )
    _render_auth_row(connection, status, actions, bearer_error=bearer_error)
    candidate = actions.resolve(ConnectionOverride(base_url=base_url or None))
    listing = actions.list_models(candidate)
    model = _render_model_picker(candidate, listing, actions)
    st.caption(f"{_listing_caption(listing)} · {vision_text}")
    if st.button("Test connection", key=KEY_TEST):
        chosen = actions.resolve(ConnectionOverride(base_url=base_url or None, model=model or None))
        result = actions.test(chosen)
        st.session_state[STATE_TEST_RESULT] = (
            f"{result} {ORIGIN_NOTE}" if chosen.origin_changed else result
        )
    if STATE_TEST_RESULT in st.session_state:
        st.caption(st.session_state[STATE_TEST_RESULT])
    if st.button("Apply", key=KEY_APPLY):
        st.session_state[STATE_OVERRIDE] = ConnectionOverride(
            base_url=base_url or None, model=model or None
        )
        st.session_state[STATE_DIALOG_OPEN] = False
        st.rerun()


def _render_auth_row(
    connection: LlmConnection,
    status: BearerStatus,
    actions: ConnectionActions,
    *,
    bearer_error: str | None,
) -> None:
    left, right = st.columns([4, 1])
    left.caption(_auth_row_text(connection, status, bearer_error=bearer_error))
    # Renew exists only for a configured token service (D7/R7): an environment
    # key is re-read on every request already.
    if connection.auth_mode is AuthMode.TOKEN_SERVICE and right.button("Renew", key=KEY_RENEW):
        error = actions.renew()
        if error is not None:
            st.session_state[STATE_TEST_RESULT] = f"renew failed: {error}"
        st.rerun()


def _auth_row_text(
    connection: LlmConnection, status: BearerStatus, *, bearer_error: str | None
) -> str:
    if connection.auth_mode is AuthMode.TOKEN_SERVICE and bearer_error is None:
        return f"token …{status.last_four} · renewed {_renewed(status)}"  # D4: both inline
    return auth_cell(connection, status, bearer_error=bearer_error)


def _render_model_picker(
    candidate: LlmConnection, listing: ModelListing, actions: ConnectionActions
) -> str:
    if listing.error is not None:  # E6: the text-field fallback
        return st.text_input("Model", value=candidate.model or "", key=KEY_MODEL_TEXT)
    ids = list(listing.model_ids)
    left, right = st.columns([5, 1])
    index = ids.index(candidate.model) if candidate.model in ids else 0
    chosen = left.selectbox("Model", options=ids, index=index, key=KEY_MODEL) if ids else ""
    if right.button("↻", key=KEY_REFRESH, help="refresh the model list"):
        actions.refresh_models(candidate)
        st.rerun()
    return chosen or ""


def _listing_caption(listing: ModelListing) -> str:
    if listing.error is not None:
        return f"listing failed: {listing.error}"
    return f"{len(listing.model_ids)} models listed"


__all__ = (
    "ConnectionActions",
    "auth_cell",
    "open_connection_dialog",
    "render_connection_status_line",
    "vision_cell",
)
```

- [ ] **Step 5: Replace `app.py`**

```python
"""Streamlit chat UI for the ask-your-docs agent.

Launched by the ``harness-ask-your-docs`` CLI (``harness.ask_your_docs.cli``).
Workspace and config prefill from env: PYDOCS_WORKSPACE, PYDOCS_CONFIG. The
chat model's endpoint, model and bearer come from the LLM connection (design
§4.9): ``ask_your_docs.llm`` < OPENAI_BASE_URL / LLM_MODEL < --base-url /
--model (copied into the environment by the CLI) < the Connection dialog
(session only).

AppTest seams (session state, tests only): ``connection_bearer`` (a
BearerSource used instead of the registry), ``connection_list_models`` (the
listing seam) and ``connection_transport`` (the httpx transport handed to the
Test-connection helper).
"""

from __future__ import annotations

import asyncio
import base64
import os
import threading
from pathlib import Path

import openai
import streamlit as st

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.harness.ask_your_docs.agent import ask, build_agent, weave_attachments
from pydocs_mcp.harness.ask_your_docs.attachments import (
    ImageAttachment,
    text_only_policy,
    update_image_store,
    validate_attachment,
)
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerSource,
    TokenServiceError,
    redact_bearer,
)
from pydocs_mcp.harness.ask_your_docs.catalog import workspace_catalog
from pydocs_mcp.harness.ask_your_docs.connection_dialog import (
    KEY_OPEN,
    STATE_DIALOG_OPEN,
    STATE_OVERRIDE,
    ConnectionActions,
    open_connection_dialog,
    render_connection_status_line,
    vision_cell,
)
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    LlmConnection,
    bearer_for_connection,
    connection_identity,
    resolve_llm_connection,
    resolve_vision_capabilities,
    run_connection_test,
)
from pydocs_mcp.harness.ask_your_docs.model_listing import (
    ModelListing,
    cached_model_listing,
    clear_model_listing_cache,
)
from pydocs_mcp.harness.ask_your_docs.reformulation import reformulate
from pydocs_mcp.harness.ask_your_docs.theme import (
    current_palette,
    render_appearance_toggle,
    theme_css,
)
from pydocs_mcp.retrieval.config.app_config import AppConfig
from pydocs_mcp.retrieval.config.ask_your_docs_models import AuthMode

st.set_page_config(
    page_title="ask your docs",
    page_icon="✦",
    layout="centered",
    # Keep the sidebar (and its page-navigation menu: chat / graph) open on load.
    initial_sidebar_state="expanded",
)


@st.cache_resource
def event_loop() -> asyncio.AbstractEventLoop:
    # The agent's async work must live on ONE loop across Streamlit reruns.
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return loop


def run(coro):
    return asyncio.run_coroutine_threadsafe(coro, event_loop()).result()


@st.cache_resource
def load_catalog(workspace: str) -> dict[str, list[str]]:
    # Cached per workspace (no ttl) and shared with the agent prompt, so the
    # pickers and the model always see the same projects. A newly indexed repo
    # appears on restart. Read-only — never mutates the bundles.
    return workspace_catalog(workspace)


@st.cache_resource
def load_ayd_config(config: str | None):
    # One YAML file configures both the pydocs-mcp subprocess and the agent
    # (spec §3.5): the same PYDOCS_CONFIG path, loaded through AppConfig
    # layering (defaults → overlay → PYDOCS_ASK_YOUR_DOCS__* env).
    return AppConfig.load(explicit_path=Path(config) if config else None).ask_your_docs


def resolve_connection(config: str | None, dialog: ConnectionOverride) -> LlmConnection:
    """The page's connection: YAML < environment (the CLI copied its flags there) < dialog."""
    return resolve_llm_connection(
        load_ayd_config(config).llm, os.environ, ConnectionOverride(), dialog, config_path=config
    )


def page_connection(config: str | None) -> LlmConnection:
    override = st.session_state.get(STATE_OVERRIDE) or ConnectionOverride()
    return resolve_connection(config, override)


def page_bearer(connection: LlmConnection) -> BearerSource:
    """The registry's bearer for the identity — one per process (design §4.4) — or the test seam."""
    seeded = st.session_state.get("connection_bearer")
    return seeded if seeded is not None else bearer_for_connection(connection)


def preflight_bearer(connection: LlmConnection, bearer: BearerSource) -> str | None:
    """Fetch the token once at render so the status line is honest (H3); the E1 text on failure."""
    if connection.auth_mode is not AuthMode.TOKEN_SERVICE:
        return None
    try:
        bearer.current()
    except TokenServiceError as exc:
        return str(exc)
    return None


@st.cache_resource
def get_capabilities(
    model: str,
    base_url: str | None,
    config: str | None,
    identity: tuple,
    _connection: LlmConnection,
    _bearer: BearerSource,
):
    # One verdict pair per (model, base_url, config, auth identity): the status
    # line, text_only_policy and the agent read the same one (design §4.7).
    # Underscore-prefixed objects are not hashed; the key carries no secret.
    cfg = load_ayd_config(config)
    return run(resolve_vision_capabilities(_connection, _bearer, cfg.multimodal.detection))


@st.cache_resource
def get_agent(
    workspace: str,
    model: str,
    base_url: str | None,
    config: str | None,
    identity: tuple,
    _connection: LlmConnection,
    _bearer: BearerSource,
):
    # Keyed on the auth identity, never on a token: Renew mutates the bearer's
    # cache and leaves this entry alone (R7); a new endpoint or model builds anew.
    main_caps, vision_caps = get_capabilities(model, base_url, config, identity, _connection, _bearer)
    return run(
        build_agent(
            workspace,
            model,
            base_url,
            config,
            catalog=load_catalog(workspace),
            config=load_ayd_config(config),
            capabilities=main_caps,
            vision_capabilities=vision_caps,
            connection=_connection,
            bearer=_bearer,
        )
    )


def dialog_actions(
    config: str | None, connection: LlmConnection, bearer: BearerSource
) -> ConnectionActions:
    """The page-side callbacks the dialog needs — the event loop and the caches stay here."""
    list_seam = st.session_state.get("connection_list_models")
    transport = st.session_state.get("connection_transport")

    def _list(candidate: LlmConnection) -> ModelListing:
        try:
            return run(cached_model_listing(candidate, page_bearer(candidate), list_models=list_seam))
        except PydocsMCPError as exc:  # a bearer failure (E1/E4/E5): shown in the caption
            return ModelListing((), redact_bearer(str(exc), bearer), 0.0)

    def _refresh(candidate: LlmConnection) -> ModelListing:
        clear_model_listing_cache(candidate)
        return _list(candidate)

    def _test(candidate: LlmConnection) -> str:
        return run(run_connection_test(candidate, page_bearer(candidate), transport=transport))

    def _renew() -> str | None:
        try:
            bearer.renew(bearer.peek() or None, reason="manual")
        except TokenServiceError as exc:
            return str(exc)
        clear_model_listing_cache(connection)
        return None

    return ConnectionActions(
        resolve=lambda override: resolve_connection(config, override),
        list_models=_list,
        refresh_models=_refresh,
        test=_test,
        renew=_renew,
    )


_CODE_CHOICES = {"All code": "all", "Own code": "project", "Dependencies": "deps"}

with st.sidebar:
    st.markdown('<div class="side-label">Appearance</div>', unsafe_allow_html=True)
    render_appearance_toggle()

    st.markdown('<div class="side-label">Connection</div>', unsafe_allow_html=True)
    workspace = st.text_input("Workspace", os.environ.get("PYDOCS_WORKSPACE", ""))
    config_path = st.text_input("pydocs config (optional)", os.environ.get("PYDOCS_CONFIG", "")) or None
    connection = page_connection(config_path)
    bearer = page_bearer(connection)
    identity = connection_identity(connection)
    bearer_error = preflight_bearer(connection, bearer)
    vision_caps = None
    if connection.model and bearer_error is None:
        _main_caps, vision_caps = get_capabilities(
            connection.model, connection.base_url, config_path, identity, connection, bearer
        )
    render_connection_status_line(
        connection, bearer.describe(), vision_caps, bearer_error=bearer_error
    )
    # State-driven opener: AppTest always runs the full script, so a transient
    # `if st.button(...)` alone would never re-enter the dialog on the next run.
    if st.button("Connection", key=KEY_OPEN):
        st.session_state[STATE_DIALOG_OPEN] = True
    if st.session_state.get(STATE_DIALOG_OPEN):
        open_connection_dialog(
            connection,
            bearer.describe(),
            dialog_actions(config_path, connection, bearer),
            vision_text=vision_cell(vision_caps),
            bearer_error=bearer_error,
        )
    st.caption("Point Workspace at a folder of pydocs-mcp index bundles.")

    # Scope pickers. The project pin is forced onto every tool call; the package
    # and own-vs-dependency pins constrain the search tools (see agent._intercept).
    project_pin = package_pin = ""
    code_pin = "all"
    if workspace:
        try:
            projects = load_catalog(workspace)
        except Exception as exc:  # unreadable dir, no bundles, corrupt db
            projects = {}
            st.warning(f"Couldn't scan workspace: {exc}")
        if projects:
            st.markdown('<div class="side-label">Scope</div>', unsafe_allow_html=True)
            picked = st.selectbox("Project", ["All projects", *projects], key="scope_project")
            project_pin = "" if picked == "All projects" else picked
            code_pin = _CODE_CHOICES[
                st.radio("Code", list(_CODE_CHOICES), horizontal=True, key="scope_code")
            ]
            pool = sorted(
                {
                    p
                    for name, pkgs in projects.items()
                    if not project_pin or name == project_pin
                    for p in pkgs
                }
            )
            # No picker when own code is pinned (packages are dependencies) or
            # the pinned slice has no dependency packages indexed.
            if code_pin != "project" and pool:
                picked = st.selectbox("Package", ["All packages", *pool], key="scope_package")
                package_pin = "" if picked == "All packages" else picked
            st.caption("Searches run only inside this scope.")

st.markdown(theme_css(current_palette()), unsafe_allow_html=True)
st.markdown(
    '<div class="brand">ask your <span class="accent">docs</span></div>'
    '<div class="brand-sub">grounded answers from your indexed code and docs</div>',
    unsafe_allow_html=True,
)

if not workspace:
    st.markdown(
        """<div class="empty">
        <div class="empty-title">Point me at your indexed repos</div>
        <div>Set a <b>Workspace</b> in the sidebar — a folder of pydocs-mcp
        <code>.db</code> / <code>.tq</code> bundles — then ask things like:</div>
        <div class="eg">how does routing work?</div>
        <div class="eg">what does IndexStorePort.load return?</div>
        <div class="eg">who calls BaseIndexStore.append?</div>
        </div>""",
        unsafe_allow_html=True,
    )
    st.stop()

if "messages" not in st.session_state:
    st.session_state.messages, st.session_state.history = [], []

for role, text in st.session_state.messages:
    with st.chat_message(role):
        st.markdown(text)

attached = st.session_state.setdefault("attached", [])
if attached:
    st.caption("Attached from the graph:")
    cols = st.columns(len(attached) + 1)
    for i, sym in enumerate(list(attached)):
        if cols[i].button(f"✕ {sym.rsplit('.', 1)[-1]}", key=f"chip_{sym}"):
            attached.remove(sym)
            st.rerun()
    if cols[-1].button("clear all", key="chip_clear"):
        attached.clear()
        st.rerun()

# Image chips from the last image-bearing question — visually distinct from
# the symbol-name buttons above (🖼 markdown pills, not buttons). Pre-send
# removal is the chat_input file widget's own ✕ (accept_file arrives
# atomically with the question, spec §4.7).
image_chips = st.session_state.setdefault("image_chips", [])
if image_chips:
    st.caption("Images attached to the last question:")
    st.markdown(" ".join(f"`🖼 {name}`" for name in image_chips))


def _collect_images(files, images_cfg) -> tuple[ImageAttachment, ...]:
    """UploadedFiles → validated ImageAttachments; violations render an
    inline error chip and drop the offending file (spec §3.6)."""
    if len(files) > images_cfg.max_per_turn:
        st.warning(
            f"only the first {images_cfg.max_per_turn} images were kept (images.max_per_turn)"
        )
    collected: list[ImageAttachment] = []
    for f in files[: images_cfg.max_per_turn]:
        att = ImageAttachment(
            name=f.name,
            media_type=f.type or "application/octet-stream",
            data_b64=base64.b64encode(f.getvalue()).decode(),
        )
        try:
            validate_attachment(att, images_cfg)
        except ValueError as exc:
            st.error(str(exc))
            continue
        collected.append(att)
    return tuple(collected)


def _refuse(question: str, message: str) -> None:
    """Fail loudly BEFORE any LLM call: nothing is sent, the question stays visible."""
    st.error(redact_bearer(message, bearer))
    st.info(f"Your question (not sent): {question}")
    st.stop()


if submission := st.chat_input(
    "Ask about your indexed projects…",
    accept_file="multiple",
    file_type=["png", "jpg", "jpeg", "webp", "gif"],
):
    question = submission.text or ""
    if bearer_error is not None:
        _refuse(question, bearer_error)
    if connection.model is None:
        _refuse(question, "No model chosen — open Connection and pick one (design E19).")
    ayd_cfg = load_ayd_config(config_path)
    images = _collect_images(list(submission.files or ()), ayd_cfg.images)
    verdict = text_only_policy(images, vision_caps, ayd_cfg.multimodal, model=connection.model)
    if verdict is not None and verdict.kind == "reject":
        _refuse(question, verdict.message)  # spec §3.8: the policy check, not an exception
    transient_note = ""
    if verdict is not None and verdict.kind == "describe":
        st.warning("The model cannot see the attached image(s); answering from text only.")
        # The cannot-see note rides ask()'s transient_note (attached AFTER
        # reformulation, never persisted) — the scope-pin pattern.
        transient_note = verdict.message
        images = ()
    st.session_state.image_chips = [att.name for att in images]
    # Session image store: bytes from recent turns stay reinspectable by the
    # reinspect_images tool (history itself keeps only the placeholder).
    image_store = st.session_state.setdefault("image_store", {})
    # Snapshot BEFORE folding this turn's images: the current attachment was
    # just seen (inline) or extracted (vision node) — only LATER questions
    # need to reinspect it, and same-turn re-reads would be wasted vision
    # calls (necessity gating).
    prior_images = dict(image_store)
    update_image_store(image_store, images, retention=ayd_cfg.images.session_retention)
    shown = question + ("\n\n" + " ".join(f"`🖼 {att.name}`" for att in images) if images else "")
    st.session_state.messages.append(("user", shown))
    with st.chat_message("user"):
        st.markdown(shown)
    with st.chat_message("assistant"), st.spinner("searching your docs…"):
        # A fresh immutable snapshot per question — not shared across sessions.
        scope = {"project": project_pin, "package": package_pin, "code": code_pin}
        woven = weave_attachments(attached, question)
        st.session_state.attached = []
        try:
            agent, llm = get_agent(
                workspace, connection.model, connection.base_url, config_path, identity, connection, bearer
            )
            # reformulate is text-only by contract (§3.6): it runs on the woven
            # question BEFORE image blocks are attached.
            standalone = run(reformulate(llm, st.session_state.history, woven))
            answer = run(
                ask(
                    agent,
                    st.session_state.history,
                    standalone,
                    scope=scope,
                    images=images,
                    image_store=prior_images,  # PRIOR turns only — see snapshot note above
                    transient_note=transient_note,
                )
            )
        except (PydocsMCPError, openai.APIError) as exc:
            # The one boundary between an auth failure and the browser (H4):
            # never the raw SDK message, which can carry the presented bearer.
            _refuse(question, str(exc))
        st.markdown(answer)
    st.session_state.messages.append(("assistant", answer))
```

- [ ] **Step 6: Edit the CLI help text**

In `cli.py`, add `from pydocs_mcp.retrieval.config.ask_your_docs_models import _DEFAULT_MODEL` and replace lines 52–53 with:

```python
    parser.add_argument(
        "--model",
        help=(
            f"OpenAI-format model id (default without an ask_your_docs.llm block: {_DEFAULT_MODEL}); "
            "overrides ask_your_docs.llm.model and LLM_MODEL"
        ),
    )
    parser.add_argument(
        "--base-url",
        help="OpenAI-format base URL; overrides ask_your_docs.llm.base_url and OPENAI_BASE_URL",
    )
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/harness/ask_your_docs/ tests/test_doc_conformance.py -q`
Expected: PASS — including `test_app_attachment.py` and `test_app_image_attachment.py` (the `vision: yes (static)` scan finds the merged status line) and the documented-invocation checks in `test_doc_conformance.py`. If `at.chat_input[0].set_value(...)` cannot submit through the `accept_file` widget on the installed Streamlit, replace that one assertion path with a seeded `st.session_state["connection_bearer"]` whose `describe()` is called on render and assert the `TOKEN_UNAVAILABLE` status cell instead — and say so in the commit message.

- [ ] **Step 8: Lint, budgets, commit**

Run: `ruff check python/pydocs_mcp/harness/ask_your_docs tests/harness/ask_your_docs && ruff format --check python/pydocs_mcp/harness/ask_your_docs tests/harness/ask_your_docs && complexipy python/pydocs_mcp/harness/ask_your_docs --max-complexity-allowed 15 && vulture python/pydocs_mcp --min-confidence 80 && pytest tests/harness/ask_your_docs/test_module_line_budgets.py -q`
Expected: clean; both `app.py` and `connection_dialog.py` under 500 lines.

```bash
git checkout -- complexipy-snapshot.json
git add python/pydocs_mcp/harness/ask_your_docs/connection_dialog.py python/pydocs_mcp/harness/ask_your_docs/app.py python/pydocs_mcp/harness/ask_your_docs/cli.py tests/harness/ask_your_docs/
git commit -m "harness(ask-your-docs): connection status line + dialog (model discovery, Renew, Test connection); page keyed on the auth identity"
```

---
## Task 12: The example config, the README, the CHANGELOG and the full gate

**Files:**
- Modify: `examples/harness/ask_your_docs_agent/configs/serve_cpu_openvino.yaml` (whole file below)
- Modify: `examples/harness/ask_your_docs_agent/README.md:31-32`, `:52-54`, `:100-106`
- Modify: `CHANGELOG.md` (under `## [0.6.0] — Unreleased`)
- Unchanged by rule: `examples/harness/ask_your_docs_agent/configs/index_gpu.yaml` (R10/D10, byte for byte)

**Interfaces:**
- Consumes: everything above.
- Produces: the documented block (spec §5.3–§5.5). Closes AC-30 and the end-to-end gate.

- [ ] **Step 1: Replace `serve_cpu_openvino.yaml`**

```yaml
# SERVE-time config — CPU inference via OpenVINO. Only the QUERY is embedded
# at serve time (one short text per search), so CPU is plenty.
#
# Usage:
#   harness-ask-your-docs --workspace ~/pydocs-index --config configs/serve_cpu_openvino.yaml
#
# DO NOT INDEX with this file: `backend` folds into the chunk cache identity,
# so re-indexing under it would re-embed everything on CPU. Index with
# index_gpu.yaml; serve with this one.
embedding:
  provider: sentence_transformers
  # Low on GPU memory? Swap both files to the 0.6B sibling:
  #   model_name: Qwen/Qwen3-Embedding-0.6B
  #   dim: 1024
  model_name: Qwen/Qwen3-Embedding-4B   # must match index_gpu.yaml
  dim: 2560                             # must match index_gpu.yaml
  max_seq_length: 2048                  # must match index_gpu.yaml
  backend: openvino          # sentence-transformers auto-exports on first load
  query_prompt_name: query   # Qwen3 embeddings are asymmetric — use its query prompt

# The chat model: one OpenAI-format endpoint behind an internal token service.
# The token is fetched on the first question, renewed on a 401 (the request is
# retried once with the new token), and never written to disk or shown in
# full. Override the endpoint or model per launch with --base-url / --model,
# or per session in the Connection dialog; the status line says when the
# endpoint differs from this file. Models are discovered from the endpoint.
ask_your_docs:
  llm:
    base_url: http://llm.internal/v1
    auth:
      token_url: http://localhost:8899/access-token
    vision: true                        # the served model is multimodal; no probe
  # architecture: auto                  # auto | text_react | inline | vision_subagent
  # multimodal:
  #   preferred_architecture: inline    # what "auto" builds on a vision model
  #   text_only_fallback: reject        # reject | describe
  # images:
  #   max_per_turn: 3
  #   max_bytes: 5000000
  #   max_reinspect_per_turn: 2
```

- [ ] **Step 2: Edit the README**

`examples/harness/ask_your_docs_agent/README.md:31-32` becomes:

```markdown
- **Your LLM**: any model served over the OpenAI API protocol, hosted or local,
  via the base URL — with an internal token service or an environment-variable
  key as the bearer, and the endpoint's models listed in the UI.
```

`:52-54` — replace "the six pydocs-mcp tools (`search_codebase`, `get_symbol`, `get_references`, `get_context`, `get_overview`, `get_why`)" with "the nine pydocs-mcp tools (`search_codebase`, `get_symbol`, `get_references`, `get_context`, `get_overview`, `get_why`, `grep`, `glob`, `read_file`)".

`:100-106` (the flags/env paragraph) becomes:

```markdown
`harness-ask-your-docs` launches the Streamlit UI. The chat model's endpoint
comes from the `ask_your_docs.llm` block of the same YAML the `--config` flag
points at: `base_url` (an OpenAI-format endpoint), `auth.token_url` (an
internal token service whose response body is the bearer; set `token_field`
when the body is JSON) or `auth.api_key_env` (the name of the environment
variable holding a key), and `vision` (`true`, `false`, `null` to detect, or
`{model: …}` to send images to a second model on the same endpoint).
`OPENAI_BASE_URL` / `LLM_MODEL` override the YAML, `--base-url` / `--model`
override those, and the sidebar's Connection dialog overrides everything for
the session — it lists the endpoint's models, renews the token and tests the
connection; the status line says when the session's endpoint differs from the
configured one. With no `llm` block the agent uses the vendor default endpoint
and `OPENAI_API_KEY`, as before. Keys are never put in YAML or on the command
line. `--workspace` / `--config` / `--port` and `PYDOCS_WORKSPACE` /
`PYDOCS_CONFIG` prefill the Workspace and config inputs; anything after `--`
is forwarded to `streamlit run` (e.g. `-- --server.headless true`). Answers
cite `project` + `package.module` and render code in fenced blocks.
```

- [ ] **Step 3: Add the CHANGELOG entry**

Under `## [0.6.0] — Unreleased` → `### Added`, append:

```markdown
- **Ask-your-docs LLM connection.** One `ask_your_docs.llm` YAML block configures
  the chat model's OpenAI-format endpoint, its bearer (an internal token service
  renewed on `401` with the request retried once, or a named environment
  variable), and vision (`true` / `false` / detect / a second model on the same
  endpoint). The sidebar's four connection inputs become one status line and a
  Connection dialog that lists the endpoint's models, renews the token and
  tests the connection. Both capability probes now use the agent's credential
  (without a block, the endpoint probe therefore carries `OPENAI_API_KEY` when
  that variable is set). The bearer follows the effective endpoint; an override
  on another origin, or a plain-http non-loopback endpoint, is flagged on the
  status line and in one log line. No block ⇒ otherwise unchanged behavior.
  Design: `docs/superpowers/specs/2026-09-05-ask-your-docs-llm-connection-design.md`.
```

and under `### Changed` (create the heading after `### Added` if the release has none):

```markdown
- **`ask_your_docs.multimodal.preferred_architecture` default `vision_subagent` →
  `inline`.** A multimodal main model now answers and sees in one prompt; set
  `vision_subagent` back for the separate describe hop.
```

- [ ] **Step 4: Run the README audit and the doc-conformance suite**

Run:

```bash
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" -not -path "*/node_modules/*" -not -path "*/.git/*" | xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+"
```

Expected: no output. Then `grep -niE "vllm|ollama|litellm|openrouter" examples/harness/ask_your_docs_agent/README.md examples/harness/ask_your_docs_agent/configs/serve_cpu_openvino.yaml CHANGELOG.md` prints nothing for the edited paragraphs (AC-30), and `pytest tests/test_doc_conformance.py -q` passes. `git diff --stat examples/harness/ask_your_docs_agent/configs/index_gpu.yaml` prints nothing.

- [ ] **Step 5: Run the full gate**

```bash
ruff check python/ tests/ benchmarks/
ruff format --check python/ tests/ benchmarks/
mypy python/pydocs_mcp
complexipy python/pydocs_mcp --max-complexity-allowed 15
vulture python/pydocs_mcp --min-confidence 80
pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90 -q
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q
git checkout -- complexipy-snapshot.json
```

Expected: every command green (coverage ≥ 90 %); the two pre-existing `benchmarks/tests` registry-population failures, if still present at this branch's HEAD, are unrelated (version skew) — report them, do not fix them here.

- [ ] **Step 6: Commit**

```bash
git add examples/harness/ask_your_docs_agent/configs/serve_cpu_openvino.yaml examples/harness/ask_your_docs_agent/README.md CHANGELOG.md
git commit -m "docs(ask-your-docs): serve config with the token-service block, README connection paragraph, CHANGELOG"
```

---

## Self-review

**Spec coverage.** R1–R10 → Tasks 1 (R2 keys, R6 default flip), 2 (R4 lifecycle, R9 errors), 3 (R3 precedence), 5 (the factory, R1 consumers), 6 (R5 discovery), 7–8 (R6 routing), 9–10 (R8 binding, byte identity), 11 (R7 UI), 12 (R10 documents). H1/H2 → Task 3 (flags + logs) and Task 11 (status-line notes); H3 → Tasks 2 (compare-and-swap, rate limit), 4 (propagation, no caching), 9 (build-time failure); H4 → Tasks 2 (`redact_bearer`, `translate_auth_errors`, `display_url`, E2/E3 shapes), 7 (tool-result redaction), 11 (send-loop boundary). Errors E1–E19 → E1/E2/E3 Task 2; E4 Tasks 2/5; E5 Task 2; E6 Task 6; E7/E8/E14/E16/E17 Task 1; E9 Task 3; E10 unchanged (`text_only_policy`); E11 Tasks 5/11; E12/E13 Task 8; E15/E18 Tasks 3/11; E19 Task 9. Acceptance criteria AC-1…AC-45 are each named on the task that closes them (AC-10 Task 2; AC-21/22 Task 1; AC-27 Task 10; AC-29 Task 9; AC-30 Task 12; AC-38 Task 5). Open decisions O1 (timeout / max_retries YAML keys) and O2 (`extra_body`) stay open: nothing here adds those keys.

**Placeholder scan.** No TBD/TODO; every code step shows the code; the only "unchanged" elision is the two prefix tables in Task 4, named by line range.

**Type consistency.** `BearerSource.renew(rejected=None, *, reason="manual")` is the same in Task 2's Protocol, the fakes and every caller (Tasks 2, 11); `connection_identity` returns the 3-tuple everywhere (Tasks 3, 6, 11); `build_chat_model(..., tolerate_missing_key=, transport=)` matches Tasks 5, 6, 11; `detect_capabilities(..., connection=, bearer=, list_models=, probe_llm=)` matches Tasks 4, 6, 7; `AgentBuildContext(vision_llm=, vision_capabilities=, bearer=)` matches Tasks 7–9; `run_connection_test` is the one name for the spec's "test_connection" helper (Tasks 5, 11); `get_agent` / `get_capabilities` carry `(…, identity, _connection, _bearer)` in Task 11 and the spec §4.9.
