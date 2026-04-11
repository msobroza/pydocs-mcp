"""Core graph building primitives."""


class StateGraph:
    """A graph whose nodes communicate via a shared state object.

    The StateGraph is the main construct for building agent workflows.
    Each node is a function that takes the current state and returns updates.

    Parameters
    ----------
    state_schema : type
        The type of the state object that nodes read and write.
    """

    def __init__(self, state_schema):
        self.state_schema = state_schema
        self.nodes = {}
        self.edges = {}

    def add_node(self, name, action):
        """Add a node to the graph.

        Parameters
        ----------
        name : str
            The name of the node.
        action : callable
            A function that takes state and returns state updates.
        """
        self.nodes[name] = action
        return self

    def add_edge(self, source, target):
        """Add a direct edge between two nodes.

        Parameters
        ----------
        source : str
            Name of the source node.
        target : str
            Name of the target node.
        """
        self.edges[source] = target
        return self

    def add_conditional_edges(self, source, condition, mapping):
        """Add conditional branching from a node.

        Parameters
        ----------
        source : str
            Name of the source node.
        condition : callable
            Function that takes state and returns a string key.
        mapping : dict[str, str]
            Maps condition output to target node names.
        """
        self.edges[source] = {"condition": condition, "mapping": mapping}
        return self

    def set_entry_point(self, name):
        """Set the entry point node for the graph."""
        self.entry_point = name
        return self

    def compile(self):
        """Compile the graph into a runnable."""
        return CompiledGraph(self)


class CompiledGraph:
    """A compiled StateGraph ready for execution.

    Call invoke() to run the graph with an initial state.
    """

    def __init__(self, graph):
        self.graph = graph

    def invoke(self, state):
        """Run the graph with the given initial state.

        Parameters
        ----------
        state : dict
            Initial state to start the graph execution.

        Returns
        -------
        dict
            The final state after all nodes have executed.
        """
        return state
