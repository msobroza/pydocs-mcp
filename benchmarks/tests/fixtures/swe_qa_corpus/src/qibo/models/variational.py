"""Synthetic qibo variational models fixture (SWE-QA-Pro corpus)."""


class VQE:
    """Variational quantum eigensolver."""

    def __init__(self, circuit, hamiltonian):
        self.circuit = circuit
        self.hamiltonian = hamiltonian

    def minimize(self, params):
        return sum(params)


class QAOA(VQE):
    """QAOA extends VQE with a mixing hamiltonian."""

    def __init__(self, circuit, hamiltonian, mixer):
        super().__init__(circuit, hamiltonian)
        self.mixer = mixer

    def minimize(self, params):
        return super().minimize(params) + len(params)
