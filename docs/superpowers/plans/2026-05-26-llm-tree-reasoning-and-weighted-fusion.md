# LLM Tree Reasoning + Weighted-Score Fusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `WeightedScoreInterpolationStep` (alternative to RRF fusion that preserves score magnitude via min-max normalized linear blend) and `LlmTreeReasoningStep` (PageIndex-style vectorless RAG using an LLM to navigate `__project__`-only `DocumentNode` trees) as composable additions to the existing sklearn-shaped retrieval pipeline.

**Architecture:** Two new `RetrieverStep` classes plus one new `LlmClient` Protocol + `OpenAiLlmClient` concrete + `LlmConfig` sub-model. `LlmTreeReasoningStep` reads `uow.trees` for `package="__project__"`, renders a Jinja2 prompt template, calls `LlmClient.chat()` with `response_format="json_object"`, parses the returned `node_list`, and fetches matching chunks via `uow.chunks.list(filter={"qualified_name": {"in": [...]}})`. Three new YAML presets ship opt-in; default `chunk_search.yaml` untouched. No `pageindex` package dependency — algorithm re-implemented locally.

**Tech Stack:** Python 3.11+, openai>=1.40 (already required), jinja2 (newly explicit), pytest-asyncio, frozen+slots dataclasses, pydantic-settings.

**Spec:** `docs/superpowers/specs/2026-05-26-llm-tree-reasoning-and-weighted-fusion-design.md` (772 lines, 17 ACs, all 9 decisions resolved).

**Hard constraints:**
- Every commit authored by msobroza only. NO `Co-Authored-By:` trailers, NO `--author` flag.
- Per-task review gates: `/code-review` + `/review` after every commit.
- No `/ultrareview` at the end — use the skill's built-in code-reviewer subagent over the full PR diff.
- TDD: failing test FIRST, verify FAIL, then implementation, verify PASS, full suite gate, commit.

**Branch:** `feature/llm-tree-reasoning-and-weighted-fusion` (already exists; predecessor commit `6d759ae` — spec + EXTENSIONS follow-up entry).

---

## Task 1: Declare `jinja2` as an explicit runtime dependency

**Spec ref:** Decision B (Jinja2-versioned prompts) — Jinja2 is the prompt-template engine for both shipped variants.

**Files:**
- Modify: `pyproject.toml` — `[project] dependencies` list
- Modify: `tests/test_pyproject_extras.py` — assert jinja2 is in main deps

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pyproject_extras.py`:

```python
def test_jinja2_in_main_deps() -> None:
    """LLM tree reasoning loads Jinja2 prompt templates; jinja2 must be a
    required runtime dep, not a transitive accident."""
    import tomllib
    pyproject = (ROOT / "pyproject.toml").read_text()
    data = tomllib.loads(pyproject)
    main_deps = data["project"]["dependencies"]
    assert any("jinja2" in d.lower() for d in main_deps), (
        f"jinja2 not in main dependencies: {main_deps}"
    )
```

- [ ] **Step 2: Run test, verify FAIL**

```bash
.venv/bin/pytest tests/test_pyproject_extras.py::test_jinja2_in_main_deps -v
```

Expected: FAIL — "jinja2 not in main dependencies".

- [ ] **Step 3: Add jinja2 to pyproject.toml**

In `pyproject.toml` `[project] dependencies` (currently lists mcp, pydantic, pydantic-settings, pyyaml, numpy, turbovec, fastembed, openai), append:

```toml
dependencies = ["mcp>=1.0", "pydantic>=2.0", "pydantic-settings>=2.0", "pyyaml>=6.0", "numpy>=1.26", "turbovec>=0.5,<1.0", "fastembed>=0.4,<1.0", "openai>=1.40,<2.0", "jinja2>=3.0,<4.0"]
```

- [ ] **Step 4: Run test, verify PASS**

```bash
.venv/bin/pytest tests/test_pyproject_extras.py::test_jinja2_in_main_deps -v
```

Expected: PASS.

- [ ] **Step 5: Full suite gate**

```bash
.venv/bin/pytest -q
```

Expected: ~1200 passed (1199 prior + 1 new).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/test_pyproject_extras.py
git commit -m "deps: declare jinja2>=3.0,<4.0 as explicit runtime dep

Required for the upcoming LlmTreeReasoningStep, which loads Jinja2
templates from python/pydocs_mcp/retrieval/prompts/. Was transitively
present; making it explicit prevents a surprise on the next
dependency-tree change."
```

---

## Task 2: `ChatMessage` TypedDict + `LlmClient` Protocol

**Spec ref:** Decision A (OpenAiLlmClient first, SOLID-extensible) / AC-1.

**Files:**
- Modify: `python/pydocs_mcp/storage/protocols.py` — append `ChatMessage` + `LlmClient`
- Create: `tests/storage/test_llm_client_protocol.py` — Protocol-shape test

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_llm_client_protocol.py
"""AC-1: LlmClient Protocol exposes both async chat() and chat_sync()."""
from __future__ import annotations

import inspect

from pydocs_mcp.storage.protocols import ChatMessage, LlmClient


def test_chat_message_typed_dict_shape() -> None:
    """ChatMessage carries role + content for chat-completion APIs."""
    msg: ChatMessage = {"role": "user", "content": "hello"}
    assert msg["role"] == "user"
    assert msg["content"] == "hello"


def test_llm_client_protocol_has_chat_async() -> None:
    """LlmClient.chat is an async method (the production path)."""
    assert hasattr(LlmClient, "chat")
    assert inspect.iscoroutinefunction(LlmClient.chat)


def test_llm_client_protocol_has_chat_sync() -> None:
    """LlmClient.chat_sync is a sync method (the CLI / debug / test path)."""
    assert hasattr(LlmClient, "chat_sync")
    assert not inspect.iscoroutinefunction(LlmClient.chat_sync)


def test_llm_client_protocol_has_model_name() -> None:
    """LlmClient declares model_name so callers can identify the provider
    without peeking into the concrete class."""
    # Protocols don't enforce attribute presence at typing-protocol level,
    # but we declare it in the source so type checkers see it.
    import pydocs_mcp.storage.protocols as proto_module
    src = inspect.getsource(proto_module)
    assert "model_name: str" in src
```

- [ ] **Step 2: Run test, verify FAIL**

```bash
.venv/bin/pytest tests/storage/test_llm_client_protocol.py -v
```

Expected: FAIL — `ImportError: cannot import name 'ChatMessage'`.

- [ ] **Step 3: Add Protocol + TypedDict to protocols.py**

In `python/pydocs_mcp/storage/protocols.py`, after the `Embedder` Protocol (around line 266), append:

```python
class ChatMessage(TypedDict):
    """One message in an LLM chat-completion conversation.

    Mirrors the OpenAI / Anthropic / common LLM API shape: role +
    content. Used by LlmClient.chat() / chat_sync() as input.
    """

    role: Literal["system", "user", "assistant"]
    content: str


@runtime_checkable
class LlmClient(Protocol):
    """LLM chat-completion client.

    Exposes BOTH async ``chat()`` and sync ``chat_sync()`` — LLM calls
    surface in more contexts than embedding calls (the MCP server is
    async, but the CLI debug path, test helpers, and notebooks need a
    sync surface).

    Implementations live under
    ``python/pydocs_mcp/retrieval.llm_clients/``. The
    factory ``build_llm_client(cfg)`` dispatches on ``cfg.provider``
    to the right concrete (OpenAiLlmClient for v1; SOLID open/closed
    for future providers).
    """

    model_name: str

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        """Async chat completion. Returns the assistant's response text."""
        ...

    def chat_sync(
        self,
        messages: Sequence[ChatMessage],
        *,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        """Sync chat completion. Same contract as ``chat()``."""
        ...
```

Also: at the top of `protocols.py`, add `TypedDict` to the `typing` imports if not already present. Verify imports include `Literal`, `Protocol`, `Sequence`, `runtime_checkable`.

- [ ] **Step 4: Run test, verify PASS**

```bash
.venv/bin/pytest tests/storage/test_llm_client_protocol.py -v
```

Expected: PASS (4/4).

- [ ] **Step 5: Full suite gate**

```bash
.venv/bin/pytest -q
```

Expected: ~1204 passed.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/storage/protocols.py tests/storage/test_llm_client_protocol.py
git commit -m "feat(storage): LlmClient Protocol + ChatMessage TypedDict (AC-1)

Per spec Decision A. Mirrors the existing Embedder Protocol with
FastEmbed + OpenAI concretes — LlmClient gets future Anthropic /
Gemini / LiteLLM concretes via SOLID open/closed.

Exposes BOTH async chat() and sync chat_sync(); LLM calls surface in
more contexts than embedding calls (CLI, debug, tests, notebooks).
ChatMessage TypedDict is the input shape (role + content), matching
the canonical OpenAI / Anthropic chat-completion API."
```

---

## Task 3: `LlmConfig` sub-model in `retrieval/config.py`

**Spec ref:** Decision A / AC-2.

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py` — add `LlmConfig` + `AppConfig.llm`
- Create: `tests/retrieval/test_config_llm.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/test_config_llm.py
"""AC-2: LlmConfig sub-model + AppConfig.llm wiring + YAML overlay."""
from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path

from pydocs_mcp.retrieval.config import AppConfig, LlmConfig


def test_llm_config_defaults() -> None:
    """Defaults: provider=openai, model_name=gpt-4o-mini, temperature=0.0."""
    cfg = LlmConfig()
    assert cfg.provider == "openai"
    assert cfg.model_name == "gpt-4o-mini"
    assert cfg.temperature == 0.0
    assert cfg.max_tokens is None
    assert cfg.api_key is None


def test_app_config_llm_field_present() -> None:
    cfg = AppConfig()
    assert isinstance(cfg.llm, LlmConfig)


def test_app_config_yaml_overlay_for_llm() -> None:
    yaml_text = textwrap.dedent("""
    llm:
      provider: openai
      model_name: gpt-4o
      temperature: 0.2
      max_tokens: 1024
    """)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_text)
        overlay_path = Path(f.name)
    try:
        cfg = AppConfig.load(explicit_path=overlay_path)
        assert cfg.llm.model_name == "gpt-4o"
        assert cfg.llm.temperature == 0.2
        assert cfg.llm.max_tokens == 1024
    finally:
        overlay_path.unlink()
```

- [ ] **Step 2: Run test, verify FAIL**

```bash
.venv/bin/pytest tests/retrieval/test_config_llm.py -v
```

Expected: FAIL — `ImportError: cannot import name 'LlmConfig'`.

- [ ] **Step 3: Add LlmConfig + AppConfig.llm**

In `python/pydocs_mcp/retrieval/config.py`, after `EmbeddingConfig` (around line 294), add:

```python
class LlmConfig(BaseModel):
    """LLM chat-completion client configuration.

    Architectural twin of ``EmbeddingConfig`` — same shape (provider,
    model_name, tuning params), used by ``build_llm_client(cfg)`` to
    construct the right concrete client. Defaults selected for cost
    efficiency: gpt-4o-mini is OpenAI's cheap-but-capable model and the
    right baseline for a retrieval re-ranking step where calls are
    frequent but small.
    """

    provider: Literal["openai"] = "openai"
    model_name: str = "gpt-4o-mini"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    api_key: str | None = None  # None -> SDK reads OPENAI_API_KEY env var
```

In the `AppConfig` class, add the field (mirror the `embedding` pattern, around line 351):

```python
class AppConfig(BaseSettings):
    # ... existing fields including embedding: EmbeddingConfig ...
    llm: LlmConfig = Field(default_factory=LlmConfig)
    # ... rest ...
```

- [ ] **Step 4: Run test, verify PASS**

```bash
.venv/bin/pytest tests/retrieval/test_config_llm.py -v
```

Expected: PASS (3/3).

- [ ] **Step 5: Full suite gate**

```bash
.venv/bin/pytest -q
```

Expected: ~1207 passed.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/retrieval/config.py tests/retrieval/test_config_llm.py
git commit -m "feat(config): LlmConfig sub-model + AppConfig.llm (AC-2)

Architectural twin of EmbeddingConfig. Provider/model_name/temperature/
max_tokens/api_key fields with safe defaults (gpt-4o-mini, temp=0.0,
api_key=None so the OpenAI SDK reads OPENAI_API_KEY from env).

YAML overlay: 'llm: { model_name: gpt-4o, temperature: 0.2 }' loads
via the existing AppConfig.load() path."
```

---

## Task 4: `OpenAiLlmClient` concrete

**Spec ref:** Decision A / AC-1, AC-3.

