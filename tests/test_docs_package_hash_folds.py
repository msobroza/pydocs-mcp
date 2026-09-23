"""The docs name every package-hash fold, in code order (issue #347).

CLAUDE.md's **Package-level** cache bullet is the one place an agent reads what
``ContentHashStage`` folds before it touches a cache key. A fold missing from it,
or listed out of order, sends the next change to the wrong position — and the
fold ORDER is part of every stored hash. Code order itself is pinned by
tests/extraction/test_content_hash_fold_composition.py; this suite pins the prose
against it.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydocs_mcp.extraction.pipeline.stages import content_hash as content_hash_module

ROOT = Path(__file__).resolve().parents[1]

# ContentHashStage._ordered_salts, innermost first, each named by the phrase the
# bullet uses for it. The structuring LLM's identity rides inside the decision
# token, so it has no entry of its own.
_FOLD_PHRASES_IN_CODE_ORDER = (
    "exclusion fingerprint",
    "`MODULE_ID_RULE_VERSION`",
    "`decision_capture`",
    "`members:<token>`",
    "`refs:<sorted distinct kinds>`",
    "loadable-grammar fingerprint",
    "chunk-tree salt",
    "identity salt",
)


def _package_level_cache_bullet() -> str:
    claude_md = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    match = re.search(r"^- \*\*Package-level\*\* — .*$", claude_md, re.MULTILINE)
    assert match is not None, "CLAUDE.md lost its '- **Package-level** — …' cache bullet"
    return match.group(0)


def test_package_level_bullet_names_every_fold_in_code_order() -> None:
    bullet = _package_level_cache_bullet()
    positions = {phrase: bullet.find(phrase) for phrase in _FOLD_PHRASES_IN_CODE_ORDER}
    missing = [phrase for phrase, at in positions.items() if at < 0]
    assert missing == [], f"CLAUDE.md Package-level bullet omits folds {missing!r}"
    in_doc_order = sorted(positions, key=positions.__getitem__)
    assert in_doc_order == list(_FOLD_PHRASES_IN_CODE_ORDER), (
        f"CLAUDE.md lists the folds as {in_doc_order!r}, expected code order "
        f"{list(_FOLD_PHRASES_IN_CODE_ORDER)!r}"
    )


def test_package_level_bullet_names_every_stock_pin() -> None:
    """Each pin is the one value that folds nothing, and re-pinning one moves
    stored hashes, so every pin the stage module defines is named where agents look."""
    pins = sorted(name for name in vars(content_hash_module) if name.startswith("_STOCK_"))
    assert pins, "content_hash.py defines no _STOCK_* pin — the scan is broken"
    undocumented = [pin for pin in pins if f"`{pin}`" not in _package_level_cache_bullet()]
    assert undocumented == [], f"CLAUDE.md Package-level bullet omits pins {undocumented!r}"


def test_documentation_skip_section_names_the_settings_folds() -> None:
    documentation = (ROOT / "DOCUMENTATION.md").read_text(encoding="utf-8")
    skip_section = documentation.split("### Skip when nothing changed", 1)[1].split("###", 1)[0]
    for setting in ("`decision_capture`", "`extraction.members`", "`reference_graph.capture`"):
        assert setting in skip_section, (
            f"DOCUMENTATION.md 'Skip when nothing changed' does not name {setting}"
        )
