"""The Broach, Gliebe and Dill (2011) cycling preset."""

from __future__ import annotations

import copy
import dataclasses
import math
from pathlib import Path
from typing import Any

import geopandas as gpd
import networkx as nx
import pandas as pd
import pytest
import yaml
from shapely.geometry import LineString, MultiLineString, Point

from walkbike_routes import config, demo, study
from walkbike_routes.analysis.screens import (
    BIKE_LANE,
    BIKE_SIGNAL,
    SIDEWALK,
    junction_cost,
    treat_edges,
)
from walkbike_routes.choice.path_size import Alternative, ChoiceSettings, path_size_probabilities
from walkbike_routes.costs import presets
from walkbike_routes.exceptions import AccessibilityError
from walkbike_routes.network import osm_streets as o
from walkbike_routes.network import prepare
from walkbike_routes.network.base import costs_are_valid, routable_links_to_graph
from walkbike_routes.network.broach import (
    METRES_PER_MILE,
    STRESS_TERMS,
    BroachMovements,
    BroachProfile,
    _slow_signals,
    build_broach_links,
    facility_kind,
    link_ratio,
    traffic_band,
)
from walkbike_routes.network.signals import SignalWaitInventory, build_signal_wait_inventory
from walkbike_routes.routing.turns import sink, turn_graph

DEMO = Path(__file__).resolve().parents[1] / "config" / "demo.yaml"
CRS = 26910
MILE_S = METRES_PER_MILE / (10 * 0.44704)  # seconds per mile at the preset's 10 mph


def _profile(name: str = "broach-2011") -> BroachProfile:
    profile = presets.load("bike", name).profile()
    assert isinstance(profile, BroachProfile)
    return profile


# The paper's Table 3: the % of distance each term is worth, exp(b / b_d) - 1.
TABLE_3 = [
    ("turns_per_mi", 7.4, 4.2),
    ("upslope_2_4", 72.3, 37.1),
    ("upslope_4_6", 290.4, 120.3),
    ("upslope_6_plus", 1106.6, 323.9),
    ("signals_per_mi", 3.6, 2.1),
    ("stop_signs_per_mi", 0.9, 0.5),
    ("left_turn_unsignalised_10_20k_per_mi", 16.2, 9.1),
    ("left_turn_unsignalised_20k_plus_per_mi", 43.1, 23.1),
    ("unsignalised_cross_10k_plus_right_turn_per_mi", 6.7, 3.8),
    ("unsignalised_cross_5_10k_per_mi", 7.2, 4.1),
    ("unsignalised_cross_10_20k_per_mi", 10.4, 5.9),
    ("unsignalised_cross_20k_plus_per_mi", 61.7, 32.2),
    ("bike_boulevard", -17.9, -10.8),
    ("bike_path", -26.0, -16.0),
    ("traffic_10_20k_no_lane", 22.3, 36.8),
    ("traffic_20_30k_no_lane", 137.3, 140.0),
    ("traffic_30k_plus_no_lane", 619.4, 715.7),
    ("bridge_bike_lane", -29.3, -18.2),
    ("bridge_separated", -44.9, -29.2),
]


@pytest.mark.parametrize(("term", "noncommute", "commute"), TABLE_3)
def test_the_coefficients_reproduce_the_papers_table_3(
    term: str, noncommute: float, commute: float
) -> None:
    for name, expected in (("broach-2011", noncommute), ("broach-2011-commute", commute)):
        got = 100 * (math.exp(_profile(name).ratio(term)) - 1)
        assert got == pytest.approx(expected, abs=max(0.6, abs(expected) * 0.012)), name


def test_traffic_bands_come_from_road_class() -> None:
    assert traffic_band("cycleway", None) == -1
    assert traffic_band("residential", None) == 0
    assert traffic_band("tertiary", "2") == 1
    assert traffic_band("secondary", None) == 2
    assert traffic_band("secondary", 4) == 3  # four lanes: at least 20-30k
    assert traffic_band("residential", 4) == 0  # lanes only raise a busy road
    assert traffic_band("primary", None) == 3
    assert traffic_band("trunk", None) == 4
    # A slip lane carries only the turning traffic, not its road's.
    assert traffic_band("primary_link", None) == 0
    assert traffic_band("motorway_link", None) == 2
    assert traffic_band("primary_link", None, 12000) == 2  # a count still wins