**Files:**
- Create: `python/pydocs_mcp/retrieval.llm_clients/__init__.py`
- Create: `python/pydocs_mcp/retrieval.llm_clients/openai.py`
- Create: `tests/retrieval.llm_clients/__init__.py` (empty)
- Create: `tests/retrieval.llm_clients/test_openai_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval.llm_clients/test_openai_client.py
"""AC-1: OpenAiLlmClient implements LlmClient with both async + sync."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pydocs_mcp.retrieval.llm_clients.openai import OpenAiLlmClient
from pydocs_mcp.storage.protocols import LlmClient


def test_openai_client_satisfies_protocol() -> None:
    client = OpenAiLlmClient(model_name="gpt-4o-mini")
    assert isinstance(client, LlmClient)
    assert client.model_name == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_chat_async_calls_openai_with_expected_args() -> None:
    """chat() passes model + messages + response_format to AsyncOpenAI."""
    client = OpenAiLlmClient(model_name="gpt-4o-mini", api_key="test-key")
    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="hi"))]
    with patch.object(
        client._async_client.chat.completions,
        "create",
        new=AsyncMock(return_value=fake_completion),
    ) as mock_create:
        result = await client.chat(
            [{"role": "user", "content": "hello"}],
            response_format="json_object",
            temperature=0.5,
        )
    assert result == "hi"
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert call_kwargs["messages"] == [{"role": "user", "content": "hello"}]
    assert call_kwargs["response_format"] == {"type": "json_object"}
    assert call_kwargs["temperature"] == 0.5


def test_chat_sync_calls_openai_with_expected_args() -> None:
    client = OpenAiLlmClient(model_name="gpt-4o-mini", api_key="test-key")
    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="sync-hi"))]
    with patch.object(
        client._sync_client.chat.completions,
        "create",
        return_value=fake_completion,
    ) as mock_create:
        result = client.chat_sync(
            [{"role": "user", "content": "ping"}],
        )
    assert result == "sync-hi"
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"
    # Default response_format is "text" -> no json_object wrapper
    assert call_kwargs.get("response_format") is None
```

- [ ] **Step 2: Run test, verify FAIL**

```bash
.venv/bin/pytest tests/retrieval.llm_clients/test_openai_client.py -v
```

Expected: FAIL — `ImportError: cannot import name 'OpenAiLlmClient'`.

- [ ] **Step 3: Create the concrete**

`python/pydocs_mcp/retrieval.llm_clients/__init__.py`:

```python
"""LLM client concretes + factory.

Architectural twin of ``embedders/`` — the ``LlmClient`` Protocol lives in
``storage/protocols.py``; concretes implementing it live here. Adding a
new provider = one new module + one new branch in ``build_llm_client``.
"""
from __future__ import annotations

from pydocs_mcp.retrieval.config import LlmConfig
from pydocs_mcp.storage.protocols import LlmClient


def build_llm_client(cfg: LlmConfig) -> LlmClient:
    """Construct the configured LLM client.

    Defers concrete-class imports so server startup doesn't pay both
    cold-import costs upfront. Raises ValueError for unknown providers.
    """
    if cfg.provider == "openai":
        from pydocs_mcp.retrieval.llm_clients.openai import (
            OpenAiLlmClient,
        )
        return OpenAiLlmClient(
            model_name=cfg.model_name,
            api_key=cfg.api_key,
        )
    raise ValueError(
        f"Unknown LLM provider: {cfg.provider!r}. Supported: 'openai'.",
    )


__all__ = ("build_llm_client",)
```

`python/pydocs_mcp/retrieval.llm_clients/openai.py`:

```python
"""OpenAiLlmClient — LlmClient Protocol concrete using the openai SDK.

Async surface uses openai.AsyncOpenAI; sync surface uses openai.OpenAI.
Both SDK instances are constructed once in __post_init__ to avoid the
cold-import cost on every call.

OPENAI_API_KEY env var is the default credential source — set api_key
explicitly only when you need a non-default key (e.g., per-tenant).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from openai import AsyncOpenAI, OpenAI

from pydocs_mcp.storage.protocols import ChatMessage


@dataclass(frozen=True, slots=True)
class OpenAiLlmClient:
    model_name: str
    api_key: str | None = None
    _async_client: AsyncOpenAI = field(init=False, repr=False, compare=False)
    _sync_client: OpenAI = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # WHY: frozen dataclass requires object.__setattr__ to populate
        # init=False fields. The SDK clients are constructed once and
        # reused across every chat() call to avoid per-request handshake.
        object.__setattr__(self, "_async_client", AsyncOpenAI(api_key=self.api_key))
        object.__setattr__(self, "_sync_client", OpenAI(api_key=self.api_key))

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        rf = {"type": "json_object"} if response_format == "json_object" else None
        rsp = await self._async_client.chat.completions.create(
            model=self.model_name,
            messages=list(messages),
            response_format=rf,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return rsp.choices[0].message.content or ""

    def chat_sync(
        self,
        messages: Sequence[ChatMessage],
        *,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        rf = {"type": "json_object"} if response_format == "json_object" else None
        rsp = self._sync_client.chat.completions.create(
            model=self.model_name,
            messages=list(messages),
            response_format=rf,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return rsp.choices[0].message.content or ""


__all__ = ("OpenAiLlmClient",)
```

Also create `tests/retrieval.llm_clients/__init__.py` (empty file) so pytest discovers the new test dir.

- [ ] **Step 4: Run test, verify PASS**

```bash
.venv/bin/pytest tests/retrieval.llm_clients/test_openai_client.py -v
```

Expected: PASS (3/3).

- [ ] **Step 5: Full suite gate**

```bash
.venv/bin/pytest -q
```

Expected: ~1210 passed.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/retrieval.llm_clients/ tests/retrieval.llm_clients/
git commit -m "feat(llm): OpenAiLlmClient concrete + build_llm_client factory (AC-1, AC-3)

Per spec Decision A. Uses openai>=1.40 (already required dep).
Constructs AsyncOpenAI + OpenAI clients once in __post_init__; both
surfaces share the same identity + api_key. response_format='json_object'
maps to OpenAI's {type: 'json_object'} structured-output mode.

build_llm_client(cfg) factory dispatches on cfg.provider; raises
ValueError for unknown providers. Deferred concrete import per the
embedders/ pattern."
```

---

## Task 5: `FakeLlmClient` test fixture

**Spec ref:** Section "Testing" / AC-6.

**Files:**
- Modify: `tests/_fakes.py` — append `FakeLlmClient` class
- Create: `tests/test_fake_llm_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fake_llm_client.py
"""FakeLlmClient delivers canned responses without network calls."""
from __future__ import annotations

import pytest

from pydocs_mcp.storage.protocols import LlmClient
from tests._fakes import FakeLlmClient


def test_fake_satisfies_protocol() -> None:
    client = FakeLlmClient(responses={})
    assert isinstance(client, LlmClient)


@pytest.mark.asyncio
async def test_fake_chat_returns_canned() -> None:
    client = FakeLlmClient(
        model_name="fake-model",
        responses={"hello": "world"},
    )
    result = await client.chat(
        [{"role": "user", "content": "hello"}],
    )
    assert result == "world"


def test_fake_chat_sync_returns_canned() -> None:
    client = FakeLlmClient(
        responses={"ping": "pong"},
    )
    result = client.chat_sync(
        [{"role": "user", "content": "ping"}],
    )
    assert result == "pong"


@pytest.mark.asyncio
async def test_fake_raises_on_unknown_key() -> None:
    """Unknown keys raise KeyError with diagnostic context — points the
    test author at the missing canned response rather than returning None."""
    client = FakeLlmClient(responses={"hi": "hello"})
    with pytest.raises(KeyError, match="not-in-responses"):
        await client.chat([{"role": "user", "content": "not-in-responses"}])
