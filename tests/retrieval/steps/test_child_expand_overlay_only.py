"""child_expand is overlay-only: nothing shipped reaches the step (#393).

The default-off half of the byte-identity contract: no shipped pipeline YAML
names the step and no default search route reaches it, so the shipped
configuration searches exactly as it did before the step existed. (The wired
twin, ``tests/test_child_expand_wire.py``, pins the rendered side.)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pydocs_mcp.retrieval.config import AppConfig, _resolve_pipeline_path

_SHIPPED_PIPELINES = Path(__file__).resolve().parents[3] / "python" / "pydocs_mcp" / "pipelines"


def _load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _types_in(node: object) -> set[str]:
    """Every step ``type`` named anywhere in a pipeline YAML, nested forms included."""
    if isinstance(node, list):
        return set().union(*map(_types_in, node))
    if not isinstance(node, dict):
        return set()
    own = {node["type"]} if isinstance(node.get("type"), str) else set()
    return own.union(*map(_types_in, node.values()))


def test_no_shipped_pipeline_names_the_step() -> None:
    shipped = sorted(_SHIPPED_PIPELINES.glob("*.yaml"))

    named = [p.name for p in shipped if "child_expand" in _types_in(_load_yaml(p))]

    assert len(shipped) >= 18 and named == []


def test_no_default_search_route_reaches_the_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Shipped defaults only: no env path, no ./pydocs-mcp.yaml, no ~/.config file.
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    routes = AppConfig.load().pipelines["chunk"].routes

    reached = set().union(
        *(_types_in(_load_yaml(_resolve_pipeline_path(r.pipeline_path))) for r in routes)
    )

    assert "graph_expand" in reached  # the walk sees the shipped default's steps
    assert "child_expand" not in reached
