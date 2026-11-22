"""Turn costs by searching a graph expanded to movements."""

from __future__ import annotations

import networkx as nx
import pytest

from walkbike_routes.analysis.accessibility import node_accessibility
from walkbike_routes.choice import Alternative, ChoiceSettings, bound_to_best
from walkbike_routes.models import TravelDirection
from walkbike_routes.routing import measure_path, multi_source_k_paths, multi_source_paths
from walkbike_routes.routing.costs import CostComponents
from walkbike_routes.routing.turns import is_virtual, place, sink, state, turn_graph

TURN = ("o", "a", "t")  # the one costly movement


def _graph() -> nx.DiGraph[str]:
    """Two ways from o to t: through a (shorter, with a costly turn) or through b."""
    graph: nx.DiGraph[str] = nx.DiGraph()
    for u, v, cost in (("o", "a", 100.0), ("a", "t", 100.0), ("o", "b", 105.0),
                       ("b", "t", 105.0)):  # fmt: skip
        for x, y in ((u, v), (v, u)):
            graph.add_edge(
                x, y, physical_edge_id=f"{u}{v}", routable_link_id=f"{x}{y}",
                physical_length_m=cost, travel_time_s=cost, movement_time_s=cost,
                generalized_cost_s=cost,
            )  # fmt: skip
    return graph


def _turns(u: str, v: str, w: str) -> CostComponents:
    return CostComponents(turn_cost_s=50.0 if (u, v, w) == TURN else 0.0)


def _best(graph: nx.Graph[str], destination: str) -> list[str]:
    tree = multi_source_paths(
        graph, {"T": destination}, weight="generalized_cost_s",
        direction=TravelDirection.TO_DESTINATION,
    )  # fmt: skip
    return list(tree.path_by_node["o"])


def test_a_turn_cost_changes_the_best_route() -> None:
    plain = measure_path(_graph(), _best(_graph(), "t"))
    assert plain.physical_edge_ids == ("oa", "at")
    expanded = turn_graph(_graph(), _turns)
    turned = measure_path(expanded, _best(expanded, sink("t")))
    assert turned.physical_edge_ids == ("ob", "bt")
    assert turned.generalized_cost_s == pytest.approx(210.0)
    assert turned.routable_link_ids == ("ob", "bt")  # the virtual link to the end is skipped


def test_movements_carry_the_turn_cost_but_not_a_delay() -> None:
    expanded = turn_graph(_graph(), _turns)
    moved = expanded[state("o", "a")][state("a", "t")]
    assert moved["turn_cost_s"] == 50.0 and moved["generalized_cost_s"] == 150.0
    assert moved["travel_time_s"] == 100.0
    assert not expanded.has_edge(state("o", "a"), state("a", "o"))  # no turning straight back
    assert expanded["o"][state("o", "a")]["generalized_cost_s"] == 100.0  # starting is free
    assert is_virtual(expanded, sink("t")) and is_virtual(expanded, state("o", "a"))
    assert not is_virtual(expanded, "o")


def test_near_best_routes_include_turn_costs() -> None:
    expanded = turn_graph(_graph(), _turns)
    tree = multi_source_k_paths(
        expanded, {"T": sink("t")}, weight="generalized_cost_s",
        direction=TravelDirection.TO_DESTINATION, maximum_paths=2, maximum_cost_gap=100.0,
        maximum_cost_ratio=2.0,
    )  # fmt: skip
    found = tree.alternatives_by_node["o"]
    assert [a.cost for a in found] == pytest.approx([210.0, 250.0])
    assert measure_path(expanded, list(found[1].path)).physical_edge_ids == ("oa", "at")


def test_a_route_starting_at_the_destination_is_empty() -> None:
    expanded = turn_graph(_graph(), _turns)
    here = measure_path(expanded, [*_best(expanded, sink("o"))])
    assert here.physical_edge_ids == () and here.generalized_cost_s == 0