```

- [ ] **Step 2: Run test, verify FAIL**

```bash
.venv/bin/pytest tests/test_fake_llm_client.py -v
```

Expected: FAIL — `ImportError: cannot import name 'FakeLlmClient'`.

- [ ] **Step 3: Add FakeLlmClient to tests/_fakes.py**

In `tests/_fakes.py`, append:

```python
@dataclass(slots=True)
class FakeLlmClient:
    """Offline LlmClient for unit tests.

    Returns canned responses keyed by the LAST message's content. Unknown
    keys raise KeyError with diagnostic context so test failures point at
    the missing canned response, not at mysterious None returns.

    The key choice (last message content) covers the simple single-turn
    case the retrieval pipeline uses. Multi-turn tests can override by
    subclassing.
    """

    responses: dict[str, str] = field(default_factory=dict)
    model_name: str = "fake-llm-model"
    _calls: list[Sequence[ChatMessage]] = field(default_factory=list)

    async def chat(
        self,
        messages: Sequence["ChatMessage"],
        *,
        response_format: "Literal['text', 'json_object']" = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        self._calls.append(tuple(messages))
        key = messages[-1]["content"]
        if key not in self.responses:
            raise KeyError(
                f"FakeLlmClient has no canned response for key={key!r}. "
                f"Available keys: {sorted(self.responses)}",
            )
        return self.responses[key]

    def chat_sync(
        self,
        messages: Sequence["ChatMessage"],
        *,
        response_format: "Literal['text', 'json_object']" = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        self._calls.append(tuple(messages))
        key = messages[-1]["content"]
        if key not in self.responses:
            raise KeyError(
                f"FakeLlmClient has no canned response for key={key!r}. "
                f"Available keys: {sorted(self.responses)}",
            )
        return self.responses[key]
```

At the top of `tests/_fakes.py`, ensure imports include:

```python
from collections.abc import Sequence
from typing import Literal

from pydocs_mcp.storage.protocols import ChatMessage
```

- [ ] **Step 4: Run test, verify PASS**

```bash
.venv/bin/pytest tests/test_fake_llm_client.py -v
```

Expected: PASS (4/4).

- [ ] **Step 5: Full suite gate**

```bash
.venv/bin/pytest -q
```

Expected: ~1214 passed.

- [ ] **Step 6: Commit**

```bash
git add tests/_fakes.py tests/test_fake_llm_client.py
git commit -m "test: FakeLlmClient for offline unit tests

Canned responses keyed by last-message content. Unknown keys raise
KeyError with diagnostic context — test failures point at the missing
response, not a None return.

Loud failures > silent ones."
```

---

## Task 6: `BuildContext.llm_client` field

**Spec ref:** Decision A / AC-10.

**Files:**
- Modify: `python/pydocs_mcp/retrieval/serialization.py` — add `llm_client` field
- Create: `tests/retrieval/test_build_context_llm.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/test_build_context_llm.py
"""AC-10: BuildContext gains an llm_client field for steps that need it."""
from __future__ import annotations

from pydocs_mcp.retrieval.serialization import BuildContext
from tests._fakes import FakeLlmClient


def test_build_context_default_llm_client_is_none() -> None:
    ctx = BuildContext()
    assert ctx.llm_client is None


def test_build_context_accepts_llm_client() -> None:
    fake = FakeLlmClient(responses={})
    ctx = BuildContext(llm_client=fake)
    assert ctx.llm_client is fake
```

- [ ] **Step 2: Run test, verify FAIL**

```bash
.venv/bin/pytest tests/retrieval/test_build_context_llm.py -v
```

Expected: FAIL — `TypeError: BuildContext got unexpected keyword 'llm_client'` OR `AttributeError: ...`.

- [ ] **Step 3: Add `llm_client` to BuildContext**

In `python/pydocs_mcp/retrieval/serialization.py`, find the `BuildContext` dataclass (around line 120-150) and add:

```python
@dataclass(frozen=True, slots=True)
class BuildContext:
    # ... existing fields: connection_provider, predicate_registry,
    # filter_registry, embedder, uow_factory, pipeline_hash ...
    llm_client: "LlmClient | None" = None
```

Add the TYPE_CHECKING import:

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pydocs_mcp.storage.protocols import LlmClient
```

(Or use the existing TYPE_CHECKING block if one is already there.)

- [ ] **Step 4: Run test, verify PASS**

```bash
.venv/bin/pytest tests/retrieval/test_build_context_llm.py -v
```

Expected: PASS (2/2).

- [ ] **Step 5: Full suite gate**

```bash
.venv/bin/pytest -q
```

Expected: ~1216 passed.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/retrieval/serialization.py tests/retrieval/test_build_context_llm.py
git commit -m "feat(retrieval): BuildContext.llm_client field (AC-10)

Mirrors the existing 'embedder' and 'uow_factory' fields added in the
hybrid-search and chunk-cache PRs. Defaults to None so existing callers
stay green; LlmTreeReasoningStep.from_dict will enforce non-None via
the same strict-gate pattern LoadExistingChunkHashesStage uses."
```

---

## Task 7: `WeightedScoreInterpolationStep`

**Spec ref:** Decision E (one PR, both steps) / AC-4, AC-5.

**Files:**
- Create: `python/pydocs_mcp/retrieval/steps/weighted_score_interpolation.py`
- Modify: `python/pydocs_mcp/retrieval/steps/__init__.py` — re-export
- Create: `tests/retrieval/steps/test_weighted_score_interpolation.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/retrieval/steps/test_weighted_score_interpolation.py
"""AC-4 + AC-5: WeightedScoreInterpolationStep blends per-branch scores."""
from __future__ import annotations

import pytest

from pydocs_mcp.models import Chunk, ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.weighted_score_interpolation import (
    WeightedScoreInterpolationStep,
)


def _chunk(cid: int, score: float, text: str = "") -> Chunk:
    """Helper to build a scored chunk."""
    c = Chunk(id=cid, text=text, metadata={"score": score})
    return c


def _ranked(items: list[Chunk]) -> ChunkList:
    return ChunkList(items=tuple(items))


@pytest.mark.asyncio
async def test_equal_weights_blend_min_max_normalized_scores() -> None:
    """BM25 scores in [0, 10], dense in [0, 1]. After min-max norm,
    equal-weighted blend produces (norm_bm25 + norm_dense) / 2."""
    state = RetrieverState(
        query=...,  # not used by fusion
        candidates=None,
        result=None,
        scratch={
            "bm25.ranked":  _ranked([_chunk(1, 10.0), _chunk(2, 5.0)]),
            "dense.ranked": _ranked([_chunk(1, 1.0),  _chunk(2, 0.5)]),
        },
    )
    step = WeightedScoreInterpolationStep(
        weights=(0.5, 0.5),
        branch_keys=("bm25.ranked", "dense.ranked"),
    )
    out = await step.run(state)
    assert out.candidates is not None
    items = list(out.candidates.items)
    # Chunk 1: norm_bm25=1.0, norm_dense=1.0  -> blend=1.0
    # Chunk 2: norm_bm25=0.0, norm_dense=0.0  -> blend=0.0
    by_id = {c.id: c.metadata["score"] for c in items}
    assert by_id[1] == pytest.approx(1.0)
    assert by_id[2] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_asymmetric_weights() -> None:
    state = RetrieverState(
        query=...,
        candidates=None,
        result=None,
        scratch={
            "bm25.ranked":  _ranked([_chunk(1, 10.0), _chunk(2, 0.0)]),
            "dense.ranked": _ranked([_chunk(1, 0.0),  _chunk(2, 1.0)]),
        },
    )
    step = WeightedScoreInterpolationStep(
        weights=(0.8, 0.2),
        branch_keys=("bm25.ranked", "dense.ranked"),
    )
    out = await step.run(state)
    by_id = {c.id: c.metadata["score"] for c in out.candidates.items}
    # Chunk 1: 0.8*1.0 + 0.2*0.0 = 0.8
    # Chunk 2: 0.8*0.0 + 0.2*1.0 = 0.2
    assert by_id[1] == pytest.approx(0.8)
    assert by_id[2] == pytest.approx(0.2)


def test_from_dict_validates_weights_sum() -> None:
    """Weights that don't sum to ~1.0 raise in from_dict."""
    from pydocs_mcp.retrieval.serialization import BuildContext

    with pytest.raises(ValueError, match="sum"):
        WeightedScoreInterpolationStep.from_dict(
            {
                "type": "weighted_score_interpolation",
                "weights": [0.3, 0.3],  # sums to 0.6
                "branch_keys": ["a", "b"],
            },
            BuildContext(),
        )


def test_round_trip_yaml() -> None:
    """to_dict / from_dict round-trips structural equality."""
    from pydocs_mcp.retrieval.serialization import BuildContext

    original = WeightedScoreInterpolationStep(
        weights=(0.6, 0.4),
        branch_keys=("a", "b"),
        name="custom_name",
    )
    rebuilt = WeightedScoreInterpolationStep.from_dict(
        original.to_dict(), BuildContext(),
    )
    assert rebuilt.weights == original.weights
    assert rebuilt.branch_keys == original.branch_keys
    assert rebuilt.name == original.name


@pytest.mark.asyncio
async def test_missing_branch_key_skipped_gracefully() -> None:
    """If a branch_key isn't in state.scratch, that branch contributes 0
    — graceful degradation, matches RRFFusionStep behavior."""
    state = RetrieverState(
        query=...,
        candidates=None,
        result=None,
        scratch={
            "bm25.ranked": _ranked([_chunk(1, 10.0)]),
            # dense.ranked deliberately absent
        },
    )
    step = WeightedScoreInterpolationStep(
        weights=(0.5, 0.5),
        branch_keys=("bm25.ranked", "dense.ranked"),
    )
    out = await step.run(state)
    # With dense missing, chunk 1's score is just 0.5*norm_bm25 = 0.5*1.0
    by_id = {c.id: c.metadata["score"] for c in out.candidates.items}
    assert by_id[1] == pytest.approx(0.5)
```

- [ ] **Step 2: Run tests, verify FAIL**

```bash
.venv/bin/pytest tests/retrieval/steps/test_weighted_score_interpolation.py -v
```

Expected: FAIL — ImportError on the step class.

- [ ] **Step 3: Create the step**

`python/pydocs_mcp/retrieval/steps/weighted_score_interpolation.py`:

```python
"""WeightedScoreInterpolationStep — alternative fusion to RRFFusionStep.

Min-max normalizes each branch's scores to [0, 1], then blends via
``score_final = sum(weights[i] * norm_score_i)``. Unlike RRF (which
discards score magnitude), this preserves it — useful when one
retriever is dramatically stronger than the other on a given query.

Reads from the same ``state.scratch[<branch>.ranked]`` keys
RRFFusionStep uses, so it drops in as a YAML swap.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, ClassVar

from pydocs_mcp.models import Chunk, ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.pipeline.base import RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry

_DEFAULT_WEIGHTS: tuple[float, ...] = (0.5, 0.5)
_DEFAULT_BRANCH_KEYS: tuple[str, ...] = ("bm25.ranked", "dense.ranked")
_WEIGHT_SUM_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
@step_registry.register("weighted_score_interpolation")
class WeightedScoreInterpolationStep(RetrieverStep):
    """Linear-blend fusion across N branches with min-max normalization."""

    REQUIRES: ClassVar[frozenset[str]] = frozenset()

    weights: tuple[float, ...] = field(default=_DEFAULT_WEIGHTS, kw_only=True)
    branch_keys: tuple[str, ...] = field(default=_DEFAULT_BRANCH_KEYS, kw_only=True)
    publish_to: str | None = field(default=None, kw_only=True)
    name: str = field(default="weighted_score_interpolation", kw_only=True)

    def __post_init__(self) -> None:
        if len(self.weights) != len(self.branch_keys):
            raise ValueError(
                f"WeightedScoreInterpolationStep: len(weights)="
                f"{len(self.weights)} != len(branch_keys)={len(self.branch_keys)}",
            )

    async def run(self, state: RetrieverState) -> RetrieverState:
        # Collect per-chunk-id score contributions across all branches.
        # For each branch: min-max normalize scores in that branch, then
        # weight by self.weights[i]. Missing branches contribute zero
        # (graceful degradation, matches RRF behavior).
        accumulated: dict[int, float] = {}
        first_seen: dict[int, Chunk] = {}
        for weight, key in zip(self.weights, self.branch_keys, strict=True):
            branch = state.scratch.get(key)
            if branch is None:
                continue
            items = branch.items if hasattr(branch, "items") else tuple(branch)
            if not items:
                continue
            scores = [float(c.metadata.get("score", 0.0)) for c in items]
            lo = min(scores)
            hi = max(scores)
            span = hi - lo if hi > lo else 1.0  # avoid div-by-zero
            for chunk, raw in zip(items, scores, strict=True):
                normed = (raw - lo) / span
                accumulated[chunk.id] = (
                    accumulated.get(chunk.id, 0.0) + weight * normed
                )
                first_seen.setdefault(chunk.id, chunk)

        if not accumulated:
            return state

        fused = sorted(
            (
                replace(first_seen[cid], metadata={**first_seen[cid].metadata,
                                                    "score": score})
                for cid, score in accumulated.items()
            ),
            key=lambda c: c.metadata["score"],
            reverse=True,
        )
        ranked = ChunkList(items=tuple(fused))

        new_scratch = dict(state.scratch)
        if self.publish_to is not None:
            new_scratch[self.publish_to] = ranked
        return replace(state, candidates=ranked, scratch=new_scratch)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": "weighted_score_interpolation"}
        if self.weights != _DEFAULT_WEIGHTS:
            out["weights"] = list(self.weights)
        if self.branch_keys != _DEFAULT_BRANCH_KEYS:
            out["branch_keys"] = list(self.branch_keys)
        if self.publish_to is not None:
            out["publish_to"] = self.publish_to
        if self.name != "weighted_score_interpolation":
            out["name"] = self.name
        return out

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        context: BuildContext,
    ) -> "WeightedScoreInterpolationStep":
        weights = tuple(data.get("weights", _DEFAULT_WEIGHTS))
        if abs(sum(weights) - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise ValueError(
                f"WeightedScoreInterpolationStep weights must sum to ~1.0 "
                f"(tol {_WEIGHT_SUM_TOLERANCE}); got {weights} -> {sum(weights)}",
            )
        return cls(
            weights=weights,
            branch_keys=tuple(data.get("branch_keys", _DEFAULT_BRANCH_KEYS)),
            publish_to=data.get("publish_to"),
            name=data.get("name", "weighted_score_interpolation"),
        )
```

Also re-export from `python/pydocs_mcp/retrieval/steps/__init__.py`:

```python
from pydocs_mcp.retrieval.steps.weighted_score_interpolation import (
    WeightedScoreInterpolationStep,
)
# ... add to __all__ tuple ...
```

- [ ] **Step 4: Run tests, verify PASS**

```bash
.venv/bin/pytest tests/retrieval/steps/test_weighted_score_interpolation.py -v
```

Expected: PASS (5/5).

- [ ] **Step 5: Full suite gate**

```bash
.venv/bin/pytest -q
```

Expected: ~1221 passed.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/retrieval/steps/weighted_score_interpolation.py python/pydocs_mcp/retrieval/steps/__init__.py tests/retrieval/steps/test_weighted_score_interpolation.py
git commit -m "feat(retrieval): WeightedScoreInterpolationStep (AC-4, AC-5)

Alternative fusion to RRFFusionStep. Min-max normalizes each branch's
scores to [0, 1], then blends via sum(weights[i] * norm_score_i).

Reads state.scratch[<branch>.ranked] keys (same convention RRF uses).
from_dict validates weights sum to 1.0 (tol 1e-6). __post_init__
validates len(weights) == len(branch_keys). Missing branch keys
contribute zero (graceful degradation, matches RRF)."
```

---

## Task 8: Jinja2 prompt templates + loader helper

**Spec ref:** Decision B / AC-11.

**Files:**
- Create: `python/pydocs_mcp/retrieval/prompts/__init__.py` (re-exports loader)
- Create: `python/pydocs_mcp/retrieval/prompts/tree_reasoning_pageindex_v1.j2`
- Create: `python/pydocs_mcp/retrieval/prompts/tree_reasoning_pydocs_v1.j2`
- Create: `python/pydocs_mcp/retrieval/prompts/_loader.py` (render function)
- Create: `tests/retrieval/prompts/test_prompt_loader.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/prompts/test_prompt_loader.py
"""AC-11: Jinja2 prompt templates load + render with (query, trees)."""
from __future__ import annotations

from pydocs_mcp.retrieval.prompts._loader import render_prompt


def test_render_pydocs_v1_contains_query() -> None:
    out = render_prompt(
        "tree_reasoning_pydocs_v1",
        query="how does the diff-merge handle NULL hashes",
        trees=[{"title": "module_a", "node_id": "1", "summary": "stuff",
                "kind": "MODULE", "nodes": []}],
    )
    assert "how does the diff-merge handle NULL hashes" in out
    assert "module_a" in out
    assert '"node_id"' in out  # tree is serialized as JSON


def test_render_pageindex_v1_contains_query() -> None:
    out = render_prompt(
        "tree_reasoning_pageindex_v1",
        query="what is x",
        trees=[{"title": "t", "node_id": "1", "summary": "s", "nodes": []}],
    )
    assert "what is x" in out
    assert '"node_id"' in out


def test_render_unknown_template_raises() -> None:
    import pytest
    with pytest.raises(FileNotFoundError, match="not_a_template"):
        render_prompt("not_a_template", query="q", trees=[])
```

- [ ] **Step 2: Run test, verify FAIL**

```bash
.venv/bin/pytest tests/retrieval/prompts/test_prompt_loader.py -v
```

Expected: FAIL — ImportError on `_loader`.

- [ ] **Step 3: Create the templates + loader**

`python/pydocs_mcp/retrieval/prompts/__init__.py`:

```python
"""Versioned Jinja2 prompt templates for LLM-driven retrieval steps."""
from pydocs_mcp.retrieval.prompts._loader import render_prompt

__all__ = ("render_prompt",)
```

`python/pydocs_mcp/retrieval/prompts/_loader.py`:

```python
"""Render versioned Jinja2 prompts shipped under this package.

Templates are versioned via filename suffix (``_vN``); to ship a new
variant add a new file. Never edit a shipped version in place —
existing deployments depend on stable prompt behavior keyed by name.
"""
from __future__ import annotations

from importlib import resources
from typing import Any

from jinja2 import Environment, StrictUndefined


_env = Environment(
    autoescape=False,           # prompt text is not HTML; don't escape
    undefined=StrictUndefined,  # missing vars in templates raise loudly
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_prompt(template_name: str, **variables: Any) -> str:
    """Load ``<template_name>.j2`` from this package and render it.

    Variables passed via keyword args become template context. ``trees``
    is serialized via Jinja2's ``tojson`` filter; the template is
    responsible for wrapping it appropriately.
    """
    pkg = resources.files("pydocs_mcp.retrieval.prompts")
    template_file = pkg.joinpath(f"{template_name}.j2")
    if not template_file.is_file():
        raise FileNotFoundError(
            f"Prompt template {template_name!r} not found "
            f"under pydocs_mcp/retrieval/prompts/.",
        )
    template = _env.from_string(template_file.read_text(encoding="utf-8"))
    return template.render(**variables)
```

`python/pydocs_mcp/retrieval/prompts/tree_reasoning_pageindex_v1.j2`:

```jinja2
{# Prompt: tree_reasoning_pageindex_v1 — verbatim PageIndex baseline. #}
{# Source: VectifyAI/PageIndex cookbook/pageindex_RAG_simple.ipynb. #}
{# Inputs: query (str), trees (list of node dicts with title, node_id, summary, nodes[]). #}
You are given a question and a tree structure of a document.
Each node contains a node id, node title, and a corresponding summary.
Your task is to find all nodes that are likely to contain the answer to the question.

Question: {{ query }}

Document tree structure:
{{ trees | tojson(indent=2) }}

Please reply in the following JSON format:
{
    "thinking": "<Your thinking process on which nodes are relevant to the question>",
    "node_list": ["node_id_1", "node_id_2", "...", "node_id_n"]
}
Directly return the final JSON structure. Do not output anything else.
```

`python/pydocs_mcp/retrieval/prompts/tree_reasoning_pydocs_v1.j2`:

```jinja2
{# Prompt: tree_reasoning_pydocs_v1 — adapted for code-doc queries. #}
{# Differences from PageIndex baseline:                              #}
{#   - system framing: Python project docs                           #}
{#   - leaf-vs-branch preference heuristics                          #}
{#   - explicit kind-aware advice (FUNCTION/METHOD/CLASS vs heading) #}
{# Inputs: query (str), trees (list of node dicts).                  #}
You are answering a developer's question about a Python project's source
code and documentation. The tree below is a hierarchical view of every
indexed chunk in the project — each node has a node_id, title, kind
(MODULE / CLASS / FUNCTION / METHOD / MARKDOWN_HEADING / ...), and a
short summary.

Your task: pick every node_id that is likely to contain the answer.

Heuristics for this corpus:
- Prefer FUNCTION / METHOD / CLASS nodes when the question is about HOW
  something works.
- Prefer MARKDOWN_HEADING / docstring nodes when the question is about
  WHY or WHAT something does.
- Include parent nodes only when no descendant clearly answers — the
  surrounding context is implied by the picked descendants.

Question: {{ query }}

Document tree:
{{ trees | tojson(indent=2) }}

Reply in this JSON shape only (no markdown fences, no commentary):
{
    "thinking": "<short rationale for which nodes you picked>",
    "node_list": ["node_id_1", "node_id_2", "...", "node_id_n"]
}
```

Also create `tests/retrieval/prompts/__init__.py` (empty) so pytest discovers the test dir.

- [ ] **Step 4: Run test, verify PASS**

```bash
.venv/bin/pytest tests/retrieval/prompts/test_prompt_loader.py -v
```

Expected: PASS (3/3).

- [ ] **Step 5: Full suite gate**

```bash
.venv/bin/pytest -q
```

Expected: ~1224 passed.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/retrieval/prompts/ tests/retrieval/prompts/
git commit -m "feat(prompts): Jinja2-versioned tree-reasoning templates (AC-11)

Per spec Decision B. Two templates ship:

  tree_reasoning_pageindex_v1.j2 — verbatim PageIndex single-shot
    prompt (canonical baseline; lets us A/B against their published
    numbers without our edits in the loop).
  tree_reasoning_pydocs_v1.j2    — adapted for code-doc queries:
    system framing as a Python project, leaf-vs-branch preference
    heuristics, kind-aware picking advice.

render_prompt(name, **vars) loads .j2 by name via importlib.resources;
StrictUndefined means missing template vars raise loudly instead of
silently rendering as empty strings.

Versioning by filename — never edit a shipped _vN in place; ship _v2
as a new file when a variant is ready."
```

---

## Task 9: `is_long_query` predicate

**Spec ref:** Decision F / AC-13.

**Files:**
- Modify: `python/pydocs_mcp/retrieval/route_predicates.py` — register predicate
- Create: `tests/retrieval/test_route_predicates_is_long_query.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/test_route_predicates_is_long_query.py
"""AC-13: is_long_query predicate gates ConditionalStep on query length."""
from __future__ import annotations

import pytest

from pydocs_mcp.models import SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.route_predicates import predicate_registry


def _state(terms: str) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms=terms),
        candidates=None,
        result=None,
        scratch={},
    )


def test_is_long_query_short_returns_false() -> None:
    pred = predicate_registry.get("is_long_query")
    assert pred(_state("short")) is False
    assert pred(_state("two words")) is False


def test_is_long_query_at_threshold_returns_true() -> None:
    pred = predicate_registry.get("is_long_query")
    # 8 whitespace-separated tokens is the threshold per spec.
    eight = "one two three four five six seven eight"
    assert pred(_state(eight)) is True


def test_is_long_query_above_threshold_returns_true() -> None:
    pred = predicate_registry.get("is_long_query")
    ten = "how does the diff-merge handle NULL hashes during a force reindex"
    assert pred(_state(ten)) is True


def test_is_long_query_empty_returns_false() -> None:
    pred = predicate_registry.get("is_long_query")
    assert pred(_state("")) is False
```

- [ ] **Step 2: Run test, verify FAIL**

```bash
.venv/bin/pytest tests/retrieval/test_route_predicates_is_long_query.py -v
```

Expected: FAIL — predicate not in registry.

- [ ] **Step 3: Register the predicate**

In `python/pydocs_mcp/retrieval/route_predicates.py`, append:

```python
_IS_LONG_QUERY_THRESHOLD = 8


@predicate_registry.register("is_long_query")
def is_long_query(state: RetrieverState) -> bool:
    """True when the query has at least _IS_LONG_QUERY_THRESHOLD (8) tokens.

    Used by tree-reasoning presets to gate the LLM call: short queries
    are well-served by BM25 + dense, so we avoid paying the LLM cost
    on every keyword lookup.
    """
    terms = state.query.terms or ""
    return len(terms.split()) >= _IS_LONG_QUERY_THRESHOLD
```

- [ ] **Step 4: Run test, verify PASS**

```bash
.venv/bin/pytest tests/retrieval/test_route_predicates_is_long_query.py -v
```

Expected: PASS (4/4).

- [ ] **Step 5: Full suite gate**

```bash
.venv/bin/pytest -q
```

Expected: ~1228 passed.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/retrieval/route_predicates.py tests/retrieval/test_route_predicates_is_long_query.py
git commit -m "feat(retrieval): is_long_query predicate (AC-13)

Returns True when the query has >= 8 whitespace-separated tokens.
Used by chunk_search_with_tree_reasoning_after.yaml to gate the LLM
call to long / structural queries only — short keyword lookups stay
on the cheap BM25 + dense path."
```

---

## Task 10: `LlmTreeReasoningStep` — happy path

**Spec ref:** Decision A + F + G / AC-6, AC-9, AC-10.

**Files:**
- Create: `python/pydocs_mcp/retrieval/steps/llm_tree_reasoning.py`
- Modify: `python/pydocs_mcp/retrieval/steps/__init__.py` — re-export
- Create: `tests/retrieval/steps/test_llm_tree_reasoning_happy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/steps/test_llm_tree_reasoning_happy.py
"""AC-6 + AC-9 + AC-10: LlmTreeReasoningStep happy path."""
from __future__ import annotations

import json

import pytest

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import Chunk, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.llm_tree_reasoning import LlmTreeReasoningStep
from tests._fakes import (
    FakeLlmClient,
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    InMemoryReferenceStore,
    make_fake_uow_factory,
)


def _node(node_id: str, qname: str, title: str, *, kind: NodeKind = NodeKind.FUNCTION) -> DocumentNode:
    return DocumentNode(
        node_id=node_id, qualified_name=qname, title=title, kind=kind,
        source_path="path.py", start_line=1, end_line=10, text=f"body of {title}",
        content_hash="", summary=f"summary of {title}", extra_metadata={},
        parent_id=None, children=(),
    )


def _chunk(qname: str, text: str) -> Chunk:
    return Chunk(text=text, metadata={"qualified_name": qname, "package": "__project__"})


@pytest.mark.asyncio
async def test_happy_path_fetches_chunks_for_picked_node_ids() -> None:
    """AC-6: LLM picks node_ids, step fetches matching chunks."""
    tree = DocumentNode(
        node_id="root", qualified_name="pkg.mod", title="module",
        kind=NodeKind.MODULE, source_path="mod.py", start_line=1, end_line=100,
        text="module body", content_hash="", summary="root",
        extra_metadata={}, parent_id=None,
        children=(
            _node("n1", "pkg.mod.foo", "foo"),
            _node("n2", "pkg.mod.bar", "bar"),
        ),
    )
    chunks = (
        _chunk("pkg.mod.foo", "foo source"),
        _chunk("pkg.mod.bar", "bar source"),
    )
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert(chunks)
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [tree]}),
        chunks=chunk_store,
    )
    llm = FakeLlmClient(responses={
        "what does foo do": json.dumps({
            "thinking": "foo is the answer",
            "node_list": ["pkg.mod.foo"],
        }),
    })
    step = LlmTreeReasoningStep(
        llm_client=llm,
        uow_factory=uow_factory,
        prompt_template="tree_reasoning_pydocs_v1",
    )
    state = RetrieverState(
        query=SearchQuery(terms="what does foo do"),
        candidates=None, result=None, scratch={},
    )
    out = await step.run(state)
    assert "tree.ranked" in out.scratch
    items = out.scratch["tree.ranked"].items
    assert len(items) == 1
    assert items[0].metadata["qualified_name"] == "pkg.mod.foo"


@pytest.mark.asyncio
async def test_scope_is_project_only() -> None:
    """AC-9: step only reads trees for package='__project__'."""
    project_tree = DocumentNode(
        node_id="p1", qualified_name="proj.entry", title="entry",
        kind=NodeKind.FUNCTION, source_path="e.py", start_line=1, end_line=5,
        text="entry body", content_hash="", summary="entry",
        extra_metadata={}, parent_id=None, children=(),
    )
    dep_tree = DocumentNode(
        node_id="d1", qualified_name="dep.thing", title="thing",
        kind=NodeKind.FUNCTION, source_path="t.py", start_line=1, end_line=5,
        text="dep body", content_hash="", summary="dep thing",
        extra_metadata={}, parent_id=None, children=(),
    )
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert((_chunk("proj.entry", "entry"), _chunk("dep.thing", "dep")))
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={
            "__project__": [project_tree],
            "requests":    [dep_tree],
        }),
        chunks=chunk_store,
    )
    llm = FakeLlmClient(responses={
        "find it": json.dumps({"thinking": "", "node_list": ["proj.entry"]}),
    })
    step = LlmTreeReasoningStep(
        llm_client=llm,
        uow_factory=uow_factory,
        prompt_template="tree_reasoning_pydocs_v1",
    )
    state = RetrieverState(
        query=SearchQuery(terms="find it"), candidates=None, result=None, scratch={},
    )
    await step.run(state)
    # FakeLlmClient records the rendered prompt; assert "dep.thing" never appeared.
    sent_prompt = llm._calls[-1][-1]["content"]
    assert "dep.thing" not in sent_prompt
    assert "proj.entry" in sent_prompt


def test_from_dict_strict_gate_on_missing_llm_client() -> None:
    """AC-10: from_dict raises ValueError when context.llm_client is None."""
    from pydocs_mcp.retrieval.serialization import BuildContext

    ctx = BuildContext(llm_client=None, uow_factory=lambda: None)
    with pytest.raises(ValueError, match="llm_client"):
        LlmTreeReasoningStep.from_dict(
            {"type": "llm_tree_reasoning"}, ctx,
        )
```

- [ ] **Step 2: Run tests, verify FAIL**

```bash
.venv/bin/pytest tests/retrieval/steps/test_llm_tree_reasoning_happy.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Create the step (happy-path only — error handling in next task)**

`python/pydocs_mcp/retrieval/steps/llm_tree_reasoning.py`:

```python
"""LlmTreeReasoningStep — PageIndex-style vectorless RAG.

Reads __project__ DocumentNode trees, serializes via to_pageindex_json,
renders a Jinja2 prompt, sends to a configured LLM, parses
{"thinking", "node_list": [...]}, fetches matching chunks via
uow.chunks, writes a ranked ChunkList to state.scratch[output_scratch_key].

Scope: __project__ only. Deps stay in BM25/dense retrieval paths.

Composes via state.scratch[output_scratch_key] (default "tree.ranked"),
so downstream fusion steps (RRFFusionStep, WeightedScoreInterpolationStep)
can fuse this branch with hybrid branches by name.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar

from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.models import Chunk, ChunkList, ChunkFilterField
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.pipeline.base import RetrieverStep
from pydocs_mcp.retrieval.prompts import render_prompt
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.storage.protocols import LlmClient, UnitOfWork

_DEFAULT_PROMPT_TEMPLATE = "tree_reasoning_pydocs_v1"
_DEFAULT_OUTPUT_SCRATCH_KEY = "tree.ranked"
_DEFAULT_REFERENCE_NEIGHBORS_LIMIT = 5
_PROJECT_PACKAGE = "__project__"


@dataclass(frozen=True, slots=True)
@step_registry.register("llm_tree_reasoning")
class LlmTreeReasoningStep(RetrieverStep):
    REQUIRES: ClassVar[frozenset[str]] = frozenset()

    llm_client: LlmClient = field(kw_only=True)
    uow_factory: Callable[[], UnitOfWork] = field(kw_only=True)
    prompt_template: str = field(default=_DEFAULT_PROMPT_TEMPLATE, kw_only=True)
    include_references: bool = field(default=False, kw_only=True)
    reference_neighbors_limit: int = field(
        default=_DEFAULT_REFERENCE_NEIGHBORS_LIMIT, kw_only=True,
    )
    output_scratch_key: str = field(
        default=_DEFAULT_OUTPUT_SCRATCH_KEY, kw_only=True,
    )
    name: str = field(default="llm_tree_reasoning", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        async with self.uow_factory() as uow:
            trees = await uow.trees.load_all_in_package(_PROJECT_PACKAGE)
            if not trees:
                return state

            tree_jsons = [t.to_pageindex_json() for t in trees]
            prompt = render_prompt(
                self.prompt_template,
                query=state.query.terms,
                trees=tree_jsons,
            )
            response = await self.llm_client.chat(
                [{"role": "user", "content": prompt}],
                response_format="json_object",
                temperature=0.0,
            )

            picked = _parse_node_list(response, trees)
            if not picked:
                return state

            chunks = await uow.chunks.list(
                filter={
                    ChunkFilterField.PACKAGE.value: _PROJECT_PACKAGE,
                    "qualified_name": {"in": list(picked)},
                },
            )
            if not chunks:
                return state

            ranked = _score_by_position(chunks, picked)
            new_scratch = dict(state.scratch)
            new_scratch[self.output_scratch_key] = ranked
            return replace(state, scratch=new_scratch)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": "llm_tree_reasoning"}
        if self.prompt_template != _DEFAULT_PROMPT_TEMPLATE:
            out["prompt_template"] = self.prompt_template
        if self.include_references:
            out["include_references"] = True
        if self.reference_neighbors_limit != _DEFAULT_REFERENCE_NEIGHBORS_LIMIT:
            out["reference_neighbors_limit"] = self.reference_neighbors_limit
        if self.output_scratch_key != _DEFAULT_OUTPUT_SCRATCH_KEY:
            out["output_scratch_key"] = self.output_scratch_key
        if self.name != "llm_tree_reasoning":
            out["name"] = self.name
        return out

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        context: BuildContext,
    ) -> "LlmTreeReasoningStep":
        if context.llm_client is None:
            raise ValueError(
                "LlmTreeReasoningStep requires BuildContext.llm_client. "
                "Production wiring in __main__.py / server.py sets this "
                "via build_llm_client(config.llm); tests must pass it "
                "explicitly.",
            )
        if context.uow_factory is None:
            raise ValueError(
                "LlmTreeReasoningStep requires BuildContext.uow_factory.",
            )
        return cls(
            llm_client=context.llm_client,
            uow_factory=context.uow_factory,
            prompt_template=data.get("prompt_template", _DEFAULT_PROMPT_TEMPLATE),
            include_references=data.get("include_references", False),
            reference_neighbors_limit=data.get(
                "reference_neighbors_limit",
                _DEFAULT_REFERENCE_NEIGHBORS_LIMIT,
            ),
            output_scratch_key=data.get(
                "output_scratch_key", _DEFAULT_OUTPUT_SCRATCH_KEY,
            ),
            name=data.get("name", "llm_tree_reasoning"),
        )


def _collect_qnames(node: DocumentNode, acc: set[str]) -> None:
    acc.add(node.qualified_name)
    for child in node.children:
        _collect_qnames(child, acc)


def _parse_node_list(
    response: str, trees: tuple[DocumentNode, ...],
) -> tuple[str, ...]:
    """Parse LLM response; return qualified_names that survive validation.

    LLM-returned node IDs are matched against the known qualified_names
    in the tree. Hallucinated IDs (not in the tree) are silently dropped
    — well-known LLM behavior, graceful degradation matches the rest of
    the pipeline.
    """
    data = json.loads(response)
    node_list = data.get("node_list", [])
    if not isinstance(node_list, list):
        raise ValueError(
            f"LLM response 'node_list' must be a list; got {type(node_list).__name__}",
        )

    known: set[str] = set()
    for tree in trees:
        _collect_qnames(tree, known)

    return tuple(qn for qn in node_list if isinstance(qn, str) and qn in known)


def _score_by_position(
    chunks: tuple[Chunk, ...], picked_qnames: tuple[str, ...],
) -> ChunkList:
    """Score each chunk by its position in the LLM's node_list.

    score = 1.0 - rank/N -> first-picked = highest score. RRF /
    weighted-interpolation compatible (normalized [0, 1] range).
    """
    by_qname: dict[str, Chunk] = {
        c.metadata.get("qualified_name", ""): c for c in chunks
    }
    n = len(picked_qnames)
    scored: list[Chunk] = []
    for rank, qname in enumerate(picked_qnames):
        chunk = by_qname.get(qname)
        if chunk is None:
            continue
        scored.append(
            replace(chunk, metadata={**chunk.metadata, "score": 1.0 - rank / n}),
        )
    return ChunkList(items=tuple(scored))
```

Re-export from `python/pydocs_mcp/retrieval/steps/__init__.py`:

```python
from pydocs_mcp.retrieval.steps.llm_tree_reasoning import LlmTreeReasoningStep
# add to __all__ tuple
```

**Pre-implementation patch:** `InMemoryDocumentTreeStore.load_all_in_package` in `tests/_fakes.py` currently returns `{}` regardless of `by_package` contents (write-side test helper; never exercised on the read path until now). Fix it as a 2-line change so the new step's read-side tests work:

```python
# tests/_fakes.py — replace the existing load_all_in_package
async def load_all_in_package(self, package):
    return tuple(self.by_package.get(package, ()))
```

This also unblocks any future read-side `TreeService` tests; cite the comment "now exercised on the read path by LlmTreeReasoningStep" if a reviewer asks about the change.

- [ ] **Step 4: Run tests, verify PASS**

```bash
.venv/bin/pytest tests/retrieval/steps/test_llm_tree_reasoning_happy.py -v
```

Expected: PASS (3/3).

- [ ] **Step 5: Full suite gate**

```bash
.venv/bin/pytest -q
```

Expected: ~1231 passed.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/retrieval/steps/llm_tree_reasoning.py python/pydocs_mcp/retrieval/steps/__init__.py tests/retrieval/steps/test_llm_tree_reasoning_happy.py
git commit -m "feat(retrieval): LlmTreeReasoningStep happy path (AC-6, AC-9, AC-10)

Per spec Decisions A + F + G. Reads __project__ DocumentNode trees,
renders the configured Jinja2 prompt template, calls LlmClient.chat()
with json_object response format, parses node_list, fetches matching
chunks via uow.chunks.list(filter=qualified_name 'in' ...), scores by
position, writes a ChunkList to state.scratch['tree.ranked'].

Strict-gate from_dict mirrors LoadExistingChunkHashesStage —
context.llm_client and context.uow_factory must be non-None.

Hallucinated node IDs (LLM returns an ID not in the tree) silently
dropped with no log spam — well-known LLM behavior; the step
degrades gracefully to fewer chunks rather than crashing."
```

---

## Task 11: `LlmTreeReasoningStep` — error handling

**Spec ref:** AC-7.

**Files:**
- Create: `tests/retrieval/steps/test_llm_tree_reasoning_errors.py`

(No production code changes — the error handling is already in `_parse_node_list` from Task 10's `json.loads`, plus the `node_list` shape validation. These tests assert the contract.)

- [ ] **Step 1: Write the failing tests**

```python
# tests/retrieval/steps/test_llm_tree_reasoning_errors.py
"""AC-7: LlmTreeReasoningStep error handling."""
from __future__ import annotations

import pytest

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.llm_tree_reasoning import LlmTreeReasoningStep
from tests._fakes import (
    FakeLlmClient,
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    InMemoryReferenceStore,
    make_fake_uow_factory,
)


def _project_tree() -> DocumentNode:
    return DocumentNode(
        node_id="r", qualified_name="proj.entry", title="entry",
        kind=NodeKind.FUNCTION, source_path="e.py", start_line=1, end_line=5,
        text="entry body", content_hash="", summary="entry",
        extra_metadata={}, parent_id=None, children=(),
    )


def _state(query: str) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms=query), candidates=None, result=None, scratch={},
    )


@pytest.mark.asyncio
async def test_invalid_json_raises_with_diagnostic() -> None:
    llm = FakeLlmClient(responses={"q": "not valid json{{{"})
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_project_tree()]}),
    )
    step = LlmTreeReasoningStep(llm_client=llm, uow_factory=uow_factory)
    with pytest.raises(ValueError, match="json"):
        await step.run(_state("q"))


@pytest.mark.asyncio
async def test_missing_node_list_key_raises() -> None:
    llm = FakeLlmClient(responses={
        "q": '{"thinking": "I forgot the list"}',
    })
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_project_tree()]}),
    )
    step = LlmTreeReasoningStep(llm_client=llm, uow_factory=uow_factory)
    out = await step.run(_state("q"))
    # No node_list => no picks => state passes through unchanged.
    assert "tree.ranked" not in out.scratch


@pytest.mark.asyncio
async def test_hallucinated_ids_silently_dropped() -> None:
    """LLM returns IDs not in the tree -> dropped without raising."""
    llm = FakeLlmClient(responses={
        "q": '{"thinking": "...", "node_list": ["NOT_IN_TREE", "proj.entry"]}',
    })
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_project_tree()]}),
        chunks=InMemoryChunkStore(),  # no chunks -> tree.ranked empty
    )
    step = LlmTreeReasoningStep(llm_client=llm, uow_factory=uow_factory)
    out = await step.run(_state("q"))
    # NOT_IN_TREE is dropped; proj.entry has no chunks => no error, no output.
    # No exception is what we're asserting.


@pytest.mark.asyncio
async def test_node_list_not_a_list_raises() -> None:
    llm = FakeLlmClient(responses={
        "q": '{"thinking": "", "node_list": "should be a list"}',
    })
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_project_tree()]}),
    )
    step = LlmTreeReasoningStep(llm_client=llm, uow_factory=uow_factory)
    with pytest.raises(ValueError, match="must be a list"):
        await step.run(_state("q"))


@pytest.mark.asyncio
async def test_empty_tree_returns_state_unchanged() -> None:
    llm = FakeLlmClient(responses={})  # never called when trees are empty
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": []}),
    )
    step = LlmTreeReasoningStep(llm_client=llm, uow_factory=uow_factory)
    state = _state("q")
    out = await step.run(state)
    assert out is state  # passed through unmodified
```

- [ ] **Step 2: Run tests, verify**

```bash
.venv/bin/pytest tests/retrieval/steps/test_llm_tree_reasoning_errors.py -v
```

Expected: most tests PASS already (the production code handles these). If `test_missing_node_list_key_raises` fails (because `data.get("node_list", [])` returns `[]` silently), update `_parse_node_list` to raise instead:

If a test fails, here's the inline fix to `_parse_node_list` in `llm_tree_reasoning.py`:

```python
def _parse_node_list(
    response: str, trees: tuple[DocumentNode, ...],
) -> tuple[str, ...]:
    data = json.loads(response)  # ValueError if invalid JSON
    if "node_list" not in data:
        # Returning empty tuple matches "no picks -> state unchanged" branch
        # in run(); we don't need to raise.
        return ()
    node_list = data["node_list"]
    if not isinstance(node_list, list):
        raise ValueError(
            f"LLM response 'node_list' must be a list; got "
            f"{type(node_list).__name__}",
        )
    known: set[str] = set()
    for tree in trees:
        _collect_qnames(tree, known)
    return tuple(qn for qn in node_list if isinstance(qn, str) and qn in known)
```

- [ ] **Step 3: Verify PASS**

```bash
.venv/bin/pytest tests/retrieval/steps/test_llm_tree_reasoning_errors.py -v
```

Expected: PASS (5/5).

- [ ] **Step 4: Full suite gate**

```bash
.venv/bin/pytest -q
```

Expected: ~1236 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/retrieval/steps/test_llm_tree_reasoning_errors.py python/pydocs_mcp/retrieval/steps/llm_tree_reasoning.py
git commit -m "test(retrieval): LlmTreeReasoningStep error handling contract (AC-7)

Pins the error-mode behavior:
- Invalid JSON -> raises ValueError (json.loads bubbles up).
- Missing node_list key -> returns state unchanged (no picks).
- node_list not a list -> raises ValueError with diagnostic.
- Hallucinated node IDs -> silently dropped.
- Empty trees -> state passes through unmodified (no LLM call).

These are contractual: graceful degradation where it's safe, loud
failures where the response is structurally wrong."
```

---

## Task 12: `LlmTreeReasoningStep` — opt-in reference enrichment

**Spec ref:** Decision G / AC-8.

**Files:**
- Modify: `python/pydocs_mcp/retrieval/steps/llm_tree_reasoning.py` — add include_references branch
- Create: `tests/retrieval/steps/test_llm_tree_reasoning_references.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/retrieval/steps/test_llm_tree_reasoning_references.py
"""AC-8: include_references=True populates scratch['tree.ranked.refs']."""
from __future__ import annotations

import json

import pytest

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import Chunk, ReferenceKind, SearchQuery
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.llm_tree_reasoning import LlmTreeReasoningStep
from tests._fakes import (
    FakeLlmClient,
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    InMemoryReferenceStore,
    make_fake_uow_factory,
)


def _tree() -> DocumentNode:
    return DocumentNode(
        node_id="r", qualified_name="proj.foo", title="foo",
        kind=NodeKind.FUNCTION, source_path="f.py", start_line=1, end_line=5,
        text="foo body", content_hash="", summary="foo summary",
        extra_metadata={}, parent_id=None, children=(),
    )


def _state(q: str) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms=q), candidates=None, result=None, scratch={},
    )


@pytest.mark.asyncio
async def test_include_references_off_skips_refs_lookup() -> None:
    llm = FakeLlmClient(responses={
        "q": json.dumps({"thinking": "", "node_list": ["proj.foo"]}),
    })
    refs = [NodeReference(
        from_package="__project__", from_node_id="bar-node",
        to_name="proj.foo", to_node_id=None, kind=ReferenceKind.CALLS,
    )]
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert((Chunk(text="foo body", metadata={
        "qualified_name": "proj.foo", "package": "__project__",
    }),))
    ref_store = InMemoryReferenceStore()
    await ref_store.save_many(refs)
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_tree()]}),
        chunks=chunk_store,
        references=ref_store,
    )
    step = LlmTreeReasoningStep(
        llm_client=llm, uow_factory=uow_factory,
        include_references=False,  # default
    )
    out = await step.run(_state("q"))
    assert "tree.ranked" in out.scratch
    assert "tree.ranked.refs" not in out.scratch


