"""Compatibility adapters from pilot tables to canonical core schemas."""

from __future__ import annotations

import geopandas as gpd

from walkbike_routes.attributes import parse_osm_tags
from walkbike_routes.edges import drop_duplicate_physical_edges
from walkbike_routes.models import LinkDirection, Mode
from walkbike_routes.routing.costs import CostComponents


def canonical_physical_edges(edges: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Adapt pilot `edge_id` rows to destination-neutral physical edges."""
    result = edges.copy()
    if "physical_edge_id" not in result:
        result["physical_edge_id"] = result.edge_id.astype(str)
    if "source_network_id" not in result:
        source = result["osm_way_id"] if "osm_way_id" in result else result["physical_edge_id"]
        result["source_network_id"] = source.astype(str)
    if "junction" not in result:
        if "junction_tag" in result:
            result["junction"] = result.junction_tag
        elif "osm_tags" in result:
            result["junction"] = result.osm_tags.map(
                lambda value: parse_osm_tags(value).get("junction")
            )
    keep = ["physical_edge_id", "source_network_id", "u", "v", "length_m", "geometry"]
    optional = [
        "name",
        "highway",
        "oneway",
        "oneway_bicycle",
        "junction",
        "movement_type",
        "crossing",
        "crossing_control_state",
        "crossing_road",
        "sidewalk",
        "sidewalk_inferred",
        "bike_allowed",
        "ped_allowed",
        "car_allowed",
        "speed_mph",
        "lanes",
        "bike_facility",
        "lts_best",
        "lts_furth",
        "lts_furth_defaults",
        "pedestrian_status",
        "grade",
        "traffic_band",
        "traffic_volume_type",
        "traffic_aadt",
        "traffic_aadt_source",
        "footway",
        "service",
        "surface",
        "crossed_road",
        "crossing_marking",
        "crossing_share",
        "crossing_group",
        "beside_road",
        "beside_name",
    ]
    result["u"] = result.u.astype(str)
    result["v"] = result.v.astype(str)
    canonical = gpd.GeoDataFrame(
        result[[*keep, *(column for column in optional if column in result)]],
        geometry="geometry",
        crs=edges.crs,
    )
    return drop_duplicate_physical_edges(canonical)


def canonical_origins(
    origins: gpd.GeoDataFrame,
    *,
    weight_field: str,
    weight_unit: str,
) -> gpd.GeoDataFrame:
    """Adapt any point inventory with an explicit weight field."""
    result = origins[["origin_id", weight_field, "geometry"]].copy()
    result = result.rename(columns={weight_field: "demand_weight"})
    result["origin_id"] = result.origin_id.astype(str)
    result["weight_unit"] = weight_unit
    return gpd.GeoDataFrame(result, geometry="geometry", crs=origins.crs)


def canonical_bidirectional_links(
    edges: gpd.GeoDataFrame,
    *,
    mode: Mode,
    speed_mps: float,
    link_kind: str,
    evidence_source: str,
    evidence_confidence: str,
) -> gpd.GeoDataFrame:
    """Adapt eligible physical edges to simple bidirectional routable links."""
    physical = canonical_physical_edges(edges)
    result = physical.rename(columns={"length_m": "physical_length_m"}).copy()
    result["routable_link_id"] = mode.value + ":" + result.physical_edge_id.astype(str)
    result["mode"] = mode.value
    result["direction"] = LinkDirection.BOTH.value
    result["link_kind"] = link_kind
    travel_time = result.physical_length_m.astype(float) / speed_mps
    for field in CostComponents().as_link_attributes():
        result[field] = 0.0
    result["movement_time_s"] = travel_time
    result["travel_time_s"] = travel_time
    result["generalized_cost_s"] = travel_time
    result["evidence_source"] = evidence_source
    result["evidence_confidence"] = evidence_confidence
    keep = [
        "routable_link_id",
        "physical_edge_id",
        "u",
        "v",
        "mode",
        "direction",
        "link_kind",
        "physical_length_m",
        *CostComponents().as_link_attributes(),
        "evidence_source",
        "evidence_confidence",
        "geometry",
    ]
    return gpd.GeoDataFrame(result[keep], geometry="geometry", crs=edges.crs)
