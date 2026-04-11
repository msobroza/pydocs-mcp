"""Sampling parameters for text generation."""


class SamplingParams:
    """Sampling parameters for text generation.

    Parameters
    ----------
    temperature : float, default=1.0
        Controls randomness. Lower values make the model more deterministic.
    top_p : float, default=1.0
        Nucleus sampling parameter.
    top_k : int, default=-1
        Top-k sampling parameter. -1 means no top-k.
    max_tokens : int, default=16
        Maximum number of tokens to generate per output sequence.
    stop : list[str], optional
        List of strings that stop generation when produced.
    """

    def __init__(self, temperature=1.0, top_p=1.0, top_k=-1,
                 max_tokens=16, stop=None):
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k
        self.max_tokens = max_tokens
        self.stop = stop or []