def test_bad_profiles_are_refused() -> None:
    good = dict(_profile().coefficients)
    with pytest.raises(ValueError, match="missing"):
        BroachProfile("x", coefficients={k: v for k, v in good.items() if k != "bike_path"})
    with pytest.raises(ValueError, match="trip"):
        BroachProfile("x", coefficients=good, trip="errand")
    with pytest.raises(ValueError, match="negative"):
        BroachProfile("x", coefficients={**good, "ln_distance": 1.0, "ln_distance_commute": 0.0})
    with pytest.raises(ValueError, match="lane_term"):
        BroachProfile("x", coefficients=good, lane_term="turns_per_mi")


def _net() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """A residential street a-b-c meeting a primary road at c, a stop at b,
    a 5% climb from a to b and a cycleway c-d."""
    points = {"a": (0, 0), "b": (200, 0), "c": (400, 0), "n": (400, 200), "s": (400, -200),
              "d": (600, 0)}  # fmt: skip
    nodes = gpd.GeoDataFrame(
        {"node_id": list(points), "control": ["none", "stop", "none", "none", "none", "none"],
         "elevation_m": [0.0, 10.0, 10.0, 10.0, 10.0, 10.0]},
        geometry=[Point(p) for p in points.values()], crs=CRS,
    )  # fmt: skip
    rows = [("ab", "a", "b", "residential", "none"), ("bc", "b", "c", "residential", "none"),
            ("nc", "n", "c", "primary", "none"), ("cs", "c", "s", "primary", "lane"),
            ("cd", "c", "d", "cycleway", "path")]  # fmt: skip
    links = gpd.GeoDataFrame(
        {"link_id": [r[0] for r in rows], "u": [r[1] for r in rows], "v": [r[2] for r in rows],
         "highway": [r[3] for r in rows], "bike_facility": [r[4] for r in rows],
         "lanes": [None] * 5, "speed_mph": [None] * 5, "sidewalk": ["both"] * 5},
        geometry=[LineString([points[r[1]], points[r[2]]]) for r in rows], crs=CRS,
    )  # fmt: skip
    return prepare.physical_edges(links, nodes), prepare.nodes(nodes)


def _row(links: gpd.GeoDataFrame, edge: str, direction: str) -> Any:
    return links[(links.physical_edge_id == edge) & (links.direction == direction)].iloc[0]


def test_a_counted_road_is_banded_by_its_count() -> None:
    edges, nodes = _net()
    edges["traffic_aadt"] = [None, None, 13000.0, None, None]  # nc: a primary with 13k a day
    links = build_broach_links(edges, nodes, _profile())
    flat = 200 / (10 * 0.44704)
    assert _row(links, "nc", "forward").stress_cost_s == pytest.approx(flat * -1.05 / -5.22)
    assert _row(links, "cs", "forward").stress_cost_s == 0  # the lane still removes it


def _moves(
    edges: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame, profile: BroachProfile | None = None
) -> BroachMovements:
    profile = profile or _profile()
    links = build_broach_links(edges, nodes, profile)
    graph = routable_links_to_graph(links, directed=True)
    return BroachMovements(graph, edges, nodes, profile)  # type: ignore[arg-type]


def _miles(term: float) -> float:
    """A per-mile coefficient as seconds per event, everyday trips."""
    return term / -5.22 * MILE_S


def test_movements_are_classified_from_headings_at_intersections() -> None:
    moves = _moves(*_net())  # heading east from b to c: d ahead, n to the left, s to the right
    assert [moves.movement("b", "c", w) for w in "dns"] == ["through", "left", "right"]
    assert moves.movement("n", "c", "b") == "right"  # southbound, turning west
    assert moves.movement("s", "c", "b") == "left"
    assert moves.movement("a", "b", "c") == "through"  # only two ways meet at b


