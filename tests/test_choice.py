"""Splitting trips across near-best routes, checked against hand calculations."""

from __future__ import annotations

import math

import geopandas as gpd
import networkx as nx
import pandas as pd
import pytest
from shapely.geometry import LineString

from walkbike_routes.choice import (
    Alternative,
    ChoiceSettings,
    near_best_alternatives,
    path_size_probabilities,
    select_diverse,
    shared_length_ratio,
    split_routes,
)
from walkbike_routes.exceptions import AccessibilityError
from walkbike_routes.routing.flows import shared_route_flows

LENGTHS = {"a": 100.0, "b": 100.0, "c": 100.0, "d": 100.0, "e": 100.0}


def _route(edges: str, cost: float, time_s: float | None = None) -> Alternative:
    length = sum(LENGTHS[e] for e in edges)
    return Alternative("gate", tuple(edges), length, time_s if time_s is not None else cost, cost)


def _shares(*routes: Alternative, scale: float = 60.0, beta: float = 1.0) -> list[float]:
    shares, _ = path_size_probabilities(
        routes, edge_lengths=LENGTHS, logit_scale_s=scale, path_size_exponent=beta
    )
    return list(shares)


def test_two_separate_routes_of_equal_cost_split_evenly() -> None:
    assert _shares(_route("ab", 100), _route("cd", 100)) == pytest.approx([0.5, 0.5])


def test_a_route_one_logit_scale_dearer_gets_e_to_the_minus_one_as_much() -> None:
    first, second = _shares(_route("ab", 100), _route("cd", 160))
    assert second / first == pytest.approx(math.exp(-1))


def test_overlapping_routes_share_a_route_s_worth_of_probability() -> None:
    # Routes 1 and 2 share edge a (half their length): each has path size 0.75.
    # Route 3 is separate (path size 1).  Weights 0.75 : 0.75 : 1.
    shares = _shares(_route("ab", 100), _route("ac", 100), _route("de", 100))
    assert shares == pytest.approx([0.75 / 2.5, 0.75 / 2.5, 1 / 2.5])
    # With no path-size correction the three would split evenly.
    assert _shares(_route("ab", 100), _route("ac", 100), _route("de", 100), beta=0) == (
        pytest.approx([1 / 3] * 3)
    )


def test_near_duplicates_are_dropped_and_the_cheapest_kept_first() -> None:
    first, copy, other = _route("abc", 100), _route("abd", 101), _route("de", 120)
    assert shared_length_ratio(first, copy, LENGTHS) == pytest.approx(2 / 3)
    kept = select_diverse(
        (other, copy, first), routes=3, maximum_shared_length_ratio=0.5, edge_lengths=LENGTHS
    )
    assert kept == (first, other)
    assert select_diverse((first, other), routes=1, maximum_shared_length_ratio=0.5,
                          edge_lengths=LENGTHS) == (first,)  # fmt: skip


def _best(origin: str, edges: str, cost: float, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "origin_id": origin, "attached_node_id": f"n{origin}", "route_reachable": True,
        "within_time_limit": True, "time_limit_min": 10.0, "snap_travel_time_s": 30.0,
        "access_point_id": "gate", "physical_edge_ids": ";".join(edges),
        "physical_length_m": 100.0 * len(edges), "network_travel_time_s": cost,
        "generalized_cost_s": cost, "demand_weight": 10.0,
    }  # fmt: skip
    row.update(extra)
    return row


def test_each_origin_splits_its_weight_within_the_time_cap() -> None:
    routes = pd.DataFrame(
        [
            _best("1", "ab", 100),
            _best("2", "cd", 100, within_time_limit=False),
            _best("3", "e", 100, route_reachable=False),
        ]
    )
    alternatives = {
        # A same-cost twin, a separate route, and one too slow for the 10-minute cap.
        "n1": (_route("ab", 100), _route("cd", 100), _route("de", 130, time_s=600)),
    }
    shares = split_routes(routes, alternatives, settings=ChoiceSettings(), edge_lengths=LENGTHS)
    assert shares.origin_id.tolist() == ["1", "1"]
    assert shares.physical_edge_ids.tolist() == ["a;b", "c;d"]
    assert shares.assigned_weight.tolist() == pytest.approx([5.0, 5.0])
    assert shares.total_travel_time_s.tolist() == pytest.approx([130.0, 130.0])
    one = split_routes(
        routes, alternatives, settings=ChoiceSettings(routes=1), edge_lengths=LENGTHS
    )
    assert one.route_share.tolist() == [1.0] and one.physical_edge_ids.tolist() == ["a;b"]


