"""Route A candidate injection (ADR 0017 §Decision 5): the rollout env slot
additively gains ``PYDOCS_SERVE__DESCRIPTIONS_PATH`` so the served product binds
the candidate description surface. Additive — non-candidate rollouts unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydocs_eval.agent_track._types import ArmConfig
from pydocs_eval.trajectory.rollout import (
    RolloutRequest,
    trace_env_map,
    write_trace_mcp_config,
)


def test_trace_env_map_omits_descriptions_by_default() -> None:
    """No descriptions_path ⇒ byte-identical to the pre-Route-A env (correlation only)."""
    env = trace_env_map(trajectory_id="t", trace_dir=Path("/d"))
    assert env == {"PYDOCS_TRACE__TRAJECTORY_ID": "t", "PYDOCS_TRACE__DIR": "/d"}


def test_trace_env_map_injects_descriptions_path() -> None:
    """A candidate path adds the product override var additively."""
    env = trace_env_map(
        trajectory_id="t", trace_dir=Path("/d"), descriptions_path=Path("/c/desc.md")
    )
    assert env["PYDOCS_SERVE__DESCRIPTIONS_PATH"] == "/c/desc.md"
    assert env["PYDOCS_TRACE__TRAJECTORY_ID"] == "t"  # trace vars untouched


def _request(tmp_path: Path, *, descriptions_path: Path | None) -> RolloutRequest:
    return RolloutRequest(
        arm=ArmConfig(name="indexed", mcp=True),
        prompt="q",
        workspace=tmp_path / "ws",
        corpus_dir=tmp_path / "corpus",
        python=Path("/venv/bin/python"),
        trace_dir=tmp_path / "trace",
        trajectory_id="123e4567-e89b-12d3-a456-426614174000",
        instance_id="i1",
        claude_cli_version="2.1.76",
        descriptions_path=descriptions_path,
    )


def test_mcp_config_carries_descriptions_env_for_candidate(tmp_path: Path) -> None:
    """The indexed arm's .mcp.json env block carries the candidate override."""
    path = write_trace_mcp_config(_request(tmp_path, descriptions_path=tmp_path / "desc.md"))
    assert path is not None
    server = json.loads(path.read_text())["mcpServers"]["pydocs-mcp"]
    assert server["env"]["PYDOCS_SERVE__DESCRIPTIONS_PATH"] == str(tmp_path / "desc.md")


def test_mcp_config_omits_descriptions_env_without_candidate(tmp_path: Path) -> None:
    """A plain rollout's .mcp.json has no descriptions override — additive-only."""
    path = write_trace_mcp_config(_request(tmp_path, descriptions_path=None))
    assert path is not None
    server = json.loads(path.read_text())["mcpServers"]["pydocs-mcp"]
    assert "PYDOCS_SERVE__DESCRIPTIONS_PATH" not in server["env"]


def test_route_a_env_var_matches_product_constant() -> None:
    """The re-declared env var name is byte-for-byte the product's single source.

    rollout.py re-declares the name (not imports it) to stay base-install
    importable; this parity test pins it against the product so a rename fails
    loudly rather than silently mis-injecting.
    """
    from pydocs_mcp.application.description_override import DESCRIPTIONS_PATH_ENV_VAR

    from pydocs_eval.trajectory import rollout

    assert rollout._SERVE_DESCRIPTIONS_PATH_ENV == DESCRIPTIONS_PATH_ENV_VAR
