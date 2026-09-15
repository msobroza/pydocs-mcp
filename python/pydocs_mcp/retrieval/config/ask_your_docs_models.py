"""Ask-your-docs agent config sub-models.

Spec 2026-07-11-multimodal-image-agent §3.5 (architecture, multimodal, images)
and 2026-09-05-ask-your-docs-llm-connection-design §5.1 (the ``llm`` block).

The first agent-side consumer of AppConfig — sanctioned because agent architecture
choice and multimodal-detection strategy are "A/B-testable against a benchmark"
behaviors (CLAUDE.md §MCP API surface vs YAML configuration litmus test). Light
pydantic only: importing this from the ``[harness-ask-your-docs]`` extra pulls no
heavy deps. Defaults are duplicated in ``defaults/default_config.yaml`` on purpose
— the YAML is the user-visible knob (CLAUDE.md §Default values).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Re-exported: these moved to their own modules to keep this one inside its line budget.
from pydocs_mcp.retrieval.config.ask_your_docs_image_models import ImagesConfig
from pydocs_mcp.retrieval.config.ask_your_docs_llm_models import (
    _DEFAULT_API_KEY_ENV,  # noqa: F401 — re-export for the existing import path
    _DEFAULT_MODEL,  # noqa: F401 — re-export for the existing import path
    _DEFAULT_RENEW_ON_STATUS,  # noqa: F401 — re-export for the existing import path
    AuthMode,
    LlmAuthConfig,
    LlmConnectionConfig,
    VisionModelConfig,
    VisionRule,
)
from pydocs_mcp.retrieval.config.ask_your_docs_multimodal_models import (
    MultimodalConfig,
    MultimodalDetectionConfig,
)
from pydocs_mcp.retrieval.config.ask_your_docs_scope_models import (
    ANY_PROJECT,
    ScopeBranchDefault,
    ScopeCode,
    ScopeDefaultsConfig,
    ScopeSlice,
)
from pydocs_mcp.retrieval.config.ask_your_docs_ui_models import AskYourDocsUiConfig

# Single source (CLAUDE.md §Default values): harness modules import this, never the literal.
# Agent turns one question may take, on the chat page and in a campaign alike
# (harness/ask_your_docs/turn_budget.py turns it into graph steps).
_DEFAULT_MAX_AGENT_TURNS = 12


class AskYourDocsConfig(BaseModel):
    """Top-level ``ask_your_docs:`` block — architecture, multimodal, LLM connection, UI."""

    model_config = ConfigDict(extra="forbid")

    # One of agent_registry.names(); "text_react" pins pre-image behavior
    # exactly, "auto" routes by the detected capability.
    architecture: str = Field(default="auto")
    # Agent turns one question may take. The eval binding carries its own
    # per-arm value (a campaign dimension); both turn it into graph steps
    # through the same ``turn_run_config``.
    max_agent_turns: int = Field(default=_DEFAULT_MAX_AGENT_TURNS, ge=1)
    # Run one search_codebase with the user's question, verbatim, before the
    # model's first turn, and show the model that finished call. OFF because it
    # spends a call on every question whether or not the turn needed retrieval;
    # on repoqa-qa/small_test the verbatim question retrieved 0.90 at k=10
    # against 0.73-0.77 for the queries the model wrote itself.
    seed_search_with_question: bool = Field(default=False)
    multimodal: MultimodalConfig = Field(default_factory=MultimodalConfig)
    images: ImagesConfig = Field(default_factory=ImagesConfig)
    # Soft (project, branch, slice) defaults for the chat and graph pages.
    scope: ScopeDefaultsConfig = Field(default_factory=ScopeDefaultsConfig)
    # Endpoint, bearer and vision rule; None = today (vendor default, OPENAI_API_KEY via SDK).
    llm: LlmConnectionConfig | None = Field(default=None)
    # The chat page's activity panel; display only (ask_your_docs_ui_models.py).
    ui: AskYourDocsUiConfig = Field(default_factory=AskYourDocsUiConfig)


__all__ = (
    "ANY_PROJECT",
    "AskYourDocsConfig",
    "AuthMode",
    "ImagesConfig",
    "LlmAuthConfig",
    "LlmConnectionConfig",
    "MultimodalConfig",
    "MultimodalDetectionConfig",
    "ScopeBranchDefault",
    "ScopeCode",
    "ScopeDefaultsConfig",
    "ScopeSlice",
    "VisionModelConfig",
    "VisionRule",
)
