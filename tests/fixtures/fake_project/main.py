"""ML Demo — a sample project that uses sklearn, vllm, and langgraph.

This module demonstrates a pipeline that trains a model, uses an LLM
for text generation, and orchestrates steps with a graph.
"""
from ml_pipeline import run_pipeline


def main():
    """Entry point for the ML demo."""
    result = run_pipeline()
    print(f"Pipeline result: {result}")


if __name__ == "__main__":
    main()