def test_movement_costs_match_a_hand_calculation() -> None:
    moves = _moves(*_net())
    # Elm (residential) meets the primary at c without a signal.
    ahead = moves("b", "c", "d")
    assert ahead.turn_cost_s == 0 and ahead.crossing_cost_s == pytest.approx(_miles(-2.51))
    left = moves("b", "c", "n")  # a left across the primary is a crossing too
    assert left.turn_cost_s == pytest.approx(_miles(-0.371))
    assert left.crossing_cost_s == pytest.approx(_miles(-2.51))
    right = moves("b", "c", "s")  # a right onto a 10k+ road has its own, smaller term
    assert right.turn_cost_s == pytest.approx(_miles(-0.371))
    assert right.crossing_cost_s == pytest.approx(_miles(-0.338))
    # Leaving the primary: a left across its 20k+ traffic, a right costs only the turn.
    off = moves("s", "c", "b")
    assert off.turn_cost_s == pytest.approx(_miles(-0.371 - 1.87)) and off.crossing_cost_s == 0
    assert moves("n", "c", "b").turn_cost_s == pytest.approx(_miles(-0.371))
    assert moves("n", "c", "b").crossing_cost_s == 0
    assert moves("a", "b", "c").generalized_cost_s == 0  # no intersection at b
    commute = _moves(*_net(), _profile("broach-2011-commute"))("b", "c", "n")
    assert commute.turn_cost_s == pytest.approx(-0.371 / (-5.22 - 3.76) * MILE_S)


def test_a_left_turn_off_a_10_20k_road_has_the_lower_term() -> None:
    edges, nodes = _net()
    edges["traffic_aadt"] = [None, None, None, 15000.0, None]
    assert _moves(edges, nodes)("s", "c", "b").turn_cost_s == pytest.approx(_miles(-0.371 - 0.782))


def test_a_count_changing_along_a_road_is_no_crossing() -> None:
    edges, nodes = _net()
    edges["name"] = ["Elm", "Elm", "Main", "Main", None]
    edges["traffic_aadt"] = [None, None, 9000.0, 25000.0, None]  # n-c quieter than c-s
    moves = _moves(edges, nodes)
    assert moves("n", "c", "s").crossing_cost_s == 0  # Main carries on past c
    assert moves("b", "c", "d").crossing_cost_s == pytest.approx(_miles(-2.51))  # 20k+ at c
    edges["name"] = ["Elm", "Elm", "Main", "Other", None]
    assert _moves(edges, nodes)("n", "c", "s").crossing_cost_s > 0  # another road meets it


def test_signals_remove_the_unsignalised_terms_but_not_the_turn() -> None:
    edges, nodes = _net()
    nodes.loc[nodes.node_id == "c", ["control", "highway"]] = ["signal", "traffic_signals"]
    moves = _moves(edges, nodes)
    assert moves("b", "c", "d").generalized_cost_s == 0
    assert moves("s", "c", "b").turn_cost_s == pytest.approx(_miles(-0.371))
    assert moves("s", "c", "b").crossing_cost_s == 0


def test_a_slow_signal_holds_every_passage_but_a_right_on_red() -> None:
    edges, nodes = _net()  # one signal node, c: b-c is the street, n-c-s the primary
    nodes.loc[nodes.node_id == "c", ["control", "highway"]] = ["signal", "traffic_signals"]
    nodes["extra_wait_s"] = [40.0 if n == "c" else None for n in nodes.node_id]
    moves = _moves(edges, nodes)
    assert moves.through("b", "c", "c", "d").control_cost_s == 40.0  # straight on
    assert moves.through("b", "c", "c", "n").control_cost_s == 40.0  # left
    assert moves.through("b", "c", "c", "s").control_cost_s == 0  # right, on red
    nodes["right_on_red"] = ["no" if n == "c" else "" for n in nodes.node_id]
    assert _moves(edges, nodes).through("b", "c", "c", "s").control_cost_s == 40.0
    assert moves("b", "c", "s").control_cost_s == 0  # a route starting inside pays none


