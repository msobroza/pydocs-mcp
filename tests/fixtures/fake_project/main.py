"""ML Demo — a sample project that uses sklearn, vllm, and langgraph.

This module demonstrates a pipeline that trains a model, uses an LLM
for text generation, and orchestrates steps with a LangGraph agent.
"""
from ml_pipeline import run_pipeline, train_model, generate_text, build_agent_graph


def main():
    """Entry point for the ML demo.

    Runs the full pipeline and prints results from each stage:
    model training, text generation, and agent graph execution.
    """
    result = run_pipeline()
    print(f"Model: {result['model']}")
    print(f"Text: {result['text']}")
    print(f"Graph: {result['graph']}")


if __name__ == "__main__":
    main()
