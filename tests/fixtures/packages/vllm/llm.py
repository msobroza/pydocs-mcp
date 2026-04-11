"""Core LLM engine interface."""


class LLM:
    """An LLM for generating texts from given prompts and sampling parameters.

    This class provides the main interface for offline batch inference.

    Parameters
    ----------
    model : str
        The name or path of a HuggingFace Transformers model.
    tokenizer : str, optional
        The name or path of a HuggingFace tokenizer.
    tensor_parallel_size : int, default=1
        The number of GPUs to use for distributed execution.
    dtype : str, default='auto'
        Data type for model weights and activations.
    max_model_len : int, optional
        Model context length override.
    """

    def __init__(self, model, tokenizer=None, tensor_parallel_size=1,
                 dtype="auto", max_model_len=None):
        self.model = model
        self.tokenizer = tokenizer
        self.tensor_parallel_size = tensor_parallel_size

    def generate(self, prompts, sampling_params=None):
        """Generate completions for the input prompts.

        Parameters
        ----------
        prompts : list[str]
            A list of prompts to generate completions for.
        sampling_params : SamplingParams, optional
            The sampling parameters for text generation.

        Returns
        -------
        list[RequestOutput]
            A list of RequestOutput objects containing the generated text.
        """
        return []