def _dual_carriageway() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """A signal where a street meets a divided road: southbound carriageway
    n1-c1-s1 at x=400, northbound s2-c2-n2 at x=420, the street a-c1-c2-e."""
    points = {"a": (0, 0), "c1": (400, 0), "c2": (420, 0), "e": (800, 0),
              "n1": (400, 300), "s1": (400, -300), "n2": (420, 300), "s2": (420, -300)}  # fmt: skip
    nodes = gpd.GeoDataFrame(
        {"node_id": list(points),
         "control": ["signal" if k in {"c1", "c2"} else "none" for k in points]},
        geometry=[Point(p) for p in points.values()], crs=CRS,
    )  # fmt: skip
    rows = [("ac1", "a", "c1", "tertiary", "no"), ("c1c2", "c1", "c2", "tertiary", "no"),
            ("c2e", "c2", "e", "tertiary", "no"), ("n1c1", "n1", "c1", "primary", "yes"),
            ("c1s1", "c1", "s1", "primary", "yes"), ("s2c2", "s2", "c2", "primary", "yes"),
            ("c2n2", "c2", "n2", "primary", "yes")]  # fmt: skip
    links = gpd.GeoDataFrame(
        {"link_id": [r[0] for r in rows], "u": [r[1] for r in rows], "v": [r[2] for r in rows],
         "highway": [r[3] for r in rows], "oneway": [r[4] for r in rows],
         "name": ["St", "St", "St", "Rd", "Rd", "Rd", "Rd"],
         "bike_facility": ["none"] * 7, "lanes": [None] * 7, "speed_mph": [None] * 7,
         "sidewalk": ["both"] * 7},
        geometry=[LineString([points[r[1]], points[r[2]]]) for r in rows], crs=CRS,
    )  # fmt: skip
    return prepare.physical_edges(links, nodes), prepare.nodes(nodes)


def test_a_divided_road_signal_is_one_junction_for_turns() -> None:
    edges, nodes = _dual_carriageway()
    moves = _moves(edges, nodes)
    assert moves.clusters["c1"] == moves.clusters["c2"]
    turn = _miles(-0.371)
    # Southbound, turning east onto the street: one left, though it crosses c1 and c2.
    assert moves.through("n1", "c1", "c2", "e").turn_cost_s == pytest.approx(turn)
    assert moves.through("n1", "c1", "c1", "s1").turn_cost_s == 0  # straight on
    assert moves.through("a", "c1", "c2", "e").turn_cost_s == 0  # along the street
    right = moves.through("n1", "c1", "c1", "a")
    assert right.turn_cost_s == pytest.approx(turn) and right.control_cost_s == 0
    # The ban and the wait, given on one node, hold for the whole signal.
    nodes["right_on_red"] = ["no" if n == "c2" else "" for n in nodes.node_id]
    nodes["extra_wait_s"] = [50.0 if n == "c1" else None for n in nodes.node_id]
    slow = _moves(edges, nodes)
    assert slow.through("n1", "c1", "c1", "a").control_cost_s == 50.0
    assert slow.through("s2", "c2", "c2", "n2").control_cost_s == 50.0
    graph = turn_graph(slow.graph, slow)
    # Westbound from e to a crosses c2 then c1: one passage, straight on, no turn.
    path = nx.shortest_path(graph, "e", sink("a"), weight="generalized_cost_s")
    costs = [graph[x][y].get("turn_cost_s", 0.0) for x, y in zip(path, path[1:], strict=False)]
    assert sum(costs) == 0


def test_slow_signal_values_spread_over_the_signal() -> None:
    signals = SignalWaitInventory(
        cluster_by_node={"x": "k", "y": "k"}, crossing_event_by_wait_edge={}
    )
    nodes = gpd.GeoDataFrame(
        {"node_id": ["x", "y", "z"], "right_on_red": ["no", "", ""],
         "extra_wait_s": [10.0, 30.0, 99.0]},
        geometry=[Point(0, 0), Point(1, 1), Point(9, 9)],
    )  # fmt: skip
    extra, banned = _slow_signals(nodes, signals)
    assert extra == {"x": 30.0, "y": 30.0} and banned == {"x", "y"}  # z is no signal
    for blank in (["", " ", None], pd.Series([pd.NA] * 3, dtype="Float64")):
        nodes["extra_wait_s"] = blank
        assert _slow_signals(nodes, signals)[0] == {}
    assert _slow_signals(nodes.drop(columns=["right_on_red", "extra_wait_s"]), signals) == (
        {}, frozenset(),
    )  # fmt: skip


def test_a_right_turn_pays_for_the_road_turned_onto() -> None:
    edges, nodes = _net()
    edges["traffic_aadt"] = [None, None, 25000.0, 1000.0, None]  # busy only north of c
    moves = _moves(edges, nodes)
    assert moves("b", "c", "s").crossing_cost_s == 0  # right onto the quiet arm
    assert moves("d", "c", "n").crossing_cost_s == pytest.approx(_miles(-0.338))  # onto busy


def test_crossing_a_slip_lane_is_not_crossing_its_road() -> None:
    edges, nodes = _net()
    edges.loc[edges.physical_edge_id.isin(["nc", "cs"]), "highway"] = "primary_link"
    edges.loc[edges.physical_edge_id.isin(["nc", "cs"]), "name"] = None
    assert _moves(edges, nodes)("b", "c", "d").crossing_cost_s == 0  # under 5k: no term


