"""The serving fingerprint an arm records beside its ``arm.json``.

The settings digest says what the AGENT ran under; it does not see the serving
YAML's retrieval/embedding content, the endpoint a ``--base-url`` named, or the
index the arm served. The fingerprint names each of those per key — beside the
llm block flattened key by key, so a refusal can say ``llm.params.temperature``
rather than "the digest differs" — and ``--reuse-baseline`` compares it.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from pydocs_eval.campaign.before_after_corpora import IndexIdentity
from pydocs_eval.campaign.before_after_serving import (
    SERVING_FINGERPRINT_FILENAME,
    VENDOR_DEFAULT_ENDPOINT,
    ServingBasis,
    ServingFingerprint,
    ServingKey,
    effective_endpoint,
    read_serving_fingerprint,
    serving_config_digest,
    write_serving_fingerprint,
)

_INDEX = IndexIdentity(embedder_model="m", embedder_dim=8, scope_id="scope")
_BLOCK = {
    "provider": "openrouter",
    "params": {"temperature": 1.0, "top_p": 0.95},
    "provider_routing": {"order": ["deepinfra/bf16"], "allow_fallbacks": False},
}


def _yaml(path: Path, payload: object, *, comment: str = "") -> Path:
    path.write_text(f"{comment}\n{yaml.safe_dump(payload)}", encoding="utf-8")
    return path


def _basis(**overrides: object) -> ServingBasis:
    fields: dict[str, object] = {"config_digest": "c" * 64, "endpoint": "http://e", "index": _INDEX}
    return ServingBasis(**{**fields, **overrides})  # type: ignore[arg-type]


# --- the serving YAML's content, not its path -------------------------------


def test_the_config_digest_reads_content_not_the_path_or_comments(tmp_path: Path) -> None:
    payload = {"embedding": {"model_name": "m", "dim": 8}}
    one = _yaml(tmp_path / "one.yaml", payload, comment="# one")
    other = _yaml(tmp_path / "elsewhere.yaml", payload, comment="# another comment")

    assert serving_config_digest(one) == serving_config_digest(other)


def test_a_changed_retrieval_setting_changes_the_config_digest(tmp_path: Path) -> None:
    one = _yaml(tmp_path / "one.yaml", {"embedding": {"model_name": "m", "dim": 8}})
    other = _yaml(tmp_path / "two.yaml", {"embedding": {"model_name": "m", "dim": 16}})

    assert serving_config_digest(one) != serving_config_digest(other)


def test_agent_keys_compared_elsewhere_do_not_fold_into_the_config_digest(
    tmp_path: Path,
) -> None:
    """The runner's YAML carries its agent's llm, architecture and turn cap; a plan's
    serving file may not — those reach the settings digest, model and turn cap instead."""
    retrieval = {"embedding": {"model_name": "m", "dim": 8}}
    agent = {"architecture": "auto", "max_agent_turns": 12, "llm": {"model": "x", "vision": True}}
    runner = _yaml(tmp_path / "runner.yaml", {**retrieval, "ask_your_docs": agent})
    serve = _yaml(tmp_path / "serve.yaml", retrieval)

    assert serving_config_digest(runner) == serving_config_digest(serve)


def test_any_other_ask_your_docs_key_folds_into_the_config_digest(tmp_path: Path) -> None:
    retrieval = {"embedding": {"model_name": "m", "dim": 8}}
    seeded = {**retrieval, "ask_your_docs": {"seed_search_with_question": False}}

    assert serving_config_digest(_yaml(tmp_path / "a.yaml", seeded)) != serving_config_digest(
        _yaml(tmp_path / "b.yaml", retrieval)
    )


# --- the endpoint, by the harness's own precedence --------------------------


def test_the_endpoint_follows_flag_then_block_then_file() -> None:
    block = {"base_url": "http://block"}
    assert effective_endpoint(cli_url="http://flag", block=block, file_url="http://f") == (
        "http://flag"
    )
    assert effective_endpoint(cli_url=None, block=block, file_url="http://f") == "http://block"
    assert effective_endpoint(cli_url=None, block=None, file_url="http://f") == "http://f"
    assert effective_endpoint(cli_url=None, block={}, file_url=None) == VENDOR_DEFAULT_ENDPOINT


# --- the fingerprint, per key ----------------------------------------------


def test_the_fingerprint_names_every_serving_key_and_flattens_the_llm_block() -> None:
    fingerprint = _basis().fingerprint(_BLOCK)

    assert fingerprint.values == {
        ServingKey.SERVING_CONFIG: "c" * 64,
        ServingKey.ENDPOINT: "http://e",
        ServingKey.EMBEDDER_MODEL: "m",
        ServingKey.EMBEDDER_DIM: 8,
        ServingKey.SCOPE_ID: "scope",
        "llm.provider": "openrouter",
        "llm.params.temperature": 1.0,
        "llm.params.top_p": 0.95,
        "llm.provider_routing.order": ["deepinfra/bf16"],
        "llm.provider_routing.allow_fallbacks": False,
    }


def test_two_fingerprints_name_each_key_that_differs() -> None:
    recorded = _basis().fingerprint(_BLOCK)
    hotter = {**_BLOCK, "params": {"temperature": 0.2, "top_p": 0.95}}
    expected = _basis(endpoint="http://other").fingerprint(hotter)

    assert recorded.differing_keys(expected) == ("endpoint", "llm.params.temperature")
    assert recorded.difference_lines(expected) == [
        "endpoint: recorded 'http://e', the plan's baseline 'http://other'",
        "llm.params.temperature: recorded 1.0, the plan's baseline 0.2",
    ]


def test_a_key_only_one_side_has_is_named_as_absent() -> None:
    recorded = _basis().fingerprint({"provider": "openrouter"})
    expected = _basis().fingerprint(None)

    assert recorded.difference_lines(expected) == [
        "llm.provider: recorded 'openrouter', the plan's baseline <absent>"
    ]


# --- on disk ----------------------------------------------------------------


def test_the_fingerprint_round_trips_through_its_sidecar(tmp_path: Path) -> None:
    fingerprint = _basis().fingerprint(_BLOCK)

    path = write_serving_fingerprint(tmp_path, fingerprint)

    assert path == tmp_path / SERVING_FINGERPRINT_FILENAME
    assert read_serving_fingerprint(tmp_path) == fingerprint
    assert json.loads(path.read_text())["fingerprint"]["llm.params.temperature"] == 1.0


def test_an_arm_without_the_sidecar_reads_as_unrecorded(tmp_path: Path) -> None:
    assert read_serving_fingerprint(tmp_path) is None


def test_a_fingerprint_equals_its_json_round_trip() -> None:
    """Tuples and lists compare alike once recorded, so a match never hinges on the type."""
    fingerprint = _basis().fingerprint({"provider_routing": {"order": ("a", "b")}})

    assert fingerprint == ServingFingerprint(values=json.loads(json.dumps(fingerprint.values)))
