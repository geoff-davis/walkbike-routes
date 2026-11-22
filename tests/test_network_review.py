"""Network rules found wrong in review: each test failed before its fix."""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from walkbike_routes.costs import presets
from walkbike_routes.network import osm_streets as o
from walkbike_routes.network import prepare
from walkbike_routes.network.base import routable_links_to_graph
from walkbike_routes.network.bicycle import classify_lts, infer_traffic_band, parse_number
from walkbike_routes.network.broach import BroachMovements, build_broach_links
from walkbike_routes.network.broach_walk import WalkBroachMovements, build_walk_broach_links
from walkbike_routes.network.furth import classify_furth
from walkbike_routes.network.signals import build_signal_wait_inventory

CRS = 26911


def test_an_untagged_one_way_street_has_half_the_default_lanes() -> None:
    # One lane, as when the lane is tagged: not the two lanes of a two-way street in one direction.
    untagged = classify_furth({"highway": "residential", "oneway": "yes", "speed_mph": 25.0})
    tagged = classify_furth(
        {"highway": "residential", "oneway": "yes", "speed_mph": 25.0, "lanes": 1.0}
    )
    assert untagged.lts == tagged.lts == 2  # it was 3, the two-lane row


def test_a_50_kmh_limit_is_a_30_mph_street() -> None:
    road = {"highway": "residential", "lanes": 2, "traffic_band": "moderate"}
    assert (
        classify_lts(road | {"speed_mph": 31.1}).lts == classify_lts(road | {"speed_mph": 30}).lts
    )
    assert classify_lts(road | {"speed_mph": 31.1}).lts == 2
    assert classify_lts(road | {"speed_mph": 34}).lts == 3  # 34 is the 35 band


@pytest.mark.parametrize("value", ["0", "0.0", 0.0, 0, "-", ""])
def test_zero_is_not_a_speed_or_a_lane_count(value: Any) -> None:
    assert parse_number(value) is None


def test_a_maxspeed_of_zero_is_no_limit() -> None:
    assert o.speed_mph("0") is None and o.speed_mph("0 mph") is None
    assert o.speed_mph("50") == pytest.approx(31.1, abs=0.05)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("no", "no"), ("yes", "yes"), ("-1", "-1"), ("designated", None), ("use_sidepath", None)],
)
def test_only_a_direction_is_read_from_oneway_bicycle(value: str, expected: str | None) -> None:
    tags = {"highway": "residential", "oneway": "yes", "oneway:bicycle": value}
    assert o.oneway_bicycle(tags) == expected


@pytest.mark.parametrize("value", ["reversible", "alternating"])
def test_a_street_that_changes_direction_is_closed_both_ways(value: str) -> None:
    from walkbike_routes.attributes import oneway_allows

    way = o.oneway({"highway": "primary", "oneway": value})
    assert not oneway_allows(way, None, True) and not oneway_allows(way, None, False)


def test_a_ramp_meter_is_not_a_signal() -> None:
    assert o.control({"highway": "traffic_signals", "traffic_signals": "ramp_meter"}) == "none"
    assert o.control({"highway": "traffic_signals"}) == "signal"


def test_link_classes_have_a_traffic_band() -> None:
    assert infer_traffic_band("trunk_link", None, None)[0] == "high"
    assert infer_traffic_band("tertiary_link", None, None)[0] == "moderate"


def _crossing(groups: list[str] | None) -> gpd.GeoDataFrame:
    edges = gpd.GeoDataFrame(
        {"physical_edge_id": ["leg_a", "leg_b"], "source_network_id": ["way_a", "way_b"],
         "u": ["X", "Y"], "v": ["Y", "Z"], "length_m": [10.0, 10.0],
         "highway": ["footway"] * 2, "movement_type": ["across"] * 2,
         "crossing": ["traffic_signals"] * 2, "crossing_control_state": ["signal_osm"] * 2},
        geometry=[LineString([(0, 0), (0, 10)]), LineString([(0, 10), (0, 20)])], crs=CRS,
    )  # fmt: skip
    if groups is not None:
        edges["crossing_group"] = groups
    return edges


def test_a_crossing_drawn_as_two_ways_is_one_wait() -> None:
    nodes = gpd.GeoDataFrame(
        {"node_id": ["X", "Y", "Z"], "highway": [None] * 3},
        geometry=[Point(0, 0), Point(0, 10), Point(0, 20)], crs=CRS,
    )  # fmt: skip
    joined = build_signal_wait_inventory(nodes, _crossing(["g", "g"]))
    assert len(joined.crossing_event_by_wait_edge) == 1
    assert joined.crossing_leg_edges == frozenset({"leg_a", "leg_b"})
    for separate in (_crossing(None), _crossing(["", ""])):
        assert len(build_signal_wait_inventory(nodes, separate).crossing_event_by_wait_edge) == 2


def test_links_without_a_projection_are_refused() -> None:
    links = gpd.GeoDataFrame(
        {"link_id": ["a"], "u": ["x"], "v": ["y"], "highway": ["residential"]},
        geometry=[LineString([(-117.7, 34.1), (-117.69, 34.1)])], crs=4326,
    )  # fmt: skip
    with pytest.raises(ValueError, match="projected"):
        prepare.physical_edges(links)