def test_a_link_drawn_in_parts_still_has_a_heading() -> None:
    edges, nodes = _net()
    parts = MultiLineString([[(400, 200), (400, 100)], [(400, 100), (400, 0)]])
    ids = edges.physical_edge_id
    geometry = [parts if e == "nc" else g for e, g in zip(ids, edges.geometry, strict=True)]
    edges["geometry"] = gpd.GeoSeries(geometry, index=edges.index, crs=edges.crs)
    assert _moves(edges, nodes).movement("n", "c", "b") == "right"


def test_a_node_missing_from_the_table_is_refused() -> None:
    edges, nodes = _net()
    links = build_broach_links(edges, nodes, _profile())
    graph = routable_links_to_graph(links, directed=True)
    with pytest.raises(ValueError, match="missing from the node table"):
        BroachMovements(graph, edges, nodes[nodes.node_id != "c"], _profile())  # type: ignore[arg-type]


def test_a_road_bikes_may_not_use_is_still_crossed() -> None:
    edges, nodes = _net()
    edges.loc[edges.physical_edge_id.isin(["nc", "cs"]), "bike_allowed"] = False
    moves = _moves(edges, nodes)
    assert not moves.graph.has_node("n")  # only b and d meet c by bike
    assert moves("b", "c", "d").crossing_cost_s == pytest.approx(_miles(-2.51))


def test_a_signalised_crossing_leg_pays_no_unsignalised_term() -> None:
    edges, nodes = _net()
    edges.loc[edges.physical_edge_id == "cd", "crossing"] = "traffic_signals"
    assert _moves(edges, nodes)("b", "c", "d").generalized_cost_s == 0


def test_counts_survive_preparing_the_edges() -> None:
    edges, _ = _net()
    assert "traffic_aadt" not in edges
    points = {"a": (0, 0), "b": (200, 0)}
    links = gpd.GeoDataFrame(
        {"link_id": ["ab"], "u": ["a"], "v": ["b"], "highway": ["primary"],
         "bike_facility": ["none"], "lanes": [None], "speed_mph": [None], "sidewalk": ["both"],
         "traffic_aadt": ["13000"], "traffic_aadt_source": ["count"]},
        geometry=[LineString([points["a"], points["b"]])], crs=CRS,
    )  # fmt: skip
    prepared = prepare.physical_edges(links)
    assert prepared.traffic_aadt.tolist() == [13000.0]
    assert prepared.traffic_aadt_source.tolist() == ["count"]


def test_link_costs_match_a_hand_calculation() -> None:
    edges, nodes = _net()
    profile = _profile()
    links = build_broach_links(edges, nodes, profile)
    v = 10 * 0.44704
    mile_s = METRES_PER_MILE / v
    flat = 200 / v
    b_d = -5.22
    # a->b climbs 5%: the 4-6% term on its length, then the stop sign at b.
    up = _row(links, "ab", "forward")
    assert up.stress_cost_s == pytest.approx(flat * -7.11 / b_d)
    assert up.control_cost_s == pytest.approx(-0.0483 / b_d * mile_s)
    assert up.generalized_cost_s == pytest.approx(flat * (1 - 7.11 / b_d) + -0.0483 / b_d * mile_s)
    # b->a descends: no slope term, no stop at a.
    assert _row(links, "ab", "reverse").generalized_cost_s == pytest.approx(flat)
    # Crossings depend on the movement, so links carry none.
    assert (links.crossing_cost_s == 0).all()
    # Riding along the primary road costs the 20-30k term.
    along = _row(links, "nc", "forward")
    assert along.stress_cost_s == pytest.approx(flat * -4.51 / b_d)
    # A bike lane removes the traffic term.
    assert _row(links, "cs", "forward").stress_cost_s == 0
    # The cycleway is cheaper than its length.
    path = _row(links, "cd", "forward")
    assert path.facility_cost_s == pytest.approx(flat * 1.57 / b_d)
    assert path.generalized_cost_s == pytest.approx(flat * (1 + 1.57 / b_d))
    assert (links.travel_time_s == links.movement_time_s).all()  # no signals here