def test_the_best_route_stays_first_when_the_search_found_a_cheaper_twin() -> None:
    routes = pd.DataFrame([_best("1", "ab", 100)])
    cheaper = {"n1": (_route("ac", 99),)}
    shares = split_routes(routes, cheaper, settings=ChoiceSettings(), edge_lengths=LENGTHS)
    assert shares.physical_edge_ids.iloc[0] == "a;b"


def test_flows_add_each_share_once_per_edge() -> None:
    edges = gpd.GeoDataFrame(
        {"physical_edge_id": list("abc")},
        geometry=[LineString([(0, i), (1, i)]) for i in range(3)],
        crs=26910,
    )
    shares = pd.DataFrame(
        {
            "origin_id": ["1", "1", "2"],
            "assigned_weight": [6.0, 4.0, 3.0],
            "physical_edge_ids": ["a;b", "a;c;a", "b"],
        }
    )
    flows = shared_route_flows(shares, edges).set_index("physical_edge_id")
    assert flows.potential_flow.to_dict() == {"a": 10.0, "b": 9.0, "c": 4.0}
    assert flows.contributing_origins.to_dict() == {"a": 1, "b": 2, "c": 1}
    with pytest.raises(AccessibilityError, match="missing physical edges"):
        shared_route_flows(shares.assign(physical_edge_ids="z"), edges)
    with pytest.raises(AccessibilityError, match="finite and nonnegative"):
        shared_route_flows(shares.assign(assigned_weight=-1.0), edges)
    with pytest.raises(AccessibilityError, match="missing fields"):
        shared_route_flows(shares.drop(columns="assigned_weight"), edges)


@pytest.mark.parametrize(
    "settings",
    [{"routes": 0}, {"routes": 5, "search_paths": 4}, {"maximum_cost_ratio": 0.9},
     {"maximum_shared_length_ratio": 0}, {"logit_scale_s": 0}, {"path_size_exponent": -1}],
)  # fmt: skip
def test_unusable_settings_are_refused(settings: dict[str, float]) -> None:
    with pytest.raises(AccessibilityError):
        ChoiceSettings(**settings)  # type: ignore[arg-type]


def _graph(links: list[tuple[str, str, str, float, float, float]]) -> nx.DiGraph[str]:
    graph: nx.DiGraph[str] = nx.DiGraph()
    for edge, u, v, length, time_s, cost in links:
        graph.add_edge(u, v, physical_edge_id=edge, physical_length_m=length,
                       travel_time_s=time_s, generalized_cost_s=cost)  # fmt: skip
    return graph


def test_a_route_over_the_cap_does_not_crowd_out_a_diverse_one_within_it() -> None:
    # A: cheapest, 500 s.  B and C share the long edge X (over 90% of each);
    # B is cheaper but takes 610 s, over a 600 s cap, so C must stand in for it.
    graph = _graph([
        ("A1", "O", "a", 250, 250, 500), ("A2", "a", "G", 250, 250, 500),
        ("X", "O", "x", 570, 570, 990), ("B", "x", "G", 40, 40, 50),
        ("C1", "x", "c", 10, 10, 27), ("C2", "c", "G", 10, 10, 27),
    ])  # fmt: skip
    lengths = {e: d["physical_length_m"] for _, _, d in graph.edges(data=True)
               for e in [d["physical_edge_id"]]}  # fmt: skip
    found = near_best_alternatives(graph, {"gate": "G"}, weight="generalized_cost_s",
                                   settings=ChoiceSettings(), nodes={"O"})  # fmt: skip
    routes = pd.DataFrame([{**_best("1", "", 1000), "attached_node_id": "O",
                            "physical_edge_ids": "A1;A2", "physical_length_m": 500.0,
                            "network_travel_time_s": 500.0, "snap_travel_time_s": 0.0}])  # fmt: skip
    shares = split_routes(routes, found, settings=ChoiceSettings(), edge_lengths=lengths)
    assert shares.physical_edge_ids.tolist() == ["A1;A2", "X;C1;C2"]