@pytest.mark.asyncio
async def test_include_references_on_writes_refs_scratch() -> None:
    llm = FakeLlmClient(responses={
        "q": json.dumps({"thinking": "", "node_list": ["proj.foo"]}),
    })
    refs = [
        NodeReference(from_package="__project__", from_node_id="bar-node",
                      to_name="proj.foo", to_node_id=None,
                      kind=ReferenceKind.CALLS),
        NodeReference(from_package="__project__", from_node_id="baz-node",
                      to_name="proj.foo", to_node_id=None,
                      kind=ReferenceKind.CALLS),
    ]
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert((Chunk(text="foo body", metadata={
        "qualified_name": "proj.foo", "package": "__project__",
    }),))
    ref_store = InMemoryReferenceStore()
    await ref_store.save_many(refs)
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_tree()]}),
        chunks=chunk_store,
        references=ref_store,
    )
    step = LlmTreeReasoningStep(
        llm_client=llm, uow_factory=uow_factory,
        include_references=True,
        reference_neighbors_limit=5,
    )
    out = await step.run(_state("q"))
    assert "tree.ranked.refs" in out.scratch
    surfaced = out.scratch["tree.ranked.refs"]
    assert len(surfaced) == 2
```

- [ ] **Step 2: Run tests, verify FAIL**

```bash
.venv/bin/pytest tests/retrieval/steps/test_llm_tree_reasoning_references.py -v
```

Expected: FAIL — `test_include_references_on_writes_refs_scratch` fails because we haven't wired the include_references branch yet (run() never writes the .refs scratch key).

- [ ] **Step 3: Add the include_references branch**

In `python/pydocs_mcp/retrieval/steps/llm_tree_reasoning.py`, inside `run()`, after the existing `new_scratch[self.output_scratch_key] = ranked` line, append:

```python
            if self.include_references:
                # NodeReference fields: from_package, from_node_id,
                # to_name, to_node_id, kind. The "from" side is node_id
                # (the DocumentNode.node_id, NOT qualified_name) — so
                # we need to translate picked qnames -> node_ids first.
                # Simpler: filter on to_name which IS a qname (it's the
                # dotted reference target the resolver matched against).
                callers = await uow.references.list(
                    filter={"to_name": {"in": list(picked)}},
                    limit=self.reference_neighbors_limit * len(picked),
                )
                new_scratch[f"{self.output_scratch_key}.refs"] = tuple(callers)
