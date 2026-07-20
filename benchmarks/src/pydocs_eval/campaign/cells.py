"""Campaign cell definitions — one measured configuration in the grid (ADR 0016).

A *cell* is one arm configuration the campaign runs over the shared instance
list: an :class:`~pydocs_eval.agent_track._types.ArmConfig` (tool surface, incl.
the ADR 0016 stage-2 ``tools`` tuple), a suggestion-group serve-YAML overlay
reference (indexed cells only — the hint-emitting tools live behind the MCP
server, so the factor is structurally inert in the bare arm, ADR 0016 §Options),
and the harness-side session-start injection flag (NOT a serve YAML flip — the
runner prepends the ``session-start-context`` pack, ADR 0016 action item 3).

The 6-cell screening grid (ADR 0016 §Stage 1) is built by :func:`screening_cells`:
``bare × injection {2} + indexed × suggestions × injection {4}`` — suggestions is
crossed with the indexed arm ONLY, so it is 6 cells, never a full 2×2×2. Cells are
value objects with a canonical :meth:`CellConfig.to_dict` so the lockfile hashes
them into the campaign ID.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydocs_eval.agent_track._types import DEFAULT_MODEL, ArmConfig

# The two suggestion-overlay reference values. ``None`` overlay = the shipped
# default (suggestions ON, ADR 0007); ``_SUGGESTIONS_OFF_OVERLAY`` names the
# serve-YAML overlay that flips the three suggestion bools off. The bare arm
# carries NO overlay (the factor is inert there, ADR 0016 §Options).
_SUGGESTIONS_OFF_OVERLAY = "suggestions_off"


@dataclass(frozen=True, slots=True)
class CellConfig:
    """One campaign cell: an arm + its suggestion overlay + injection flag.

    ``arm`` is the tool-surface config (bare / indexed, plus the stage-2
    ``tools`` tuple). ``suggestion_overlay`` names the serve-YAML overlay that
    tunes the suggestion-group factor (``None`` = shipped default ON); it MUST be
    ``None`` for a bare cell, where the hint-emitting MCP tools are absent so the
    factor cannot manifest (ADR 0016 §Options). ``injection`` is the harness-side
    session-start-context prepend flag (ADR 0016 action item 3), realized by the
    runner, not by any serve flag.
    """

    name: str
    arm: ArmConfig
    suggestion_overlay: str | None = None
    injection: bool = False

    def __post_init__(self) -> None:
        # Suggestions are structurally inert in the bare arm (no MCP server, so
        # the hint-emitting tools are absent) — an overlay there would be a
        # silent no-op that falsely implies a measured factor. Reject it at the
        # boundary rather than emit a cell whose overlay does nothing.
        if not self.arm.mcp and self.suggestion_overlay is not None:
            raise ValueError(
                f"cell {self.name!r} sets suggestion_overlay="
                f"{self.suggestion_overlay!r} on a bare arm (mcp=False); the "
                "suggestion factor is inert without the MCP server — use an "
                "indexed arm or drop the overlay"
            )

    def to_dict(self) -> dict[str, object]:
        """Canonical cell block for the lockfile (feeds the campaign-ID hash)."""
        return {
            "name": self.name,
            "arm": _arm_to_dict(self.arm),
            "suggestion_overlay": self.suggestion_overlay,
            "injection": self.injection,
        }


def _arm_to_dict(arm: ArmConfig) -> dict[str, object]:
    """Canonical, hashable view of an ``ArmConfig`` (order-stable keys)."""
    return {
        "name": arm.name,
        "model": arm.model,
        "max_turns": arm.max_turns,
        "mcp": arm.mcp,
        "no_tools": arm.no_tools,
        "tools": list(arm.tools) if arm.tools is not None else None,
    }


def screening_cells(
    *, model: str = DEFAULT_MODEL, max_turns: int | None = None
) -> tuple[CellConfig, ...]:
    """The 6 pre-registered stage-1 screening cells (ADR 0016 §Stage 1).

    ``bare × injection {2} + indexed × suggestions × injection {4}`` — suggestions
    crossed with the indexed arm only (inert in bare), so exactly 6 cells, not a
    full 2×2×2. The anchor contrast (indexed vs bare, suggestions-on injection-off)
    is ``indexed_sugg-on_inj-off`` vs ``bare_inj-off``.

    Example:
        >>> len(screening_cells())
        6
    """
    turns = max_turns if max_turns is not None else ArmConfig(name="_").max_turns
    bare = tuple(_bare_cell(inj, model, turns) for inj in (False, True))
    indexed = tuple(
        _indexed_cell(sugg_on, inj, model, turns)
        for sugg_on in (True, False)
        for inj in (False, True)
    )
    return bare + indexed


def _bare_cell(injection: bool, model: str, max_turns: int) -> CellConfig:
    arm = ArmConfig(name="bare", model=model, max_turns=max_turns, mcp=False)
    return CellConfig(name=f"bare_inj-{_flag(injection)}", arm=arm, injection=injection)


def _indexed_cell(sugg_on: bool, injection: bool, model: str, max_turns: int) -> CellConfig:
    arm = ArmConfig(name="indexed", model=model, max_turns=max_turns, mcp=True)
    overlay = None if sugg_on else _SUGGESTIONS_OFF_OVERLAY
    return CellConfig(
        name=f"indexed_sugg-{_flag(sugg_on)}_inj-{_flag(injection)}",
        arm=arm,
        suggestion_overlay=overlay,
        injection=injection,
    )


def _flag(value: bool) -> str:
    return "on" if value else "off"
