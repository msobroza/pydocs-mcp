"""Pin stdlib_qnames.json bundle (sub-PR follow-up to #5c)."""
from __future__ import annotations

import json


def _load() -> dict:
    """Load the bundled JSON via importlib.resources for path-independence."""
    from importlib.resources import files
    text = files("pydocs_mcp.defaults").joinpath("stdlib_qnames.json").read_text(encoding="utf-8")
    return json.loads(text)


def test_stdlib_qnames_bundle_has_reasonable_count():
    """Sanity: stdlib + builtins should produce thousands of qnames."""
    data = _load()
    assert isinstance(data, dict)
    assert "count" in data
    assert "qnames" in data
    assert len(data["qnames"]) == data["count"]
    # CPython 3.11+ has 200+ stdlib modules and 50+ builtins; expect >=1500.
    assert data["count"] >= 1500, f"only {data['count']} qnames"


def test_stdlib_qnames_includes_canonical_examples():
    """Spot-check the qnames most likely to appear in real reference graphs."""
    qnames = set(_load()["qnames"])
    # Stdlib modules
    assert "os" in qnames
    assert "os.path" in qnames
    assert "asyncio" in qnames
    assert "typing" in qnames
    assert "pathlib" in qnames
    # Stdlib callables
    assert "os.path.join" in qnames
    assert "asyncio.to_thread" in qnames or "asyncio.run" in qnames  # version-dependent set
    # Builtins (both bare + namespaced)
    assert "len" in qnames
    assert "builtins.len" in qnames
    assert "isinstance" in qnames
    assert "str" in qnames


def test_stdlib_qnames_records_python_version():
    """The JSON carries the Python version it was generated against."""
    data = _load()
    assert "python_version" in data
    assert isinstance(data["python_version"], str)
    # Format like "3.11" or "3.12"
    parts = data["python_version"].split(".")
    assert len(parts) >= 2
    assert int(parts[0]) >= 3
    assert int(parts[1]) >= 11


def test_stdlib_qnames_excludes_private_module_prefix():
    """Private stdlib modules (_collections, _thread) are filtered out."""
    qnames = _load()["qnames"]
    # No qname starts with _ except builtins-namespaced ones that happen
    # to nest under a public module.
    bad = [q for q in qnames if q.startswith("_") and not q.startswith("builtins.")]
    assert not bad, f"Found private-module qnames: {bad[:5]}"


def test_stdlib_qnames_no_duplicates():
    """The JSON list is unique + sorted."""
    qnames = _load()["qnames"]
    assert len(qnames) == len(set(qnames))
    assert qnames == sorted(qnames)
