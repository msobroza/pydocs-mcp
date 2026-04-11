"""ML Pipeline — orchestrates training, inference, and graph-based workflows.

This module demonstrates a pipeline that:
1. Splits data and trains a RandomForest classifier using sklearn
2. Generates text completions using vLLM batch inference
3. Orchestrates multi-step agent workflows with LangGraph
"""
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import train_test_split, GridSearchCV
from vllm import LLM, SamplingParams
from langgraph.graph import StateGraph


def train_model(data, target, n_estimators=100):
    """Train a RandomForest classifier on the given data.

    Uses sklearn's RandomForestClassifier with train_test_split for
    evaluation. Returns the fitted model and test accuracy.

    Parameters
    ----------
    data : array-like
        Training features.
    target : array-like
        Training labels.
    n_estimators : int
        Number of trees in the forest.

    Returns
    -------
    dict
        Trained model and accuracy metrics.
    """
    X_train, X_test, y_train, y_test = train_test_split(
        data, target, test_size=0.2, random_state=42
    )
    clf = RandomForestClassifier(n_estimators=n_estimators, random_state=42)
    clf.fit(X_train, y_train)
    predictions = clf.predict(X_test)
    return {"model": clf, "predictions": predictions}


def tune_hyperparameters(data, target):
    """Tune model hyperparameters using GridSearchCV.

    Searches over n_estimators and max_depth for the best
    GradientBoostingClassifier configuration.

    Parameters
    ----------
    data : array-like
        Training features.
    target : array-like
        Training labels.

    Returns
    -------
    dict
        Best parameters and estimator from grid search.
    """
    estimator = GradientBoostingClassifier(learning_rate=0.1)
    param_grid = {
        "n_estimators": [50, 100, 200],
        "max_depth": [3, 5, 7],
    }
    search = GridSearchCV(estimator, param_grid, cv=5, scoring="accuracy")
    search.fit(data, target)
    return {"best_params": search.param_grid, "estimator": search.estimator}


def generate_text(prompt, temperature=0.7, max_tokens=256):
    """Generate text using vLLM batch inference engine.

    Creates an LLM instance with the specified model and generates
    completions using SamplingParams for controlling output quality.

    Parameters
    ----------
    prompt : str
        Input prompt for the LLM.
    temperature : float
        Sampling temperature for generation randomness.
    max_tokens : int
        Maximum number of tokens to generate.

    Returns
    -------
    list
        Generated text completions from the vLLM engine.
    """
    llm = LLM(model="meta-llama/Llama-2-7b-hf", tensor_parallel_size=1)
    params = SamplingParams(
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=0.95,
    )
    outputs = llm.generate([prompt], sampling_params=params)
    return outputs


def build_agent_graph(state_schema):
    """Build a LangGraph agent workflow for multi-step reasoning.

    Creates a StateGraph with classify, route, and respond nodes.
    Uses conditional edges to dynamically route between nodes based
    on the classification result.

    Parameters
    ----------
    state_schema : type
        The state type that graph nodes read and write.

    Returns
    -------
    CompiledGraph
        A compiled graph ready for invocation.
    """
    graph = StateGraph(state_schema)

    graph.add_node("classify", lambda state: state)
    graph.add_node("route", lambda state: state)
    graph.add_node("respond", lambda state: state)

    graph.set_entry_point("classify")
    graph.add_edge("classify", "route")
    graph.add_conditional_edges(
        "route",
        lambda state: "respond",
        {"respond": "respond"},
    )

    return graph.compile()


def run_pipeline():
    """Run the full ML pipeline: train, generate, orchestrate.

    Demonstrates end-to-end usage of sklearn for model training,
    vLLM for text generation, and LangGraph for agent orchestration.

    Returns
    -------
    dict
        Pipeline execution results from all three stages.
    """
    model_result = train_model([1, 2, 3], [0, 1, 0], n_estimators=50)
    text_result = generate_text("Explain machine learning", temperature=0.5)
    graph = build_agent_graph(dict)
    graph_result = graph.invoke({"input": "test query"})
    return {"model": model_result, "text": text_result, "graph": graph_result}