def test_bikes_stay_off_steps_and_pedestrian_streets_without_access_tags() -> None:
    kinds = ["residential", "footway", "steps", "pedestrian", "cycleway", "motorway"]
    links = gpd.GeoDataFrame(
        {"link_id": kinds, "u": ["x"] * 6, "v": ["y"] * 6, "highway": kinds,
         "bike_facility": ["none"] * 6, "lanes": [None] * 6, "speed_mph": [None] * 6,
         "sidewalk": ["unknown"] * 6},
        geometry=[LineString([(0, i), (100, i)]) for i in range(6)], crs=CRS,
    )  # fmt: skip
    edges = prepare.physical_edges(links)
    assert dict(zip(edges.highway, edges.bike_allowed, strict=True)) == {
        "residential": True, "footway": False, "steps": False, "pedestrian": False,
        "cycleway": True, "motorway": False,
    }  # fmt: skip


def _divided(gap: float) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Elm runs a-c1-c2-d across Main, a primary road drawn as two one-way lines ``gap`` apart."""
    points = {"a": (0, 0), "c1": (200, 0), "c2": (200 + gap, 0), "d": (400 + gap, 0),
              "n1": (200, 200), "s1": (200, -200), "n2": (200 + gap, 200),
              "s2": (200 + gap, -200)}  # fmt: skip
    nodes = gpd.GeoDataFrame(
        {"node_id": list(points), "control": ["none"] * len(points)},
        geometry=[Point(p) for p in points.values()], crs=CRS,
    )  # fmt: skip
    rows = [("e1", "a", "c1", "residential", "Elm", "no"),
            ("e2", "c1", "c2", "residential", "Elm", "no"),
            ("e3", "c2", "d", "residential", "Elm", "no"),
            ("m1", "n1", "c1", "primary", "Main", "yes"), ("m2", "c1", "s1", "primary", "Main", "yes"),
            ("m3", "s2", "c2", "primary", "Main", "yes"), ("m4", "c2", "n2", "primary", "Main", "yes")]  # fmt: skip
    links = gpd.GeoDataFrame(
        {"link_id": [r[0] for r in rows], "u": [r[1] for r in rows], "v": [r[2] for r in rows],
         "highway": [r[3] for r in rows], "name": [r[4] for r in rows],
         "oneway": [r[5] for r in rows], "bike_facility": ["none"] * len(rows),
         "lanes": [None] * len(rows), "speed_mph": [None] * len(rows),
         "sidewalk": ["unknown"] * len(rows)},
        geometry=[LineString([points[r[1]], points[r[2]]]) for r in rows], crs=CRS,
    )  # fmt: skip
    return prepare.physical_edges(links, nodes), prepare.nodes(nodes)


def _cycling(gap: float) -> BroachMovements:
    profile: Any = presets.load("bike", "broach-2011").profile()
    edges, nodes = _divided(gap)
    graph: Any = routable_links_to_graph(build_broach_links(edges, nodes, profile), directed=True)
    return BroachMovements(graph, edges, nodes, profile)


def test_cycling_across_a_divided_road_is_one_crossing() -> None:
    whole = _cycling(200.0)("a", "c1", "c2").crossing_cost_s  # a block apart: two crossings
    assert whole > 0 and _cycling(200.0)("c1", "c2", "d").crossing_cost_s == whole
    moves = _cycling(20.0)
    for path in (("a", "c1", "c2", "d"), ("d", "c2", "c1", "a")):
        halves = [moves(*path[i : i + 3]).crossing_cost_s for i in (0, 1)]
        assert halves == pytest.approx([whole / 2, whole / 2])
    # Turning off one carriageway and across the other is half a crossing, not none.
    assert moves("n1", "c1", "c2").crossing_cost_s == 0
    assert moves("c1", "c2", "d").crossing_cost_s == pytest.approx(whole / 2)


def test_walking_across_a_divided_road_is_one_crossing() -> None:
    profile: Any = presets.load("walk", "broach-2016").profile()
    costs = []
    for gap in (200.0, 20.0):
        edges, nodes = _divided(gap)
        links = build_walk_broach_links(edges, profile, nodes=nodes)
        graph: Any = routable_links_to_graph(links, directed=True)
        moves = WalkBroachMovements(graph, edges, nodes, profile)
        costs.append(
            [moves("a", "c1", "c2").crossing_cost_s, moves("c1", "c2", "d").crossing_cost_s]
        )
        assert moves.crossed("c1", "c2", "d") == "arterial"  # still a crossing to the screen
    (whole, again), halves = costs
    assert whole > 0 and again == whole
    assert halves == pytest.approx([whole / 2, whole / 2])


def test_a_reversible_street_with_a_bike_lane_is_read() -> None:
    tags = {"highway": "primary", "oneway": "reversible", "cycleway": "lane"}
    assert o.way_attributes(tags)["oneway"] == "reversible"


@pytest.mark.parametrize("value", ["-25 mph", "-50"])
def test_a_negative_limit_is_no_limit(value: str) -> None:
    assert o.speed_mph(value) is None and parse_number(value) is None and parse_number(-5) is None


def test_a_car_link_of_no_length_is_kept() -> None:
    from walkbike_routes.network.motor import build_motor_arcs

    edges = gpd.GeoDataFrame(
        {"physical_edge_id": ["stub", "road"], "u": ["a", "b"], "v": ["b", "c"],
         "length_m": [0.0, 100.0], "travel_time_s": [0.0, 10.0], "oneway": ["no", "no"]},
        geometry=[LineString([(0, 0), (0, 0)]), LineString([(0, 0), (100, 0)])], crs=CRS,
    )  # fmt: skip
    arcs, _, _ = build_motor_arcs(edges)
    assert set(arcs.edge_id) == {"stub", "road"}
