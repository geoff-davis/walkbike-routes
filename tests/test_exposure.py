"""Where routes meet traffic today: risk points, crossings and tiers, on made-up streets."""

from __future__ import annotations

import json

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from walkbike_routes.analysis import exposure, exposure_crossings, exposure_lists, exposure_rules

# A four-way junction: Main Street (busy, east-west) meets Oak Street (quiet,
# north-south) at node c.  A mapped crosswalk crosses Main Street east of it.
NODES = {"c": (0, 0), "w": (-100, 0), "e": (100, 0), "n": (0, 100), "s": (0, -100),
         "f1": (50, -12), "f2": (50, 12)}  # fmt: skip


def _edge(pid: str, u: str, v: str, **attrs: object) -> dict[str, object]:
    line = LineString([NODES[u], NODES[v]])
    row: dict[str, object] = {
        "physical_edge_id": pid, "u": u, "v": v, "name": "", "highway": "residential",
        "lanes": 2.0, "speed_mph": 25.0, "traffic_aadt": float("nan"), "car_allowed": True,
        "lts_best": 1, "pedestrian_status": "supported", "movement_type": "along",
        "crossed_road": "", "crossing_marking": "", "crossing": "", "length_m": line.length,
        "geometry": line,
    }  # fmt: skip
    row.update(attrs)
    return row


def _edges(**changes: dict[str, object]) -> gpd.GeoDataFrame:
    main = {"name": "Main Street", "highway": "secondary", "lts_best": 3}
    oak = {"name": "Oak Street"}
    rows = [
        _edge("mw", "w", "c", **main), _edge("me", "c", "e", **main),
        _edge("on", "c", "n", **oak), _edge("os", "s", "c", **oak),
        _edge("x1", "f1", "f2", name="", highway="footway", car_allowed=False,
              crossed_road="secondary", crossing_marking="unmarked"),
    ]  # fmt: skip
    for row in rows:
        row.update(changes.get(str(row["physical_edge_id"]), {}))
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:26910")


def _nodes(control: str = "none") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "node_id": list(NODES),
            "control": [control if n == "c" else "none" for n in NODES],
            "crossing_marking": [""] * len(NODES),
        },
        geometry=[Point(xy) for xy in NODES.values()],
        crs="EPSG:26910",
    )


def _shares(*routes: tuple[str, float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"physical_edge_ids": [r for r, _ in routes], "assigned_weight": [w for _, w in routes]}
    )


def test_cycling_points_follow_traffic_stress() -> None:
    risk = exposure.street_risk(_edges(), "bike")
    assert (
        risk.risk_points["mw"] == 3 and "high traffic stress for cycling" in risk.risk_factors["mw"]
    )
    assert risk.risk_points["on"] == 0
    assert risk.risk_points["x1"] == 0  # a crossing is scored as a crossing, not a street


def test_walking_points_come_from_missing_sidewalks_and_traffic() -> None:
    edges = _edges(
        mw={"pedestrian_status": "unknown"},
        on={"pedestrian_status": "problematic"},
        me={"pedestrian_status": "unknown", "speed_mph": 40.0},
    )
    risk = exposure.street_risk(edges, "walk")
    assert risk.risk_points["mw"] == 4  # band 2 (a secondary road), no known sidewalk
    assert risk.risk_points["on"] == 1  # band 0
    assert risk.risk_points["me"] == 5 and "35 mph or faster" in risk.risk_factors["me"]
    assert risk.risk_points["os"] == 0  # a sidewalk


def test_drop_off_traffic_adds_a_point_only_on_the_busiest_car_streets() -> None:
    edges = _edges()
    cars = pd.Series({"mw": 100.0, "me": 1.0, "on": 1.0, "os": 1.0})
    risk = exposure.street_risk(edges, "bike", cars)
    assert risk.risk_points["mw"] == 4 and "drop-off" in risk.risk_factors["mw"]
    assert risk.risk_points["me"] == 3
    assert risk.risk_points["on"] == 0  # no risk of its own, so no drop-off point either


