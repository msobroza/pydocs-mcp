"""The serving fingerprint: what an arm was SERVED under, recorded key by key.

``ArmVariant.digest`` folds what the agent runs under (architecture, turn cap,
the merged llm block, harness keys, guidance, scaffold). It does not see what
the serving side decides: the serving YAML's own content (embedder, discovery,
retrieval settings — the file's text as written, not files it names by path nor
the environment layer ``AppConfig.load`` adds), the endpoint a ``--base-url``
named, the llm block an arm that pins none is served from that file, or the
index the arm served. Two arms that agree on the digest can still differ in any of these,
and ``--reuse-baseline`` then compares a candidate against a baseline served
differently (issue #385, owner follow-up 2026-11-08).

So every arm records a :class:`ServingFingerprint` — a flat ``key -> value``
mapping — in ``serving.json`` beside its ``arm.json``. Flat and per key, so a
refusal names ``llm.params.temperature`` or ``index.embedder_dim``, not a hash.
The llm block each arm actually uses is flattened in too: the arm's own merged
block, or — only when it pins none — the serving file's ``ask_your_docs.llm``,
the one the binding falls back to. A pinned block's keys are already in the
digest, but the digest only says THAT something differs.

The file's raw content, not the loaded ``AppConfig``: a dump of the loaded model
would carry every default of the product doing the loading, so a product commit
that adds one config field would make every earlier arm unreusable.

A sidecar rather than a field of ``arm.json``: a run that reuses nothing keeps
writing the bytes it always wrote (pinned by a golden test), and an arm written
before this file existed reads as "not recorded" — never as a match.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from pydocs_eval.campaign.before_after import flat_settings
from pydocs_eval.campaign.before_after_arm_settings import (
    ARCHITECTURE_KEY,
    LLM_KEY,
    MAX_AGENT_TURNS_KEY,
    MODEL_KEY,
)
from pydocs_eval.campaign.before_after_corpora import IndexIdentity
from pydocs_eval.campaign.index_cache import resolve_scope_id

if TYPE_CHECKING:  # the product is imported function-locally (eval floor)
    from pydocs_mcp.retrieval.config.app_config import AppConfig

    from pydocs_eval.campaign.before_after import ArmLlmBlock

SERVING_FINGERPRINT_FILENAME = "serving.json"
_FINGERPRINT_FIELD = "fingerprint"

# What the plan and the fingerprint say when nothing named an endpoint — the SDK's own host.
VENDOR_DEFAULT_ENDPOINT = "vendor default"

# The serving YAML's ``ask_your_docs`` keys an arm never takes from the file, each
# compared under its own key instead: ``llm`` reaches an arm only as the pinned
# block (the binding refuses file-sourced model settings) and the model flag,
# ``architecture`` comes from the arm's variant, ``max_agent_turns`` is the turn
# cap. Folding them would make the chat runner's YAML — which must carry its
# agent's settings — differ from the serving file a plan points ``--config`` at,
# though both serve the same thing.
_AGENT_KEYS_COMPARED_ELSEWHERE = frozenset({LLM_KEY, ARCHITECTURE_KEY, MAX_AGENT_TURNS_KEY})
_AGENT_SECTION = "ask_your_docs"
_ABSENT = "<absent>"
_PLANS_BASELINE = "the plan's baseline"
# Not a JSON value, so it can never equal one a fingerprint recorded.
_MISSING = object()


class ServingKey(StrEnum):
    """The fingerprint's fixed keys; the llm block adds one ``llm.<path>`` key per leaf."""

    SERVING_CONFIG = "serving_config"
    ENDPOINT = "endpoint"
    EMBEDDER_MODEL = "index.embedder_model"
    EMBEDDER_DIM = "index.embedder_dim"
    SCOPE_ID = "index.scope_id"


