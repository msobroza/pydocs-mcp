"""Pin the import-order invariant: pydocs_mcp.storage.filters must be importable
as a cold first-touch entry. Task 20's S7 shim previously closed a cycle that
made this fail; the PEP 562 __getattr__ fix keeps models.py a leaf in the
import graph."""
import subprocess
import sys


def test_storage_filters_cold_import_does_not_raise():
    """Reproduces B3-C1: a fresh Python process importing pydocs_mcp.storage.filters
    must succeed without ImportError. Pytest passes despite the regression because
    conftest.py preloads modules in a fortunate order; this test isolates the bare
    cold-import path in a subprocess."""
    result = subprocess.run(
        [sys.executable, "-c", "from pydocs_mcp.storage.filters import FieldEq; print('OK')"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"cold import failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_indexing_stats_lazy_shim_resolves():
    """PEP 562 __getattr__ shim returns the canonical class on first access."""
    import pydocs_mcp.models as models_mod
    from pydocs_mcp.application.indexing_service import IndexingStats as Canonical

    # First access: triggers __getattr__ + module-level import.
    assert models_mod.IndexingStats is Canonical
    # Second access uses the resolved attribute (or re-runs __getattr__ — both fine).
    assert models_mod.IndexingStats is Canonical