@pytest.mark.parametrize(
    ("route", "crosses"),
    [
        ("os;on", True),  # straight across Main Street
        ("mw;me", False),  # along it
        ("os;me", False),  # right turn onto it
        ("os;mw", True),  # left turn onto it
        ("mw;on", True),  # left turn off it
        ("mw;os", False),  # right turn off it
    ],
)
def test_crossings_at_a_junction(route: str, crosses: bool) -> None:
    events = exposure.crossing_events(_shares((route, 1.0)), _edges(), _nodes())
    assert events.place.tolist() == (["J:c"] if crosses else [])


def test_a_mapped_crosswalk_is_a_crossing_of_its_road() -> None:
    events = exposure.crossing_events(_shares(("x1", 2.0)), _edges(), _nodes())
    assert events.kind.tolist() == ["way"] and events.weight.tolist() == [2.0]
    crossings = exposure.exposed_crossings(events, _edges(), _nodes(), "walk", counted=10)
    row = crossings.iloc[0]
    assert row.risk_points == 3  # band 2, and unmarked
    assert "no marked crosswalk" in row.risk_factors and row.people == 2.0


def test_a_signal_cuts_a_crossings_points_to_a_quarter() -> None:
    shares = _shares(("os;on", 6.0))
    plain = exposure.exposed_crossings(
        exposure.crossing_events(shares, _edges(), _nodes()), _edges(), _nodes(), "bike", 6
    )
    signal = exposure.exposed_crossings(
        exposure.crossing_events(shares, _edges(), _nodes("signal")),
        _edges(), _nodes("signal"), "bike", 6,
    )  # fmt: skip
    assert plain.risk_points.iloc[0] == 2  # band 2
    assert signal.risk_points.iloc[0] == 0.5 and "signal" in signal.risk_factors.iloc[0]
    walked = exposure.exposed_crossings(
        exposure.crossing_events(_shares(("x1", 1.0)), _edges(), _nodes()),
        _edges(x1={"crossing": "traffic_signals"}), _nodes(), "walk", 1,
    )  # fmt: skip
    assert walked.risk_points.iloc[0] == 0.75


def test_people_crossing_sum_the_routes_counting_each_once() -> None:
    shares = _shares(("os;on", 2.0), ("mw;on", 3.0), ("mw;me", 7.0), ("os;on;on", 1.0))
    events = exposure.crossing_events(shares, _edges(), _nodes())
    crossings = exposure.exposed_crossings(events, _edges(), _nodes(), "bike", 13)
    assert crossings.people.tolist() == [6.0]


def test_streets_join_same_named_neighbours_and_skip_places_without_risk() -> None:
    edges = _edges()
    flow = pd.Series({"mw": 10.0, "me": 4.0, "on": 20.0})
    risk = exposure.street_risk(edges, "bike")
    streets = exposure.exposed_streets(edges, flow, risk, counted=30)
    assert streets.label.tolist() == ["Main Street"]  # its two ends meet no other street
    row = streets.iloc[0]
    assert row.people == 10.0 and row.person_km == pytest.approx(1.4)
    assert row.exposure == pytest.approx(30.0) and row.tier == "fix first"  # people × points


def test_tiers() -> None:
    assert exposure_rules.tier_of(3, 5, 1000) == "fix first"
    assert exposure_rules.tier_of(3, 4.9, 1000) == "watch"
    assert exposure_rules.tier_of(3, 1, 10) == "fix first"  # 5% of 10 counted is 0.5
    assert exposure_rules.tier_of(2.9, 100, 100) == "watch"


def test_no_routes_give_empty_lists_with_their_columns() -> None:
    streets, crossings = exposure.exposure(
        _edges(), _nodes(), pd.Series(dtype=float), _shares(), "walk"
    )
    assert streets.empty and list(streets.columns) == exposure.STREET_COLUMNS
    assert crossings.empty and list(crossings.columns) == exposure.CROSSING_COLUMNS