def test_reachable_nodes_list_only_the_original_nodes() -> None:
    expanded = turn_graph(_graph(), _turns)
    tree = multi_source_paths(
        expanded, {"T": sink("t")}, weight="generalized_cost_s",
        direction=TravelDirection.TO_DESTINATION,
    )  # fmt: skip
    table = node_accessibility(expanded, tree, thresholds_min=(10.0,))
    assert sorted(table.node_id) == ["a", "b", "o", "t"]


def test_a_negative_movement_cost_is_refused() -> None:
    with pytest.raises(ValueError, match="invalid turn_cost_s"):
        turn_graph(_graph(), lambda u, v, w: CostComponents(turn_cost_s=-1.0))


def test_near_best_routes_do_not_pass_a_node_twice() -> None:
    graph: nx.DiGraph[str] = nx.DiGraph()
    for u, v, cost in (("o", "t", 100.0), ("o", "a", 1.0), ("a", "b", 1.0), ("b", "o", 1.0),
                       ("o", "s", 52.5), ("s", "t", 52.5)):  # fmt: skip
        graph.add_edge(
            u, v, physical_edge_id=f"{u}{v}", physical_length_m=cost, travel_time_s=cost,
            generalized_cost_s=cost,
        )  # fmt: skip
    expanded = turn_graph(graph, lambda u, v, w: CostComponents())
    tree = multi_source_k_paths(
        expanded, {"T": sink("t")}, weight="generalized_cost_s",
        direction=TravelDirection.TO_DESTINATION, maximum_paths=2, maximum_cost_gap=10.0,
        maximum_cost_ratio=1.1, place=place,
    )  # fmt: skip
    routes = [measure_path(expanded, list(a.path)).physical_edge_ids
              for a in tree.alternatives_by_node["o"]]  # fmt: skip
    assert routes == [("ot",), ("os", "st")]  # not round the loop back to o
    assert place(state("a", "b")) == "b" and place(sink("b")) == sink("b") and place("o") == "o"


def test_alternatives_are_bounded_by_the_actual_best_route() -> None:
    # The best route (cost 5) went round a block; the near-best search found only 100 and 104.
    found = {"o": (Alternative("T", ("x",), 1.0, 1.0, 100.0), Alternative("T", ("y",), 1.0, 1.0, 104.0)),
             "p": (Alternative("T", ("z",), 1.0, 1.0, 100.0),)}  # fmt: skip
    settings = ChoiceSettings(maximum_cost_gap_s=60.0, maximum_cost_ratio=1.1)
    bounded = bound_to_best(found, {"o": 5.0, "p": 95.0}, settings=settings)
    assert bounded["o"] == ()
    assert [a.physical_edge_ids for a in bounded["p"]] == [("z",)]
    assert bound_to_best(found, {}, settings=settings) == found
    # Routes found by length are bounded on length, whatever their generalized cost.
    by_length = {"o": (Alternative("T", ("x",), 105.0, 200.0, 200.0, 105.0),)}
    assert bound_to_best(by_length, {"o": 100.0}, settings=settings) == by_length


class _Junction:
    """j1 and j2 are one junction: per-node moves cost 30 each, a passage 40."""

    clusters = {"j1": "J", "j2": "J"}

    def __call__(self, u: str, v: str, w: str) -> CostComponents:
        return CostComponents(turn_cost_s=30.0)

    def through(self, a: str, b: str, x: str, w: str) -> CostComponents:
        self.seen.append((a, b, x, w))
        return CostComponents(turn_cost_s=40.0)

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, str, str]] = []


def _split_junction() -> nx.DiGraph[str]:
    """o - j1 - j2 - t, with j1 and j2 a junction drawn as two nodes, and a spur j2 - s."""
    graph: nx.DiGraph[str] = nx.DiGraph()
    for u, v, cost in (("o", "j1", 100.0), ("j1", "j2", 5.0), ("j2", "t", 100.0),
                       ("j2", "s", 50.0)):  # fmt: skip
        for x, y in ((u, v), (v, u)):
            graph.add_edge(
                x, y, physical_edge_id=f"{u}{v}", routable_link_id=f"{x}{y}",
                physical_length_m=cost, travel_time_s=cost, movement_time_s=cost,
                generalized_cost_s=cost,
            )  # fmt: skip
    return graph