def test_a_shortest_distance_search_reads_the_cost_gap_in_metres_at_riding_speed() -> None:
    graph = _graph([
        ("P1", "O", "p", 500, 112, 112), ("P2", "p", "G", 500, 112, 112),
        ("Q1", "O", "q", 545, 122, 122), ("Q2", "q", "G", 545, 122, 122),
    ])  # fmt: skip
    search = {"weight": "physical_length_m", "settings": ChoiceSettings(), "nodes": {"O"}}
    seconds_as_metres = near_best_alternatives(graph, {"gate": "G"}, **search)  # type: ignore[arg-type]
    assert len(seconds_as_metres["O"]) == 1  # a 60 m gap: the 90 m longer route is out
    at_speed = near_best_alternatives(graph, {"gate": "G"}, weight_per_second=4.47, **search)  # type: ignore[arg-type]
    assert len(at_speed["O"]) == 2  # 60 s at 4.47 m/s is 268 m


def test_a_detour_near_the_destination_is_judged_against_the_whole_trip() -> None:
    # 600 s to x, then 10 s direct or 12 s round by y.  The detour is 20% more
    # than what remains from x but well within 10% of the trip from O.
    graph = _graph([
        ("OX", "O", "x", 600, 600, 600), ("XG", "x", "G", 10, 10, 10),
        ("XY", "x", "y", 6, 6, 6), ("YG", "y", "G", 6, 6, 6),
    ])  # fmt: skip
    found = near_best_alternatives(graph, {"gate": "G"}, weight="generalized_cost_s",
                                   settings=ChoiceSettings(), nodes={"O", "x"})  # fmt: skip
    assert [a.physical_edge_ids for a in found["O"]] == [("OX", "XG"), ("OX", "XY", "YG")]
    assert [a.physical_edge_ids for a in found["x"]] == [("XG",)]  # 12 s is over 10% more than 10


def test_routes_found_by_length_split_by_length_not_by_time() -> None:
    # The shorter route waits at a signal: 100 m in 150 s, against 105 m in 80 s.
    graph = _graph([
        ("HA", "H", "a", 50, 40, 40), ("AG", "a", "G", 50, 110, 110),
        ("HB", "H", "b", 52, 40, 40), ("BG", "b", "G", 53, 40, 40),
    ])  # fmt: skip
    lengths = {"HA": 50.0, "AG": 50.0, "HB": 52.0, "BG": 53.0}
    found = near_best_alternatives(graph, {"gate": "G"}, weight="physical_length_m",
                                   settings=ChoiceSettings(), nodes={"H"}, weight_per_second=1.34)  # fmt: skip
    routes = pd.DataFrame([{**_best("1", "", 150), "attached_node_id": "H",
                            "physical_edge_ids": "HA;AG", "physical_length_m": 100.0,
                            "network_travel_time_s": 150.0, "snap_travel_time_s": 0.0}])  # fmt: skip
    shares = split_routes(routes, found, settings=ChoiceSettings(), edge_lengths=lengths,
                          length_speed_mps=1.34)  # fmt: skip
    assert shares.physical_edge_ids.tolist() == ["HA;AG", "HB;BG"]
    assert shares.route_share[0] > shares.route_share[1]  # the shorter route leads


def test_the_same_streets_twice_are_one_route() -> None:
    twin = Alternative("gate", ("A",), 100.0, 100.0, 100.0)
    routes = pd.DataFrame([{**_best("1", "", 100), "attached_node_id": "O",
                            "physical_edge_ids": "B", "physical_length_m": 100.0,
                            "network_travel_time_s": 100.0, "snap_travel_time_s": 0.0}])  # fmt: skip
    settings = ChoiceSettings(maximum_shared_length_ratio=1.0)
    shares = split_routes(routes, {"O": (twin, twin)}, settings=settings,
                          edge_lengths={"A": 100.0, "B": 100.0})  # fmt: skip
    assert shares.physical_edge_ids.tolist() == ["B", "A"]
    assert shares.route_share.tolist() == pytest.approx([0.5, 0.5])


def test_a_route_over_a_link_of_unknown_length_is_refused() -> None:
    with pytest.raises(AccessibilityError, match="no known length: 'B'"):
        path_size_probabilities(
            (
                Alternative("gate", ("A",), 100.0, 100.0, 100.0),
                Alternative("gate", ("B",), 100.0, 100.0, 100.0),
            ),
            edge_lengths={"A": 100.0}, logit_scale_s=60.0, path_size_exponent=1.0,
        )  # fmt: skip
    with pytest.raises(AccessibilityError, match="no known length: 'C'"):  # even at zero length
        path_size_probabilities(
            (Alternative("gate", ("C",), 0.0, 0.0, 0.0),),
            edge_lengths={}, logit_scale_s=60.0, path_size_exponent=1.0,
        )  # fmt: skip
