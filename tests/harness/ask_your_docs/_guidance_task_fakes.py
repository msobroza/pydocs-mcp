"""Named fakes for the ``guidance_task`` served-prompt tests (issue #391).

Each fake stands in for ONE external seam of a real ask-your-docs build, so a
test reads the system message the real build path hands a model:

- :class:`FakeChatModelFactory` — ``agent.build_chat_model``: every model the build
  asks for is one recording :class:`FakeLlm` (no network, no key read).
- :class:`FakeSessionStartPack` — ``agent.build_session_start_context_for_agent_prompt``:
  a fixed pack, so the fold order after the pack is observable.
- :class:`FakeTracedServeSession` — ``binding._serve_session_tools``: binds no tools
  and writes the serve child's trace header, so ``run_task`` finds its trace.
- :class:`FakeOverCapPackagedSeed` — the skill loader's packaged-resource reader: the
  packaged seed with ``BACKBONE`` inflated one token past its cap.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Mapping
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from ._agent_fakes import FakeLlm
from ._binding_fakes import record_server_calls


class FakeChatModelFactory:
    """Stands in for ``build_chat_model``; ``llm.calls`` holds every served message list."""

    def __init__(self) -> None:
        self.llm = FakeLlm()

    def __call__(self, _connection: Any, _bearer: Any, **_kwargs: Any) -> FakeLlm:
        return self.llm

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeChatModelFactory:
        from pydocs_mcp.harness.ask_your_docs import agent as agent_module

        monkeypatch.setattr(agent_module, "build_chat_model", self)
        return self

    def served_system_message(self) -> str:
        """The system message of the first model call — what the model was served."""
        return str(self.llm.calls[0][0].content)


class FakeSessionStartPack:
    """Stands in for the session-start pack builder; always returns ``text``."""

    def __init__(self, text: str) -> None:
        self.text = text

    async def __call__(self, _workspace: str, _config_path: str | None) -> str:
        return self.text

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeSessionStartPack:
        from pydocs_mcp.harness.ask_your_docs import agent as agent_module

        monkeypatch.setattr(agent_module, "build_session_start_context_for_agent_prompt", self)
        return self


class FakeTracedServeSession:
    """Stands in for ``binding._serve_session_tools``: header-only trace, no tools."""

    @contextlib.asynccontextmanager
    async def session(self, _settings: object, trace_env: Mapping[str, str]) -> AsyncIterator[list]:
        await record_server_calls(trace_env, [])
        yield []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeTracedServeSession:
        from pydocs_mcp.harness.ask_your_docs import binding

        monkeypatch.setattr(binding, "_serve_session_tools", self.session)
        return self


class FakeOverCapPackagedSeed:
    """Stands in for ``importlib.resources`` in the skill loader: the packaged seed,
    rewritten under ``directory`` with ``BACKBONE`` one token over its cap."""

    def __init__(self, directory: Path) -> None:
        from pydocs_mcp.application.description_source import CHARS_PER_TOKEN, render_sections
        from pydocs_mcp.harness.core import skill_artifact_loader as loader

        sections = packaged_seed_sections()
        over_cap = (loader.BACKBONE_TOKEN_BUDGET + 1) * CHARS_PER_TOKEN
        sections[loader.BACKBONE_HEADER] = "x" * over_cap
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        (directory / loader._SEED_FILENAME).write_text(render_sections(sections), "utf-8")

    def files(self, _package: object) -> Path:
        return self.directory

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeOverCapPackagedSeed:
        from pydocs_mcp.harness.core import skill_artifact_loader

        monkeypatch.setattr(skill_artifact_loader, "resources", self)
        return self


def packaged_seed_sections() -> dict[str, str]:
    """The packaged seed's ten sections, read the way the loader reads them — also
    the shape of an eval arm's ``guidance_sections`` mapping."""
    from pydocs_mcp.application.description_source import parse_sections
    from pydocs_mcp.harness.core import skill_artifact_loader as loader

    text = resources.files(loader._SEED_PACKAGE).joinpath(loader._SEED_FILENAME).read_text("utf-8")
    return dict(parse_sections(text, allowed=loader.SKILL_ARTIFACT_HEADERS))