def serving_config_digest(path: Path) -> str:
    """SHA-256 of the serving YAML's parsed content — never its path or its comments.

    Raises:
        OSError / yaml.YAMLError: the file cannot be read or parsed.
    """
    return _content_digest(_serving_yaml(path))


def _serving_yaml(path: Path) -> Mapping[str, object]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, Mapping) else {}


def _content_digest(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(_served_part(payload), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _file_llm_block(payload: Mapping[str, object]) -> dict[str, object] | None:
    """The file's ``ask_your_docs.llm``: what an arm that pins no block is served."""
    section = payload.get(_AGENT_SECTION)
    block = section.get(LLM_KEY) if isinstance(section, Mapping) else None
    return dict(block) if isinstance(block, Mapping) else None


def _served_part(payload: Mapping[str, object]) -> dict[str, object]:
    """The YAML minus the agent keys compared elsewhere; an emptied section is dropped."""
    section = payload.get(_AGENT_SECTION)
    if not isinstance(section, Mapping):
        return dict(payload)
    rest = {k: v for k, v in section.items() if k not in _AGENT_KEYS_COMPARED_ELSEWHERE}
    served = {k: v for k, v in payload.items() if k != _AGENT_SECTION}
    return {**served, _AGENT_SECTION: rest} if rest else served


def effective_endpoint(
    *, cli_url: str | None, block: Mapping[str, object] | None, file_url: str | None
) -> str:
    """The host the plan prints: ``--base-url`` over the arm block over the file."""
    block_url = None if block is None else block.get("base_url")
    return str(cli_url or block_url or file_url or VENDOR_DEFAULT_ENDPOINT)


def index_identity_of(config: AppConfig) -> IndexIdentity:
    """The index a serving config can serve: its embedder and its ingestion scope."""
    return IndexIdentity(
        embedder_model=config.embedding.model_name,
        embedder_dim=config.embedding.dim,
        scope_id=resolve_scope_id(None, config=config),
    )


@dataclass(frozen=True, slots=True)
class ServingFingerprint:
    """One arm's serving settings, flat: ``key -> JSON value``."""

    values: Mapping[str, object]

    def differing_keys(self, expected: ServingFingerprint) -> tuple[str, ...]:
        """Every key either side holds whose values differ, sorted.

        A key one side records as ``null`` and the other never records differs:
        "explicitly unset" and "not set" can reach the harness differently.
        """
        keys = sorted({*self.values, *expected.values})
        mine, theirs = self.values, expected.values
        return tuple(k for k in keys if mine.get(k, _MISSING) != theirs.get(k, _MISSING))

    def difference_lines(
        self, expected: ServingFingerprint, *, expected_label: str = _PLANS_BASELINE
    ) -> list[str]:
        """One ``key: recorded X, <expected_label> Y`` per differing key."""
        return [
            f"{key}: recorded {_shown(self.values, key)}, "
            f"{expected_label} {_shown(expected.values, key)}"
            for key in self.differing_keys(expected)
        ]


def _shown(values: Mapping[str, object], key: str) -> str:
    return repr(values[key]) if key in values else _ABSENT


@dataclass(frozen=True, slots=True)
class ServingBasis:
    """What the serving side decides for BOTH arms; each arm adds its own llm block.

    ``cli_url`` is ``--base-url`` (it beats any block); ``file_llm`` is the
    serving file's ``ask_your_docs.llm``, served only to an arm that pins none.
    """

    config_digest: str
    cli_url: str | None
    file_llm: Mapping[str, object] | None
    index: IndexIdentity

    def fingerprint(self, arm_block: Mapping[str, object] | None) -> ServingFingerprint:
        """The fingerprint of one arm, whose pinned llm block is ``arm_block`` (or None)."""
        served = dict((self.file_llm if arm_block is None else arm_block) or {})
        # The model is the identity gate's own field (``--model`` beats any block).
        served.pop(MODEL_KEY, None)
        endpoint = self.cli_url or served.get("base_url") or VENDOR_DEFAULT_ENDPOINT
        values: dict[str, object] = {
            ServingKey.SERVING_CONFIG: self.config_digest,
            ServingKey.ENDPOINT: endpoint,
            ServingKey.EMBEDDER_MODEL: self.index.embedder_model,
            ServingKey.EMBEDDER_DIM: self.index.embedder_dim,
            ServingKey.SCOPE_ID: self.index.scope_id,
            **{f"{LLM_KEY}.{key}": value for key, value in flat_settings(served)},
        }
        # Normalized through JSON, so a fresh fingerprint equals its recorded copy
        # (a tuple reads back as a list, a StrEnum key as its string).
        return ServingFingerprint(values=json.loads(json.dumps(values, default=str)))


@dataclass(frozen=True, slots=True)
class ServingSettings:
    """What the serving YAML decides for BOTH arms — read once, printed in the plan.

    ``endpoint`` is the plan's printed host; each arm's fingerprint resolves its
    own from ``basis`` and the block that arm pins.
    """

    endpoint: str
    max_agent_turns: int
    basis: ServingBasis

    @property
    def identity(self) -> IndexIdentity:
        return self.basis.index


def serving_settings_from_args(
    args: argparse.Namespace, llm_block: ArmLlmBlock | None
) -> ServingSettings:
    """Read the serving YAML once: the endpoint, the turn budget, the index identity.

    The endpoint follows the harness's OWN precedence (:func:`effective_endpoint`),
    so the plan names the host the run will really talk to, not one a serving
    file happens to still mention.

    The index identity is what a per-corpus workspace has to match: a bundle
    built with another embedder cannot be served by this config at all, so the
    plan checks it before an arm starts rather than letting the serve child fail.
    """
    from pydocs_mcp.retrieval.config.app_config import AppConfig

    config = AppConfig.load(args.config)
    ask = config.ask_your_docs
    block = None if llm_block is None else llm_block.settings
    file_url = ask.llm.base_url if ask.llm else None
    payload = _serving_yaml(Path(args.config))
    basis = ServingBasis(
        config_digest=_content_digest(payload),
        cli_url=args.base_url,
        file_llm=_file_llm_block(payload),
        index=index_identity_of(config),
    )
    return ServingSettings(
        endpoint=effective_endpoint(cli_url=args.base_url, block=block, file_url=file_url),
        max_agent_turns=int(ask.max_agent_turns),
        basis=basis,
    )


def write_serving_fingerprint(arm_dir: Path, fingerprint: ServingFingerprint) -> Path:
    """Persist the fingerprint beside the arm's ``arm.json``; return the file written."""
    path = arm_dir / SERVING_FINGERPRINT_FILENAME
    payload = {_FINGERPRINT_FIELD: dict(fingerprint.values)}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def read_serving_fingerprint(arm_dir: Path) -> ServingFingerprint | None:
    """The fingerprint ``arm_dir`` recorded; None when it recorded none (an older arm).

    Raises:
        ValueError: the file exists but holds no ``fingerprint`` mapping.
    """
    path = arm_dir / SERVING_FINGERPRINT_FILENAME
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    recorded = payload.get(_FINGERPRINT_FIELD) if isinstance(payload, Mapping) else None
    if not isinstance(recorded, Mapping):
        raise ValueError(
            f"{path} holds {payload!r}; expected "
            f"{{{_FINGERPRINT_FIELD!r}: {{<serving key>: <value>, ...}}}}"
        )
    return ServingFingerprint(values=dict(recorded))


__all__ = (
    "SERVING_FINGERPRINT_FILENAME",
    "VENDOR_DEFAULT_ENDPOINT",
    "ServingBasis",
    "ServingFingerprint",
    "ServingKey",
    "ServingSettings",
    "effective_endpoint",
    "index_identity_of",
    "read_serving_fingerprint",
    "serving_config_digest",
    "serving_settings_from_args",
    "write_serving_fingerprint",
)