def test_the_lanes_preset_gives_lanes_the_boulevard_term() -> None:
    edges, nodes = _net()
    edges.loc[edges.physical_edge_id == "bc", "bike_facility"] = "lane"  # a quiet street's lane
    flat = 200 / (10 * 0.44704)
    bonus = flat * 1.03 / -5.22
    plain = build_broach_links(edges, nodes, _profile())
    lanes = build_broach_links(edges, nodes, _profile("broach-2011-lanes"))
    # The paper's model: a lane on a quiet street is worth nothing.
    assert _row(plain, "bc", "forward").generalized_cost_s == pytest.approx(flat)
    # With lane_term, the lane gets the boulevard term, on quiet and busy streets alike,
    # and the busy street's traffic term stays removed.
    for edge in ("bc", "cs"):
        row = _row(lanes, edge, "forward")
        assert row.stress_cost_s == 0
        assert row.facility_cost_s == pytest.approx(bonus)
        assert row.generalized_cost_s == pytest.approx(flat + bonus)
    # Streets without a lane, and the path, are costed as in the paper.
    for edge in ("ab", "nc", "cd"):
        assert _row(lanes, edge, "forward").generalized_cost_s == pytest.approx(
            _row(plain, edge, "forward").generalized_cost_s
        )


def test_the_child_preset_doubles_the_stress_terms_only() -> None:
    adult, child = _profile(), _profile("broach-2011-child")
    assert child.speed_mph == 6.0 and child.stress_scale == 2.0
    for name in sorted(adult.coefficients):
        if name.endswith("_commute") or name == "ln_path_size":
            continue
        factor = 2.0 if name in STRESS_TERMS else 1.0
        assert child.ratio(name) == pytest.approx(factor * adult.ratio(name)), name
    commute = BroachProfile("c", coefficients=adult.coefficients, trip="commute", stress_scale=2)
    plain = BroachProfile("p", coefficients=adult.coefficients, trip="commute")
    assert commute.ratio("traffic_20_30k_no_lane") == pytest.approx(
        2 * plain.ratio("traffic_20_30k_no_lane")
    )
    edges, nodes = _net()
    flat = 200 / (6 * 0.44704)
    along = _row(build_broach_links(edges, nodes, child), "nc", "forward")
    assert along.stress_cost_s == pytest.approx(flat * 2 * -4.51 / -5.22)
    with pytest.raises(ValueError, match="stress_scale"):
        BroachProfile("x", coefficients=adult.coefficients, stress_scale=0)


def test_the_models_signal_term_replaces_the_wait_in_the_cost() -> None:
    edges, nodes = _net()
    nodes.loc[nodes.node_id == "c", ["control", "highway"]] = ["signal", "traffic_signals"]
    signals = build_signal_wait_inventory(nodes, edges)
    links = build_broach_links(edges, nodes, _profile(), signal_wait_inventory=signals)
    into = _row(links, "bc", "forward")
    mile_s = METRES_PER_MILE / (10 * 0.44704)
    assert into.signal_wait_s > 0
    assert into.travel_time_s == pytest.approx(into.movement_time_s + into.signal_wait_s)
    assert into.generalized_cost_s == pytest.approx(into.movement_time_s + -0.186 / -5.22 * mile_s)


def test_signals_are_found_from_the_nodes_when_no_inventory_is_given() -> None:
    edges, nodes = _net()
    nodes.loc[nodes.node_id == "c", ["control", "highway"]] = ["signal", "traffic_signals"]
    into = _row(build_broach_links(edges, nodes, _profile()), "bc", "forward")
    assert into.signal_wait_s > 0


def test_the_screen_treatment_removes_the_traffic_term() -> None:
    edges, nodes = _net()
    treated = treat_edges(edges, BIKE_LANE)
    before = _row(build_broach_links(edges, nodes, _profile()), "nc", "forward")
    after = _row(build_broach_links(treated, nodes, _profile()), "nc", "forward")
    assert before.stress_cost_s > 0 and after.stress_cost_s == 0


def test_a_zero_cost_route_takes_every_trip_and_bad_exponents_are_refused() -> None:
    a = Alternative("x", (), 0.0, 0.0, 0.0)
    b = Alternative("x", ("e2",), 100.0, 10.0, 100.0)
    shares, _ = path_size_probabilities(
        (a, b), edge_lengths={"e2": 100.0}, logit_scale_s=60, path_size_exponent=1.81,
        log_cost_exponent=5.22,
    )  # fmt: skip
    assert shares == pytest.approx((1.0, 0.0))
    for bad in (float("nan"), 0.0, -1.0, float("inf")):
        with pytest.raises(AccessibilityError):
            ChoiceSettings(log_cost_exponent=bad)


