"""Inheritance graph. Exercises Rule B + INHERITS edges."""

from __future__ import annotations


class Base:
    """Base class with a method."""

    def announce(self) -> str:
        return "Base"


class Middle(Base):
    """Inherits Base — INHERITS edge to ac15_pkg.inheritance.Base."""

    def announce(self) -> str:
        return f"Middle({super().announce()})"


class Leaf(Middle):
    """Inherits Middle — chain of INHERITS edges."""

    def announce(self) -> str:
        return f"Leaf({super().announce()})"
