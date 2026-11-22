"""Resolve destination geometries to canonical network access points."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import geopandas as gpd
from shapely.geometry.base import BaseGeometry

from walkbike_routes.exceptions import AccessibilityError
from walkbike_routes.models import DestinationType, Mode, TravelDirection
from walkbike_routes.validation import require_metric_projected_crs

ACCESS_POINT_COLUMNS = (
    "access_point_id",
    "destination_id",
    "node_id",
    "mode",
    "direction",
    "access_role",
    "source",
    "confidence",
    "review_status",
    "snap_distance_m",
    "access_weight",
    "geometry",
)


@dataclass(frozen=True)
class AccessPointGenerationPolicy:
    """Modes, directions, and evidence labels for generated access points."""

    modes: tuple[Mode, ...]
    directions: tuple[TravelDirection, ...] = (TravelDirection.BOTH,)
    maximum_snap_distance_m: float = 200.0
    source: str = "generated from canonical network geometry"
    confidence: str = "provisional"
    review_status: str = "candidate_unreviewed"

    def __post_init__(self) -> None:
        if not self.modes:
            raise AccessibilityError("an access-point policy requires at least one mode")
        if not self.directions:
            raise AccessibilityError("an access-point policy requires at least one direction")
        if self.maximum_snap_distance_m < 0:
            raise AccessibilityError("maximum_snap_distance_m must be nonnegative")


def _empty(crs: Any) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {column: [] for column in ACCESS_POINT_COLUMNS if column != "geometry"},
        geometry=gpd.GeoSeries([], crs=crs),
        crs=crs,
    )


def _check_crs(*frames: gpd.GeoDataFrame) -> None:
    populated = [frame for frame in frames if frame is not None]
    if not populated:
        return
    for frame in populated:
        require_metric_projected_crs(frame, "destination resolution input")
    expected = populated[0].crs
    if expected is None or any(frame.crs != expected for frame in populated[1:]):
        raise AccessibilityError("destination resolution inputs must share one declared CRS")


def _nearest_node(
    geometry: BaseGeometry,
    nodes: gpd.GeoDataFrame,
    maximum_distance_m: float,
) -> tuple[str, BaseGeometry, float]:
    if nodes.empty:
        raise AccessibilityError("cannot resolve a destination against an empty node inventory")
    ranked = nodes[["node_id", "geometry"]].copy()
    ranked["snap_distance_m"] = ranked.geometry.distance(geometry)
    ranked["node_sort"] = ranked.node_id.astype(str)
    nearest = ranked.sort_values(["snap_distance_m", "node_sort"]).iloc[0]
    distance = float(str(nearest.snap_distance_m))
    if distance > maximum_distance_m:
        raise AccessibilityError(
            f"nearest network node is {distance:.1f} m away, beyond the "
            f"{maximum_distance_m:.1f} m access-point limit"
        )
    return str(nearest.node_id), nearest.geometry, distance


def _rows_for_node(
    *,
    destination_id: str,
    access_key: str,
    node_id: str,
    node_geometry: BaseGeometry,
    role: str,
    policy: AccessPointGenerationPolicy,
    snap_distance_m: float,
    access_weight: float = 1.0,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for mode in policy.modes:
        for direction in policy.directions:
            rows.append(
                {
                    "access_point_id": f"{destination_id}:{access_key}:{node_id}",
                    "destination_id": destination_id,
                    "node_id": node_id,
                    "mode": mode.value,
                    "direction": direction.value,
                    "access_role": role,
                    "source": policy.source,
                    "confidence": policy.confidence,
                    "review_status": policy.review_status,
                    "snap_distance_m": snap_distance_m,
                    "access_weight": access_weight,
                    "geometry": node_geometry,
                }
            )
    return rows


def select_access_points(
    access_points: gpd.GeoDataFrame,
    *,
    destination_id: str,
    mode: Mode,
    direction: TravelDirection,
) -> gpd.GeoDataFrame:
    """Select access points eligible for a destination, mode, and trip direction."""
    if direction is TravelDirection.BOTH:
        allowed_directions = {value.value for value in TravelDirection}
    else:
        allowed_directions = {direction.value, TravelDirection.BOTH.value}
    selected = access_points[
        access_points.destination_id.astype(str).eq(destination_id)
        & access_points["mode"].astype(str).eq(mode.value)
        & access_points.direction.astype(str).isin(allowed_directions)
    ].copy()
    return gpd.GeoDataFrame(selected, geometry="geometry", crs=access_points.crs)


def resolve_point_access_points(
    destinations: gpd.GeoDataFrame,
    nodes: gpd.GeoDataFrame,
    policy: AccessPointGenerationPolicy,
) -> gpd.GeoDataFrame:
    """Snap each point destination to its nearest network node."""
    _check_crs(destinations, nodes)
    rows: list[dict[str, object]] = []
    points = destinations[destinations.destination_type.astype(str).eq(DestinationType.POINT.value)]
    for raw_destination in points.itertuples(index=False):
        destination: Any = raw_destination
        node_id, geometry, distance = _nearest_node(
            destination.geometry,
            nodes,
            policy.maximum_snap_distance_m,
        )
        rows.extend(
            _rows_for_node(
                destination_id=str(destination.destination_id),
                access_key="point",
                node_id=node_id,
                node_geometry=geometry,
                role="snapped point destination",
                policy=policy,
                snap_distance_m=distance,
            )
        )
    if not rows:
        return _empty(destinations.crs)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=destinations.crs)


def _polygon_candidate_nodes(
    polygon: BaseGeometry,
    nodes: gpd.GeoDataFrame,
    links: gpd.GeoDataFrame,
) -> list[tuple[str, BaseGeometry, float]]:
    boundary = polygon.boundary
    candidates: dict[str, tuple[BaseGeometry, float]] = {}
    crossing_links = links[links.geometry.intersects(boundary)]
    node_lookup = nodes.set_index(nodes.node_id.astype(str), drop=False)
    for raw_link in crossing_links.itertuples(index=False):
        link: Any = raw_link
        endpoints: list[tuple[str, BaseGeometry, bool, float]] = []
        for raw_node_id in (link.u, link.v):
            node_id = str(raw_node_id)
            if node_id not in node_lookup.index:
                continue
            node = node_lookup.loc[node_id]
            geometry = cast(BaseGeometry, node.geometry)
            endpoints.append(
                (
                    node_id,
                    geometry,
                    bool(polygon.covers(geometry)),
                    float(boundary.distance(geometry)),
                )
            )
        inside = [candidate for candidate in endpoints if candidate[2]]
        chosen = inside or sorted(endpoints, key=lambda item: (item[3], item[0]))[:1]
        for node_id, geometry, _is_inside, distance in chosen:
            current = candidates.get(node_id)
            if current is None or distance < current[1]:
                candidates[node_id] = (geometry, distance)
    return [
        (node_id, geometry, distance)
        for node_id, (geometry, distance) in sorted(candidates.items())
    ]


def resolve_polygon_access_points(
    destinations: gpd.GeoDataFrame,
    nodes: gpd.GeoDataFrame,
    routable_links: gpd.GeoDataFrame,
    policy: AccessPointGenerationPolicy,
) -> gpd.GeoDataFrame:
    """Generate network-node candidates where links enter a polygon boundary.

    Each candidate is tied to an actual entering link endpoint. The polygon
    centroid is deliberately not used as a routing target.
    """
    _check_crs(destinations, nodes, routable_links)
    rows: list[dict[str, object]] = []
    polygons = destinations[
        destinations.destination_type.astype(str).eq(DestinationType.POLYGON.value)
    ]
    for raw_destination in polygons.itertuples(index=False):
        destination: Any = raw_destination
        for mode in policy.modes:
            mode_links = routable_links[routable_links["mode"].astype(str).eq(mode.value)]
            mode_policy = AccessPointGenerationPolicy(
                modes=(mode,),
                directions=policy.directions,
                maximum_snap_distance_m=policy.maximum_snap_distance_m,
                source=policy.source,
                confidence=policy.confidence,
                review_status=policy.review_status,
            )
            candidates = _polygon_candidate_nodes(destination.geometry, nodes, mode_links)
            for node_id, geometry, distance in candidates:
                rows.extend(
                    _rows_for_node(
                        destination_id=str(destination.destination_id),
                        access_key="boundary",
                        node_id=node_id,
                        node_geometry=geometry,
                        role="generated polygon boundary candidate",
                        policy=mode_policy,
                        snap_distance_m=distance,
                    )
                )
    if not rows:
        return _empty(destinations.crs)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=destinations.crs)
