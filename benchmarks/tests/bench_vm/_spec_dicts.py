"""Minimal valid run-spec mappings per kind, and a writer for spec files."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMITTED_RUNS_DIR = REPO_ROOT / "infra" / "bench-vm" / "runs"


def retrieval_spec(**overrides: object) -> dict[str, object]:
    return {
        "git_ref": "main",
        "kind": "retrieval",
        "configs": ["benchmarks/configs/baseline.yaml"],
        **overrides,
    }


def agent_track_spec(**overrides: object) -> dict[str, object]:
    return {
        "git_ref": "main",
        "kind": "agent_track",
        "confirm_spend": True,
        "max_usd": 20.0,
        **overrides,
    }


def before_after_spec(**overrides: object) -> dict[str, object]:
    return {
        "git_ref": "main",
        "kind": "before_after",
        "dataset": "repoqa",
        "split": "small_test",
        "configs": ["benchmarks/configs/ask_openrouter_qwen3_4b.yaml"],
        "baseline_ref": "v0.5.1",
        "candidate_ref": "main",
        "confirm_spend": True,
        "max_usd": 5,
        **overrides,
    }


def write_spec(directory: Path, stem: str, spec: dict[str, object]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.yaml"
    path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return path
