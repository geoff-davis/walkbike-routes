"""Exposure and screen cases found wrong in review: each test failed before its fix."""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.geometry import LineString, Point

from tests import test_exposure as T
from walkbike_routes.analysis import exposure, exposure_crossings, exposure_rules, places
from walkbike_routes.analysis.barriers import barrier_sections

CRS = "EPSG:26910"


def _net(points: dict[str, Any], rows: list[tuple[str, str, str, dict[str, Any]]]):
    def edge(pid: str, u: str, v: str, attrs: dict[str, Any]) -> dict[str, object]:
        row = T._edge("mw", "w", "c", **attrs)
        line = LineString([points[u], points[v]])
        row.update(physical_edge_id=pid, u=u, v=v, geometry=line, length_m=line.length)
        return row

    edges = gpd.GeoDataFrame([edge(*r) for r in rows], geometry="geometry", crs=CRS)
    nodes = gpd.GeoDataFrame(
        {"node_id": list(points), "control": "none", "crossing_marking": ""},
        geometry=[Point(p) for p in points.values()], crs=CRS,
    )  # fmt: skip
    return edges, nodes


def test_a_road_that_changes_name_at_a_junction_is_still_crossed() -> None:
    edges = T._edges(mw={"name": "West Main Street"}, me={"name": "East Main Street"})
    events = exposure.crossing_events(T._shares(("os;on", 10.0)), edges, T._nodes())
    assert events.place.tolist() == ["J:c"]
    crossing = exposure.exposed_crossings(events, edges, T._nodes(), "bike", 10).iloc[0]
    assert crossing.people == 10 and crossing.risk_points == 2
    assert crossing.risk_factors.startswith("crossing East Main Street / West Main Street:")
    # Riding along it, through the change of name, crosses nothing.
    assert exposure.crossing_events(T._shares(("mw;me", 1.0)), edges, T._nodes()).empty


def test_two_roads_meeting_end_to_end_at_a_bend_are_not_a_crossing() -> None:
    busy = {"highway": "secondary"}
    edges, nodes = _net(
        {"a": (-100, 0), "b": (0, 0), "c": (0, 100)},
        [("p", "a", "b", busy | {"name": "Main Street"}),
         ("q", "b", "c", busy | {"name": "Foothill Road"})],
    )  # fmt: skip
    for route in ("p;q", "q;p"):
        assert exposure.crossing_events(T._shares((route, 7.0)), edges, nodes).empty


def test_a_crossing_way_ending_on_its_road_is_one_crossing() -> None:
    points = dict(T.NODES) | {"m": (50.0, 0.0)}
    main = {"name": "Main Street", "highway": "secondary"}
    path = {"name": "", "highway": "footway", "car_allowed": False, "crossed_road": "secondary"}
    edges, nodes = _net(
        points,
        [("mw", "w", "c", main), ("me1", "c", "m", main), ("me2", "m", "e", main),
         ("on", "c", "n", {"name": "Oak Street"}), ("os", "s", "c", {"name": "Oak Street"}),
         ("h", "f1", "m", path)],
    )  # fmt: skip
    events = exposure.crossing_events(T._shares(("h;me1", 10.0)), edges, nodes)
    assert events.kind.tolist() == ["way"]