def test_a_crosswalk_through_the_road_node_is_counted_once() -> None:
    edges = _edges()
    pieces = [
        _edge("xs", "s", "c", highway="footway", car_allowed=False, crossed_road="secondary",
              crossing_group="X"),
        _edge("xn", "c", "n", highway="footway", car_allowed=False, crossed_road="secondary",
              crossing_group="X"),
    ]  # fmt: skip
    edges = gpd.GeoDataFrame(
        pd.concat([edges, gpd.GeoDataFrame(pieces, crs=edges.crs)], ignore_index=True),
        geometry="geometry", crs=edges.crs,
    )  # fmt: skip
    events = exposure.crossing_events(_shares(("xs;xn", 5.0)), edges, _nodes())
    assert events.place.tolist() == ["X"]


def test_a_missing_speed_on_one_leg_does_not_hide_the_other() -> None:
    edges = _edges(mw={"speed_mph": float("nan"), "lanes": float("nan")},
                   me={"speed_mph": 40.0, "lanes": 4.0})  # fmt: skip
    events = exposure.crossing_events(_shares(("os;on", 6.0)), edges, _nodes())
    crossings = exposure.exposed_crossings(events, edges, _nodes(), "bike", 6)
    assert crossings.risk_points.iloc[0] == 5  # 4 lanes raise band 2 to 3; 35+ mph; 4+ lanes


def test_drop_off_traffic_alone_gives_no_points() -> None:
    cars = pd.Series({"on": 100.0, "mw": 1.0, "me": 1.0, "os": 1.0})
    assert exposure.street_risk(_edges(), "bike", cars).risk_points["on"] == 0


def test_out_and_back_links_still_find_the_node_passed() -> None:
    # os reaches c from the south; a pair of links c<->n is ridden out and back,
    # then the route continues east along Main: no crossing.  Ridden north and
    # left onto Main westbound from c, it crosses.
    edges = _edges()
    extra = [_edge("p1", "c", "n", name="Oak Street"), _edge("p2", "n", "c", name="Oak Street")]
    edges = gpd.GeoDataFrame(
        pd.concat([edges, gpd.GeoDataFrame(extra, crs=edges.crs)], ignore_index=True),
        geometry="geometry", crs=edges.crs,
    )  # fmt: skip
    assert exposure_crossings._turns(
        exposure_crossings._Network(edges, _nodes()), ["p1", "p2", "mw"]
    )[-1] == (
        "p2",
        "mw",
        "c",
    )


