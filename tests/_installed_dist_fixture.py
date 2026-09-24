"""Shared test helper: a real installed distribution on ``sys.path``, outside any repo.

Dependency indexing finds a distribution through ``importlib.metadata`` and reads
the files its ``RECORD`` lists. A suite that needs a dependency whose CONTENT it
controls, such as a module carrying a ``# WHY:`` marker, therefore needs a real
dist-info on ``sys.path``. The frozen dev venv carries no such dist (none of its
packages holds a decision marker), and borrowing a real one would tie the suite
to whatever version uv.lock pins.

Put such a marker inside a function or class body. The Python chunker's module
node carries the module docstring, not top-level comments, so a top-level
``# WHY:`` is never mined.

:func:`installed_distribution` writes ``<parent>/site-packages/<name>/`` plus
``<name>-0.1.dist-info/{METADATA, RECORD, top_level.txt}``, and prepends that
``site-packages`` to ``sys.path`` for the test (``monkeypatch`` restores it). On
exit it forgets any module a pass imported from it. Put ``parent`` under
``tmp_path``: that is outside every git repository, so nothing the pass runs can
walk up into one.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

_VERSION = "0.1"


@dataclass(frozen=True, slots=True)
class InstalledDistribution:
    """Where :func:`installed_distribution` put the dist, and under which name."""

    name: str
    site_packages: Path

    @property
    def package_dir(self) -> Path:
        """The dist's one import package, ``<site-packages>/<name>``."""
        return self.site_packages / self.name


@contextmanager
def installed_distribution(
    parent: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str,
    modules: Mapping[str, str],
) -> Iterator[InstalledDistribution]:
    """Install ``name``, one import package holding ``modules`` (file name → source).

    An empty ``__init__.py`` is added unless ``modules`` supplies one.

    Example::

        source = "def attempt():\\n    # WHY: keep the pool bounded\\n    return 1\\n"
        modules = {"mod.py": source}
        with installed_distribution(tmp_path, monkeypatch, name="acme", modules=modules):
            ...  # find_installed_distribution("acme") now finds it
    """
    dist = InstalledDistribution(name=name, site_packages=parent / "site-packages")
    _write_distribution(dist, {"__init__.py": "", **modules})
    monkeypatch.syspath_prepend(str(dist.site_packages))
    try:
        yield dist
    finally:
        _forget_imported_modules(name)


def _write_distribution(dist: InstalledDistribution, sources: Mapping[str, str]) -> None:
    """The package files, then a dist-info whose RECORD lists every one of them."""
    dist.package_dir.mkdir(parents=True)
    for file_name, source in sources.items():
        (dist.package_dir / file_name).write_text(source, encoding="utf-8")
    info = dist.site_packages / f"{dist.name}-{_VERSION}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {dist.name}\nVersion: {_VERSION}\n", encoding="utf-8"
    )
    (info / "top_level.txt").write_text(f"{dist.name}\n", encoding="utf-8")
    listed = [f"{dist.name}/{file_name}" for file_name in sources]
    listed += [f"{info.name}/{file_name}" for file_name in ("METADATA", "top_level.txt", "RECORD")]
    (info / "RECORD").write_text("".join(f"{path},,\n" for path in listed), encoding="utf-8")


def _forget_imported_modules(name: str) -> None:
    """Drop ``name`` and its submodules from ``sys.modules`` (an inspect-mode
    pass imports them), so no later test sees a module whose files are gone."""
    for module_name in [m for m in sys.modules if m == name or m.startswith(f"{name}.")]:
        del sys.modules[module_name]


__all__ = ("InstalledDistribution", "installed_distribution")
