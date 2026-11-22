# mypy: disable-error-code="arg-type,var-annotated,index,call-overload,assignment,misc"
# The pandas and shapely stubs reject these tests' table idioms.
"""Compare shared turn-aware searches to independent exhaustive DAG routes."""

import itertools

import networkx as nx
import pytest

from walkbike_routes.routing.transitions import transition_arrival_paths, transition_departure_paths


def test_both_directions_match_explicit_path_costs():
    graph = nx.DiGraph()
    for i, u in enumerate("ABCDE"):
        for j, v in enumerate("ABCDE"):
            if i < j:
                graph.add_edge(u, v, cost=(j - i) ** 2)
    graph.add_node("lost")

    def penalty(u, v, w):
        return 6 if v == "C" else 0

    def cost(path):
        return sum(graph[u][v]["cost"] for u, v in itertools.pairwise(path)) + sum(
            penalty(*path[i : i + 3]) for i in range(len(path) - 2)
        )

    for engine, gates, targets, arrival in [
        (transition_arrival_paths, ["D", "E"], ["A", "B", "D", "lost"], True),
        (transition_departure_paths, ["A", "B"], ["B", "D", "E", "lost"], False),
    ]:
        distances, paths = engine(
            graph, gates, targets, lambda u, v, data: float(data["cost"]), penalty
        )
        assert "lost" not in distances
        for node in set(targets) - {"lost"}:
            options = []
            for gate in gates:
                source, destination = (node, gate) if arrival else (gate, node)
                options.extend(nx.all_simple_paths(graph, source, destination))
                if source == destination:
                    options.append([source])
            assert distances[node] == min(map(cost, options))
            assert cost(paths[node]) == distances[node]


@pytest.mark.parametrize("engine", [transition_arrival_paths, transition_departure_paths])
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1])
def test_transition_engine_rejects_invalid_cost(engine, invalid):
    graph = nx.DiGraph()
    graph.add_edge("A", "B", cost=1)
    graph.add_edge("B", "C", cost=1)
    gates, origins = (["C"], ["A"]) if engine is transition_arrival_paths else (["A"], ["C"])
    with pytest.raises(ValueError, match="finite"):
        engine(graph, gates, origins, lambda u, v, data: invalid, lambda u, v, w: 0)
    with pytest.raises(ValueError, match="finite"):
        engine(graph, gates, origins, lambda u, v, data: 1, lambda u, v, w: invalid)


def test_equal_gates_tie_by_name_not_by_set_order():
    graph = nx.DiGraph()
    for u, v in [("gate_north", "home"), ("gate_south", "home")]:
        graph.add_edge(u, v, t=10.0)
        graph.add_edge(v, u, t=10.0)
    for gates in (["gate_north", "gate_south"], ["gate_south", "gate_north"]):
        _, paths = transition_departure_paths(
            graph, gates, ["home"], lambda u, v, a: a["t"], lambda u, v, w: 0.0
        )
        assert paths["home"] == ["gate_north", "home"]