def _divided(median: str, north: str) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Main Street as two one-way lines 20 m apart; a side street crosses both."""
    points = {"s": (0, -100), "n1": (0, 0), "n2": (0, 20), "n": (0, 120), "w1": (-100, 0),
              "e1": (100, 0), "w2": (-100, 20), "e2": (100, 20)}  # fmt: skip
    main = {"name": "Main Street", "highway": "primary", "oneway": "yes", "lanes": 2.0}
    return _net(
        points,
        [("a1", "w1", "n1", main), ("a2", "n1", "e1", main), ("b1", "e2", "n2", main),
         ("b2", "n2", "w2", main), ("s1", "s", "n1", {"name": "Oak Street"}),
         ("m", "n1", "n2", {"name": median}), ("s2", "n2", "n", {"name": north})],
    )  # fmt: skip


def test_a_divided_road_is_one_junction_whatever_the_side_street_is_called() -> None:
    for median, north in (("Oak Street", "Oak Street"), ("", "Elm Street"), ("", "Oak Street")):
        edges, nodes = _divided(median, north)
        events = exposure.crossing_events(T._shares(("s1;m;s2", 10.0)), edges, nodes)
        crossings = exposure.exposed_crossings(events, edges, nodes, "walk", 10)
        assert len(crossings) == 1, (median, north)
        assert (
            crossings.people.iloc[0] == 10 and "4 or more lanes" in crossings.risk_factors.iloc[0]
        )
    found = places.Places(edges)
    assert places.junction_groups(found, {"J:n1": 10.0, "J:n2": 10.0})[0][1] == ["J:n1", "J:n2"]


def test_side_streets_a_few_metres_apart_along_one_road_stay_two_junctions() -> None:
    main = {"name": "Main Street", "highway": "secondary"}
    edges, _ = _net(
        {"w": (-100, 0), "a": (0, 0), "b": (40, 0), "e": (140, 0), "n": (0, 100), "s": (40, -100)},
        [("m1", "w", "a", main), ("m2", "a", "b", main), ("m3", "b", "e", main),
         ("o", "a", "n", {"name": "Oak Street"}), ("x", "b", "s", {"name": "Elm Street"})],
    )  # fmt: skip
    groups = places.junction_groups(places.Places(edges), {"J:a": 5.0, "J:b": 4.0})
    assert [keys for _, keys in groups] == [["J:a"], ["J:b"]]


def test_links_that_read_differently_are_not_joined_into_one_place() -> None:
    edges = T._edges(
        mw={"pedestrian_status": "unknown", "highway": "tertiary"},
        me={"pedestrian_status": "unknown", "highway": "residential", "speed_mph": 40.0},
    )
    risk = exposure.street_risk(edges, "walk")
    assert risk.risk_points["mw"] == risk.risk_points["me"] == 2  # the same points, differently
    streets = exposure.exposed_streets(edges, pd.Series({"mw": 10.0, "me": 1.0}), risk, 30)
    assert len(streets) == 2
    assert set(streets.risk_factors) == {risk.risk_factors["mw"], risk.risk_factors["me"]}


def test_a_reason_names_both_shortfalls_and_which_bar_applied() -> None:
    both = exposure_rules.tier_reason(1, 2, 1000)
    assert "1 risk point (fix first needs 3)" in both and "fewer than 5 people" in both
    share = exposure_rules.tier_reason(4, 1.2, 24.68)
    assert "fewer than 1.23 people use it (about 1.2; the bar is 5% of the 24.7 counted)" in share
    assert "the bar is" not in exposure_rules.tier_reason(4, 2, 1000)  # the plain number applied
    assert "about 4.996" in exposure_rules.tier_reason(2, 4.996, 1000)  # not "about 5 people"


def test_the_crosswalk_factor_says_it_is_what_the_map_shows() -> None:
    events = exposure.crossing_events(T._shares(("os;on", 5.0)), T._edges(), T._nodes())
    crossing = exposure.exposed_crossings(events, T._edges(), T._nodes(), "walk", 5).iloc[0]
    assert "no marked crosswalk on the map (+1)" in crossing.risk_factors


def test_cycling_points_read_the_presets_stress_levels() -> None:
    edges = T._edges()
    edges["lts_furth"] = [4, 4, 1, 1, 1]
    assert exposure.street_risk(edges, "bike").risk_points["mw"] == 3
    assert exposure.street_risk(edges, "bike", lts_column="lts_furth").risk_points["mw"] == 5
    missing = T._edges(mw={"lts_best": float("nan")})
    assert exposure.street_risk(missing, "bike").risk_points["mw"] == 0  # and no crash


def test_car_flows_keyed_by_number_still_find_their_streets() -> None:
    edges = T._edges()
    edges["physical_edge_id"] = ["1", "2", "3", "4", "5"]
    cars = pd.Series({1: 9.0, 2: 1.0, 3: 1.0, 4: 1.0})
    assert "drop-off" in exposure.street_risk(edges, "bike", cars).risk_factors["1"]


def test_a_junction_with_no_street_near_has_a_plain_label() -> None:
    edges = gpd.GeoDataFrame(
        [T._edge("a", "w", "c"), T._edge("b", "c", "e")], geometry="geometry", crs=CRS
    )
    assert places.Places(edges).junction("c") == "Junction"


def test_section_labels_leave_out_unnamed_cross_streets() -> None:
    graph: nx.DiGraph[str] = nx.DiGraph()
    for u, v, pid, name, highway in (
        ("a", "b", "p1", "Main Street", "secondary"), ("b", "c", "p2", "Main Street", "secondary"),
        ("a", "x", "f1", "", "footway"), ("c", "y", "s1", "", "service"),
    ):  # fmt: skip
        for start, end in ((u, v), (v, u)):
            graph.add_edge(start, end, routable_link_id=f"{pid}:{start}{end}",
                           physical_edge_id=pid, name=name, highway=highway, length_m=100.0)  # fmt: skip
    edges = gpd.GeoDataFrame(
        {"physical_edge_id": ["p1", "p2", "f1", "s1"], "name": ["Main Street"] * 2 + [""] * 2,
         "highway": ["secondary", "secondary", "footway", "service"]},
        geometry=[LineString([(0, 0), (1, 1)])] * 4,
    )  # fmt: skip
    names = places.edge_names(edges)
    _, sections = barrier_sections(
        graph, lambda at: at["physical_edge_id"] in ("p1", "p2"),
        name_of=lambda pid, _a: names.get(pid, ""),
    )  # fmt: skip
    assert sections.label.tolist() == ["Main Street"]


def _loop(
    *spurs: tuple[str, str, tuple[float, float]],
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """A secondary road that leaves ``c`` east and comes back from the south, and some spurs."""
    points = {"c": (0.0, 0.0)} | {node: xy for _, node, xy in spurs}
    edges, nodes = _net(points, [(pid, "c", node, {"name": pid.title()}) for pid, node, _ in spurs])
    loop = T._edge("mw", "w", "c", name="Loop", highway="secondary")
    loop.update(physical_edge_id="loop", u="c", v="c",
                geometry=LineString([(0, 0), (10, 0), (10, -10), (0, -10), (0, 0)]))  # fmt: skip
    return T._with(edges, [loop]), nodes


def test_a_loop_is_two_legs_of_its_own_road() -> None:
    # Arriving north along the loop's last leg and turning left (west) crosses the loop road.
    edges, nodes = _loop(("spur", "w", (-100.0, 0.0)), ("north", "n", (0.0, 100.0)))
    events = exposure.crossing_events(T._shares(("loop;spur", 1.0)), edges, nodes)
    assert events.detail.tolist() == ["Loop"]
    # With one other street, its node still has three legs.
    edges, nodes = _loop(("spur", "n", (0.0, 100.0)))
    assert exposure_crossings._Network(edges, nodes).motor_legs("c") == 3
    events = exposure.crossing_events(T._shares(("spur;loop", 1.0)), edges, nodes)
    assert events.detail.tolist() == ["Loop"]


def test_a_divided_road_that_changes_name_keeps_one_name_on_both_carriageways() -> None:
    points = {"s": (0, -100), "n1": (0, 0), "n2": (0, 20), "n": (0, 120), "w1": (-100, 0),
              "e1": (100, 0), "w2": (-100, 20), "e2": (100, 20)}  # fmt: skip
    main = {"highway": "primary", "oneway": "yes", "lanes": 2.0}
    west, east = main | {"name": "West Main"}, main | {"name": "East Main"}
    edges, nodes = _net(
        points,
        [("a1", "w1", "n1", west | {"traffic_aadt": 25000.0}),
         ("a2", "n1", "e1", east | {"traffic_aadt": 15000.0}),
         ("b1", "e2", "n2", east | {"traffic_aadt": 25000.0}),
         ("b2", "n2", "w2", west | {"traffic_aadt": 15000.0}),
         ("s1", "s", "n1", {"name": "Oak Street"}), ("m", "n1", "n2", {"name": "Oak Street"}),
         ("s2", "n2", "n", {"name": "Oak Street"})],
    )  # fmt: skip
    events = exposure.crossing_events(T._shares(("s1;m;s2", 10.0)), edges, nodes)
    assert set(events.detail) == {"East Main / West Main"}
    crossing = exposure.exposed_crossings(events, edges, nodes, "bike", 10)
    assert len(crossing) == 1 and "4 or more lanes" in crossing.risk_factors.iloc[0]


def test_a_gap_link_joins_a_junction_even_beside_a_link_of_the_road() -> None:
    main = {"name": "Main Street", "highway": "secondary"}
    edges, _ = _net(
        {"a": (0, 0), "b": (20, 0), "n": (0, 100), "s": (20, -100)},
        [("m", "a", "b", main), ("gap", "a", "b", {"name": ""}),
         ("o", "a", "n", {"name": "Oak Street"}), ("x", "b", "s", {"name": "Elm Street"})],
    )  # fmt: skip
    found = places.Places(edges)
    assert places.same_junction(found, "a", "b") and places.same_junction(found, "b", "a")