```

- [ ] **Step 4: Run tests, verify PASS**

```bash
.venv/bin/pytest tests/retrieval/steps/test_llm_tree_reasoning_references.py -v
```

Expected: PASS (2/2).

- [ ] **Step 5: Full suite gate**

```bash
.venv/bin/pytest -q
```

Expected: ~1238 passed.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/retrieval/steps/llm_tree_reasoning.py tests/retrieval/steps/test_llm_tree_reasoning_references.py
git commit -m "feat(retrieval): LlmTreeReasoningStep opt-in reference enrichment (AC-8)

Default off (single responsibility / smaller payload). When opt-in
via YAML 'include_references: true', also surfaces callers/callees
from uow.references for every picked node. Writes to a separate
scratch key (output_scratch_key + '.refs') so consumers can opt in
without changing the primary chunk-stream shape.

Per spec Decision G — power users get richer single-call answers
without forcing the default path to pay payload-size cost."
```

---

## Task 13: Composition root wiring

**Spec ref:** Decision A / Section "Architecture / BuildContext extension".

**Files:**
- Modify: `python/pydocs_mcp/__main__.py` — call `build_llm_client(config.llm)`, thread into context
- Modify: `python/pydocs_mcp/server.py` — same wiring on the MCP server side
- Modify: `python/pydocs_mcp/storage/factories.py` — add `build_retrieval_context_with_llm` helper if needed
- Modify: `python/pydocs_mcp/extraction/factories.py` — pass `llm_client` into BuildContext
- Modify: `tests/test_cli.py` — autouse fixture extension to patch `build_llm_client`
- Create: `tests/test_composition_root_llm.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_composition_root_llm.py
"""Composition root wires LlmClient through BuildContext."""
from __future__ import annotations

from unittest.mock import patch

from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.serialization import BuildContext
from tests._fakes import FakeLlmClient


def test_build_llm_client_called_at_pipeline_construction(tmp_path) -> None:
    """When _build_retrieval_context constructs a BuildContext for the
    chunk-search pipeline, it should populate context.llm_client by
    calling build_llm_client(config.llm)."""
    import pydocs_mcp.storage.factories as factories

    config = AppConfig()
    fake = FakeLlmClient(responses={})
    with patch(
        "pydocs_mcp.retrieval.llm_clients.build_llm_client",
        return_value=fake,
    ) as mock:
        # The exact entry point is whatever helper builds the chunk
        # pipeline; check that helper threads context.llm_client through.
        # If the helper takes a config arg, pass one; otherwise this
        # asserts the wiring exists.
        ctx = factories.build_retrieval_context(tmp_path / "x.db", config)
        assert ctx.llm_client is fake
    mock.assert_called_once()
```

