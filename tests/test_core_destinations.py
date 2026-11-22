# mypy: disable-error-code="arg-type,var-annotated,index,call-overload,assignment,misc"
# The pandas and shapely stubs reject these tests' table idioms.
"""Tests for generic destination resolution and accessibility summaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon

from walkbike_routes.adapters import canonical_bidirectional_links
from walkbike_routes.analysis import calculate_accessibility
from walkbike_routes.destinations import (
    AccessPointGenerationPolicy,
    resolve_point_access_points,
    resolve_polygon_access_points,
)
from walkbike_routes.models import DestinationType, Mode, TravelDirection
from walkbike_routes.network import routable_links_to_graph

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


def _network() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    data = _load()
    nodes = _points(data["nodes"])
    links = canonical_bidirectional_links(
        _lines(data["edges"]),
        mode=Mode.BICYCLE,
        speed_mps=4.0,
        link_kind="fixture roadway or path",
        evidence_source="synthetic fixture",
        evidence_confidence="high",
    )
    links["generalized_cost_s"] = links.travel_time_s
    return nodes, links


def _destination(
    destination_id: str,
    destination_type: DestinationType,
    geometry: Point | Polygon,
) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "destination_id": [destination_id],
            "label": [destination_id.replace("_", " ").title()],
            "destination_type": [destination_type.value],
            "source": ["synthetic fixture"],
            "review_status": ["synthetic_reviewed"],
        },
        geometry=[geometry],
        crs=CRS,
    )


def _policy() -> AccessPointGenerationPolicy:
    return AccessPointGenerationPolicy(
        modes=(Mode.BICYCLE,),
        directions=(TravelDirection.BOTH,),
        source="synthetic generated candidates",
        confidence="high",
        review_status="synthetic_reviewed",
    )


def test_point_destination_resolves_to_nearest_node_with_snap_evidence() -> None:
    nodes, _links = _network()
    destination = _destination(
        "point_destination",
        DestinationType.POINT,
        Point(590190, 4137000),
    )
    access = resolve_point_access_points(destination, nodes, _policy())
    assert access.node_id.tolist() == ["G"]
    assert access.snap_distance_m.iloc[0] == pytest.approx(10.0)
    assert access.review_status.iloc[0] == "synthetic_reviewed"


def test_polygon_destination_uses_multiple_boundary_entries_not_centroid() -> None:
    nodes, links = _network()
    polygon = Polygon(
        [
            (590150, 4136880),
            (590225, 4136880),
            (590225, 4137120),
            (590150, 4137120),
        ]
    )
    destination = _destination("district", DestinationType.POLYGON, polygon)
    access = resolve_polygon_access_points(destination, nodes, links, _policy())
    assert set(access.node_id) == {"G", "NG", "SG"}
    assert len(access) == 3
    assert not any(point.equals(polygon.centroid) for point in access.geometry)
    assert access.access_role.eq("generated polygon boundary candidate").all()


def test_fixture_explicit_destination_routes_without_school_fields() -> None:
    data = _load()
    _nodes, links = _network()
    graph = routable_links_to_graph(links, directed=False)
    result = calculate_accessibility(
        graph,
        _points(data["generic_origins"]),
        pd.DataFrame(data["origin_attachments"]),
        _points(data["access_points"]),
        study_id="generic_fixture",
        destination_id="fixture_destination",
        profile_id="fixture_bicycle",
        scenario_id="fastest",
        mode=Mode.BICYCLE,
        direction=TravelDirection.TO_DESTINATION,
        route_weight="generalized_cost_s",
        thresholds_min=(1.0,),
        snap_speed_mps=4.0,
    )
    route = result.routes.iloc[0]
    assert route.access_point_id == "fixture_bicycle_access"
    assert route.destination_id == "fixture_destination"
    assert route.total_travel_time_s == pytest.approx(50.0)


def test_accessibility_uses_distinct_polygon_entries_and_equal_origin_weights() -> None:
    nodes, links = _network()
    graph = routable_links_to_graph(links, directed=False)
    polygon = Polygon(
        [
            (590150, 4136880),
            (590225, 4136880),
            (590225, 4137120),
            (590150, 4137120),
        ]
    )
    access = resolve_polygon_access_points(
        _destination("district", DestinationType.POLYGON, polygon),
        nodes,
        links,
        _policy(),
    )
    origins = gpd.GeoDataFrame(
        {"origin_id": ["west", "northwest"]},
        geometry=[Point(590000, 4137000), Point(590000, 4137080)],
        crs=CRS,
    )
    attachments = pd.DataFrame(
        {
            "origin_id": ["west", "northwest"],
            "mode": ["bicycle", "bicycle"],
            "attached_node_id": ["W", "NW"],
            "snap_distance_m": [0.0, 0.0],
            "network_attached": [True, True],
            "attachment_policy": ["fixture", "fixture"],
        }
    )
    result = calculate_accessibility(
        graph,
        origins,
        attachments,
        access,
        study_id="generic_fixture",
        destination_id="district",
        profile_id="fixture_bicycle",
        scenario_id="fastest",
        mode=Mode.BICYCLE,
        direction=TravelDirection.TO_DESTINATION,
        route_weight="generalized_cost_s",
        thresholds_min=(0.5, 1.0),
        snap_speed_mps=4.0,
    )
    selected = result.routes.set_index("origin_id").access_point_id
    assert selected["west"] == "district:boundary:G"
    assert selected["northwest"] == "district:boundary:NG"
    assert result.routes.demand_weight.eq(1.0).all()
    assert result.routes.weight_unit.eq("origin_count").all()
    one_minute = result.summary[result.summary.threshold_min.eq(1.0)].iloc[0]
    assert one_minute.reachable_origin_count == 2
    assert one_minute.reachable_demand_weight == pytest.approx(2.0)
    assert set(result.access_point_usage.access_point_id) == {
        "district:boundary:G",
        "district:boundary:NG",
    }
    assert result.unreachable_origins.empty


@pytest.mark.parametrize("crs", ["EPSG:4326", "EPSG:2227"])
def test_destination_resolution_rejects_nonmetric_crs(crs):
    from walkbike_routes.exceptions import AccessibilityError

    destinations = gpd.GeoDataFrame(
        {"destination_id": ["d"], "destination_type": ["point"]},
        geometry=[Point(0, 0)],
        crs=crs,
    )
    nodes = gpd.GeoDataFrame({"node_id": ["n"]}, geometry=[Point(0.01, 0)], crs=crs)
    with pytest.raises(AccessibilityError, match="projected|meters"):
        resolve_point_access_points(
            destinations, nodes, AccessPointGenerationPolicy(modes=(Mode.BICYCLE,))
        )
