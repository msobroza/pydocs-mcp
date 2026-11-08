"""The serving fingerprint: what an arm was SERVED under, recorded key by key.

``ArmVariant.digest`` folds what the agent runs under (architecture, turn cap,
the merged llm block, harness keys, guidance, scaffold). It does not see what
the serving side decides: the serving YAML's own content (embedder, discovery,
retrieval pipelines), the endpoint a ``--base-url`` named, or the index the arm
served. Two arms that agree on the digest can still differ in any of these,
and ``--reuse-baseline`` then compares a candidate against a baseline served
differently (issue #385, owner follow-up 2026-11-08).

So every arm records a :class:`ServingFingerprint` — a flat ``key -> value``
mapping — in ``serving.json`` beside its ``arm.json``. Flat and per key, so a
refusal names ``llm.params.temperature`` or ``index.embedder_dim``, not a hash.
The llm block is flattened in too: those keys are already in the digest, but
the digest only says THAT something differs.

A sidecar rather than a field of ``arm.json``: a run that reuses nothing keeps
writing the bytes it always wrote (pinned by a golden test), and an arm written
before this file existed reads as "not recorded" — never as a match.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

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
_AGENT_KEYS_COMPARED_ELSEWHERE = frozenset({"llm", "architecture", "max_agent_turns"})
_AGENT_SECTION = "ask_your_docs"
_LLM_PREFIX = "llm"
_ABSENT = "<absent>"


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
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    canonical = json.dumps(_served_part(payload), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    """The host the run really talks to: ``--base-url`` over the arm block over the file."""
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
        """Every key either side holds whose values differ, sorted."""
        keys = sorted({*self.values, *expected.values})
        return tuple(k for k in keys if self.values.get(k) != expected.values.get(k))

    def difference_lines(self, expected: ServingFingerprint) -> list[str]:
        """One ``key: recorded X, the plan's baseline Y`` per differing key."""
        return [
            f"{key}: recorded {_shown(self.values, key)}, "
            f"the plan's baseline {_shown(expected.values, key)}"
            for key in self.differing_keys(expected)
        ]


def _shown(values: Mapping[str, object], key: str) -> str:
    return repr(values[key]) if key in values else _ABSENT


@dataclass(frozen=True, slots=True)
class ServingBasis:
    """What the serving side decides for BOTH arms; each arm adds its own llm block."""

    config_digest: str
    endpoint: str
    index: IndexIdentity

    def fingerprint(self, llm_block: Mapping[str, object] | None) -> ServingFingerprint:
        values: dict[str, object] = {
            ServingKey.SERVING_CONFIG: self.config_digest,
            ServingKey.ENDPOINT: self.endpoint,
            ServingKey.EMBEDDER_MODEL: self.index.embedder_model,
            ServingKey.EMBEDDER_DIM: self.index.embedder_dim,
            ServingKey.SCOPE_ID: self.index.scope_id,
            **dict(_flattened(_LLM_PREFIX, llm_block or {})),
        }
        # Normalized through JSON, so a fresh fingerprint equals its recorded copy
        # (a tuple reads back as a list, a StrEnum key as its string).
        return ServingFingerprint(values=json.loads(json.dumps(values, default=str)))


@dataclass(frozen=True, slots=True)
class ServingSettings:
    """What the serving YAML decides for BOTH arms — read once, printed in the plan."""

    endpoint: str
    max_agent_turns: int
    identity: IndexIdentity
    config_digest: str

    @property
    def basis(self) -> ServingBasis:
        return ServingBasis(
            config_digest=self.config_digest, endpoint=self.endpoint, index=self.identity
        )


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
    return ServingSettings(
        endpoint=effective_endpoint(cli_url=args.base_url, block=block, file_url=file_url),
        max_agent_turns=int(ask.max_agent_turns),
        identity=index_identity_of(config),
        config_digest=serving_config_digest(Path(args.config)),
    )


def _flattened(prefix: str, mapping: Mapping[str, object]) -> Iterator[tuple[str, object]]:
    for key, value in mapping.items():
        path = f"{prefix}.{key}"
        if isinstance(value, Mapping):
            yield from _flattened(path, value)
        else:
            yield path, value


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
    recorded = json.loads(path.read_text(encoding="utf-8")).get(_FINGERPRINT_FIELD)
    if not isinstance(recorded, Mapping):
        raise ValueError(
            f"{path} holds {recorded!r} under {_FINGERPRINT_FIELD!r}; "
            "expected a mapping of serving key -> value"
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
