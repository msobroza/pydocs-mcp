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

import pytest
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
    fields: dict[str, object] = {
        "config_digest": "c" * 64,
        "cli_url": "http://e",
        "file_llm": None,
        "index": _INDEX,
    }
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
    expected = _basis(cli_url="http://other").fingerprint(hotter)

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


# --- each arm's own block (review fixes) ------------------------------------


def _basis_with(*, cli_url: str | None = None, file_llm: dict | None = None) -> ServingBasis:
    return ServingBasis(config_digest="c" * 64, cli_url=cli_url, file_llm=file_llm, index=_INDEX)


def test_the_endpoint_is_the_arms_own_block_unless_the_flag_names_one() -> None:
    overlay = {"base_url": "http://overlay"}

    assert _basis_with().fingerprint(overlay).values["endpoint"] == "http://overlay"
    assert _basis_with(cli_url="http://flag").fingerprint(overlay).values["endpoint"] == (
        "http://flag"
    )


def test_an_arm_that_pins_no_block_is_served_the_files_block() -> None:
    """The binding reads the serving file's llm only when the arm pins none."""
    basis = _basis_with(file_llm={"base_url": "http://file", "timeout_seconds": 30})

    unpinned = basis.fingerprint(None).values
    pinned = basis.fingerprint({"provider": "openrouter"}).values

    assert (unpinned["endpoint"], unpinned["llm.timeout_seconds"]) == ("http://file", 30)
    assert pinned["endpoint"] == VENDOR_DEFAULT_ENDPOINT
    assert "llm.timeout_seconds" not in pinned


def test_the_model_key_is_left_to_the_identity_gate() -> None:
    values = _basis_with().fingerprint({"model": "x", "provider": "p"}).values

    assert "llm.model" not in values and values["llm.provider"] == "p"


def test_a_key_recorded_as_null_differs_from_one_never_recorded() -> None:
    recorded = _basis_with().fingerprint({"params": {"temperature": None}})

    assert recorded.differing_keys(_basis_with().fingerprint({})) == ("llm.params.temperature",)


def test_a_sidecar_that_is_not_a_mapping_is_refused_by_name(tmp_path: Path) -> None:
    (tmp_path / SERVING_FINGERPRINT_FILENAME).write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match=r"holds \[\]; expected \{'fingerprint'"):
        read_serving_fingerprint(tmp_path)


def test_the_serving_llm_keys_are_the_plan_diffs_dotted_keys() -> None:
    """One flattener: the reuse gate and the plan's settings diff name a key alike."""
    basis = ServingBasis(config_digest="d", cli_url=None, file_llm=None, index=_INDEX)

    values = basis.fingerprint(_BLOCK).values

    llm_keys = {key for key in values if key.startswith("llm.")}
    assert llm_keys == {
        "llm.provider",
        "llm.params.temperature",
        "llm.params.top_p",
        "llm.provider_routing.order",
        "llm.provider_routing.allow_fallbacks",
    }