def test_a_junction_drawn_as_several_nodes_is_one_movement() -> None:
    junction = _Junction()
    expanded = turn_graph(_split_junction(), junction)
    route = measure_path(expanded, _best(expanded, sink("t")))
    assert route.physical_edge_ids == ("oj1", "j1j2", "j2t")
    # One passage (40), not a movement at j1 and another at j2 (30 + 30).
    assert route.generalized_cost_s == pytest.approx(100 + 5 + 100 + 40)
    assert ("o", "j1", "j2", "t") in junction.seen
    assert ("o", "j1", "j1", "o") not in junction.seen  # no leaving the way it came in
    assert [place(n) for n in _best(expanded, sink("t"))][1:4] == ["j1", "j2", "t"]


def test_a_route_can_end_inside_a_junction() -> None:
    expanded = turn_graph(_split_junction(), _Junction())
    route = measure_path(expanded, _best(expanded, sink("j2")))
    assert route.physical_edge_ids == ("oj1", "j1j2")
    assert route.generalized_cost_s == pytest.approx(105.0)  # nothing charged at the end


def test_clusters_need_a_through_method() -> None:
    class Half:
        clusters = {"j1": "J"}

        def __call__(self, u: str, v: str, w: str) -> CostComponents:
            return CostComponents()

    with pytest.raises(ValueError, match="through method"):
        turn_graph(_split_junction(), Half())


def test_inside_a_junction_only_the_entering_link_is_closed() -> None:
    # Junction {b, x}; a joins both.  A route in by a-b that moves on to x may
    # leave by x-a, a different link from the one it came in by.
    graph: nx.DiGraph[str] = nx.DiGraph()
    for u, v in [("a", "b"), ("b", "x"), ("x", "a"), ("x", "c")]:
        for start, end in ((u, v), (v, u)):
            graph.add_edge(start, end, physical_length_m=10.0, travel_time_s=5.0,
                           generalized_cost_s=5.0, physical_edge_id="".join(sorted((u, v))))  # fmt: skip

    class Free:
        clusters = {"b": "J", "x": "J"}

        def __call__(self, u: str, v: str, w: str) -> CostComponents:
            return CostComponents()

        def through(self, a: str, b: str, x: str, w: str) -> CostComponents:
            return CostComponents()

    expanded = turn_graph(graph, Free())
    at_x = next(n for n in expanded.successors(state("a", "b")) if place(n) == "x")
    assert state("x", "a") in set(expanded.successors(at_x))
    assert state("b", "a") not in set(expanded.successors(state("a", "b")))  # no U-turn


def test_tracing_from_the_destination_on_a_turn_graph_is_refused() -> None:
    import pandas as pd

    from walkbike_routes.exceptions import AccessibilityError
    from walkbike_routes.models import Mode
    from walkbike_routes.routing import trace_routes

    graph: nx.DiGraph[str] = nx.DiGraph()
    graph.add_edge("a", "b", physical_length_m=10.0, travel_time_s=5.0,
                   generalized_cost_s=5.0, physical_edge_id="ab")  # fmt: skip
    expanded = turn_graph(graph, lambda u, v, w: CostComponents())
    tree = multi_source_paths(expanded, {"gate": "a"}, weight="generalized_cost_s",
                              direction=TravelDirection.FROM_DESTINATION)  # fmt: skip
    with pytest.raises(AccessibilityError, match="turn graph"):
        trace_routes(
            expanded, tree, pd.DataFrame({"origin_id": []}),
            pd.DataFrame({"origin_id": [], "mode": []}), study_id="s", destination_id="d",
            profile_id="p", scenario_id="s", mode=Mode.BICYCLE, time_limit_min=None,
            snap_speed_mps=4.0,
        )  # fmt: skip