@pytest.mark.parametrize(
    ("label", "kind"),
    [("bike lane", "lane"), ("Class II", "lane"), ("class ii (buffered)", "lane"),
     ("Class IV (protected)", "track"), ("track", "track"), ("separated path", "path"),
     ("Class I", "path"), ("bike boulevard", "boulevard"), ("none", "none"), (None, "none")],
)  # fmt: skip
def test_facility_labels_from_any_source_are_understood(label: str | None, kind: str) -> None:
    assert facility_kind(label) == kind


def test_a_stop_tagged_the_osm_way_is_charged() -> None:
    edges, nodes = _net()
    osm_style = nodes.drop(columns="control").assign(
        highway=lambda n: n.node_id.map({"b": "stop"}).fillna("")
    )
    up = _row(build_broach_links(edges, osm_style, _profile()), "ab", "forward")
    assert up.control_cost_s == pytest.approx(-0.0483 / -5.22 * METRES_PER_MILE / (10 * 0.44704))


def test_missing_optional_values_cost_nothing_extra() -> None:
    edges, nodes = _net()
    edges = edges.drop(columns="lanes")
    edges["grade"] = pd.array([pd.NA] * len(edges), dtype="Float64")
    edges["bike_facility"] = pd.array([pd.NA] * len(edges), dtype="string")
    links = build_broach_links(edges, nodes, _profile())
    assert _row(links, "ab", "forward").stress_cost_s == 0  # no grade, no slope term
    assert facility_kind(pd.NA) == "none" and traffic_band(pd.NA, pd.NA) == 0


def test_routes_split_on_the_ratio_of_their_costs() -> None:
    a = Alternative("x", ("e1",), 100.0, 10.0, 100.0)
    b = Alternative("x", ("e2",), 100.0, 10.0, 110.0)
    lengths = {"e1": 100.0, "e2": 100.0}
    shares, _ = path_size_probabilities(
        (a, b), edge_lengths=lengths, logit_scale_s=60, path_size_exponent=1.81,
        log_cost_exponent=5.22,
    )  # fmt: skip
    assert shares[1] / shares[0] == pytest.approx(1.1**-5.22)
    shares, _ = path_size_probabilities(
        (a, b), edge_lengths=lengths, logit_scale_s=60, path_size_exponent=1.81,
        log_cost_exponent=5.22, access_cost_s=100.0,
    )  # fmt: skip
    assert shares[1] / shares[0] == pytest.approx((210 / 200) ** -5.22)


def test_boulevards_are_read_from_osm() -> None:
    assert o.bike_facility({"highway": "residential", "bicycle_road": "yes"}) == "boulevard"
    assert o.bike_facility({"highway": "residential", "cyclestreet": "yes"}) == "boulevard"
    assert o.bike_facility({"highway": "residential"}) == "none"


def test_a_study_runs_with_the_broach_preset(tmp_path: Path) -> None:
    raw: dict[str, Any] = copy.deepcopy(yaml.safe_load(DEMO.read_text()))
    raw["modes"] = ["bike"]
    raw["presets"] = {"bike": ["broach-2011", "furth"]}
    raw["time_caps_min"] = {"bike": 20}
    raw["route_choice"] = {"routes": 3}
    study.analyze(config.parse(raw, base=DEMO.parent), demo.build(), tmp_path)
    summary = pd.read_csv(tmp_path / "summary.csv")
    assert set(summary.preset) >= {"broach-2011", "furth"}
    shares = pd.read_parquet(tmp_path / "school" / "bike-broach-2011" / "route_shares.parquet")
    assert shares.groupby("origin_id").route_share.sum().to_numpy() == pytest.approx(1.0)
    assert (shares.route_rank > 1).any()


def test_a_zero_length_piece_costs_nothing_and_is_not_refused() -> None:
    # OpenStreetMap has occasional zero-length pieces: two nodes at one point.
    edges, nodes = _net()
    zero = edges.physical_edge_id.eq("cd")
    point = LineString([(400, 0), (400, 0)])
    edges = edges.set_geometry(
        gpd.GeoSeries(
            [point if z else g for z, g in zip(zero, edges.geometry, strict=True)],
            index=edges.index,
            crs=edges.crs,
        )
    )
    edges.loc[zero, "length_m"] = 0.0
    links = build_broach_links(edges, nodes, _profile())
    assert _row(links, "cd", "forward").generalized_cost_s == 0


