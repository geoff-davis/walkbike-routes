# mypy: disable-error-code="arg-type,var-annotated,index,call-overload,assignment,misc"
# The pandas and shapely stubs reject these tests' table idioms.
"""Tests for generic mode links, costs and controls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from walkbike_routes.adapters import canonical_physical_edges
from walkbike_routes.models import Mode
from walkbike_routes.network import (
    BicycleProfile,
    MotorPenaltyProfile,
    PedestrianProfile,
    build_bicycle_links,
    build_motor_arcs,
    build_pedestrian_links,
    build_signal_wait_inventory,
    classify_lts,
    motor_transition_cost,
    routable_links_to_graph,
)
from walkbike_routes.network.pedestrian import pedestrian_status
from walkbike_routes.routing import CostComponents, measure_path

FIXTURE = Path(__file__).parent / "fixtures" / "school_network" / "mini_district.json"
CRS = "EPSG:26910"


def _load() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


def _points(records: list[dict[str, Any]]) -> gpd.GeoDataFrame:
    properties = [
        {key: value for key, value in row.items() if key != "geometry"} for row in records
    ]
    return gpd.GeoDataFrame(
        properties,
        geometry=[Point(row["geometry"]) for row in records],
        crs=CRS,
    )


def _lines(records: list[dict[str, Any]]) -> gpd.GeoDataFrame:
    properties = [
        {key: value for key, value in row.items() if key != "geometry"} for row in records
    ]
    return gpd.GeoDataFrame(
        properties,
        geometry=[LineString(row["geometry"]) for row in records],
        crs=CRS,
    )


def _classified_edges() -> gpd.GeoDataFrame:
    edges = _lines(_load()["edges"])
    edges["lts_best"] = [classify_lts(row).lts for row in edges.to_dict("records")]
    edges["pedestrian_status"] = [pedestrian_status(row)[0] for row in edges.to_dict("records")]
    return edges


def test_a_bike_lane_on_a_calm_street_is_lts_2() -> None:
    example = {
        "highway": "tertiary",
        "bike_allowed": True,
        "speed_mph": 30,
        "lanes": 2,
        "traffic_band": "moderate",
        "bike_facility": "bike lane",
    }
    assert classify_lts(example).lts == 2


def test_bicycle_links_apply_lts_threshold_direction_and_stress_cost() -> None:
    edges = _classified_edges()
    strict = build_bicycle_links(
        edges,
        BicycleProfile("strict", maximum_lts=1),
        evidence_source="fixture",
        evidence_confidence="high",
    )
    assert "maple_w_m" not in set(strict.physical_edge_id)
    assert "north_w_m" in set(strict.physical_edge_id)

    weighted = build_bicycle_links(
        edges,
        BicycleProfile("weighted"),
        evidence_source="fixture",
        evidence_confidence="high",
    )
    graph = routable_links_to_graph(weighted, directed=True)
    assert graph.has_edge("W", "M") and graph.has_edge("M", "W")
    maple = weighted[weighted.physical_edge_id.eq("maple_w_m")]
    quiet = weighted[weighted.physical_edge_id.eq("north_w_m")]
    assert maple.stress_cost_s.gt(0).all()
    assert quiet.stress_cost_s.eq(0).all()


def test_bicycle_crossing_cost_is_separate_and_evidence_sensitive() -> None:
    edges = _classified_edges().iloc[[0]].copy()
    edges["movement_type"] = "across"
    edges["crossing_control_state"] = "unknown"
    profile = BicycleProfile(
        "crossing_aware",
        unknown_crossing_cost_s=12.0,
        uncontrolled_crossing_cost_s=30.0,
    )
    unknown = build_bicycle_links(edges, profile)
    assert unknown.crossing_cost_s.eq(12.0).all()
    edges["crossing_control_state"] = "uncontrolled_osm"
    uncontrolled = build_bicycle_links(edges, profile)
    assert uncontrolled.crossing_cost_s.eq(30.0).all()
    edges["crossing_control_state"] = "signal_local"
    controlled = build_bicycle_links(edges, profile)
    assert controlled.crossing_cost_s.eq(0.0).all()


def test_pedestrian_links_keep_unknown_and_poor_evidence_at_a_cost() -> None:
    edges = _classified_edges()
    unknown_index = edges.index[edges.edge_id.eq("north_w_m")][0]
    edges.loc[unknown_index, "pedestrian_status"] = "unknown"
    poor_index = edges.index[edges.edge_id.eq("maple_w_m")][0]
    edges.loc[poor_index, "pedestrian_status"] = "problematic"
    links = build_pedestrian_links(
        edges,
        PedestrianProfile("default"),
        evidence_source="fixture",
        evidence_confidence="high",
    )
    walkable = set(edges.edge_id[edges.ped_allowed.fillna(True).astype(bool)].astype(str))
    assert set(links.physical_edge_id) == walkable  # no evidence leaves a link out
    for edge_id in ("north_w_m", "maple_w_m"):
        assert links[links.physical_edge_id.eq(edge_id)].facility_cost_s.gt(0).all()


def test_signal_waits_charge_once_per_crossing_or_divided_intersection() -> None:
    node_records = [
        ("A", -30, 0, None),
        ("S1", 0, 0, "traffic_signals"),
        ("M", 10, 0, None),
        ("S2", 20, 0, "traffic_signals"),
        ("B", 50, 0, None),
        ("C0", 100, -30, None),
        ("C1", 100, -5, "traffic_signals"),
        ("C2", 100, 5, None),
        ("C3", 100, 30, None),
    ]
    nodes = gpd.GeoDataFrame(
        {
            "node_id": [value[0] for value in node_records],
            "highway": [value[3] for value in node_records],
            "osm_tags": ["{}"] * len(node_records),
        },
        geometry=[Point(value[1], value[2]) for value in node_records],
        crs=CRS,
    )
    edge_records = [
        ("road_a_s1", "road", "A", "S1", "along"),
        ("road_s1_m", "road", "S1", "M", "along"),
        ("road_m_s2", "road", "M", "S2", "along"),
        ("road_s2_b", "road", "S2", "B", "along"),
        ("cross_0", "crossing_way", "C0", "C1", "across"),
        ("cross_1", "crossing_way", "C1", "C2", "across"),
        ("cross_2", "crossing_way", "C2", "C3", "across"),
    ]
    point_by_id = nodes.set_index("node_id").geometry
    edges = gpd.GeoDataFrame(
        {
            "physical_edge_id": [value[0] for value in edge_records],
            "source_network_id": [value[1] for value in edge_records],
            "u": [value[2] for value in edge_records],
            "v": [value[3] for value in edge_records],
            "length_m": [
                point_by_id.loc[value[2]].distance(point_by_id.loc[value[3]])
                for value in edge_records
            ],
            "highway": ["residential"] * 4 + ["footway"] * 3,
            "oneway": [""] * len(edge_records),
            "movement_type": [value[4] for value in edge_records],
            "crossing": [""] * 4 + ["traffic_signals"] * 3,
            "crossing_control_state": ["unknown"] * 4 + ["signal_osm"] * 3,
            "bike_allowed": [True] * len(edge_records),
            "ped_allowed": [True] * len(edge_records),
            "lts_best": [1] * len(edge_records),
            "pedestrian_status": ["supported"] * len(edge_records),
        },
        geometry=[
            LineString([point_by_id.loc[value[2]], point_by_id.loc[value[3]]])
            for value in edge_records
        ],
        crs=CRS,
    )
    inventory = build_signal_wait_inventory(nodes, edges)

    bicycle = build_bicycle_links(
        edges,
        BicycleProfile("signals", signal_wait_s=20),
        signal_wait_inventory=inventory,
    )
    bicycle_graph = routable_links_to_graph(bicycle, directed=True)
    assert sum(
        bicycle_graph[u][v]["crossing_wait_time_s"]
        for u, v in zip(["A", "S1", "M", "S2"], ["S1", "M", "S2", "B"], strict=True)
    ) == pytest.approx(20)
    bicycle_measures = measure_path(bicycle_graph, ["A", "S1", "M", "S2", "B"])
    assert bicycle_measures.crossing_wait_time_s == pytest.approx(20)
    # A bicycle using the signalized crosswalk pays the crossing event once,
    # not a cluster entry plus the crossing event.
    for crossing_path in (["C0", "C1", "C2", "C3"], ["C3", "C2", "C1", "C0"]):
        crossing_measures = measure_path(bicycle_graph, crossing_path)
        assert crossing_measures.crossing_wait_time_s == pytest.approx(20)
    assert bicycle_measures.travel_time_s == pytest.approx(bicycle_measures.movement_time_s + 20)
    assert sum(
        bicycle_graph[u][v]["crossing_wait_time_s"]
        for u, v in zip(["B", "S2", "M", "S1"], ["S2", "M", "S1", "A"], strict=True)
    ) == pytest.approx(20)

    pedestrian = build_pedestrian_links(
        edges,
        PedestrianProfile("signals", signal_wait_s=30),
        signal_wait_inventory=inventory,
    )
    pedestrian_graph = routable_links_to_graph(pedestrian, directed=True)
    assert sum(
        pedestrian_graph[u][v]["crossing_wait_time_s"]
        for u, v in zip(["C0", "C1", "C2"], ["C1", "C2", "C3"], strict=True)
    ) == pytest.approx(30)
    pedestrian_measures = measure_path(pedestrian_graph, ["C0", "C1", "C2", "C3"])
    assert pedestrian_measures.crossing_wait_time_s == pytest.approx(30)
    assert pedestrian_measures.travel_time_s == pytest.approx(
        pedestrian_measures.movement_time_s + 30
    )
    # No signalized crossing is mapped at the divided intersection, so a walker
    # along the street pays one wait there, as a cyclist does.
    assert sum(
        pedestrian_graph[u][v]["crossing_wait_time_s"]
        for u, v in zip(["A", "S1", "M", "S2"], ["S1", "M", "S2", "B"], strict=True)
    ) == pytest.approx(30)


def test_walkers_pay_on_a_mapped_crossing_and_away_from_it() -> None:
    nodes = gpd.GeoDataFrame(
        {
            "node_id": ["W", "J", "E", "N", "S"],
            "highway": [None, "traffic_signals", None, None, None],
            "osm_tags": ["{}"] * 5,
        },
        geometry=[Point(-50, 0), Point(0, 0), Point(50, 0), Point(5, 10), Point(5, -10)],
        crs=CRS,
    )
    records = [
        ("road_w_j", "W", "J", "along", "residential", "", "unknown"),
        ("road_j_e", "J", "E", "along", "residential", "", "unknown"),
        ("cross", "S", "N", "across", "footway", "traffic_signals", "signal_osm"),
    ]
    point = nodes.set_index("node_id").geometry
    edges = gpd.GeoDataFrame(
        {
            "physical_edge_id": [r[0] for r in records],
            "source_network_id": [r[0] for r in records],
            "u": [r[1] for r in records],
            "v": [r[2] for r in records],
            "length_m": [point[r[1]].distance(point[r[2]]) for r in records],
            "movement_type": [r[3] for r in records],
            "highway": [r[4] for r in records],
            "crossing": [r[5] for r in records],
            "crossing_control_state": [r[6] for r in records],
            "oneway": [""] * 3,
            "bike_allowed": [True] * 3,
            "ped_allowed": [True] * 3,
            "lts_best": [1] * 3,
            "pedestrian_status": ["supported"] * 3,
        },
        geometry=[LineString([point[r[1]], point[r[2]]]) for r in records],
        crs=CRS,
    )
    inventory = build_signal_wait_inventory(nodes, edges)
    assert inventory.clusters_with_crossings
    walk = routable_links_to_graph(
        build_pedestrian_links(
            edges, PedestrianProfile("signals", signal_wait_s=30), signal_wait_inventory=inventory
        ),
        directed=True,
    )
    assert measure_path(walk, ["S", "N"]).crossing_wait_time_s == pytest.approx(30)
    # J is not an end of the crosswalk, so arriving there along the road waits.
    assert measure_path(walk, ["W", "J", "E"]).crossing_wait_time_s == pytest.approx(30)
    bike = routable_links_to_graph(
        build_bicycle_links(
            edges, BicycleProfile("signals", signal_wait_s=20), signal_wait_inventory=inventory
        ),
        directed=True,
    )
    assert measure_path(bike, ["W", "J", "E"]).crossing_wait_time_s == pytest.approx(20)


def test_motor_arcs_and_transition_costs_preserve_turn_control_semantics() -> None:
    data = _load()
    arcs, incoming, outgoing = build_motor_arcs(_lines(data["motor_edges"]))
    assert len(arcs) == 4
    assert arcs.arc_id.str.endswith(":f").all()
    incoming_arc = arcs[arcs.edge_id.eq("car_short_1")].iloc[0].to_dict()
    outgoing_arc = arcs[arcs.edge_id.eq("car_short_2")].iloc[0].to_dict()
    penalties = MotorPenaltyProfile(
        "central",
        signal_s=20,
        stop_s=5,
        right_turn_s=3,
        left_turn_s=7.5,
        u_turn_s=27.5,
    )
    costs, movement, control = motor_transition_cost(
        incoming_arc,
        outgoing_arc,
        via_node="CAR_C",
        controls={"CAR_C": ("signal", "signal_1")},
        penalties=penalties,
    )
    assert movement == "left_turn"
    assert control == "signal"
    assert costs.turn_cost_s == pytest.approx(7.5)
    assert costs.control_cost_s == pytest.approx(20)
    assert costs.generalized_cost_s == pytest.approx(27.5)
    assert set(incoming) >= {"CAR_C", "CAR_G"}
    assert set(outgoing) >= {"CAR_O", "CAR_C"}


def test_cost_components_add_without_losing_interpretability() -> None:
    total = CostComponents(travel_time_s=10, stress_cost_s=5) + CostComponents(
        crossing_cost_s=20,
        control_cost_s=3,
    )
    assert total.generalized_cost_s == pytest.approx(38)
    assert total.travel_time_s == pytest.approx(10)


def test_roundabouts_without_oneway_tags_are_one_way_for_every_mode() -> None:
    edges = gpd.GeoDataFrame(
        {
            "edge_id": ["circle", "street"],
            "osm_way_id": ["1", "2"],
            "u": ["A", "B"],
            "v": ["B", "C"],
            "name": ["Circle", "Street"],
            "highway": ["residential", "residential"],
            "length_m": [10.0, 10.0],
            "oneway": [None, None],
            "osm_tags": ['{"junction": "roundabout"}', "{}"],
            "bike_allowed": [True, True],
            "ped_allowed": [True, True],
            "lts_best": [1, 1],
            "pedestrian_status": ["supported", "supported"],
            "prototype02_gap_edge": [False, False],
            "edge_running_time_s": [1.0, 1.0],
        },
        geometry=[LineString([(0, 0), (10, 0)]), LineString([(10, 0), (20, 0)])],
        crs=CRS,
    )

    canonical = canonical_physical_edges(edges)
    assert canonical.junction.iloc[0] == "roundabout"
    assert pd.isna(canonical.junction.iloc[1])

    bicycle = build_bicycle_links(canonical, BicycleProfile("shared_rule"))
    circle = bicycle[bicycle.physical_edge_id.eq("circle")]
    assert circle.direction.tolist() == ["forward"]
    assert bicycle[bicycle.physical_edge_id.eq("street")].direction.tolist() == [
        "forward",
        "reverse",
    ]

    arcs, _, _ = build_motor_arcs(edges)
    assert arcs[arcs.edge_id.eq("circle")].arc_id.tolist() == ["circle:f"]


def test_unknown_sidewalk_collectors_cost_their_own_factor() -> None:
    edges = _classified_edges()
    tertiary = edges[edges.highway.eq("tertiary")].copy()
    assert not tertiary.empty
    tertiary["pedestrian_status"] = "unknown"

    with_collectors = build_pedestrian_links(tertiary, PedestrianProfile("collectors"))
    assert set(with_collectors.physical_edge_id) == set(tertiary.edge_id.astype(str))
    expected_factor = PedestrianProfile("collectors").unknown_collector_factor
    assert (
        (with_collectors.facility_cost_s / with_collectors.movement_time_s)
        .round(6)
        .eq(round(expected_factor - 1, 6))
        .all()
    )


def test_link_builders_return_typed_empty_layers_when_everything_is_filtered() -> None:
    edges = _classified_edges()
    strict = build_bicycle_links(edges, BicycleProfile("impossible", maximum_lts=0))
    assert strict.empty
    assert {"routable_link_id", "generalized_cost_s", "geometry"} <= set(strict.columns)
    assert routable_links_to_graph(strict, directed=True).number_of_edges() == 0


def _signal_corner_network() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    node_records = [
        ("R0", -40, 0, None),
        ("S", 0, 0, "traffic_signals"),
        ("R1", 40, 0, None),
        ("P0", 5, 12, None),
        ("P1", 5, 60, None),
    ]
    nodes = gpd.GeoDataFrame(
        {
            "node_id": [value[0] for value in node_records],
            "highway": [value[3] for value in node_records],
            "osm_tags": ["{}"] * len(node_records),
        },
        geometry=[Point(value[1], value[2]) for value in node_records],
        crs=CRS,
    )
    point_by_id = nodes.set_index("node_id").geometry
    edge_records = [
        ("road_r0_s", "residential", "R0", "S"),
        ("road_s_r1", "residential", "S", "R1"),
        ("path_p0_p1", "footway", "P0", "P1"),
    ]
    edges = gpd.GeoDataFrame(
        {
            "physical_edge_id": [value[0] for value in edge_records],
            "source_network_id": [value[0] for value in edge_records],
            "u": [value[2] for value in edge_records],
            "v": [value[3] for value in edge_records],
            "length_m": [
                point_by_id.loc[value[2]].distance(point_by_id.loc[value[3]])
                for value in edge_records
            ],
            "highway": [value[1] for value in edge_records],
            "oneway": [""] * len(edge_records),
            "movement_type": ["along"] * len(edge_records),
            "crossing": [""] * len(edge_records),
            "crossing_control_state": ["unknown"] * len(edge_records),
            "bike_allowed": [True] * len(edge_records),
            "ped_allowed": [True] * len(edge_records),
            "lts_best": [1] * len(edge_records),
            "pedestrian_status": ["supported"] * len(edge_records),
        },
        geometry=[
            LineString([point_by_id.loc[value[2]], point_by_id.loc[value[3]]])
            for value in edge_records
        ],
        crs=CRS,
    )
    return nodes, edges


def test_signal_footprints_do_not_charge_nearby_nonroadway_links() -> None:
    nodes, edges = _signal_corner_network()
    inventory = build_signal_wait_inventory(nodes, edges)
    # P0 sits 13 m from the signal, inside the buffered footprint, but only a
    # footway touches it, so entering it is not a roadway signal entry.
    assert "S" in inventory.cluster_by_node
    assert "P0" not in inventory.cluster_by_node
    bicycle = build_bicycle_links(
        edges, BicycleProfile("signals", signal_wait_s=20), signal_wait_inventory=inventory
    )
    path_links = bicycle[bicycle.physical_edge_id.eq("path_p0_p1")]
    assert path_links.crossing_wait_time_s.eq(0).all()
    road_links = bicycle[bicycle.physical_edge_id.eq("road_r0_s")]
    assert road_links.set_index("direction").crossing_wait_time_s.to_dict() == {
        "forward": 20.0,
        "reverse": 0.0,
    }


def test_parallel_signalized_crossing_legs_each_keep_an_event() -> None:
    nodes = gpd.GeoDataFrame(
        {"node_id": ["X", "Y"], "highway": [None, None], "osm_tags": ["{}", "{}"]},
        geometry=[Point(0, 0), Point(0, 20)],
        crs=CRS,
    )
    edges = gpd.GeoDataFrame(
        {
            "physical_edge_id": ["leg_a", "leg_b"],
            "source_network_id": ["way_a", "way_b"],
            "u": ["X", "X"],
            "v": ["Y", "Y"],
            "length_m": [20.0, 20.0],
            "highway": ["footway", "footway"],
            "movement_type": ["across", "across"],
            "crossing": ["traffic_signals", "traffic_signals"],
            "crossing_control_state": ["signal_osm", "signal_osm"],
        },
        geometry=[
            LineString([(0, 0), (0, 20)]),
            LineString([(0, 0), (2, 10), (0, 20)]),
        ],
        crs=CRS,
    )
    inventory = build_signal_wait_inventory(nodes, edges)
    assert set(inventory.crossing_event_by_wait_edge) == {"leg_a", "leg_b"}
    assert inventory.crossing_leg_edges == frozenset({"leg_a", "leg_b"})


def test_missing_traffic_band_is_unknown_whether_none_or_nan() -> None:
    import math

    base = {"highway": "residential", "bike_allowed": True, "speed_mph": 25, "lanes": 2}
    with_none = classify_lts({**base, "traffic_band": None})
    with_nan = classify_lts({**base, "traffic_band": math.nan})
    assert with_none.lts == with_nan.lts == 1
    assert "traffic_volume_band" in with_nan.missing


def test_bicycle_links_read_float_lts_and_reject_missing_lts() -> None:
    import math

    edges = _classified_edges()
    edges["lts_best"] = edges.lts_best.astype(float)
    links = build_bicycle_links(edges, BicycleProfile("float_lts"))
    assert set(links.lts) <= {1, 2, 3, 4}
    edges.loc[edges.index[0], "lts_best"] = math.nan
    with pytest.raises(ValueError, match="lts_best"):
        build_bicycle_links(edges, BicycleProfile("missing_lts"))


def test_parse_number_reads_grouped_thousands() -> None:
    from walkbike_routes.network.bicycle import parse_number

    assert parse_number("12,500") == 12500.0
    assert parse_number("69000") == 69000.0
    assert parse_number("35 mph") == 35.0
    assert parse_number("1,2") == 1.0


def test_bidirectional_links_carry_cost_components_and_build_graphs() -> None:
    from walkbike_routes.adapters import canonical_bidirectional_links

    edges = _classified_edges()
    links = canonical_bidirectional_links(
        edges,
        mode=Mode.PEDESTRIAN,
        speed_mps=1.3,
        link_kind="simple",
        evidence_source="fixture",
        evidence_confidence="high",
    )
    assert (links.generalized_cost_s == links.travel_time_s).all()
    graph = routable_links_to_graph(links, directed=True)
    assert graph.number_of_edges() == 2 * len(links)


@pytest.mark.parametrize(
    ("general", "override", "junction", "expected"),
    [
        ("yes", "no", None, {"forward", "reverse"}),
        ("no", "yes", None, {"forward"}),
        ("yes", "-1", None, {"reverse"}),
        (None, "no", "roundabout", {"forward", "reverse"}),
    ],
)
def test_bicycle_direction_override(general, override, junction, expected):
    edges = _classified_edges().iloc[:1].copy()
    edges["oneway"] = general
    edges["junction"] = junction
    edges["osm_tags"] = json.dumps({"oneway:bicycle": override})
    edges["bike_allowed"] = True
    links = build_bicycle_links(edges, BicycleProfile("override"))
    assert set(links.direction) == expected


def test_bicycle_override_does_not_change_motor_arcs():
    edges = _classified_edges().iloc[:1].copy()
    edges["oneway"] = "yes"
    edges["osm_tags"] = json.dumps({"oneway:bicycle": "no"})
    edges["travel_time_s"] = 1.0
    arcs, _, _ = build_motor_arcs(edges)
    assert len(arcs) == 1
    assert arcs.u.iloc[0] == edges.u.iloc[0]


def _signal_net(points, signals, records):
    """A small network: ``records`` are (id, u, v, highway, movement, crossing state)."""
    ids = list(points)
    nodes = gpd.GeoDataFrame(
        {
            "node_id": ids,
            "highway": ["traffic_signals" if n in signals else None for n in ids],
            "osm_tags": ["{}"] * len(ids),
        },
        geometry=[Point(*points[n]) for n in ids],
        crs=CRS,
    )
    point = nodes.set_index("node_id").geometry
    across = [r[4] == "across" for r in records]
    edges = gpd.GeoDataFrame(
        {
            "physical_edge_id": [r[0] for r in records],
            "source_network_id": [r[0] for r in records],
            "u": [r[1] for r in records],
            "v": [r[2] for r in records],
            "length_m": [point[r[1]].distance(point[r[2]]) for r in records],
            "highway": [r[3] for r in records],
            "movement_type": [r[4] for r in records],
            "crossing": ["traffic_signals" if a else "" for a in across],
            "crossing_control_state": ["signal_osm" if a else "unknown" for a in across],
            "oneway": [""] * len(records),
            "bike_allowed": [True] * len(records),
            "ped_allowed": [True] * len(records),
            "lts_best": [1] * len(records),
            "pedestrian_status": ["supported"] * len(records),
        },
        geometry=[LineString([point[r[1]], point[r[2]]]) for r in records],
        crs=CRS,
    )
    inventory = build_signal_wait_inventory(nodes, edges)
    links = build_pedestrian_links(
        edges, PedestrianProfile("signals", signal_wait_s=30), signal_wait_inventory=inventory
    )
    return routable_links_to_graph(links, directed=True)


@pytest.mark.parametrize(
    "stored", [(("W", "J"), ("E", "J")), (("J", "W"), ("J", "E")), (("W", "J"), ("J", "E"))]
)
def test_a_walker_waits_once_whichever_way_the_streets_are_stored(stored) -> None:
    points = {"W": (-50, 0), "J": (0, 0), "E": (50, 0)}
    records = [(f"s{i}", u, v, "residential", "along", "") for i, (u, v) in enumerate(stored)]
    walk = _signal_net(points, {"J"}, records)
    for path in (["W", "J", "E"], ["E", "J", "W"]):
        assert measure_path(walk, path).crossing_wait_time_s == pytest.approx(30)
    assert measure_path(walk, ["J", "W"]).crossing_wait_time_s == pytest.approx(0)


def test_a_crossing_s_far_end_intersection_does_not_charge_again() -> None:
    # A 40 m crossing: its two ends are too far apart to be one intersection.
    points = {"A": (-20, 0), "Z": (20, 0), "W": (20, -50)}
    records = [
        ("cross", "A", "Z", "footway", "across", ""),
        ("street", "W", "Z", "residential", "along", ""),
    ]
    walk = _signal_net(points, {"Z"}, records)
    assert measure_path(walk, ["W", "Z", "A"]).crossing_wait_time_s == pytest.approx(30)


def test_a_footway_reaching_a_signalized_intersection_pays_nothing() -> None:
    points = {"S": (0, 0), "Q": (10, 0), "R": (-50, 0), "P": (10, 50)}
    records = [
        ("road_q_s", "Q", "S", "service", "along", ""),
        ("road_r_s", "R", "S", "residential", "along", ""),
        ("foot", "P", "Q", "footway", "along", ""),
    ]
    walk = _signal_net(points, {"S"}, records)
    assert measure_path(walk, ["P", "Q"]).crossing_wait_time_s == pytest.approx(0)
    assert measure_path(walk, ["R", "S"]).crossing_wait_time_s == pytest.approx(30)


def test_a_signal_with_no_main_road_uses_the_plain_wait() -> None:
    walk = _signal_net(
        {"W": (-50, 0), "J": (0, 0), "E": (50, 0)},
        {"J"},
        [
            ("s0", "W", "J", "residential", "along", ""),
            ("s1", "J", "E", "residential", "along", ""),
        ],
    )
    assert measure_path(walk, ["W", "J", "E"]).crossing_wait_time_s == pytest.approx(30)
    profile = PedestrianProfile("p", signal_wait_s=30, signal_wait_cross_arterial_s=50)
    assert profile.signal_wait("cross_arterial") == 50
    assert profile.signal_wait("along_arterial") == 30
    assert profile.signal_wait("other") == 30


def test_a_cyclist_on_a_path_pays_a_signalised_crossing_once() -> None:
    points = {"A": (0, 0), "B": (20, 0), "P": (-50, 0)}
    nodes = gpd.GeoDataFrame(
        {"node_id": list(points), "highway": [None] * 3, "osm_tags": ["{}"] * 3},
        geometry=[Point(*p) for p in points.values()],
        crs=CRS,
    )
    records = [("x", "A", "B", "across", "traffic_signals"), ("p", "P", "A", "along", "")]
    edges = gpd.GeoDataFrame(
        {
            "physical_edge_id": [r[0] for r in records],
            "source_network_id": [r[0] for r in records],
            "u": [r[1] for r in records],
            "v": [r[2] for r in records],
            "length_m": [Point(points[r[1]]).distance(Point(points[r[2]])) for r in records],
            "highway": ["cycleway"] * 2,
            "movement_type": [r[3] for r in records],
            "crossing": [r[4] for r in records],
            "crossing_control_state": ["unknown"] * 2,
            "oneway": [""] * 2,
            "bike_allowed": [True] * 2,
            "ped_allowed": [True] * 2,
            "lts_best": [1] * 2,
            "pedestrian_status": ["supported"] * 2,
        },
        geometry=[LineString([points[r[1]], points[r[2]]]) for r in records],
        crs=CRS,
    )
    bike = routable_links_to_graph(
        build_bicycle_links(
            edges,
            BicycleProfile("t", signal_wait_s=20),
            signal_wait_inventory=build_signal_wait_inventory(nodes, edges),
        ),
        directed=True,
    )
    assert measure_path(bike, ["P", "A", "B"]).crossing_wait_time_s == pytest.approx(20)


@pytest.mark.parametrize("field", ["signal_wait_cross_arterial_s", "signal_wait_along_arterial_s"])
def test_negative_signal_waits_are_refused(field: str) -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        PedestrianProfile("p", **{field: -1.0})
    with pytest.raises(ValueError, match="nonnegative"):
        BicycleProfile("b", **{field: -1.0})


def test_a_cyclist_off_a_path_waits_where_no_crossing_is_used() -> None:
    # A cycleway arrives at J; the ride goes on east along a residential street
    # across the main road. A crosswalk 10 m north is in the same intersection.
    points = {"P": (-50, 0), "J": (0, 0), "E": (50, 0), "M1": (5, -50), "M2": (5, 50),
              "X1": (-10, 10), "X2": (10, 10)}  # fmt: skip
    walk_and_ride = [
        ("path", "P", "J", "cycleway", "along", ""),
        ("east", "J", "E", "residential", "along", ""),
        ("main1", "M1", "J", "primary", "along", ""),
        ("main2", "J", "M2", "primary", "along", ""),
        ("xing", "X1", "X2", "cycleway", "across", ""),
    ]
    nodes = gpd.GeoDataFrame(
        {"node_id": list(points), "highway": ["traffic_signals" if n == "J" else None
                                               for n in points], "osm_tags": ["{}"] * 7},
        geometry=[Point(*p) for p in points.values()],
        crs=CRS,
    )  # fmt: skip
    point = nodes.set_index("node_id").geometry
    edges = gpd.GeoDataFrame(
        {
            "physical_edge_id": [r[0] for r in walk_and_ride],
            "source_network_id": [r[0] for r in walk_and_ride],
            "u": [r[1] for r in walk_and_ride],
            "v": [r[2] for r in walk_and_ride],
            "length_m": [point[r[1]].distance(point[r[2]]) for r in walk_and_ride],
            "highway": [r[3] for r in walk_and_ride],
            "movement_type": [r[4] for r in walk_and_ride],
            "crossing": ["traffic_signals" if r[4] == "across" else "" for r in walk_and_ride],
            "crossing_road": ["primary" if r[4] == "across" else "" for r in walk_and_ride],
            "crossing_control_state": ["unknown"] * 5,
            "oneway": [""] * 5,
            "bike_allowed": [True] * 5,
            "ped_allowed": [True] * 5,
            "lts_best": [1] * 5,
            "pedestrian_status": ["supported"] * 5,
        },
        geometry=[LineString([point[r[1]], point[r[2]]]) for r in walk_and_ride],
        crs=CRS,
    )
    inventory = build_signal_wait_inventory(nodes, edges)
    assert len(set(inventory.cluster_by_node.values())) == 1
    bike = routable_links_to_graph(
        build_bicycle_links(
            edges,
            BicycleProfile("b", signal_wait_s=20, signal_wait_cross_arterial_s=40,
                           signal_wait_along_arterial_s=15),
            signal_wait_inventory=inventory,
        ),
        directed=True,
    )  # fmt: skip
    assert measure_path(bike, ["P", "J", "E"]).crossing_wait_time_s == pytest.approx(40)
    walk = routable_links_to_graph(
        build_pedestrian_links(
            edges.assign(sidewalk=["", "", "separate", "separate", ""]),
            PedestrianProfile("w", signal_wait_s=30, signal_wait_cross_arterial_s=50,
                              signal_wait_along_arterial_s=15),
            signal_wait_inventory=inventory,
        ),
        directed=True,
    )  # fmt: skip
    # Along the main road's carriageway at an intersection with a crosswalk:
    # charged as crossing it.
    assert measure_path(walk, ["M1", "J"]).crossing_wait_time_s == pytest.approx(50)
    no_crosswalk = build_signal_wait_inventory(nodes, edges[edges.movement_type.eq("along")])
    walk = routable_links_to_graph(
        build_pedestrian_links(
            edges[edges.movement_type.eq("along")].assign(sidewalk=["", "", "separate", "separate"]),
            PedestrianProfile("w", signal_wait_s=30, signal_wait_cross_arterial_s=50,
                              signal_wait_along_arterial_s=15),
            signal_wait_inventory=no_crosswalk,
        ),
        directed=True,
    )  # fmt: skip
    # With no crosswalk mapped, walking along the main road waits as going with its green.
    assert measure_path(walk, ["M1", "J"]).crossing_wait_time_s == pytest.approx(15)
