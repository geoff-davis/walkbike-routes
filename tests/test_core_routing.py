# mypy: disable-error-code="arg-type,var-annotated,index,call-overload,assignment,misc"
# The pandas and shapely stubs reject these tests' table idioms.
"""Tests for destination-neutral path trees, route traces, and flows."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import geopandas as gpd
import networkx as nx
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from walkbike_routes.exceptions import AccessibilityError
from walkbike_routes.models import Mode, TravelDirection
from walkbike_routes.network import routable_links_to_graph
from walkbike_routes.routing import (
    measure_path,
    multi_source_k_paths,
    multi_source_paths,
    trace_routes,
)

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


def _canonical_graph() -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_edge(
        "O",
        "X",
        routable_link_id="link_1",
        physical_edge_id="physical_shared",
        physical_length_m=100.0,
        travel_time_s=20.0,
        generalized_cost_s=200.0,
    )
    graph.add_edge(
        "X",
        "G",
        routable_link_id="link_2",
        physical_edge_id="physical_shared",
        physical_length_m=100.0,
        travel_time_s=20.0,
        generalized_cost_s=200.0,
    )
    return graph


def test_path_ties_are_resolved_by_access_point_id() -> None:
    graph = nx.Graph()
    graph.add_edge("O", "A", cost=10.0)
    graph.add_edge("O", "B", cost=10.0)
    tree = multi_source_paths(
        graph,
        {"z_access": "A", "a_access": "B"},
        weight="cost",
        direction=TravelDirection.TO_DESTINATION,
    )
    assert tree.access_point_by_node["O"] == "a_access"
    assert tree.path_by_node["O"] == ["O", "B"]


def test_direction_is_respected_on_directed_graphs() -> None:
    graph = nx.DiGraph()
    graph.add_edge("O", "G", cost=1.0)
    arrival = multi_source_paths(
        graph,
        {"gate": "G"},
        weight="cost",
        direction=TravelDirection.TO_DESTINATION,
    )
    departure = multi_source_paths(
        graph,
        {"gate": "G"},
        weight="cost",
        direction=TravelDirection.FROM_DESTINATION,
    )
    assert arrival.path_by_node["O"] == ["O", "G"]
    assert "O" not in departure.path_by_node


def test_multi_path_tree_retains_bounded_near_best_loopless_alternatives() -> None:
    graph: nx.DiGraph[str] = nx.DiGraph()
    graph.add_edge("O", "A", cost=1.0)
    graph.add_edge("A", "G1", cost=1.0)
    graph.add_edge("O", "B", cost=1.1)
    graph.add_edge("B", "G2", cost=1.1)
    graph.add_edge("O", "C", cost=2.0)
    graph.add_edge("C", "G1", cost=2.0)

    tree = multi_source_k_paths(
        graph,
        {"gate_1": "G1", "gate_2": "G2"},
        weight="cost",
        direction=TravelDirection.TO_DESTINATION,
        maximum_paths=3,
        maximum_cost_gap=1.0,
        maximum_cost_ratio=1.5,
    )

    alternatives = tree.alternatives_by_node["O"]
    assert [alternative.cost for alternative in alternatives] == pytest.approx([2.0, 2.2])
    assert [alternative.access_point_id for alternative in alternatives] == ["gate_1", "gate_2"]
    assert [alternative.path for alternative in alternatives] == [
        ("O", "A", "G1"),
        ("O", "B", "G2"),
    ]
    assert all(len(alternative.path) == len(set(alternative.path)) for alternative in alternatives)


def test_route_caps_use_travel_time() -> None:
    graph = _canonical_graph()
    tree = multi_source_paths(
        graph,
        {"access": "G"},
        weight="generalized_cost_s",
        direction=TravelDirection.TO_DESTINATION,
    )
    origins = gpd.GeoDataFrame(
        {
            "origin_id": ["reachable", "unattached"],
            "demand_weight": [5.0, 7.0],
            "weight_unit": ["people_proxy", "people_proxy"],
        },
        geometry=[Point(0, 0), Point(1, 1)],
        crs=CRS,
    )
    attachments = pd.DataFrame(
        [
            {
                "origin_id": "reachable",
                "mode": "bicycle",
                "attached_node_id": "O",
                "snap_distance_m": 0.0,
                "network_attached": True,
                "attachment_policy": "test",
            }
        ]
    )
    routes = trace_routes(
        graph,
        tree,
        origins,
        attachments,
        study_id="fixture",
        destination_id="destination",
        profile_id="stress_weighted",
        scenario_id="one_minute",
        mode=Mode.BICYCLE,
        time_limit_min=1.0,
        snap_speed_mps=4.0,
    ).set_index("origin_id")
    assert routes.loc["reachable", "total_travel_time_s"] == pytest.approx(40.0)
    assert routes.loc["reachable", "generalized_cost_s"] == pytest.approx(400.0)
    assert bool(routes.loc["reachable", "within_time_limit"])
    assert not bool(routes.loc["unattached", "network_attached"])
    assert not bool(routes.loc["unattached", "route_reachable"])


def test_trace_routes_tolerates_nullable_attachment_columns() -> None:
    graph = _canonical_graph()
    tree = multi_source_paths(
        graph,
        {"access": "G"},
        weight="generalized_cost_s",
        direction=TravelDirection.TO_DESTINATION,
    )
    origins = gpd.GeoDataFrame(
        {
            "origin_id": ["attached", "missing_flag"],
            "demand_weight": [1.0, 1.0],
            "weight_unit": ["people_proxy"] * 2,
        },
        geometry=[Point(0, 0), Point(1, 1)],
        crs=CRS,
    )
    attachments = pd.DataFrame(
        {
            "origin_id": ["attached", "missing_flag"],
            "mode": ["bicycle", "bicycle"],
            "attached_node_id": pd.array(["O", None], dtype="string"),
            "snap_distance_m": pd.array([2.0, None], dtype="Float64"),
            "network_attached": pd.array([True, None], dtype="boolean"),
            "attachment_policy": ["test", "test"],
        }
    )
    routes = trace_routes(
        graph,
        tree,
        origins,
        attachments,
        study_id="fixture",
        destination_id="destination",
        profile_id="p",
        scenario_id="s",
        mode=Mode.BICYCLE,
        time_limit_min=None,
        snap_speed_mps=4.0,
    ).set_index("origin_id")
    assert bool(routes.loc["attached", "route_reachable"])
    assert not bool(routes.loc["missing_flag", "network_attached"])
    assert not bool(routes.loc["missing_flag", "route_reachable"])


def test_measure_path_requires_an_explicit_travel_time() -> None:
    graph: nx.DiGraph[str] = nx.DiGraph()
    graph.add_edge("A", "B", physical_length_m=100.0, generalized_cost_s=1.0)
    with pytest.raises(AccessibilityError, match="travel_time_s"):
        measure_path(graph, ["A", "B"])


def test_graph_builder_keeps_the_parallel_link_preferred_by_the_objective() -> None:
    links = gpd.GeoDataFrame(
        {
            "routable_link_id": ["long_calm", "short_stressful"],
            "physical_edge_id": ["long", "short"],
            "u": ["A", "A"],
            "v": ["B", "B"],
            "mode": ["bicycle"] * 2,
            "direction": ["forward"] * 2,
            "link_kind": ["bicycle_roadway_or_path"] * 2,
            "physical_length_m": [200.0, 100.0],
            "travel_time_s": [40.0, 20.0],
            "generalized_cost_s": [40.0, 160.0],
        },
        geometry=[LineString([(0, 0), (200, 0)]), LineString([(0, 0), (100, 0)])],
        crs=CRS,
    )
    by_cost = routable_links_to_graph(links, directed=True)
    by_length = routable_links_to_graph(links, directed=True, preferred_cost="physical_length_m")
    assert by_cost["A"]["B"]["routable_link_id"] == "long_calm"
    assert by_length["A"]["B"]["routable_link_id"] == "short_stressful"


def _street_routes(direction: TravelDirection, backward_per_m: float, **drop: bool) -> pd.DataFrame:
    # Home 5 m off street a-b, 20 m along from a and 80 m from b.  The
    # destination g is 500 s from a but 50 s from b, both ways.
    graph = nx.DiGraph()
    for x, y, cost in (("a", "g", 500.0), ("g", "a", 500.0), ("b", "g", 50.0), ("g", "b", 50.0)):
        graph.add_edge(
            x, y, routable_link_id=f"{x}{y}", physical_edge_id=f"{x}{y}",
            physical_length_m=cost, travel_time_s=cost, generalized_cost_s=cost,
        )  # fmt: skip
    tree = multi_source_paths(
        graph, {"door": "g"}, weight="generalized_cost_s", direction=direction
    )
    origins = gpd.GeoDataFrame(
        {"origin_id": ["home"], "demand_weight": [1.0], "weight_unit": ["people"]},
        geometry=[Point(0, 0)],
        crs=CRS,
    )
    row = {
        "origin_id": "home", "mode": "bicycle", "attached_node_id": "a",
        "snap_distance_m": 25.0, "network_attached": True, "attachment_policy": "test",
        "attached_edge_id": "ab", "edge_distance_m": 5.0, "edge_u": "a", "edge_v": "b",
        "to_u_m": 20.0, "to_v_m": 80.0, "forward_cost_per_m": 1.0,
        "backward_cost_per_m": backward_per_m,
    }  # fmt: skip
    for column in drop:
        row.pop(column)
    return trace_routes(
        graph, tree, origins, pd.DataFrame([row]), study_id="t", destination_id="g",
        profile_id="t", scenario_id="t", mode=Mode.BICYCLE, time_limit_min=None,
        snap_speed_mps=4.0,
    ).set_index("origin_id")  # fmt: skip


def test_routes_start_from_the_cheaper_allowed_end_of_the_homes_street() -> None:
    to = TravelDirection.TO_DESTINATION
    back = TravelDirection.FROM_DESTINATION
    # Two-way: b, 80 + 50 = 130 s, beats a, 20 + 500 = 520 s.
    routes = _street_routes(to, 1.0)
    assert routes.attached_node_id["home"] == "b"
    assert routes.snap_distance_m["home"] == pytest.approx(85.0)
    assert routes.physical_edge_ids["home"] == "bg"
    # One way, a to b: leaving it must head for b; coming home it must enter at a.
    assert _street_routes(to, math.nan).attached_node_id["home"] == "b"
    arriving = _street_routes(back, math.nan)
    assert arriving.attached_node_id["home"] == "a"
    assert arriving.snap_distance_m["home"] == pytest.approx(25.0)
    assert arriving.physical_edge_ids["home"] == "ga"


def test_street_attachments_without_costs_per_metre_are_refused() -> None:
    with pytest.raises(AccessibilityError, match="cost per metre"):
        _street_routes(TravelDirection.TO_DESTINATION, 1.0, backward_cost_per_m=True)