def _with(edges: gpd.GeoDataFrame, rows: list[dict[str, object]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        pd.concat([edges, gpd.GeoDataFrame(rows, crs=edges.crs)], ignore_index=True),
        geometry="geometry", crs=edges.crs,
    )  # fmt: skip


def test_a_second_leg_leaving_the_same_way_does_not_hide_the_crossing() -> None:
    edges = _with(_edges(), [_edge("mw2", "w", "c", name="Main Street", highway="secondary")])
    edges = gpd.GeoDataFrame(
        pd.concat([edges.iloc[[5]], edges.iloc[:5]], ignore_index=True),
        geometry="geometry",
        crs=edges.crs,
    )  # fmt: skip  (the duplicate westward leg comes first)
    events = exposure.crossing_events(_shares(("os;on", 1.0)), edges, _nodes())
    assert events.place.tolist() == ["J:c"]


def test_a_longer_out_and_back_still_reaches_the_turn() -> None:
    extra = [_edge(p, "c", "n", name="Oak Street") for p in ("p1", "p2", "p3")]
    extra.append(_edge("q", "n", "w", name="Pine Street"))
    turns = exposure_crossings._turns(exposure_crossings._Network(_with(_edges(), extra), _nodes()),
                            ["p1", "p2", "p3", "q"])  # fmt: skip
    assert [t[2] for t in turns] == ["n", "c", "n"]


def test_a_quiet_loop_arrives_by_its_last_end() -> None:
    loop = _edge("loop", "c", "c", name="Elm Court")
    loop["geometry"] = LineString([(0, 0), (0, -30), (20, 0), (0, 30), (0, 0)])
    edges = _with(_edges(), [loop])
    events = exposure.crossing_events(_shares(("loop;os", 1.0)), edges, _nodes())
    assert events.place.tolist() == ["J:c"]  # in from the north, out to the south


def test_a_crosswalk_ending_on_another_busy_road_still_counts_that_road() -> None:
    edges = _edges(on={"name": "Oak Street", "highway": "primary"},
                   os={"name": "Oak Street", "highway": "primary"})  # fmt: skip
    walk = _edge("xw", "w", "c", highway="footway", car_allowed=False, crossed_road="secondary",
                 crossing_group="X")  # fmt: skip
    walk["geometry"] = LineString([(-100, 5), (0, 0)])
    events = exposure.crossing_events(_shares(("xw;on", 1.0)), _with(edges, [walk]), _nodes())
    assert sorted(events.place) == ["J:c", "X"]  # Oak is crossed by the left turn


def test_one_signal_covers_both_nodes_of_a_divided_junction() -> None:
    nodes = _nodes()
    nodes = pd.concat([nodes, gpd.GeoDataFrame(
        {"node_id": ["c2"], "control": ["signal"], "crossing_marking": [""]},
        geometry=[Point(0, 20)], crs=nodes.crs)], ignore_index=True)  # fmt: skip
    events = pd.DataFrame({"route": [0, 0], "place": ["J:c", "J:c2"], "kind": "junction",
                           "detail": "Main Street", "weight": 6.0})  # fmt: skip
    # The second carriageway of Main Street meets Oak Street at c2, 20 m north of c.
    m2 = _edge("m2", "w", "c", name="Main Street", highway="secondary")
    m2.update(u="c2", v="w2")
    m2["geometry"] = LineString([(0, 20), (-100, 20)])
    o2 = _edge("o2", "c", "n", name="Oak Street")
    o2.update(u="c2")
    o2["geometry"] = LineString([(0, 20), (0, 100)])
    edges = _with(_edges(), [m2, o2])
    crossings = exposure.exposed_crossings(events, edges, gpd.GeoDataFrame(nodes), "bike", 6)
    assert len(crossings) == 1 and crossings.risk_points.iloc[0] == 0.5


def test_a_crosswalk_ending_on_another_road_of_the_same_class_still_counts_it() -> None:
    edges = _edges(on={"name": "Oak Street", "highway": "secondary"},
                   os={"name": "Oak Street", "highway": "secondary"})  # fmt: skip
    walk = _edge("xw", "w", "c", highway="footway", car_allowed=False, crossed_road="secondary",
                 crossing_group="X")  # fmt: skip
    walk["geometry"] = LineString([(-100, 5), (0, 0)])
    events = exposure.crossing_events(_shares(("xw;on", 1.0)), _with(edges, [walk]), _nodes())
    assert sorted(events.place) == ["J:c", "X"]


def test_a_signal_at_a_node_no_route_uses_still_covers_its_junction() -> None:
    nodes = _nodes()
    nodes = pd.concat([nodes, gpd.GeoDataFrame(
        {"node_id": ["c2"], "control": ["signal"], "crossing_marking": [""]},
        geometry=[Point(0, 20)], crs=nodes.crs)], ignore_index=True)  # fmt: skip
    m2 = _edge("m2", "w", "c", name="Main Street", highway="secondary")
    m2.update(u="c2", v="w2", geometry=LineString([(0, 20), (-100, 20)]))
    o2 = _edge("o2", "c", "n", name="Oak Street")
    o2.update(u="c2", geometry=LineString([(0, 20), (0, 100)]))
    edges = _with(_edges(), [m2, o2])
    events = pd.DataFrame({"route": [0], "place": ["J:c"], "kind": "junction",
                           "detail": "Main Street", "weight": 6.0})  # fmt: skip
    crossings = exposure.exposed_crossings(events, edges, gpd.GeoDataFrame(nodes), "bike", 6)
    assert crossings.risk_points.iloc[0] == 0.5


def test_a_left_turn_onto_a_busy_road_that_ends_here_is_a_crossing() -> None:
    edges = _edges().query("physical_edge_id != 'mw'")
    events = exposure.crossing_events(_shares(("on;me", 1.0)), edges, _nodes())
    assert events.place.tolist() == ["J:c"]  # north to east is a left turn
    assert exposure.crossing_events(_shares(("os;me", 1.0)), edges, _nodes()).empty


def test_a_multipart_line_with_a_reversed_piece_gives_the_right_bearing() -> None:
    from shapely.geometry import MultiLineString

    edges = _edges(os={"geometry": MultiLineString([[(0, -100), (0, -50)], [(0, 0), (0, -50)]])})
    events = exposure.crossing_events(_shares(("os;on", 1.0)), edges, _nodes())
    assert events.place.tolist() == ["J:c"]


def test_a_divided_road_adds_up_its_carriageways_lanes() -> None:
    nodes = _nodes()
    nodes = pd.concat([nodes, gpd.GeoDataFrame(
        {"node_id": ["c2", "w2", "e2"], "control": ["none"] * 3, "crossing_marking": [""] * 3},
        geometry=[Point(0, 20), Point(-100, 20), Point(100, 20)], crs=nodes.crs)],
        ignore_index=True)  # fmt: skip
    main = {"name": "Main Street", "highway": "secondary", "oneway": "yes", "lanes": 2.0}
    edges = _edges(mw=main, me=main)
    rows = []
    for pid, u, v, line in [("m2e", "e2", "c2", [(100, 20), (0, 20)]),
                            ("m2w", "c2", "w2", [(0, 20), (-100, 20)])]:  # fmt: skip
        row = _edge(pid, "w", "c", **main)
        row.update(u=u, v=v, geometry=LineString(line))
        rows.append(row)
    link = _edge("ol", "c", "n", name="Oak Street")
    link.update(v="c2", geometry=LineString([(0, 0), (0, 20)]))
    edges = _with(edges, [*rows, link])
    events = pd.DataFrame({"route": [0, 0], "place": ["J:c", "J:c2"], "kind": "junction",
                           "detail": "Main Street", "weight": 6.0})  # fmt: skip
    crossings = exposure.exposed_crossings(events, edges, gpd.GeoDataFrame(nodes), "bike", 6)
    assert len(crossings) == 1 and crossings.risk_points.iloc[0] == 3  # band 2, 4 lanes


def test_a_signal_mapped_on_the_approaches_controls_the_junction() -> None:
    events = exposure.crossing_events(_shares(("os;on", 6.0)), _edges(), _nodes())
    crossings = exposure.exposed_crossings(
        events, _edges(), _nodes(), "bike", 6, signal_nodes=frozenset({"c"})
    )
    assert crossings.risk_points.iloc[0] == 0.5


def test_a_junction_is_drawn_at_its_node() -> None:
    from shapely.geometry import MultiLineString

    edges = _edges(os={"geometry": MultiLineString([[(0, -100), (0, -50)], [(0, 0), (0, -50)]])})
    edges = gpd.GeoDataFrame(
        pd.concat([edges[edges.physical_edge_id.eq("os")], edges[~edges.physical_edge_id.eq("os")]]),
        geometry="geometry", crs=edges.crs,
    )  # fmt: skip
    events = exposure.crossing_events(_shares(("os;on", 6.0)), edges, _nodes())
    crossings = exposure.exposed_crossings(events, edges, _nodes(), "bike", 6)
    assert crossings.geometry.iloc[0].equals(Point(0, 0))


def test_where_two_divided_roads_meet_each_carriageway_counts_once() -> None:
    # Main's two one-lane carriageways (eastbound at y=0, westbound at y=10) meet
    # Oak's two (northbound at x=0, southbound at x=10): four nodes, one junction.
    pts = {"a": (0, 0), "b": (10, 0), "d": (0, 10), "f": (10, 10),
           "W0": (-100, 0), "E0": (100, 0), "W1": (-100, 10), "E1": (100, 10),
           "S0": (0, -100), "N0": (0, 100), "S1": (10, -100), "N1": (10, 100)}  # fmt: skip
    main = {"name": "Main Street", "highway": "secondary", "oneway": "yes", "lanes": 1.0,
            "traffic_aadt": 10000.0}  # fmt: skip
    oak = {"name": "Oak Street", "oneway": "yes", "lanes": 1.0}
    links = [("W0", "a", main), ("a", "b", main), ("b", "E0", main),
             ("E1", "f", main), ("f", "d", main), ("d", "W1", main),
             ("S0", "a", oak), ("a", "d", oak), ("d", "N0", oak),
             ("N1", "f", oak), ("f", "b", oak), ("b", "S1", oak)]  # fmt: skip
    rows = []
    for u, v, attrs in links:
        row = _edge(f"{u}-{v}", "c", "n", **attrs)
        row.update(u=u, v=v, geometry=LineString([pts[u], pts[v]]))
        rows.append(row)
    edges = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:26910")
    nodes = gpd.GeoDataFrame(
        {"node_id": list(pts), "control": "none", "crossing_marking": ""},
        geometry=[Point(xy) for xy in pts.values()], crs="EPSG:26910",
    )  # fmt: skip
    events = exposure.crossing_events(
        _shares(("S0-a;a-d;d-N0", 3.0), ("N1-f;f-b;b-S1", 3.0)), edges, nodes
    )
    crossings = exposure.exposed_crossings(events, edges, nodes, "bike", 6)
    assert len(crossings) == 1 and crossings.risk_points.iloc[0] == 2  # band 2, 2 lanes


def test_a_curved_approach_is_still_the_same_carriageway() -> None:
    nodes = _nodes()
    nodes = pd.concat([nodes, gpd.GeoDataFrame(
        {"node_id": ["c2", "w2", "e2", "sw"], "control": "none", "crossing_marking": ""},
        geometry=[Point(0, 10), Point(-100, 10), Point(100, 10), Point(-100, -100)],
        crs=nodes.crs)], ignore_index=True)  # fmt: skip
    main = {"name": "Main Street", "highway": "secondary", "oneway": "yes",
            "traffic_aadt": 10000.0}  # fmt: skip
    links = [("sw", "c", [(-100, -100), (-100, -50), (-50, 0), (0, 0)], 1.0),
             ("c", "e", [(0, 0), (100, 0)], 1.0),
             ("e2", "c2", [(100, 10), (0, 10)], 2.0),
             ("c2", "w2", [(0, 10), (-100, 10)], 2.0)]  # fmt: skip
    rows = [_edge("os", "s", "c", name="Oak Street")]
    for u, v, line, lanes in links:
        row = _edge(f"{u}-{v}", "c", "n", **main, lanes=lanes)
        row.update(u=u, v=v, geometry=LineString(line))
        rows.append(row)
    link = _edge("ol", "c", "n", name="Oak Street")
    link.update(v="c2", geometry=LineString([(0, 0), (0, 10)]))
    rows.append(link)
    edges = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:26910")
    events = pd.DataFrame({"route": [0, 0], "place": ["J:c", "J:c2"], "kind": "junction",
                           "detail": "Main Street", "weight": 6.0})  # fmt: skip
    crossings = exposure.exposed_crossings(events, edges, gpd.GeoDataFrame(nodes), "bike", 6)
    assert crossings.risk_points.iloc[0] == 2  # band 2; 1 + 2 = 3 lanes


def test_a_one_way_street_bending_at_a_junction_is_one_carriageway() -> None:
    main = {"name": "Main Street", "highway": "secondary", "oneway": "yes", "lanes": 2.0,
            "traffic_aadt": 10000.0}  # fmt: skip
    edges = _edges(mw=main, me=main)
    edges.loc[edges.physical_edge_id.eq("me"), "v"] = "n"
    edges.loc[edges.physical_edge_id.eq("me"), "geometry"] = gpd.GeoSeries(
        [LineString([(0, 0), (0, 100)])], index=edges.index[edges.physical_edge_id.eq("me")]
    )
    events = pd.DataFrame({"route": [0], "place": ["J:c"], "kind": "junction",
                           "detail": "Main Street", "weight": 6.0})  # fmt: skip
    crossings = exposure.exposed_crossings(events, edges, _nodes(), "bike", 6)
    assert crossings.risk_points.iloc[0] == 2  # band 2, 2 lanes


def test_each_place_says_why_it_is_in_its_tier() -> None:
    assert exposure_rules.tier_reason(4, 86.1, 1000) == (
        "Fix first: 4 risk points (fix first needs 3), and about 86 people use it."
    )
    assert exposure_rules.tier_reason(2, 373.6, 1000) == (
        "Watch: about 374 people use it, but it has 2 risk points; fix first needs 3."
    )
    assert exposure_rules.tier_reason(4, 2.5, 1000) == (
        "Watch: 4 risk points, but fewer than 5 people use it (about 2)."
    )
    assert "about 4.99" in exposure_rules.tier_reason(4, 4.99, 1000)
    assert "about 0.02 people" in exposure_rules.tier_reason(3, 0.02, 0.2)
    streets = exposure.exposed_streets(
        _edges(), pd.Series({"mw": 10.0}), exposure.street_risk(_edges(), "bike"), counted=30
    )
    assert streets.tier_reason.iloc[0].startswith("Fix first: 3 risk points")


def test_traffic_from_the_road_type_says_it_is_estimated() -> None:
    walk = _edges(mw={"pedestrian_status": "unknown"},
                  me={"pedestrian_status": "unknown", "traffic_aadt": 12000.0})  # fmt: skip
    risk = exposure.street_risk(walk, "walk")
    assert "estimated from the road type" in risk.risk_factors["mw"]
    assert "estimated" not in risk.risk_factors["me"]
    assert exposure_lists.traffic_words(4, True) == "30,000 or more vehicles a day"


def test_reasons_keep_enough_digits_to_stay_true() -> None:
    assert "it has 2.999 risk points; fix first needs 3." in exposure_rules.tier_reason(
        2.999, 10, 1000
    )
    reason = exposure_rules.tier_reason(4, 1.233, 24.68)  # the bar is 1.234
    assert (
        "fewer than 1.234 people use it (about 1.233; the bar is 5% of the 24.7 counted)" in reason
    )


def test_a_negative_count_is_not_a_count() -> None:
    walk = _edges(mw={"pedestrian_status": "unknown", "traffic_aadt": -1.0})
    assert "estimated from the road type" in exposure.street_risk(walk, "walk").risk_factors["mw"]


def test_one_risk_point_is_singular() -> None:
    assert "it has 1 risk point;" in exposure_rules.tier_reason(1, 50, 1000)


def _run(flow: dict[str, float], *routes: tuple[str, float]) -> exposure.RunFlows:
    return exposure.RunFlows(pd.Series(flow), _shares(*routes))


def test_destinations_together_add_up_and_say_how_many_come_from_each() -> None:
    runs = {
        "almond": _run({"mw": 4.0, "me": 4.0}, ("mw;on", 3.0), ("mw;me", 1.0)),
        "egan": _run({"mw": 6.0, "me": 2.0}, ("os;on", 2.0), ("mw;on", 1.0)),
    }
    streets, crossings = exposure.combined_exposure(_edges(), _nodes(), runs, "bike")
    main = streets.iloc[0]
    assert main.people == 10.0  # the busiest link, mw, carries 4 + 6
    assert json.loads(main.people_by_destination) == {"egan": 6.0, "almond": 4.0}
    assert main.tier == "fix first"
    oak = crossings.iloc[0]  # Main Street crossed by the routes turning onto Oak
    assert oak.people == 6.0 and json.loads(oak.people_by_destination) == {
        "almond": 3.0,
        "egan": 3.0,
    }
    assert list(streets.columns) == exposure.STREET_COLUMNS
    assert list(crossings.columns) == exposure.CROSSING_COLUMNS


def test_a_place_is_judged_on_everyone_together() -> None:
    # 3 people at an LTS 3 street: under the bar of 5 for either school's 100 alone,
    # but together the street carries 6.
    runs = {
        "a": _run({"mw": 3.0}, ("mw", 100.0)),
        "b": _run({"mw": 3.0}, ("mw", 100.0)),
    }
    alone = exposure.exposure(_edges(), _nodes(), pd.Series({"mw": 3.0}),
                              _shares(("mw", 100.0)), "bike")[0]  # fmt: skip
    together = exposure.combined_exposure(_edges(), _nodes(), runs, "bike")[0]
    assert alone.tier.tolist() == ["watch"] and together.tier.tolist() == ["fix first"]
    assert alone.people_by_destination.eq("").all()


def test_drop_off_traffic_together_is_every_destinations_car_trips() -> None:
    quiet = {"me": 2.0, "on": 2.0, "os": 2.0}  # the other streets' car trips
    bikes = (pd.Series({"mw": 5.0}), _shares(("mw", 5.0)))
    a = exposure.RunFlows(*bikes, pd.Series({"mw": 1.0, **quiet}))
    b = exposure.RunFlows(*bikes, pd.Series({"mw": 9.0, **quiet}))
    alone = exposure.exposure(_edges(), _nodes(), *bikes, "bike", a.car_flow)[0]
    together = exposure.combined_exposure(_edges(), _nodes(), {"a": a, "b": b}, "bike")[0]
    assert "drop-off" not in alone.risk_factors.iloc[0]
    assert "drop-off" in together.risk_factors.iloc[0]


def test_together_edge_ids_as_numbers_or_text_add_up() -> None:
    edges = _edges(mw={"physical_edge_id": "1"})
    runs = {
        "a": exposure.RunFlows(pd.Series({1: 5.0}), _shares(("1", 5.0))),
        "b": exposure.RunFlows(pd.Series({"1": 7.0}), _shares(("1", 7.0))),
    }
    streets = exposure.combined_exposure(edges, _nodes(), runs, "bike")[0]
    assert streets.people.tolist() == [12.0]
    assert json.loads(streets.people_by_destination.iloc[0]) == {"b": 7.0, "a": 5.0}


def test_together_with_no_runs_gives_empty_lists() -> None:
    streets, crossings = exposure.combined_exposure(_edges(), _nodes(), {}, "walk")
    assert streets.empty and list(streets.columns) == exposure.STREET_COLUMNS
    assert crossings.empty and list(crossings.columns) == exposure.CROSSING_COLUMNS


def test_fix_first_places_are_numbered_first_without_gaps() -> None:
    """A watch place outranks no fix-first place, whatever its exposure."""
    table = pd.DataFrame(
        {
            "place_id": ["a", "b", "c", "d"],
            "people": [100.0, 6.0, 50.0, 8.0],
            "risk_points": [2.0, 3.0, 1.0, 4.0],
            "geometry": [Point(0, 0)] * 4,
        }
    )
    table["exposure"] = table.people * table.risk_points
    out = exposure_lists.ranked(
        table, ["rank", "place_id", "tier", "exposure", "geometry"], None, 200.0,
        exposure_rules.FixFirst(),
    )  # fmt: skip
    assert out.place_id.tolist() == ["d", "b", "a", "c"]
    assert out.tier.tolist() == ["fix first", "fix first", "watch", "watch"]
    assert out["rank"].tolist() == [1, 2, 3, 4]