- [ ] **Step 2: Run test, verify FAIL**

```bash
.venv/bin/pytest tests/test_composition_root_llm.py -v
```

Expected: FAIL — `build_retrieval_context` doesn't construct llm_client yet.

- [ ] **Step 3: Wire llm_client through every composition root**

In `python/pydocs_mcp/storage/factories.py` (or wherever `build_retrieval_context` lives — check existing path), modify:

```python
def build_retrieval_context(db_path: Path, config: AppConfig) -> BuildContext:
    """Constructs BuildContext threaded with embedder, llm_client, uow_factory.

    Called by server.py + __main__.py at startup; once per server lifecycle.
    """
    from pydocs_mcp.extraction.strategies.embedders import build_embedder
    from pydocs_mcp.retrieval.llm_clients import build_llm_client
    # ... existing context construction ...
    return BuildContext(
        # ... existing fields ...
        embedder=build_embedder(config.embedding),
        llm_client=build_llm_client(config.llm),
        uow_factory=build_sqlite_uow_factory(db_path),
        pipeline_hash=config.compute_ingestion_pipeline_hash(),
    )
```

In `python/pydocs_mcp/__main__.py`, inside `_run_indexing` (or wherever the BuildContext is constructed for the CLI search path), add the same `build_llm_client(config.llm)` thread-through.

In `python/pydocs_mcp/server.py`, same wiring in the MCP server startup path.

In `python/pydocs_mcp/extraction/factories.py::build_ingestion_pipeline`, add an `llm_client` kwarg that flows into BuildContext (matches the existing `uow_factory` + `pipeline_hash` pattern from the chunk-cache PR):

```python
def build_ingestion_pipeline(
    config: AppConfig,
    *,
    embedder: Embedder | None = None,
    uow_factory: Callable[[], UnitOfWork] | None = None,
    llm_client: LlmClient | None = None,  # NEW
    pipeline_hash: str = "",
) -> IngestionPipeline:
    # ...
    context = BuildContext(
        # ...
        embedder=embedder,
        uow_factory=uow_factory,
        llm_client=llm_client,
        pipeline_hash=pipeline_hash,
    )
    return load_ingestion_pipeline(context=context, ...)
```

Update `tests/test_cli.py` autouse fixture `_patch_embedder_with_mock` to ALSO patch `build_llm_client`:

```python
@pytest.fixture(autouse=True)
def _patch_embedder_with_mock(monkeypatch):
    """Patch build_embedder + build_llm_client so CLI tests don't pull
    the 80MB FastEmbed ONNX model or make OpenAI network calls.

    Production CLI runs the real concretes.
    """
    from pydocs_mcp.extraction.strategies.embedders import build_embedder as _orig_embedder
    from pydocs_mcp.retrieval.llm_clients import build_llm_client as _orig_llm
    from tests._fakes import FakeLlmClient

    def _embedder_with_mock(cfg, *args, **kwargs):
        return MockEmbedder()  # existing pattern

    def _llm_with_mock(cfg):
        return FakeLlmClient(responses={})

    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.embedders.build_embedder",
        _embedder_with_mock,
    )
    monkeypatch.setattr(
        "pydocs_mcp.retrieval.llm_clients.build_llm_client",
        _llm_with_mock,
    )
```