@pytest.mark.parametrize(
    ("cost", "length", "valid"),
    [(5.0, 10.0, True), (0.0, 0.0, True), (0.0, 10.0, False), (-1.0, 0.0, False),
     (math.nan, 10.0, False), (5.0, math.nan, False), (5.0, -1.0, False)],
)  # fmt: skip
def test_link_costs_are_checked(cost: float, length: float, valid: bool) -> None:
    frame = pd.DataFrame({"generalized_cost_s": [cost], "physical_length_m": [length]})
    assert costs_are_valid(frame) is valid


def _street(facility: str, highway: str = "primary") -> dict[str, Any]:
    return {"highway": highway, "bike_facility": facility, "lanes": None, "traffic_aadt": None}


def test_a_protected_lane_counts_as_072_of_paint_in_every_preset() -> None:
    for name in ("broach-2011", "broach-2011-commute", "broach-2011-lanes", "broach-2011-child"):
        profile = _profile(name)
        assert profile.protected_factor == pytest.approx(0.72), name
        for highway in ("residential", "primary"):  # quiet and 20-30k streets
            full = dataclasses.replace(profile, lane_traffic_relief=1.0)
            paint = 1 + link_ratio(full, _street("lane", highway), 0.0)
            track = 1 + link_ratio(profile, _street("track", highway), 0.0)
            assert track == pytest.approx(0.72 * paint), (name, highway)
        # Hills are added after the factor: a climb costs the same on a track.
        up = link_ratio(profile, _street("track"), 0.05) - link_ratio(
            profile, _street("track"), 0.0
        )
        assert up == pytest.approx(profile.ratio("upslope_4_6"))


def test_paint_removes_a_quarter_of_the_traffic_term_for_children() -> None:
    child, adult = _profile("broach-2011-child"), _profile()
    assert adult.lane_traffic_relief == 1.0
    traffic = child.ratio("traffic_20_30k_no_lane")
    assert traffic == pytest.approx(2 * 4.51 / 5.22)  # doubled by stress_scale
    assert link_ratio(child, _street("none"), 0.0) == pytest.approx(traffic)
    assert link_ratio(child, _street("lane"), 0.0) == pytest.approx(0.75 * traffic)
    assert link_ratio(child, _street("track"), 0.0) == pytest.approx(0.72 - 1)  # all removed
    assert link_ratio(adult, _street("lane"), 0.0) == 0
    # On a quiet street there is no term to remove, so paint changes nothing.
    assert link_ratio(child, _street("lane", "residential"), 0.0) == 0


@pytest.mark.parametrize(
    ("setting", "value"),
    [("protected_factor", 0.0), ("protected_factor", 1.2), ("lane_traffic_relief", -0.1),
     ("lane_traffic_relief", 1.5)],
)  # fmt: skip
def test_lane_settings_are_checked(setting: str, value: float) -> None:
    with pytest.raises(ValueError, match=setting):
        dataclasses.replace(_profile(), **{setting: value})  # type: ignore[arg-type]


def test_a_signal_at_an_unsignalised_crossing_trades_its_penalty_for_the_signal_term() -> None:
    moves = _moves(*_net())  # the residential street b-c crosses the primary at c, no signal
    cost = junction_cost(moves, BIKE_SIGNAL)
    assert cost is not None and junction_cost(moves, SIDEWALK) is None
    signal = _miles(-0.186)
    assert cost("b", "c", "d") == pytest.approx(signal)  # straight across: no crossing term left
    assert cost("b", "c", "n") == pytest.approx(_miles(-0.371) + signal)  # a left keeps its turn
    assert cost("n", "c", "s") is None  # riding along the primary: a signal would only add cost
    assert cost("a", "b", "c") is None  # b is no junction
    edges, nodes = _net()
    nodes.loc[nodes.node_id == "c", ["control", "highway"]] = ["signal", "traffic_signals"]
    lit = junction_cost(_moves(edges, nodes), BIKE_SIGNAL)
    assert lit is not None and lit("b", "c", "d") is None  # it has one already
