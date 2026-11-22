"""Where a destination is entered: its configured gates snapped to a mode's network, or generated ones."""

from __future__ import annotations

import logging
from typing import Any

import geopandas as gpd

from walkbike_routes import config as settings
from walkbike_routes.destinations import (
    AccessPointGenerationPolicy,
    resolve_point_access_points,
    resolve_polygon_access_points,
)
from walkbike_routes.destinations.access_points import ACCESS_POINT_COLUMNS
from walkbike_routes.models import Mode, TravelDirection
from walkbike_routes.study_context import SNAP_M, Context

log = logging.getLogger(__name__)


def access_points(
    context: Context,
    destination: settings.Destination,
    nodes: gpd.GeoDataFrame,
    links: gpd.GeoDataFrame,
    mode: Mode,
) -> gpd.GeoDataFrame:
    """Configured entrances snapped to the mode's network, or generated ones."""
    policy = AccessPointGenerationPolicy(modes=(mode,), maximum_snap_distance_m=SNAP_M)
    gates = context.gates[context.gates.destination_id.eq(destination.id)]
    if "car" in gates.columns:
        gates = gates[~gates.car.astype(bool)]
    if not gates.empty:
        return _snap_gates(gates, nodes, mode)
    target = context.destinations[context.destinations.destination_id.eq(destination.id)].copy()
    if destination.kind == "point":
        target["destination_type"] = "point"
        return resolve_point_access_points(target, nodes, policy)
    target["destination_type"] = "polygon"
    found = resolve_polygon_access_points(target, nodes, links, policy)
    if found.empty:
        raise settings.ConfigError(f"destination {destination.id!r}: no street enters its area")
    return found


def _snap_gates(gates: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame, mode: Mode) -> gpd.GeoDataFrame:
    joined = gpd.sjoin_nearest(
        gates[["access_point_id", "destination_id", "geometry"]],
        nodes[["node_id", "geometry"]],
        how="left", max_distance=SNAP_M, distance_col="snap_distance_m",
    )  # fmt: skip
    joined = joined.sort_values(["access_point_id", "snap_distance_m", "node_id"]).drop_duplicates(
        "access_point_id"
    )
    lost = joined[joined.node_id.isna()].access_point_id.tolist()
    if lost:
        raise settings.ConfigError(
            f"entrances {lost} are more than {SNAP_M:.0f} m from the network"
        )
    node_points = dict(zip(nodes.node_id.astype(str), nodes.geometry, strict=True))
    rows: list[dict[str, Any]] = [
        {
            "access_point_id": str(row.access_point_id),
            "destination_id": str(row.destination_id),
            "node_id": str(row.node_id),
            "mode": mode.value,
            "direction": TravelDirection.BOTH.value,
            "access_role": "configured entrance",
            "source": "study config",
            "confidence": "as configured",
            "review_status": "configured",
            "snap_distance_m": float(str(row.snap_distance_m)),
            "access_weight": 1.0,
            "geometry": node_points[str(row.node_id)],
        }
        for row in joined.itertuples(index=False)
    ]
    return gpd.GeoDataFrame(
        rows, columns=list(ACCESS_POINT_COLUMNS), geometry="geometry", crs=nodes.crs
    )


def car_gates(
    context: Context, destination: settings.Destination, nodes: gpd.GeoDataFrame
) -> list[str]:
    """Car stopping points: configured, else the entrances, else the nearest street."""
    gates = context.gates[context.gates.destination_id.eq(destination.id)]
    if "car" in gates.columns:  # configured drop-off points, flagged by the layer builder
        car_only = gates[gates.car.astype(bool)]
        gates = car_only if not car_only.empty else gates[~gates.car.astype(bool)]
    if not gates.empty:
        return sorted(set(_snap_gates(gates, nodes, Mode.CAR).node_id))
    target = context.destinations[context.destinations.destination_id.eq(destination.id)]
    shape = target.geometry.iloc[0]
    if destination.kind == "area":
        inside = nodes[nodes.geometry.within(shape)]
        if not inside.empty:
            return sorted(inside.node_id.astype(str))
    distance = nodes.geometry.distance(shape).to_numpy()
    nearest = nodes.iloc[int(distance.argmin())]
    if float(distance.min()) > SNAP_M:
        raise settings.ConfigError(
            f"destination {destination.id!r}: no street within {SNAP_M:.0f} m"
        )
    return [str(nearest.node_id)]