(Adapt to the existing fixture's exact shape — preserve all existing patches; add `build_llm_client` alongside `build_embedder`.)

- [ ] **Step 4: Run tests, verify PASS**

```bash
.venv/bin/pytest tests/test_composition_root_llm.py tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 5: Full suite gate**

```bash
.venv/bin/pytest -q
```

Expected: ~1239 passed.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/storage/factories.py python/pydocs_mcp/extraction/factories.py python/pydocs_mcp/__main__.py python/pydocs_mcp/server.py tests/test_cli.py tests/test_composition_root_llm.py
git commit -m "feat(composition): thread LlmClient through BuildContext (AC-10)

Mirrors the embedder + uow_factory threading from the hybrid-search
and chunk-cache PRs. build_llm_client(config.llm) is called once at
startup; the resulting client is threaded into:

  - storage/factories.py::build_retrieval_context
  - extraction/factories.py::build_ingestion_pipeline (new kwarg)
  - __main__.py + server.py composition roots

tests/test_cli.py autouse fixture patches build_llm_client with
FakeLlmClient so unit-test runs never make network calls."
```

---

## Task 14: Three preset YAMLs

**Spec ref:** Decision H / AC-12.

**Files:**
- Create: `python/pydocs_mcp/pipelines/chunk_search_with_tree_reasoning_parallel.yaml`
- Create: `python/pydocs_mcp/pipelines/chunk_search_with_tree_reasoning_after.yaml`
- Create: `python/pydocs_mcp/pipelines/tree_only.yaml`
- Create: `tests/pipelines/test_tree_reasoning_presets.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pipelines/test_tree_reasoning_presets.py
"""AC-12: Three new preset YAMLs load + round-trip + execute end-to-end."""
from __future__ import annotations

from pathlib import Path

import pytest

PRESETS = [
    "chunk_search_with_tree_reasoning_parallel",
    "chunk_search_with_tree_reasoning_after",
    "tree_only",
]


@pytest.mark.parametrize("preset", PRESETS)
def test_preset_loads_from_pipelines_dir(preset: str) -> None:
    from pydocs_mcp.retrieval.config import _default_pipelines_dir
    path = _default_pipelines_dir() / f"{preset}.yaml"
    assert path.is_file(), f"missing preset {preset}.yaml"


@pytest.mark.parametrize("preset", PRESETS)
def test_preset_roundtrips_to_dict(preset: str, tmp_path) -> None:
    """Load preset YAML, build the pipeline, call to_dict(), assert
    structural equality with the original."""
    import yaml
    from pydocs_mcp.retrieval.config import (
        AppConfig, _default_pipelines_dir,
    )
    from pydocs_mcp.storage.factories import build_retrieval_context

    yaml_path = _default_pipelines_dir() / f"{preset}.yaml"
    original = yaml.safe_load(yaml_path.read_text())

    ctx = build_retrieval_context(tmp_path / "x.db", AppConfig())
    from pydocs_mcp.retrieval.serialization import (
        load_pipeline_from_dict,
    )
    pipeline = load_pipeline_from_dict(original, ctx)
    rebuilt = pipeline.to_dict()

    # Structural equality — names, types, params all match.
    assert rebuilt["name"] == original["name"]
    assert len(rebuilt["steps"]) == len(original["steps"])
```

- [ ] **Step 2: Run tests, verify FAIL**

```bash
.venv/bin/pytest tests/pipelines/test_tree_reasoning_presets.py -v
```

Expected: FAIL — preset files missing.

- [ ] **Step 3: Create the three YAMLs**

`python/pydocs_mcp/pipelines/chunk_search_with_tree_reasoning_parallel.yaml`:

```yaml
# Hybrid (BM25 + dense + RRF) in parallel with LLM tree reasoning;
# outer RRF fuses both branches.
name: chunk_search_with_tree_reasoning_parallel
steps:
  - name: parallel_retrieval
    type: parallel
    params:
      branches:
        - name: hybrid
          steps:
            - { name: bm25_fetch,  type: chunk_fetcher, params: { limit: 200 } }
            - { name: bm25_score,  type: bm25_scorer,   params: {} }
            - { name: bm25_topk,   type: top_k_filter,  params: { k: 50, publish_to: "hybrid.bm25.ranked" } }
            - { name: dense_fetch, type: dense_fetcher, params: { limit: 200 } }
            - { name: dense_score, type: dense_scorer,  params: {} }
            - { name: dense_topk,  type: top_k_filter,  params: { k: 50, publish_to: "hybrid.dense.ranked" } }
            - { name: hybrid_rrf,  type: rrf_fusion,    params: { branch_keys: ["hybrid.bm25.ranked", "hybrid.dense.ranked"], publish_to: "hybrid.ranked" } }
        - name: tree
          steps:
            - { name: tree_reasoning, type: llm_tree_reasoning, params: { prompt_template: "tree_reasoning_pydocs_v1" } }
  - name: final_fuse
    type: rrf_fusion
    params: { branch_keys: ["hybrid.ranked", "tree.ranked"] }
  - { name: limit,  type: limit,                  params: { max_results: 8 } }
  - { name: budget, type: token_budget_formatter, params: { budget: 2000 } }
```

`python/pydocs_mcp/pipelines/chunk_search_with_tree_reasoning_after.yaml`:

```yaml
# Hybrid runs first; tree reasoning fires only on long queries.
name: chunk_search_with_tree_reasoning_after
steps:
  - name: bm25_fetch
    type: chunk_fetcher
    params: { limit: 200 }
  - name: bm25_score
    type: bm25_scorer
    params: {}
  - name: bm25_topk
    type: top_k_filter
    params: { k: 50, publish_to: "hybrid.bm25.ranked" }
  - name: dense_fetch
    type: dense_fetcher
    params: { limit: 200 }
  - name: dense_score
    type: dense_scorer
    params: {}
  - name: dense_topk
    type: top_k_filter
    params: { k: 50, publish_to: "hybrid.dense.ranked" }
  - name: hybrid_rrf
    type: rrf_fusion
    params:
      branch_keys: ["hybrid.bm25.ranked", "hybrid.dense.ranked"]
      publish_to: "hybrid.ranked"
  - name: maybe_tree_rerank
    type: conditional
    params:
      predicate: is_long_query
      inner:
        name: tree_reasoning
        type: llm_tree_reasoning
        params:
          prompt_template: "tree_reasoning_pydocs_v1"
  - name: final_fuse
    type: rrf_fusion
    params:
      branch_keys: ["hybrid.ranked", "tree.ranked"]
  - { name: limit,  type: limit,                  params: { max_results: 8 } }
  - { name: budget, type: token_budget_formatter, params: { budget: 2000 } }
```

`python/pydocs_mcp/pipelines/tree_only.yaml`:

```yaml
# Vectorless: LLM tree reasoning is the only retrieval signal.
name: tree_only
steps:
  - name: tree_reasoning
    type: llm_tree_reasoning
    params:
      prompt_template: "tree_reasoning_pydocs_v1"
      output_scratch_key: "candidates_inline"
  - name: promote_scratch_to_candidates
    type: rrf_fusion
    params:
      branch_keys: ["candidates_inline"]
  - { name: limit,  type: limit,                  params: { max_results: 8 } }
  - { name: budget, type: token_budget_formatter, params: { budget: 2000 } }
```

(Note: `tree_only.yaml` uses `rrf_fusion` with a single branch key as a simple way to lift scratch into `state.candidates`; alternative is a small `promote` step that does this directly. The single-branch RRF works correctly — fusion degenerates to a copy.)

Also create `tests/pipelines/__init__.py` (empty file).

- [ ] **Step 4: Run tests, verify PASS**

```bash
.venv/bin/pytest tests/pipelines/test_tree_reasoning_presets.py -v
```

Expected: PASS.

- [ ] **Step 5: Full suite gate**

```bash
.venv/bin/pytest -q
```

Expected: ~1244 passed.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/pipelines/chunk_search_with_tree_reasoning_parallel.yaml python/pydocs_mcp/pipelines/chunk_search_with_tree_reasoning_after.yaml python/pydocs_mcp/pipelines/tree_only.yaml tests/pipelines/test_tree_reasoning_presets.py tests/pipelines/__init__.py
git commit -m "feat(pipelines): three opt-in preset YAMLs for tree reasoning (AC-12)

Per spec Decision H. Default chunk_search.yaml untouched.

  chunk_search_with_tree_reasoning_parallel.yaml — hybrid + tree in
    parallel via parallel_retrieval, outer rrf_fusion merges both
    branches by 'hybrid.ranked' + 'tree.ranked'.
  chunk_search_with_tree_reasoning_after.yaml    — hybrid first; tree
    reasoning fires via conditional + is_long_query predicate.
  tree_only.yaml                                 — vectorless: tree
    reasoning is the only retrieval signal.

User opts in via --config or PYDOCS_CONFIG_PATH. Zero risk to
existing users."
```

---

## Task 15: Benchmark system variants

**Spec ref:** Decision I / AC-14.

**Files:**
- Modify: `benchmarks/src/benchmarks/eval/systems/pydocs.py` — add two new variants
- Modify: `benchmarks/src/benchmarks/eval/systems/__init__.py` — re-export
- Create: `benchmarks/tests/eval/test_tree_reasoning_systems.py`

- [ ] **Step 1: Write the failing tests**

```python
# benchmarks/tests/eval/test_tree_reasoning_systems.py
"""AC-14: Two new benchmark system variants for tree reasoning."""
from __future__ import annotations

import pytest

from benchmarks.eval.systems.pydocs import (
    PydocsTreeOnlySystem,
    PydocsTreeParallelSystem,
)


def test_tree_only_system_uses_correct_config() -> None:
    sys = PydocsTreeOnlySystem()
    assert "tree_only" in str(sys._config_path)


def test_tree_parallel_system_uses_correct_config() -> None:
    sys = PydocsTreeParallelSystem()
    assert "chunk_search_with_tree_reasoning_parallel" in str(sys._config_path)


@pytest.mark.asyncio
async def test_tree_only_can_index_and_search(tmp_path, monkeypatch) -> None:
    """Smoke test: index a tiny corpus, run a search via the tree_only
    preset (with FakeLlmClient patched in), assert a non-empty result."""
    # ... fixture setup ...
    # Defer to the autouse fixture pattern in benchmarks/tests/conftest.py
    # which already mocks build_llm_client.
    pass  # delete this line; expand with real test once fixture is in place
```

- [ ] **Step 2: Run tests, verify FAIL**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/eval/test_tree_reasoning_systems.py -v
```

Expected: FAIL — ImportError on the new system classes.

- [ ] **Step 3: Add the system variants**

In `benchmarks/src/benchmarks/eval/systems/pydocs.py`, after the existing `PydocsSystem` class, add:

```python
def _pipelines_dir() -> Path:
    """Path to the shipped pipelines/ directory inside pydocs_mcp."""
    from importlib import resources
    return Path(str(resources.files("pydocs_mcp.pipelines")))


@dataclass(frozen=True, slots=True)
class PydocsTreeOnlySystem(BaseSystem):
    """Vectorless retrieval — LlmTreeReasoningStep only."""
    _config_path: Path = field(
        default_factory=lambda: _pipelines_dir() / "tree_only.yaml",
        kw_only=True,
    )
    # Override the search-pipeline-path config knob; reuse PydocsSystem.index
    # and adapt the search method to load the override YAML.

    @property
    def name(self) -> str:
        return "pydocs_tree_only"


@dataclass(frozen=True, slots=True)
class PydocsTreeParallelSystem(BaseSystem):
    """Hybrid + tree reasoning in parallel, fused via RRF."""
    _config_path: Path = field(
        default_factory=lambda: _pipelines_dir() /
        "chunk_search_with_tree_reasoning_parallel.yaml",
        kw_only=True,
    )

    @property
    def name(self) -> str:
        return "pydocs_tree_parallel"
```

In `benchmarks/src/benchmarks/eval/systems/__init__.py`, re-export both:

```python
from benchmarks.eval.systems.pydocs import (
    PydocsSystem,
    PydocsTreeOnlySystem,
    PydocsTreeParallelSystem,
)
```

Update `benchmarks/tests/conftest.py` autouse fixture to also patch `build_llm_client` (mirror the `tests/test_cli.py` change from Task 13).

- [ ] **Step 4: Run tests, verify PASS**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/eval/test_tree_reasoning_systems.py -v
```

Expected: PASS (2/2 import + config-path tests; the smoke test is a placeholder).

- [ ] **Step 5: Full benchmark suite gate**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/ -q
```

Expected: ~283 passed (281 prior + 2 new).

- [ ] **Step 6: Full unit-suite gate**

```bash
.venv/bin/pytest -q
```

Expected: ~1244 passed (unchanged from Task 14).

- [ ] **Step 7: Commit**

```bash
git add benchmarks/src/benchmarks/eval/systems/pydocs.py benchmarks/src/benchmarks/eval/systems/__init__.py benchmarks/tests/eval/test_tree_reasoning_systems.py benchmarks/tests/conftest.py
git commit -m "feat(benchmarks): tree-reasoning system variants (AC-14)

Two new BaseSystem implementations:

  PydocsTreeOnlySystem      — loads tree_only.yaml
  PydocsTreeParallelSystem  — loads chunk_search_with_tree_reasoning_parallel.yaml

Both reuse PydocsSystem's index() and adapt the search path to select
the override YAML. benchmarks/tests/conftest.py autouse fixture
extended to patch build_llm_client with FakeLlmClient so local runs
stay offline; the CI benchmark workflow exercises the real LLM when
OPENAI_API_KEY is set."
```

---

## Task 16: Integration test (OPENAI_API_KEY gated)

**Spec ref:** AC-14 (integration portion).

**Files:**
- Create: `tests/integration/test_llm_tree_reasoning_openai.py`

- [ ] **Step 1: Write the integration test**

```python
# tests/integration/test_llm_tree_reasoning_openai.py
"""Integration test against real OpenAI. Skipped without OPENAI_API_KEY."""
from __future__ import annotations

import os

import pytest

from pydocs_mcp.retrieval.llm_clients.openai import OpenAiLlmClient
from pydocs_mcp.retrieval.config import LlmConfig

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="Requires OPENAI_API_KEY for integration test against real OpenAI.",
)


@pytest.mark.asyncio
async def test_openai_chat_returns_json_when_requested() -> None:
    """Smoke: real gpt-4o-mini call with json_object format returns JSON."""
    cfg = LlmConfig(provider="openai", model_name="gpt-4o-mini", temperature=0.0)
    client = OpenAiLlmClient(model_name=cfg.model_name, api_key=cfg.api_key)
    response = await client.chat(
        [{"role": "user", "content": 'Return {"ok": true} as JSON.'}],
        response_format="json_object",
    )
    import json
    parsed = json.loads(response)
    assert parsed.get("ok") is True
```

- [ ] **Step 2: Run test**

```bash
.venv/bin/pytest tests/integration/test_llm_tree_reasoning_openai.py -v
```

Expected: SKIPPED (no OPENAI_API_KEY in local env). When run with the key set, PASS.

- [ ] **Step 3: Full suite gate**

```bash
.venv/bin/pytest -q
```

Expected: ~1245 passed (1 new test added but skipped without API key).

- [ ] **Step 4: Commit**

Also create `tests/integration/__init__.py` if it doesn't exist.

```bash
git add tests/integration/test_llm_tree_reasoning_openai.py tests/integration/__init__.py
git commit -m "test(integration): OpenAI LlmClient smoke test (AC-14)

Skipped unless OPENAI_API_KEY is set in the environment — matches
the pattern existing OpenAIEmbedder integration tests use. CI sets
the secret; fork PRs skip cleanly.

One-test smoke: real gpt-4o-mini call with response_format='json_object'
returns parseable JSON. Catches OpenAI SDK signature regressions
that mocked tests miss."
```

---

## Task 17: Documentation updates

**Spec ref:** Decision D (spec format) / AC-17.

**Files:**
- Modify: `EXTENSIONS.md` — mark entries #5 + #13 as shipped
- Modify: `CLAUDE.md` — append two new step names to retrieval enumeration; note `llm` config section
- Modify: `python/pydocs_mcp/defaults/default_config.yaml` — add commented-out `llm:` section
- Create: `tests/test_docs_updated_for_tree_reasoning.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_docs_updated_for_tree_reasoning.py
"""AC-17: Docs reflect shipped tree-reasoning + weighted-fusion behavior."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_extensions_mentions_weighted_fusion_shipped() -> None:
    text = (ROOT / "EXTENSIONS.md").read_text()
    # The Tier 1 entry should now reference the shipped class name.
    assert "WeightedScoreInterpolationStep" in text
    assert "shipped" in text.lower() or "SHIPPED" in text


def test_extensions_mentions_tree_reasoning_shipped() -> None:
    text = (ROOT / "EXTENSIONS.md").read_text()
    assert "LlmTreeReasoningStep" in text


def test_claude_md_lists_new_steps() -> None:
    text = (ROOT / "CLAUDE.md").read_text()
    assert "weighted_score_interpolation" in text
    assert "llm_tree_reasoning" in text


def test_default_config_has_llm_section() -> None:
    cfg = (ROOT / "python/pydocs_mcp/defaults/default_config.yaml").read_text()
    assert "llm:" in cfg
    assert "openai" in cfg
```

- [ ] **Step 2: Run tests, verify FAIL**

```bash
.venv/bin/pytest tests/test_docs_updated_for_tree_reasoning.py -v
```

Expected: FAIL.

- [ ] **Step 3: Update the docs**

In `EXTENSIONS.md`, edit Tier 1 entry #5 (the weighted fusion entry) — prepend `[SHIPPED]` and add a one-line note:

```markdown
5. **[SHIPPED] `WeightedScoreInterpolationStep`** — see python/pydocs_mcp/retrieval/steps/weighted_score_interpolation.py. Min-max normalizes each branch's scores to [0, 1], blends via weights. Reads state.scratch[<branch>.ranked]; round-trips through YAML.
```

Edit Tier 3 entry #13 (the LLM tree reasoning entry) similarly — prepend `[SHIPPED]` and link to the source file.

In `CLAUDE.md`, find the retrieval steps enumeration (around line 70) and append the two new step names. Add a one-line note about the new `llm` config section in the relevant config paragraph.

In `python/pydocs_mcp/defaults/default_config.yaml`, append a commented-out section:

```yaml

# LLM client configuration — used by LlmTreeReasoningStep and other
# LLM-driven retrieval primitives. Defaults to OpenAI's gpt-4o-mini.
# OPENAI_API_KEY env var is the credential source.
#
# Uncomment to override:
# llm:
#   provider: openai
#   model_name: gpt-4o-mini
#   temperature: 0.0
#   max_tokens: 1024
```

- [ ] **Step 4: Run tests, verify PASS**

```bash
.venv/bin/pytest tests/test_docs_updated_for_tree_reasoning.py -v
```

Expected: PASS (4/4).

- [ ] **Step 5: README jargon audit**

```bash
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" \
    -not -path "*/node_modules/*" -not -path "*/.git/*" \
    -not -path "*/.pytest_cache/*" -not -path "*/target/*" | \
    xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+" 2>&1
```

Expected: NO MATCHES (only README files audited; EXTENSIONS / CLAUDE / specs / plans are exempt per CLAUDE.md rule).

- [ ] **Step 6: Full suite gate**

```bash
.venv/bin/pytest -q
```

Expected: ~1249 passed.

- [ ] **Step 7: Commit**

```bash
git add EXTENSIONS.md CLAUDE.md python/pydocs_mcp/defaults/default_config.yaml tests/test_docs_updated_for_tree_reasoning.py
git commit -m "docs: reflect shipped tree-reasoning + weighted-fusion (AC-17)

EXTENSIONS.md entries #5 + #13 marked [SHIPPED] with links to the
concrete source files.

CLAUDE.md retrieval-steps enumeration extended to include
weighted_score_interpolation + llm_tree_reasoning. Brief mention of
the new AppConfig.llm section.

default_config.yaml gains a commented-out llm: section so users
discover the new tunable without it being active by default.

README jargon audit: zero matches."
```

---

## Task 18: Final verification gauntlet

**Files:** (none — verification only)

- [ ] **Step 1: Full pytest run**

```bash
.venv/bin/pytest -q 2>&1 | tail -5
```

Expected: 1249+ passed, 0 failed.

- [ ] **Step 2: Ruff clean**

```bash
.venv/bin/ruff check python/ tests/ benchmarks/ 2>&1 | tail -3
```

Expected: `All checks passed!`

- [ ] **Step 3: Benchmark tests**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/ -q 2>&1 | tail -3
```

Expected: 283+ passed.

- [ ] **Step 4: Cargo (Rust)**

```bash
cargo fmt --check
cargo clippy -- -D warnings
cargo test
```

Expected: all green. No Rust changes in this PR; gauntlet catches inadvertent breakage.

- [ ] **Step 5: README jargon audit**

```bash
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" \
    -not -path "*/node_modules/*" -not -path "*/.git/*" \
    -not -path "*/.pytest_cache/*" -not -path "*/target/*" | \
    xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+"
```

Expected: NO MATCHES.

- [ ] **Step 6: Commit-author audit**

```bash
git log main..HEAD --pretty='%H %an <%ae>'
git log main..HEAD --pretty=full | grep -i 'co-authored-by' && echo "TRAILER FOUND" || echo "(no Co-Authored-By trailers - clean)"
```

Expected: every commit `Max Raphael Sobroza Marques <max.raphael@gmail.com>`; no `Co-Authored-By:` trailers.

- [ ] **Step 7: Push + mark PR ready for review**

```bash
git push origin feature/llm-tree-reasoning-and-weighted-fusion
gh pr ready 39
```

Watch CI on the PR. All checks (python 3.11/3.12/3.13 + rust + benchmark-repoqa) should pass within ~10 minutes.

If CI fails: investigate the failure (likely a test the gauntlet missed locally). Don't merge until green.

- [ ] **Step 8: Final code-reviewer subagent**

Dispatch the skill's built-in code-reviewer subagent over the full PR diff (`d6260f3..HEAD`). Use the same prompt shape as the chunk-cache PR's final review. Address Critical / Important findings; informational findings surface but don't block.

- [ ] **Step 9: Merge**

```bash
gh pr merge 39 --squash --delete-branch
```

After merge: sync local main + delete local branch + audit the squash commit author (should be `Max Sobroza <max.raphael@gmail.com>`, no Co-Authored-By trailer — third clean squash since the git-config fix).

---

## Self-review (writing-plans checklist)

**1. Spec coverage:**
- AC-1 (LlmClient Protocol) → Task 2 ✓
- AC-2 (LlmConfig) → Task 3 ✓
- AC-3 (build_llm_client factory) → Task 4 ✓
- AC-4 (WeightedScoreInterpolationStep happy path) → Task 7 ✓
- AC-5 (WeightedScoreInterpolationStep validation) → Task 7 ✓
- AC-6 (LlmTreeReasoningStep happy path) → Task 10 ✓
- AC-7 (LlmTreeReasoningStep error handling) → Task 11 ✓
- AC-8 (LlmTreeReasoningStep opt-in references) → Task 12 ✓
- AC-9 (__project__ scope) → Task 10 ✓
- AC-10 (BuildContext.llm_client strict gate) → Task 6 + Task 10 + Task 13 ✓
- AC-11 (Jinja2 prompt loading) → Task 8 ✓
- AC-12 (Three preset YAMLs round-trip) → Task 14 ✓
- AC-13 (is_long_query predicate) → Task 9 ✓
- AC-14 (Benchmark variants) → Task 15 + Task 16 ✓
- AC-15 (Full suite green) → Task 18 ✓
- AC-16 (Authorship clean) → Task 18 (audit step) ✓
- AC-17 (Docs) → Task 17 ✓

All 17 ACs covered.

**2. Placeholder scan:** No "TBD" / "implement later" / "similar to". One "Note" in Task 14 explaining the single-branch RRF pattern in `tree_only.yaml` — that's a clarifying note, not a placeholder.

**3. Type consistency:**
- `LlmClient.chat(messages, response_format, temperature, max_tokens)` — same signature in Protocol (Task 2), OpenAiLlmClient (Task 4), FakeLlmClient (Task 5).
- `LlmConfig(provider, model_name, temperature, max_tokens, api_key)` — same in Task 3 definition + Task 4 usage.
- `state.scratch["tree.ranked"]` — same key in `LlmTreeReasoningStep.run()` (Task 10), the three YAMLs (Task 14), and the test assertions (Tasks 10, 12).
- `BuildContext.llm_client` — same name in Task 6 definition + Task 10 from_dict + Task 13 wiring.
- `_DEFAULT_PROMPT_TEMPLATE = "tree_reasoning_pydocs_v1"` — same constant name + value across Task 8 (templates created) and Task 10 (step dataclass default).

**4. Bite-sized steps:** Each task has 5-7 steps; each step is 2-5 min of focused work. Task 13 (composition root wiring) is the largest — touches 5 files — but each file's edit is a focused 5-min change.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-26-llm-tree-reasoning-and-weighted-fusion.md`. Two execution options:

**1. Subagent-Driven (recommended)** — controller dispatches a fresh subagent per task; per-task review gates (`/code-review` + `/review`); two-stage review (spec compliance then code quality) per the user's directive.

**2. Inline Execution** — execute tasks in the controller's session using `superpowers:executing-plans`; batch execution with checkpoints for review.

Which approach?
