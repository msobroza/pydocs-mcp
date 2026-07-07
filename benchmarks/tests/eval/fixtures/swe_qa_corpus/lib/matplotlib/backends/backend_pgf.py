"""Synthetic matplotlib PGF backend fixture (SWE-QA corpus)."""


class LatexManager:
    """Drives a persistent LaTeX process for the PGF backend."""

    def __init__(self):
        self.latex = None

    def read(self):
        """Read character-by-character from the LaTeX subprocess stdout."""
        return self.latex.stdout.read(1)
