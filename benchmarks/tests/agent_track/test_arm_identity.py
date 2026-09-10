"""The external track's arm identity — the other half of its resume key.

Base-install by contract, so this module (and everything it exercises) must
work with no ``pydocs_mcp`` import: the arm hash is a plain string the
orchestrator treats as opaque.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import os
import subprocess
import sys
import textwrap
from dataclasses import replace
from pathlib import Path

import pydocs_eval

from pydocs_eval.agent_track._arm import (
    EXTERNAL_DELIVERY_MAP,
    SKILL_BLOCK_CHANNEL,
    TASK_PROMPT_SUFFIX_CHANNEL,
    _external_cell,
    _TASK_NAME_PLACEHOLDER,
    external_arm_hash,
)
from pydocs_eval.agent_track._guidance import deliverable_section_keys
from pydocs_eval.agent_track._types import AgentTrackConfig, ArmConfig
from pydocs_eval.arm_identity import NO_GUIDANCE_FINGERPRINT

_DATASET = "swe-qa-pro"

# The src root to hand a probe subprocess on PYTHONPATH — derived from the
# imported package so the pin works regardless of how pytest was invoked
# (the ``test_registry_population`` idiom).
_SRC_ROOT = str(Path(pydocs_eval.__file__).resolve().parents[1])

# The golden digest of the all-defaults external arm. It is a PERSISTED key:
# every ledger row written under this arm carries it, and a resume matches on
# it exactly. Moving this literal orphans every ledger ever written and forces
# a full re-spend of both arms plus the judge — so it may only change as a
# deliberate, reviewed measurement bump (the doctrine ADR 0017 applies to
# ``CellConfig.to_dict()`` golden bytes), never as a drive-by side effect of
# renaming a cell key or touching the shared canonicalizer.
#
# MOVED 2026-07-28 (external guidance delivery), deliberately: the delivery map
# went from the one-key ``{"guidance": "task_prompt_suffix"}`` stub to the three
# pattern keys routed to ``append_system_prompt.skill_block``, the cell gained
# ``settings.task_name``, and it gained ``guidance_channels`` — the channels a
# pass actually delivered on, which the static map cannot express. All three are
# first-order arm state. Cost of the re-key: zero — no campaign has been
# recorded and no ledger is committed.
#   f5b2649c… → 0576f4de…
_GOLDEN_DEFAULT_ARM_HASH = "0576f4de990e38145db14f1f74081dbc18343e8b8760dc34e95531cbae960557"


def test_the_default_arm_hash_matches_its_golden_bytes() -> None:
    assert (
        external_arm_hash(AgentTrackConfig(), dataset=_DATASET, guidance_fingerprint="abc")
        == _GOLDEN_DEFAULT_ARM_HASH
    )


def test_the_hash_is_deterministic() -> None:
    cfg = AgentTrackConfig()
    assert external_arm_hash(cfg, dataset=_DATASET, guidance_fingerprint="g") == external_arm_hash(
        cfg, dataset=_DATASET, guidance_fingerprint="g"
    )
    assert len(external_arm_hash(cfg, dataset=_DATASET, guidance_fingerprint="g")) == 64


def test_the_guidance_fingerprint_moves_the_hash() -> None:
    # The load-bearing property for PairedAgentFitness: the seed pass and a
    # candidate pass now share ONE ledger file and are separated only by this.
    cfg = AgentTrackConfig()
    assert external_arm_hash(cfg, dataset=_DATASET, guidance_fingerprint="seed") != (
        external_arm_hash(cfg, dataset=_DATASET, guidance_fingerprint="candidate")
    )
    assert external_arm_hash(
        cfg, dataset=_DATASET, guidance_fingerprint=NO_GUIDANCE_FINGERPRINT
    ) != (external_arm_hash(cfg, dataset=_DATASET, guidance_fingerprint=""))


def test_an_arm_surface_change_moves_the_hash() -> None:
    base = AgentTrackConfig()
    narrowed = replace(
        base,
        arms=tuple(replace(arm, tools=("Read",)) if arm.mcp else arm for arm in base.arms),
    )
    assert external_arm_hash(base, dataset=_DATASET, guidance_fingerprint="g") != (
        external_arm_hash(narrowed, dataset=_DATASET, guidance_fingerprint="g")
    )


def test_a_model_change_moves_the_hash() -> None:
    base = AgentTrackConfig()
    other = replace(base, arms=tuple(replace(arm, model="other-model") for arm in base.arms))
    assert external_arm_hash(base, dataset=_DATASET, guidance_fingerprint="g") != (
        external_arm_hash(other, dataset=_DATASET, guidance_fingerprint="g")
    )
    assert external_arm_hash(base, dataset=_DATASET, guidance_fingerprint="g") != (
        external_arm_hash(
            replace(base, judge_model="other-judge"), dataset=_DATASET, guidance_fingerprint="g"
        )
    )


def test_the_dataset_moves_the_hash() -> None:
    # --dataset defaults to ONE ledger path, so two corpora share a file; the
    # corpus an answer was produced over is part of what the arm measures
    # (the normative cell's ``dataset`` key).
    cfg = AgentTrackConfig()
    assert external_arm_hash(cfg, dataset="swe-qa-pro", guidance_fingerprint="g") != (
        external_arm_hash(cfg, dataset="crosscommitvuln", guidance_fingerprint="g")
    )


def test_the_task_scaffold_version_moves_the_hash(monkeypatch) -> None:
    # The §8 silent-reuse failure the widened resume key exists to prevent:
    # editing the scaffold (and bumping its version) must re-run every task
    # instead of reusing answers produced under the old instructions.
    import pydocs_eval.agent_track._arm as arm_module

    before = external_arm_hash(AgentTrackConfig(), dataset=_DATASET, guidance_fingerprint="g")
    monkeypatch.setattr(arm_module, "TASK_SCAFFOLD_VERSION", "task_scaffold_v2")
    after = external_arm_hash(AgentTrackConfig(), dataset=_DATASET, guidance_fingerprint="g")
    assert before != after


def test_every_run_level_knob_of_the_cell_moves_the_hash() -> None:
    # One row per knob folded into ``settings`` / the per-arm cells: a knob that
    # can move an answer but not the hash is a silent-reuse hole.
    base = AgentTrackConfig()
    variants = {
        "rng_seed": replace(base, rng_seed=base.rng_seed + 1),
        "task_timeout_seconds": replace(base, task_timeout_seconds=base.task_timeout_seconds + 1),
        "arm.name": replace(base, arms=tuple(replace(a, name=f"{a.name}!") for a in base.arms)),
        "arm.max_turns": replace(base, arms=tuple(replace(a, max_turns=1) for a in base.arms)),
        "arm.mcp": replace(base, arms=tuple(replace(a, mcp=not a.mcp) for a in base.arms)),
        # no_tools only where mcp is off — ``ArmConfig`` rejects the combination.
        "arm.no_tools": replace(
            base, arms=tuple(replace(a, no_tools=True) if not a.mcp else a for a in base.arms)
        ),
        "arm.tools": replace(base, arms=tuple(replace(a, tools=("Read",)) for a in base.arms)),
    }
    baseline = external_arm_hash(base, dataset=_DATASET, guidance_fingerprint="g")
    moved = {
        knob
        for knob, cfg in variants.items()
        if external_arm_hash(cfg, dataset=_DATASET, guidance_fingerprint="g") != baseline
    }
    assert moved == set(variants)


def test_budget_guardrails_do_not_move_the_hash() -> None:
    # Deliberate: max_tasks / max_usd bound a RUN, not what an arm measures.
    # Folding them in would re-key the ledger on every budget tweak and force
    # a needless re-spend of answers that are still valid.
    base = AgentTrackConfig()
    tweaked = replace(base, max_tasks=3, max_usd=1.0)
    assert external_arm_hash(base, dataset=_DATASET, guidance_fingerprint="g") == (
        external_arm_hash(tweaked, dataset=_DATASET, guidance_fingerprint="g")
    )


def test_the_cell_names_the_runner_and_the_delivery_channel() -> None:
    cell = _external_cell(AgentTrackConfig(), dataset=_DATASET)
    assert cell["runner"].endswith(":ClaudeAgentRunner")
    # Three PATTERN keys (the ``<task_name>`` placeholder is the map's documented
    # convention — the base-install floor forbids enumerating the product's task
    # names here), all routed to the one guidance channel this harness delivers.
    assert EXTERNAL_DELIVERY_MAP == {
        "BACKBONE": "append_system_prompt.skill_block",
        "TASK_HEAD: <task_name>": "append_system_prompt.skill_block",
        "HARNESS_TASK_HEAD: external.<task_name>": "append_system_prompt.skill_block",
    }
    assert [arm["name"] for arm in cell["arms"]] == [a.name for a in AgentTrackConfig().arms]


def test_the_delivery_map_is_exactly_what_the_fold_delivers() -> None:
    # The map is the HASHED statement of what this harness delivers, so it must
    # not be a hand-kept duplicate of the fold's tier set: narrowing or widening
    # ``deliverable_section_keys`` has to move the map's hash by construction,
    # otherwise a delivery change resumes rows written under the old delivery.
    assert tuple(EXTERNAL_DELIVERY_MAP) == deliverable_section_keys(_TASK_NAME_PLACEHOLDER)


def test_the_delivered_channel_moves_the_hash() -> None:
    # Two channels can carry the SAME artifact (the mapped skill block and the
    # legacy task-prompt blob), and the fingerprint is identical for both — so
    # the channel list is the only term that can keep one delivery from
    # resuming the other's ledger rows.
    cfg = AgentTrackConfig()
    digests = {
        channels: external_arm_hash(
            cfg, dataset=_DATASET, guidance_fingerprint="g", guidance_channels=channels
        )
        for channels in (
            (),
            (TASK_PROMPT_SUFFIX_CHANNEL,),
            (SKILL_BLOCK_CHANNEL,),
            (TASK_PROMPT_SUFFIX_CHANNEL, SKILL_BLOCK_CHANNEL),
        )
    }
    assert len(set(digests.values())) == len(digests)
    # The no-guidance default is what the bare CLI track mints.
    assert digests[()] == external_arm_hash(cfg, dataset=_DATASET, guidance_fingerprint="g")


def test_the_task_name_moves_the_hash() -> None:
    # The task name selects WHICH task head and harness task head fold into the
    # delivered block, so two runs of the same candidate under different task
    # names deliver different text — a different arm, not a resumable one.
    base = AgentTrackConfig()
    assert external_arm_hash(base, dataset=_DATASET, guidance_fingerprint="g") != (
        external_arm_hash(
            replace(base, task_name="vuln"), dataset=_DATASET, guidance_fingerprint="g"
        )
    )
    assert external_arm_hash(
        replace(base, task_name="vuln"), dataset=_DATASET, guidance_fingerprint="g"
    ) != (
        external_arm_hash(
            replace(base, task_name="repo_qa"), dataset=_DATASET, guidance_fingerprint="g"
        )
    )


def test_arm_config_cells_are_order_stable_and_json_shaped() -> None:
    cfg = replace(
        AgentTrackConfig(),
        arms=(ArmConfig(name="solo", tools=("Read", "Grep")),),
    )
    cell = _external_cell(cfg, dataset=_DATASET)
    assert cell["arms"][0]["tools"] == ["Read", "Grep"]


def _imported_module_names(module: object) -> set[str]:
    """Every dotted name the module's source imports (parsed, not grepped)."""
    source = Path(inspect.getfile(module)).read_text(encoding="utf-8")  # type: ignore[arg-type]
    tree = ast.parse(source)
    return {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }


def test_the_whole_agent_track_package_stays_base_install_library_free() -> None:
    # ADR 0009's 2026-07-27 floor: ``pydocs-eval-agent-track`` is a BASE console
    # script, so NO module reachable from it may import pydocs_mcp. Parsed, not
    # grepped, so a comment cannot pass.
    #
    # WIDENED 2026-07-28 from three modules to the whole package, in the commit
    # that extracted the argv builder and the transcript fold into the product:
    # that is the moment delegating "just here, behind a guard" becomes
    # tempting, and a module-scope product import in ``_command`` would have
    # passed every previous probe while breaking the console script on a base
    # install. The copy-plus-parity ruling only holds while this is enforced.
    import pydocs_eval.agent_track as package
    import pydocs_eval.arm_identity as identity_module

    package_dir = Path(inspect.getfile(package)).parent
    modules = [identity_module]
    for path in sorted(package_dir.glob("*.py")):
        modules.append(importlib.import_module(f"{package.__name__}.{path.stem}"))
    assert len(modules) > 10, "the package walk found suspiciously few modules"

    for module in modules:
        imported = _imported_module_names(module)
        offending = sorted(name for name in imported if name.startswith("pydocs_mcp"))
        assert not offending, f"{module.__name__} imports {offending}"


def test_computing_an_arm_hash_never_imports_pydocs_mcp() -> None:
    # The AST pin above covers module scope only; ``arm_identity`` imports its
    # canonicalizer function-locally, dragging in the whole ``trajectory``
    # package at CALL time. Block pydocs_mcp on the meta path in a clean
    # interpreter so an execution-time violation fails here rather than as a
    # bare ImportError inside a paid run's ``_run``.
    probe = textwrap.dedent(
        """
        import sys

        class _BlockPydocsMcp:
            def find_module(self, name, path=None):
                return self.find_spec(name, path)

            def find_spec(self, name, path=None, target=None):
                if name == "pydocs_mcp" or name.startswith("pydocs_mcp."):
                    raise AssertionError(f"base-install floor violated: imported {name!r}")
                return None

        sys.meta_path.insert(0, _BlockPydocsMcp())
        from pydocs_eval.agent_track._arm import external_arm_hash
        from pydocs_eval.agent_track._guidance import fold_guidance
        from pydocs_eval.agent_track._types import AgentTrackConfig

        digest = external_arm_hash(
            AgentTrackConfig(), dataset="swe-qa-pro", guidance_fingerprint="x"
        )
        assert len(digest) == 64
        # The guidance fold runs on the same base console script, so its
        # execution-time floor is probed here too.
        assert fold_guidance({"BACKBONE": "b"}, task_name="vuln") == "b"
        assert not [m for m in sys.modules if m.startswith("pydocs_mcp")], "pydocs_mcp resident"
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": _SRC_ROOT},
    )
    assert completed.returncode == 0, completed.stderr
